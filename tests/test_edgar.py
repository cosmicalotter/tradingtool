"""Tests del parser de Form 4 y la sincronización con EDGAR usando filings REALES de la SEC
(descargados de fixtures públicos de proyectos open source) y transporte HTTP simulado."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from tradingtool.edgar.client import EdgarClient, EdgarConfigError, EdgarHTTPError, RateLimiter
from tradingtool.edgar.daily_index import (
    daily_index_url,
    form4_entries,
    parse_form_index,
    sync_form4_day,
)
from tradingtool.edgar.form4 import (
    Form4ParseError,
    normalize_accession,
    normalize_ticker,
    parse_ownership_xml,
    parse_sec_header,
    parse_submission_text,
)

UA = "Test User test@example.com"


def _read(fixtures_dir: Path, name: str) -> str:
    return (fixtures_dir / "edgar" / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- parser XML


def test_parse_real_oxy_purchase(fixtures_dir):
    f = parse_ownership_xml(
        _read(fixtures_dir, "oxy_2026_P.xml"),
        accession="0001628280-26-045313",
        filing_date=date(2026, 6, 24),
    )
    assert f.issuer_cik == "0000797468"
    assert f.issuer_ticker == "OXY"
    assert f.form_type == "4"
    assert f.aff10b5one is False
    assert f.period_of_report == date(2026, 6, 23)
    owner = f.primary_owner
    assert owner.owner_cik == "0001814606"
    assert owner.is_director and owner.is_officer and not owner.is_ten_pct_owner
    assert owner.officer_title == "President and CEO"
    assert len(f.transactions) == 1  # las "holdings" no son transacciones
    t = f.transactions[0]
    assert (t.transaction_code, t.acquired_disposed, t.direct_indirect) == ("P", "A", "D")
    assert t.shares == 4770 and t.price_per_share == 52.38
    assert t.value_usd == pytest.approx(4770 * 52.38)
    assert t.shares_owned_after == 444098
    assert t.transaction_date == date(2026, 6, 23)
    assert not t.is_derivative
    assert f.footnotes["F1"].startswith("Based on a plan statement")


def test_parse_multiple_purchases(fixtures_dir):
    f = parse_ownership_xml(
        _read(fixtures_dir, "multi_P_2023.xml"),
        accession="000032012123000040",
        filing_date=date(2023, 3, 1),
    )
    codes = [t.transaction_code for t in f.transactions if not t.is_derivative]
    assert codes.count("P") == 3
    assert [t.seq for t in f.transactions] == list(range(len(f.transactions)))
    assert f.accession == "0000320121-23-000040"


def test_parse_mixed_codes_and_derivatives(fixtures_dir):
    f = parse_ownership_xml(
        _read(fixtures_dir, "unh_A_and_P.xml"),
        accession="0000731766-23-000001",
        filing_date=date(2023, 1, 1),
    )
    assert {t.transaction_code for t in f.transactions} >= {"A", "P"}
    d = parse_ownership_xml(
        _read(fixtures_dir, "derivative_only.xml"),
        accession="0000875320-23-000001",
        filing_date=date(2023, 1, 1),
    )
    assert d.transactions and all(t.is_derivative for t in d.transactions)


def test_parse_rejects_bad_xml_and_xxe():
    with pytest.raises(Form4ParseError):
        parse_ownership_xml("<not-xml", accession="0000000000-00-000000", filing_date=date.today())
    evil = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        "<ownershipDocument><issuer><issuerCik>1</issuerCik></issuer>&e;</ownershipDocument>"
    )
    with pytest.raises(Form4ParseError):
        parse_ownership_xml(evil, accession="0000000000-00-000000", filing_date=date.today())


def test_parse_bool_variants_and_missing_values():
    xml = """<ownershipDocument><documentType>4/A</documentType>
      <issuer><issuerCik>123</issuerCik><issuerTradingSymbol>none</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik><rptOwnerName>X</rptOwnerName>
      </reportingOwnerId><reportingOwnerRelationship><isDirector>true</isDirector>
      </reportingOwnerRelationship></reportingOwner>
      <aff10b5One>true</aff10b5One>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
        <transactionAmounts><transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare>
        </transactionAmounts></nonDerivativeTransaction></nonDerivativeTable>
      </ownershipDocument>"""
    f = parse_ownership_xml(xml, accession="000000000000000001", filing_date=date(2026, 1, 2))
    assert f.form_type == "4/A"
    assert f.issuer_cik == "0000000123"
    assert f.issuer_ticker is None
    assert f.aff10b5one is True
    assert f.owners[0].is_director and not f.owners[0].is_officer
    t = f.transactions[0]
    assert t.price_per_share is None and t.value_usd is None
    assert t.footnote_ids == ("F1",)


def test_normalizers():
    assert normalize_ticker(" brk.b ") == "BRK.B"
    assert normalize_ticker("ABC, ABCW") == "ABC"
    assert normalize_ticker("N/A") is None
    assert normalize_accession("000112760225001055") == "0001127602-25-001055"
    with pytest.raises(Form4ParseError):
        normalize_accession("123")


# ----------------------------------------------------------------------------- envío completo


def test_parse_real_submission_text(fixtures_dir):
    text = _read(fixtures_dir, "submission_0001127602-25-001055.txt")
    h = parse_sec_header(text)
    assert h["acceptance_ts"] == datetime(2025, 1, 10, 16, 7, 30)
    assert h["filing_date"] == date(2025, 1, 10)
    assert h["form_type"] == "4"
    f = parse_submission_text(text)
    assert f.accession == "0001127602-25-001055"
    assert f.issuer_cik == "0000001750"  # AAR CORP
    assert f.acceptance_ts == datetime(2025, 1, 10, 16, 7, 30)
    assert f.filing_date == date(2025, 1, 10)
    assert f.primary_owner.owner_cik == "0001806647"


def test_submission_rejects_non_form4(fixtures_dir):
    text = _read(fixtures_dir, "submission_0001127602-25-001055.txt").replace(
        "CONFORMED SUBMISSION TYPE:\t4", "CONFORMED SUBMISSION TYPE:\t8-K"
    )
    with pytest.raises(Form4ParseError):
        parse_submission_text(text)


# ----------------------------------------------------------------------------- índice diario


def test_parse_real_daily_index(fixtures_dir):
    entries = parse_form_index(_read(fixtures_dir, "form.sample.idx"))
    assert len(entries) == 31
    f4 = form4_entries(entries)
    assert len(f4) == 27
    e = f4[0]
    assert e.form_type == "4"
    assert e.cik == "792977"
    assert e.accession == "0000919574-20-002488"
    assert e.url.endswith("/Archives/edgar/data/792977/0000919574-20-002488.txt")


def test_form4_entries_dedupes_by_accession():
    text = (
        "header\n-----\n"
        "4           ISSUER INC                                                    111         20260105    edgar/data/111/0000000001-26-000001.txt\n"
        "4           OWNER JOHN                                                    222         20260105    edgar/data/222/0000000001-26-000001.txt\n"
        "4/A         OTHER CO                                                      333         20260105    edgar/data/333/0000000001-26-000002.txt\n"
        "SC 13D      SOMEONE                                                       444         20260105    edgar/data/444/0000000001-26-000003.txt\n"
    )
    e = parse_form_index(text)
    assert [x.form_type for x in e] == ["4", "4", "4/A", "SC 13D"]
    assert len(form4_entries(e)) == 2
    assert len(form4_entries(e, include_amendments=False)) == 1


def test_daily_index_url():
    assert daily_index_url(date(2026, 10, 6)).endswith("/daily-index/2026/QTR4/form.20261006.idx")
    assert "QTR1" in daily_index_url(date(2026, 3, 31))


# ----------------------------------------------------------------------------- cliente HTTP


def test_user_agent_required():
    with pytest.raises(EdgarConfigError):
        EdgarClient("")
    with pytest.raises(EdgarConfigError):
        EdgarClient("solo-nombre")


def test_rate_limiter_enforces_interval():
    t = [0.0]
    sleeps = []

    def clock():
        return t[0]

    def sleep(s):
        sleeps.append(s)
        t[0] += s

    rl = RateLimiter(5, clock=clock, sleep=sleep)
    rl.wait()
    rl.wait()
    assert sleeps and sleeps[0] == pytest.approx(0.2)
    with pytest.raises(ValueError):
        RateLimiter(11)


def test_client_retries_then_succeeds_and_sends_ua():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["User-Agent"])
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, text="ok")

    c = EdgarClient(UA, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert c.get_text("https://www.sec.gov/x") == "ok"
    assert calls == [UA] * 3


def test_client_404_and_403():
    c = EdgarClient(
        UA, transport=httpx.MockTransport(lambda r: httpx.Response(404)), sleep=lambda s: None
    )
    assert c.get_text("https://www.sec.gov/x", allow_404=True) is None
    with pytest.raises(EdgarHTTPError):
        c.get_text("https://www.sec.gov/x")
    c2 = EdgarClient(
        UA, transport=httpx.MockTransport(lambda r: httpx.Response(403)), sleep=lambda s: None
    )
    with pytest.raises(EdgarHTTPError, match="User-Agent"):
        c2.get_text("https://www.sec.gov/x")


# ----------------------------------------------------------------------------- sincronización


def test_sync_day_end_to_end(con, fixtures_dir):
    sub = _read(fixtures_dir, "submission_0001127602-25-001055.txt")
    index = (
        "Description: Daily Index\n-----\n"
        "4           AAR CORP                                                      1750        20250110    edgar/data/1750/0001127602-25-001055.txt\n"
        "4           Garascia Jessica A.                                           1806647     20250110    edgar/data/1806647/0001127602-25-001055.txt\n"
        "4           BROKEN INC                                                    999         20250110    edgar/data/999/0000000999-25-000001.txt\n"
    )
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        hits.append(url)
        if url.endswith("form.20250110.idx"):
            return httpx.Response(200, text=index)
        if url.endswith("0001127602-25-001055.txt"):
            return httpx.Response(200, text=sub)
        if url.endswith("0000000999-25-000001.txt"):
            return httpx.Response(200, text="<SEC-HEADER>basura</SEC-HEADER>")
        return httpx.Response(404)

    client = EdgarClient(UA, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    stats = sync_form4_day(client, con, date(2025, 1, 10))
    assert stats.index_found and stats.candidates == 2
    assert stats.stored == 1 and len(stats.errors) == 1
    assert con.execute("select count(*) from insider_filings").fetchone()[0] == 1
    # segunda vez: no vuelve a descargar lo que ya existe
    hits.clear()
    stats2 = sync_form4_day(client, con, date(2025, 1, 10))
    assert stats2.already_present == 1 and stats2.stored == 0
    assert not any(h.endswith("0001127602-25-001055.txt") for h in hits)


def test_sync_day_without_index(con):
    client = EdgarClient(
        UA, transport=httpx.MockTransport(lambda r: httpx.Response(404)), sleep=lambda s: None
    )
    stats = sync_form4_day(client, con, date(2026, 10, 4))  # domingo
    assert not stats.index_found and stats.stored == 0


def test_mentions_10b5_1_in_footnotes_or_remarks():
    base = """<ownershipDocument><issuer><issuerCik>1</issuerCik></issuer>
      <footnotes><footnote id="F1">{fn}</footnote></footnotes><remarks>{rem}</remarks>
      </ownershipDocument>"""

    def parse(fn, rem):
        return parse_ownership_xml(
            base.format(fn=fn, rem=rem), accession="0" * 18, filing_date=date(2020, 1, 2)
        )

    assert parse("Sold pursuant to a Rule 10b5-1 trading plan.", "").mentions_10b5_1
    assert parse("x", "Plan under Rule 10b5–1 adopted").mentions_10b5_1
    assert parse("10b-5-1 plan", "").mentions_10b5_1
    assert not parse("Weighted average price.", "").mentions_10b5_1


def test_real_filing_without_10b5_mention(fixtures_dir):
    f = parse_ownership_xml(
        _read(fixtures_dir, "oxy_2026_P.xml"),
        accession="0001628280-26-045313",
        filing_date=date(2026, 6, 24),
    )
    assert f.mentions_10b5_1 is False


def test_sync_day_treats_403_index_as_missing_and_skips_weekends(con):
    hits = []

    def handler(request):
        hits.append(str(request.url))
        return httpx.Response(403)

    client = EdgarClient(UA, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    st = sync_form4_day(client, con, date(2026, 10, 2))  # viernes: 403 = sin índice
    assert not st.index_found
    hits.clear()
    st = sync_form4_day(client, con, date(2026, 10, 3))  # sábado: ni se consulta
    assert not st.index_found and hits == []
