"""Carga de los "Insider Transactions Data Sets" trimestrales de la SEC (Forms 3/4/5).

Formato verificado con el metadata oficial (``*_FORM_345_metadata.json``) y muestras reales:
- Un .zip por trimestre (2006Q1 en adelante) con TSV: SUBMISSION, REPORTINGOWNER,
  NONDERIV_TRANS, NONDERIV_HOLDING, DERIV_TRANS, DERIV_HOLDING, FOOTNOTES, OWNER_SIGNATURE.
- Clave de unión: ``ACCESSION_NUMBER``. Fechas en formato ``DD-MON-YYYY``.
- ``SUBMISSION.AFF10B5ONE`` (casilla 10b5-1) solo existe desde 2023; puede valer 1/0/true/false.
- ``REPORTINGOWNER.RPTOWNER_RELATIONSHIP`` es texto como "Director,Officer" o "TenPercentOwner".
- El dataset NO trae la hora de aceptación, solo ``FILING_DATE``.
- La SEC movió la ruta de descarga en 2026 (``structureddata`` -> ``datastandardsinnovation``);
  se prueban ambas.

Solo se cargan Form 4 / 4/A y transacciones NO derivadas (lo que usa la estrategia).
Los filings que ya existen en la base (p. ej. vía EDGAR diario) no se sobrescriben.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import duckdb

from tradingtool.edgar.client import EdgarClient

log = logging.getLogger(__name__)

BULK_URL_TEMPLATES = (
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip",
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{q}_form345.zip",
)
FIRST_QUARTER = (2006, 1)
_Q_RE = re.compile(r"^(\d{4})[qQ]([1-4])$")
_REQUIRED = ("SUBMISSION.tsv", "REPORTINGOWNER.tsv", "NONDERIV_TRANS.tsv")


def parse_quarter(text: str) -> tuple[int, int]:
    m = _Q_RE.match(text.strip())
    if not m:
        raise ValueError(f"trimestre inválido {text!r}; usa el formato 2024Q1")
    year, q = int(m.group(1)), int(m.group(2))
    if (year, q) < FIRST_QUARTER:
        raise ValueError("los datasets de la SEC empiezan en 2006Q1")
    return year, q


def quarter_range(start: str, end: str) -> list[str]:
    y, q = parse_quarter(start)
    ey, eq = parse_quarter(end)
    if (y, q) > (ey, eq):
        raise ValueError("el trimestre inicial es posterior al final")
    out = []
    while (y, q) <= (ey, eq):
        out.append(f"{y}q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


@dataclass
class BulkLoadStats:
    quarter: str
    filings_in_file: int = 0
    filings_inserted: int = 0
    owners_inserted: int = 0
    transactions_inserted: int = 0


def download_quarter(client: EdgarClient, quarter: str, dest_dir: Path) -> Path:
    """Descarga el zip del trimestre (si no está ya en disco). Devuelve la ruta local."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{quarter}_form345.zip"
    if path.exists() and path.stat().st_size > 0:
        return path
    for tpl in BULK_URL_TEMPLATES:
        data = client.get_bytes(tpl.format(q=quarter), allow_404=True)
        if data:
            tmp = path.with_suffix(".zip.part")
            tmp.write_bytes(data)
            tmp.replace(path)
            return path
    raise FileNotFoundError(f"La SEC no tiene (aún) el dataset {quarter}")


def extract_tsvs(zip_path: Path, dest_dir: Path) -> dict[str, Path]:
    """Extrae los TSV necesarios (sin confiar en las rutas internas del zip)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name  # ignora directorios: evita "zip slip"
            if name in _REQUIRED:
                target = dest_dir / name
                with zf.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                out[name] = target
    missing = [n for n in _REQUIRED if n not in out]
    if missing:
        raise ValueError(f"{zip_path.name}: faltan archivos {missing}")
    return out


def _read_tsv_sql(path: Path) -> str:
    p = str(path).replace("'", "''")
    return (
        f"read_csv('{p}', delim='\\t', header=true, all_varchar=true, quote='', escape='', "
        "strict_mode=false, null_padding=true, ignore_errors=true)"
    )


def _columns(con: duckdb.DuckDBPyConnection, path: Path) -> set[str]:
    rel = con.sql(f"SELECT * FROM {_read_tsv_sql(path)} LIMIT 0")  # noqa: S608
    return {c.upper() for c in rel.columns}


def _col(cols: set[str], name: str) -> str:
    return name if name in cols else "NULL"


def load_tsvs(con: duckdb.DuckDBPyConnection, tsvs: dict[str, Path], quarter: str) -> BulkLoadStats:
    """Inserta en las tablas ``insider_*`` los Form 4 del trimestre que aún no estén."""
    stats = BulkLoadStats(quarter=quarter)
    sub, own, ntr = tsvs["SUBMISSION.tsv"], tsvs["REPORTINGOWNER.tsv"], tsvs["NONDERIV_TRANS.tsv"]
    sub_cols, own_cols, ntr_cols = _columns(con, sub), _columns(con, own), _columns(con, ntr)
    aff = _col(sub_cols, "AFF10B5ONE")

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE tt_bulk_sub AS
            SELECT
                trim(ACCESSION_NUMBER) AS accession,
                upper(trim(DOCUMENT_TYPE)) AS form_type,
                try_strptime(trim(FILING_DATE), '%d-%b-%Y')::DATE AS filing_date,
                try_strptime(trim(PERIOD_OF_REPORT), '%d-%b-%Y')::DATE AS period_of_report,
                lpad(regexp_replace(trim(ISSUERCIK), '[^0-9]', '', 'g'), 10, '0') AS issuer_cik,
                trim(ISSUERNAME) AS issuer_name,
                CASE WHEN upper(trim(ISSUERTRADINGSYMBOL)) IN
                        ('', 'NONE', 'N/A', 'NA', 'NULL', '-', '--') THEN NULL
                     ELSE regexp_extract(upper(trim(ISSUERTRADINGSYMBOL)), '^[^,;/ ]+')
                END AS issuer_ticker,
                CASE WHEN lower(trim({aff})) IN ('1', 'true', 'y', 'yes') THEN TRUE
                     WHEN lower(trim({aff})) IN ('0', 'false', 'n', 'no') THEN FALSE
                     ELSE NULL END AS aff10b5one
            FROM {_read_tsv_sql(sub)}
            WHERE upper(trim(DOCUMENT_TYPE)) IN ('4', '4/A')
              AND ACCESSION_NUMBER IS NOT NULL
            """  # noqa: S608
        )
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE tt_bulk_new AS
            SELECT * FROM tt_bulk_sub s
            WHERE s.filing_date IS NOT NULL AND length(s.issuer_cik) = 10
              AND NOT EXISTS (SELECT 1 FROM insider_filings f WHERE f.accession = s.accession)
            QUALIFY row_number() OVER (PARTITION BY accession ORDER BY filing_date) = 1
            """
        )
        stats.filings_in_file = con.execute("SELECT count(*) FROM tt_bulk_sub").fetchone()[0]
        stats.filings_inserted = con.execute("SELECT count(*) FROM tt_bulk_new").fetchone()[0]
        con.execute(
            """
            INSERT INTO insider_filings (accession, source, form_type, filing_date, acceptance_ts,
                period_of_report, issuer_cik, issuer_name, issuer_ticker, aff10b5one)
            SELECT accession, 'sec_bulk', form_type, filing_date, NULL, period_of_report,
                issuer_cik, issuer_name, issuer_ticker, aff10b5one
            FROM tt_bulk_new
            """
        )

        title = _col(own_cols, "RPTOWNER_TITLE")
        con.execute(
            f"""
            INSERT INTO insider_owners (accession, owner_seq, owner_cik, owner_name, is_director,
                is_officer, is_ten_pct_owner, is_other, officer_title)
            SELECT accession,
                (row_number() OVER (PARTITION BY accession ORDER BY rn) - 1)::INTEGER,
                owner_cik, owner_name,
                rel ILIKE '%director%', rel ILIKE '%officer%',
                (rel ILIKE '%tenpercent%' OR rel ILIKE '%10%'), rel ILIKE '%other%',
                officer_title
            FROM (
                SELECT trim(o.ACCESSION_NUMBER) AS accession,
                    lpad(regexp_replace(trim(o.RPTOWNERCIK), '[^0-9]', '', 'g'), 10, '0')
                        AS owner_cik,
                    trim(o.RPTOWNERNAME) AS owner_name,
                    coalesce(o.RPTOWNER_RELATIONSHIP, '') AS rel,
                    nullif(trim({title}), '') AS officer_title,
                    row_number() OVER () AS rn
                FROM {_read_tsv_sql(own)} o
            ) x
            WHERE accession IN (SELECT accession FROM tt_bulk_new)
              AND length(owner_cik) = 10
            QUALIFY row_number() OVER (PARTITION BY accession, owner_cik ORDER BY rn) = 1
            """  # noqa: S608
        )
        stats.owners_inserted = con.execute(
            "SELECT count(*) FROM insider_owners WHERE accession IN "
            "(SELECT accession FROM tt_bulk_new)"
        ).fetchone()[0]

        fn_cols = [
            c
            for c in (
                "TRANS_SHARES_FN",
                "TRANS_PRICEPERSHARE_FN",
                "TRANS_DATE_FN",
                "TRANS_CODE_FN",
                "SHRS_OWND_FOLWNG_TRANS_FN",
                "DIRECT_INDIRECT_OWNERSHIP_FN",
            )
            if c in ntr_cols
        ]
        fn_expr = (
            "nullif(concat_ws(',', "
            + ", ".join(f"nullif(trim({c}), '')" for c in fn_cols)
            + "), '')"
            if fn_cols
            else "NULL"
        )
        swap = _col(ntr_cols, "EQUITY_SWAP_INVOLVED")
        con.execute(
            f"""
            INSERT INTO insider_transactions (accession, seq, is_derivative, security_title,
                transaction_date, transaction_code, shares, price_per_share, acquired_disposed,
                shares_owned_after, direct_indirect, equity_swap, footnote_ids)
            SELECT trim(ACCESSION_NUMBER) AS accession,
                (row_number() OVER (PARTITION BY trim(ACCESSION_NUMBER)
                    ORDER BY try_cast(NONDERIV_TRANS_SK AS BIGINT)) - 1)::INTEGER,
                FALSE,
                trim(SECURITY_TITLE),
                try_strptime(trim(TRANS_DATE), '%d-%b-%Y')::DATE,
                nullif(upper(trim(TRANS_CODE)), ''),
                try_cast(replace(TRANS_SHARES, ',', '') AS DOUBLE),
                try_cast(replace(TRANS_PRICEPERSHARE, ',', '') AS DOUBLE),
                nullif(upper(trim(TRANS_ACQUIRED_DISP_CD)), ''),
                try_cast(replace(SHRS_OWND_FOLWNG_TRANS, ',', '') AS DOUBLE),
                nullif(upper(trim(DIRECT_INDIRECT_OWNERSHIP)), ''),
                CASE WHEN lower(trim({swap})) IN ('1', 'true') THEN TRUE
                     WHEN lower(trim({swap})) IN ('0', 'false') THEN FALSE ELSE NULL END,
                {fn_expr}
            FROM {_read_tsv_sql(ntr)}
            WHERE trim(ACCESSION_NUMBER) IN (SELECT accession FROM tt_bulk_new)
            """  # noqa: S608
        )
        stats.transactions_inserted = con.execute(
            "SELECT count(*) FROM insider_transactions WHERE accession IN "
            "(SELECT accession FROM tt_bulk_new)"
        ).fetchone()[0]
        con.execute("DROP TABLE IF EXISTS tt_bulk_sub")
        con.execute("DROP TABLE IF EXISTS tt_bulk_new")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return stats


def load_quarter(
    client: EdgarClient,
    con: duckdb.DuckDBPyConnection,
    quarter: str,
    raw_dir: Path,
    keep_zip: bool = True,
) -> BulkLoadStats:
    """Descarga (si hace falta), extrae y carga un trimestre. Idempotente."""
    zip_path = download_quarter(client, quarter, raw_dir / "sec_bulk")
    tsvs = extract_tsvs(zip_path, raw_dir / "sec_bulk" / quarter)
    try:
        stats = load_tsvs(con, tsvs, quarter)
    finally:
        for p in tsvs.values():
            p.unlink(missing_ok=True)
        if not keep_zip:
            zip_path.unlink(missing_ok=True)
    log.info(
        "%s: %d filings en archivo, %d nuevos, %d transacciones",
        quarter,
        stats.filings_in_file,
        stats.filings_inserted,
        stats.transactions_inserted,
    )
    return stats


def load_zip_bytes(
    con: duckdb.DuckDBPyConnection, data: bytes, quarter: str, tmp_dir: Path
) -> BulkLoadStats:
    """Atajo para tests: carga un zip en memoria."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_dir / f"{quarter}_form345.zip"
    zip_path.write_bytes(data)
    tsvs = extract_tsvs(zip_path, tmp_dir / quarter)
    return load_tsvs(con, tsvs, quarter)


def build_zip(files: dict[str, str]) -> bytes:
    """Crea un zip en memoria (útil para tests y fixtures)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()
