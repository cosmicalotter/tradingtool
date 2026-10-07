"""Tests del cargador de datasets trimestrales de la SEC con TSV en el formato oficial
(columnas tomadas del metadata *_FORM_345_metadata.json de la SEC)."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from tradingtool.edgar.bulk import (
    build_zip,
    download_quarter,
    extract_tsvs,
    load_zip_bytes,
    parse_quarter,
    quarter_range,
)
from tradingtool.edgar.client import EdgarClient
from tradingtool.insiders.store import upsert_filings
from tradingtool.models import Form4Filing

SUB_HEADER = (
    "ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tDATE_OF_ORIG_SUB\tNO_SECURITIES_OWNED\t"
    "NOT_SUBJECT_SEC16\tFORM3_HOLDINGS_REPORTED\tFORM4_TRANS_REPORTED\tDOCUMENT_TYPE\tISSUERCIK\t"
    "ISSUERNAME\tISSUERTRADINGSYMBOL\tCONTACT_NAME\tCONTACT_PHONE_NUMBER\tCONTACT_EMAIL_ADDRESS\t"
    "NOTIFICATION_EMAIL_ADDRESS\tREMARKS\tAFF10B5ONE"
)
OWN_HEADER = (
    "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\t"
    "RPTOWNER_TXT\tRPTOWNER_STREET1\tRPTOWNER_STREET2\tRPTOWNER_CITY\tRPTOWNER_STATE\t"
    "RPTOWNER_ZIPCODE\tRPTOWNER_STATE_DESC\tFILE_NUMBER"
)
NTR_HEADER = (
    "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tSECURITY_TITLE_FN\tTRANS_DATE\t"
    "TRANS_DATE_FN\tDEEMED_EXECUTION_DATE\tDEEMED_EXECUTION_DATE_FN\tTRANS_FORM_TYPE\tTRANS_CODE\t"
    "EQUITY_SWAP_INVOLVED\tEQUITY_SWAP_INVOLVED_FN\tTRANS_TIMELINESS\tTRANS_TIMELINESS_FN\t"
    "TRANS_SHARES\tTRANS_SHARES_FN\tTRANS_PRICEPERSHARE\tTRANS_PRICEPERSHARE_FN\t"
    "TRANS_ACQUIRED_DISP_CD\tTRANS_ACQUIRED_DISP_CD_FN\tSHRS_OWND_FOLWNG_TRANS\t"
    "SHRS_OWND_FOLWNG_TRANS_FN\tVALU_OWND_FOLWNG_TRANS\tVALU_OWND_FOLWNG_TRANS_FN\t"
    "DIRECT_INDIRECT_OWNERSHIP\tDIRECT_INDIRECT_OWNERSHIP_FN\tNATURE_OF_OWNERSHIP\t"
    "NATURE_OF_OWNERSHIP_FN"
)


def _row(*vals):
    return "\t".join(vals)


def _sub_rows():
    return [
        _row(
            "0001250853-24-000009",
            "01-JUL-2024",
            "28-JUN-2024",
            "",
            "0",
            "0",
            "0",
            "1",
            "4",
            "1234",
            "Acme Corp",
            "acme",
            "",
            "",
            "",
            "",
            "",
            "0",
        ),
        _row(
            "0001250853-24-000010",
            "02-JUL-2024",
            "01-JUL-2024",
            "",
            "0",
            "0",
            "0",
            "1",
            "4",
            "0000005678",
            'Beta "Quoted" Inc',
            "N/A",
            "",
            "",
            "",
            "",
            "",
            "true",
        ),
        _row(
            "0001250853-24-000011",
            "03-JUL-2024",
            "01-JUL-2024",
            "",
            "0",
            "0",
            "1",
            "0",
            "3",
            "0000005678",
            "Beta Inc",
            "BETA",
            "",
            "",
            "",
            "",
            "",
            "",
        ),
        _row(
            "0001250853-24-000012",
            "05-JUL-2024",
            "01-JUL-2024",
            "02-JUL-2024",
            "0",
            "0",
            "0",
            "1",
            "4/A",
            "0000005678",
            "Beta Inc",
            "BETA, BETAW",
            "",
            "",
            "",
            "",
            "",
            "",
        ),
    ]


def _own_rows():
    return [
        _row(
            "0001250853-24-000009",
            "999",
            "DOE JANE",
            "Director,Officer",
            "CEO",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "001-1",
        ),
        _row(
            "0001250853-24-000009",
            "0000000888",
            "DOE TRUST",
            "TenPercentOwner",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "001-1",
        ),
        _row(
            "0001250853-24-000010",
            "777",
            "FUND LP",
            "TenPercentOwner",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "001-2",
        ),
        _row(
            "0001250853-24-000012",
            "666",
            "ROE RICH",
            "Director",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "001-2",
        ),
    ]


def _ntr_rows():
    return [
        _row(
            "0001250853-24-000009",
            "6711005",
            "Common Stock",
            "",
            "28-JUN-2024",
            "",
            "",
            "",
            "4",
            "P",
            "0",
            "",
            "",
            "",
            "49990.0",
            "",
            "1.27",
            "F1",
            "A",
            "",
            "2511822.0",
            "",
            "",
            "",
            "D",
            "",
            "",
            "",
        ),
        _row(
            "0001250853-24-000009",
            "6711004",
            "Common Stock",
            "",
            "27-JUN-2024",
            "",
            "",
            "",
            "4",
            "P",
            "0",
            "",
            "",
            "",
            "258010.0",
            "",
            "1.27",
            "",
            "A",
            "",
            "2461832.0",
            "",
            "",
            "",
            "D",
            "",
            "",
            "",
        ),
        _row(
            "0001250853-24-000010",
            "6711006",
            "Common Stock",
            "",
            "01-JUL-2024",
            "",
            "",
            "",
            "4",
            "S",
            "0",
            "",
            "",
            "",
            "1000",
            "",
            "",
            "F2",
            "D",
            "",
            "0",
            "",
            "",
            "",
            "I",
            "",
            "",
            "",
        ),
        _row(
            "0001250853-24-000012",
            "6711007",
            "Common Stock",
            "",
            "01-JUL-2024",
            "",
            "",
            "",
            "4",
            "P",
            "",
            "",
            "",
            "",
            "100",
            "",
            "20.5",
            "",
            "A",
            "",
            "1100",
            "",
            "",
            "",
            "D",
            "",
            "",
            "",
        ),
    ]


def _zip(sub_header=SUB_HEADER, sub_rows=None):
    sub_rows = _sub_rows() if sub_rows is None else sub_rows
    return build_zip(
        {
            "SUBMISSION.tsv": "\n".join([sub_header, *sub_rows]) + "\n",
            "REPORTINGOWNER.tsv": "\n".join([OWN_HEADER, *_own_rows()]) + "\n",
            "NONDERIV_TRANS.tsv": "\n".join([NTR_HEADER, *_ntr_rows()]) + "\n",
            "FOOTNOTES.tsv": "ACCESSION_NUMBER\tFOOTNOTE_ID\tFOOTNOTE_TXT\n",
        }
    )


def test_quarter_helpers():
    assert parse_quarter("2024Q3") == (2024, 3)
    assert quarter_range("2024q3", "2025Q2") == ["2024q3", "2024q4", "2025q1", "2025q2"]
    with pytest.raises(ValueError):
        parse_quarter("2005Q4")
    with pytest.raises(ValueError):
        parse_quarter("2024-3")
    with pytest.raises(ValueError):
        quarter_range("2025Q1", "2024Q1")


def test_load_quarter_maps_fields(con, tmp_path):
    stats = load_zip_bytes(con, _zip(), "2024q3", tmp_path)
    assert stats.filings_in_file == 3  # el Form 3 se excluye
    assert stats.filings_inserted == 3
    assert stats.transactions_inserted == 4
    f = con.execute(
        "select accession, source, form_type, filing_date, issuer_cik, issuer_ticker, aff10b5one,"
        " acceptance_ts from insider_filings order by accession"
    ).fetchall()
    assert f[0] == (
        "0001250853-24-000009",
        "sec_bulk",
        "4",
        date(2024, 7, 1),
        "0000001234",
        "ACME",
        False,
        None,
    )
    assert f[1][5] is None and f[1][6] is True  # ticker N/A -> None ; 'true' -> True
    assert f[2][2] == "4/A" and f[2][5] == "BETA" and f[2][6] is None

    owners = con.execute(
        "select accession, owner_seq, owner_cik, is_director, is_officer, is_ten_pct_owner, "
        "officer_title from insider_owners order by accession, owner_seq"
    ).fetchall()
    assert owners[0] == ("0001250853-24-000009", 0, "0000000999", True, True, False, "CEO")
    assert owners[1][2:6] == ("0000000888", False, False, True)

    tx = con.execute(
        "select seq, transaction_date, transaction_code, shares, price_per_share, footnote_ids "
        "from insider_transactions where accession = '0001250853-24-000009' order by seq"
    ).fetchall()
    # ordenadas por NONDERIV_TRANS_SK, no por orden de archivo
    assert tx[0] == (0, date(2024, 6, 27), "P", 258010.0, 1.27, None)
    assert tx[1] == (1, date(2024, 6, 28), "P", 49990.0, 1.27, "F1")
    s = con.execute(
        "select price_per_share, direct_indirect from insider_transactions "
        "where accession = '0001250853-24-000010'"
    ).fetchone()
    assert s == (None, "I")


def test_load_is_idempotent_and_respects_existing(con, tmp_path):
    existing = Form4Filing(
        accession="0001250853-24-000009",
        source="edgar_daily",
        form_type="4",
        filing_date=date(2024, 7, 1),
        acceptance_ts=None,
        period_of_report=None,
        issuer_cik="0000001234",
        issuer_name="Acme",
        issuer_ticker="ACME",
        aff10b5one=False,
    )
    upsert_filings(con, [existing])
    s1 = load_zip_bytes(con, _zip(), "2024q3", tmp_path)
    assert s1.filings_inserted == 2
    assert (
        con.execute(
            "select source from insider_filings where accession='0001250853-24-000009'"
        ).fetchone()[0]
        == "edgar_daily"
    )
    s2 = load_zip_bytes(con, _zip(), "2024q3", tmp_path / "again")
    assert s2.filings_inserted == 0
    assert con.execute("select count(*) from insider_filings").fetchone()[0] == 3


def test_old_format_without_aff10b5one(con, tmp_path):
    header = SUB_HEADER.rsplit("\t", 1)[0]
    rows = [r.rsplit("\t", 1)[0] for r in _sub_rows()]
    stats = load_zip_bytes(con, _zip(header, rows), "2015q1", tmp_path)
    assert stats.filings_inserted == 3
    assert (
        con.execute("select count(*) from insider_filings where aff10b5one is not null").fetchone()[
            0
        ]
        == 0
    )


def test_extract_requires_files_and_blocks_zip_slip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(build_zip({"SUBMISSION.tsv": "x"}))
    with pytest.raises(ValueError, match="faltan"):
        extract_tsvs(bad, tmp_path / "out")
    slip = tmp_path / "slip.zip"
    slip.write_bytes(
        build_zip(
            {"../../SUBMISSION.tsv": "a", "x/REPORTINGOWNER.tsv": "b", "NONDERIV_TRANS.tsv": "c"}
        )
    )
    out = extract_tsvs(slip, tmp_path / "out2")
    assert all(p.parent == tmp_path / "out2" for p in out.values())


def test_download_falls_back_to_second_url(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if "datastandardsinnovation" in str(request.url):
            return httpx.Response(200, content=b"ZIPDATA")
        return httpx.Response(404)

    client = EdgarClient(
        "T U t@e.com", transport=httpx.MockTransport(handler), sleep=lambda s: None
    )
    p = download_quarter(client, "2026q2", tmp_path)
    assert p.read_bytes() == b"ZIPDATA"
    assert len(seen) == 2
    # segunda vez usa el archivo local
    seen.clear()
    download_quarter(client, "2026q2", tmp_path)
    assert seen == []


def test_download_missing_quarter(tmp_path):
    client = EdgarClient(
        "T U t@e.com",
        transport=httpx.MockTransport(lambda r: httpx.Response(404)),
        sleep=lambda s: None,
    )
    with pytest.raises(FileNotFoundError):
        download_quarter(client, "2030q1", tmp_path)
