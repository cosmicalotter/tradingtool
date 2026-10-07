from __future__ import annotations

import duckdb
import pytest

from tradingtool.db import connect
from tradingtool.tickers import normalize_ticker, ticker_sql

CASES = [
    ('"""NRESF"""', "NRESF"),
    ("(CALX)", "CALX"),
    (" brk.b ", "BRK.B"),
    ("ABC, ABCW", "ABC"),
    ("NASDAQ: ABC", "ABC"),
    ("$TSLA", "TSLA"),
    ("N/A", None),
    ("none", None),
    ('""', None),
    ("()", None),
    ("123", None),
    (None, None),
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


def test_sql_version_matches_python():
    con = duckdb.connect()
    con.execute("CREATE TABLE t (raw VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [[r] for r, _ in CASES])
    got = dict(con.execute(f"SELECT raw, {ticker_sql('raw')} FROM t").fetchall())
    assert got == {r: e for r, e in CASES}


def test_migration_cleans_existing_tickers(tmp_path):
    db = tmp_path / "x.duckdb"
    con = connect(db)
    con.execute(
        "INSERT INTO insider_filings (accession, source, form_type, filing_date, issuer_cik, "
        "issuer_ticker) VALUES ('a', 'bulk', '4', DATE '2020-01-02', '1', '(CALX)')"
    )
    con.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    con.close()
    con = connect(db)
    assert con.execute("SELECT issuer_ticker FROM insider_filings").fetchone()[0] == "CALX"
