"""Índice diario de EDGAR (form.YYYYMMDD.idx) y sincronización diaria de Form 4.

Formato verificado con índices reales: cabecera de ~10 líneas, una línea de guiones y luego
columnas de ancho fijo: Form Type | Company Name | CIK | Date Filed | File Name.
Un mismo filing aparece una vez por cada entidad relacionada (emisor y cada insider), así
que se deduplica por número de accesión.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

import duckdb

from tradingtool.edgar.client import SEC_ARCHIVES, EdgarClient, EdgarHTTPError
from tradingtool.edgar.form4 import FORM4_TYPES, Form4ParseError, parse_submission_text
from tradingtool.insiders.store import upsert_filings
from tradingtool.models import Form4Filing

log = logging.getLogger(__name__)

_LINE_RE = re.compile(
    r"^(?P<form>\S+(?: \S+)*?)\s{2,}(?P<company>.*?)\s+(?P<cik>\d{1,10})\s+"
    r"(?P<date>\d{8}|\d{4}-\d{2}-\d{2})\s+(?P<path>edgar/\S+)\s*$"
)
_ACCESSION_RE = re.compile(r"(\d{10}-\d{2}-\d{6})")


@dataclass(frozen=True)
class IndexEntry:
    form_type: str
    company: str
    cik: str
    date_filed: str
    path: str  # relativo a /Archives, p. ej. edgar/data/123/0000000000-26-000001.txt

    @property
    def accession(self) -> str | None:
        m = _ACCESSION_RE.search(self.path)
        return m.group(1) if m else None

    @property
    def url(self) -> str:
        return f"{SEC_ARCHIVES}/{self.path}"


def daily_index_url(day: date) -> str:
    q = (day.month - 1) // 3 + 1
    return f"{SEC_ARCHIVES}/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx"


def parse_form_index(text: str) -> list[IndexEntry]:
    entries: list[IndexEntry] = []
    started = False
    for line in text.splitlines():
        if not started:
            if line.startswith("-----"):
                started = True
            continue
        if not line.strip():
            continue
        m = _LINE_RE.match(line.rstrip())
        if not m:
            log.debug("Línea de índice no reconocida: %r", line)
            continue
        entries.append(
            IndexEntry(
                form_type=m.group("form").strip(),
                company=m.group("company").strip(),
                cik=m.group("cik"),
                date_filed=m.group("date"),
                path=m.group("path"),
            )
        )
    return entries


def form4_entries(entries: list[IndexEntry], include_amendments: bool = True) -> list[IndexEntry]:
    wanted = set(FORM4_TYPES) if include_amendments else {"4"}
    seen: set[str] = set()
    out = []
    for e in entries:
        acc = e.accession
        if e.form_type in wanted and acc and acc not in seen:
            seen.add(acc)
            out.append(e)
    return out


@dataclass
class SyncStats:
    day: date
    index_found: bool = False
    candidates: int = 0
    already_present: int = 0
    stored: int = 0
    errors: list[str] = field(default_factory=list)


def sync_form4_day(
    client: EdgarClient,
    con: duckdb.DuckDBPyConnection,
    day: date,
    include_amendments: bool = True,
    batch_size: int = 200,
    limit: int | None = None,
) -> SyncStats:
    """Descarga y guarda todos los Form 4 presentados en ``day``. Idempotente."""
    stats = SyncStats(day=day)
    if day.weekday() >= 5:  # sábado/domingo: la SEC no publica índice
        return stats
    try:
        text = client.get_text(daily_index_url(day), allow_404=True)
    except EdgarHTTPError as exc:
        # La SEC responde 403 (no 404) cuando un archivo de Archives no existe, p. ej. el
        # índice de un feriado o de un día aún no publicado. Se trata como "sin índice".
        if exc.status != 403:
            raise
        log.info("Índice de %s no disponible (403): feriado o aún no publicado", day)
        text = None
    if text is None:
        log.info("Sin índice diario para %s (fin de semana, feriado o aún no publicado)", day)
        return stats
    stats.index_found = True
    entries = form4_entries(parse_form_index(text), include_amendments)
    stats.candidates = len(entries)
    if entries:
        accs = [e.accession for e in entries]
        present = {
            r[0]
            for r in con.execute(
                "SELECT accession FROM insider_filings WHERE accession IN "
                "(SELECT unnest(?::VARCHAR[]))",
                [accs],
            ).fetchall()
        }
        entries = [e for e in entries if e.accession not in present]
        stats.already_present = len(present)
    if limit is not None:
        entries = entries[:limit]

    batch: list[Form4Filing] = []
    for i, e in enumerate(entries, 1):
        try:
            sub = client.get_text(e.url)
            if sub is None:  # pragma: no cover - get_text solo devuelve None con allow_404
                continue
            batch.append(parse_submission_text(sub, source="edgar_daily"))
        except Form4ParseError as exc:
            stats.errors.append(f"{e.accession}: {exc}")
            log.warning("No se pudo parsear %s: %s", e.accession, exc)
        except Exception as exc:
            stats.errors.append(f"{e.accession}: {exc!r}")
            log.warning("Error descargando %s: %r", e.accession, exc)
        if len(batch) >= batch_size:
            stats.stored += upsert_filings(con, batch)
            batch = []
        if i % 250 == 0:
            log.info("%s: %d/%d filings procesados", day, i, len(entries))
    if batch:
        stats.stored += upsert_filings(con, batch)
    return stats
