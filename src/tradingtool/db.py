"""Base de datos local (DuckDB, un solo archivo). Esquema idempotente.

Tablas:
- insider_filings / insider_owners / insider_transactions: datos de Form 4 normalizados.
- prices_daily: barras diarias (caché local).
- runs: cada ejecución (screener, ingestas, backtests) con commit y hash de config.
- signals: ideas (pasen o no los filtros) -> base de los contrafactuales.
- decisions: aprobaciones / rechazos humanos (no envían órdenes).
- outcomes: resultado de cada señal a varios horizontes (aprobada, rechazada o bloqueada).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from tradingtool.tickers import ticker_sql

SCHEMA_VERSION = 3

# Columnas agregadas después de la versión 1: se crean en bases existentes (idempotente).
MIGRATIONS = (
    "ALTER TABLE insider_filings ADD COLUMN IF NOT EXISTS mentions_10b5_1 BOOLEAN",
    "ALTER TABLE signals ADD COLUMN IF NOT EXISTS origin VARCHAR DEFAULT 'live'",
    "ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS bench2_ret DOUBLE",
    "ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS excess2_ret DOUBLE",
    "ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS bars_held INTEGER",
    "ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'complete'",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR
);

CREATE TABLE IF NOT EXISTS insider_filings (
    accession VARCHAR PRIMARY KEY,
    source VARCHAR NOT NULL,
    form_type VARCHAR NOT NULL,
    filing_date DATE NOT NULL,
    acceptance_ts TIMESTAMP,
    period_of_report DATE,
    issuer_cik VARCHAR NOT NULL,
    issuer_name VARCHAR,
    issuer_ticker VARCHAR,
    aff10b5one BOOLEAN,
    mentions_10b5_1 BOOLEAN,
    ingested_at TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS insider_owners (
    accession VARCHAR NOT NULL,
    owner_seq INTEGER NOT NULL,
    owner_cik VARCHAR NOT NULL,
    owner_name VARCHAR,
    is_director BOOLEAN,
    is_officer BOOLEAN,
    is_ten_pct_owner BOOLEAN,
    is_other BOOLEAN,
    officer_title VARCHAR,
    PRIMARY KEY (accession, owner_seq)
);

CREATE TABLE IF NOT EXISTS insider_transactions (
    accession VARCHAR NOT NULL,
    seq INTEGER NOT NULL,
    is_derivative BOOLEAN NOT NULL,
    security_title VARCHAR,
    transaction_date DATE,
    transaction_code VARCHAR,
    shares DOUBLE,
    price_per_share DOUBLE,
    acquired_disposed VARCHAR,
    shares_owned_after DOUBLE,
    direct_indirect VARCHAR,
    equity_swap BOOLEAN,
    footnote_ids VARCHAR,
    PRIMARY KEY (accession, seq)
);

CREATE TABLE IF NOT EXISTS prices_daily (
    ticker VARCHAR NOT NULL,
    date DATE NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    source VARCHAR,
    adjusted BOOLEAN,
    PRIMARY KEY (ticker, date)
);

-- ETFs de la rotación: precios ajustados por dividendos (retorno total) y la serie "CASH"
-- (letras del Tesoro, FRED). Separada de prices_daily para no mezclar ajustes distintos.
CREATE TABLE IF NOT EXISTS etf_prices_daily (
    ticker VARCHAR NOT NULL,
    date DATE NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    source VARCHAR,
    adjusted BOOLEAN,
    PRIMARY KEY (ticker, date)
);

-- Recomendación mensual de la rotación. La primera de cada mes queda congelada (no se reescribe).
CREATE TABLE IF NOT EXISTS etf_recommendations (
    as_of_date DATE NOT NULL,          -- último día hábil del mes de la señal
    strategy_version VARCHAR NOT NULL,
    rules_hash VARCHAR NOT NULL,
    weights JSON NOT NULL,             -- {ticker: peso}
    detail JSON,                       -- retornos por horizonte y elección de cada uno
    data_last_date DATE,
    created_at TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (as_of_date, strategy_version, rules_hash)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id VARCHAR PRIMARY KEY,
    kind VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    git_commit VARCHAR,
    config_hash VARCHAR,
    params JSON,
    status VARCHAR,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id VARCHAR PRIMARY KEY,
    run_id VARCHAR,
    strategy_version VARCHAR NOT NULL,
    config_hash VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    ticker VARCHAR,
    issuer_cik VARCHAR NOT NULL,
    issuer_name VARCHAR,
    score DOUBLE,
    passed BOOLEAN NOT NULL,
    origin VARCHAR NOT NULL DEFAULT 'live',  -- live | backtest (histórico)
    reasons JSON,
    features JSON,
    accessions JSON,
    created_at TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id VARCHAR PRIMARY KEY,
    signal_id VARCHAR NOT NULL,
    decision VARCHAR NOT NULL CHECK (decision IN ('approve', 'reject', 'skip')),
    reason VARCHAR,
    decided_by VARCHAR NOT NULL DEFAULT 'human',
    decided_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    planned_shares INTEGER,
    planned_entry DOUBLE,
    planned_stop DOUBLE
);

CREATE TABLE IF NOT EXISTS outcomes (
    signal_id VARCHAR NOT NULL,
    horizon_days INTEGER NOT NULL,
    entry_date DATE,
    entry_price DOUBLE,
    exit_date DATE,
    exit_price DOUBLE,
    ret DOUBLE,
    mae DOUBLE,
    mfe DOUBLE,
    bench_ret DOUBLE,
    excess_ret DOUBLE,
    bench2_ret DOUBLE,   -- benchmark secundario (por defecto IWM: empresas pequeñas)
    excess2_ret DOUBLE,
    bars_held INTEGER,
    status VARCHAR NOT NULL DEFAULT 'complete',  -- complete | truncated (dejó de cotizar)
    computed_at TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (signal_id, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_filings_issuer_date ON insider_filings (issuer_cik, filing_date);
CREATE INDEX IF NOT EXISTS idx_filings_date ON insider_filings (filing_date);
CREATE INDEX IF NOT EXISTS idx_owners_cik ON insider_owners (owner_cik);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals (as_of_date);
"""


def _stored_version(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    try:
        return int(row[0]) if row else SCHEMA_VERSION
    except ValueError:
        return 0


def _clean_tickers(con: duckdb.DuckDBPyConnection) -> None:
    """Versión 3: limpia símbolos mal escritos ya guardados ('"OMEX"', '(CALX)', ...)."""
    for table, col in (("insider_filings", "issuer_ticker"), ("signals", "ticker")):
        clean = ticker_sql(col)
        con.execute(
            f"UPDATE {table} SET {col} = {clean} "  # noqa: S608 (nombres fijos)
            f"WHERE {col} IS NOT NULL AND {col} IS DISTINCT FROM {clean}"
        )


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    has_meta = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'meta'"
    ).fetchone()[0]
    con.execute(SCHEMA_SQL)
    for stmt in MIGRATIONS:
        con.execute(stmt)
    if has_meta and _stored_version(con) < 3:
        _clean_tickers(con)
    con.execute(
        "INSERT INTO meta VALUES ('schema_version', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [str(SCHEMA_VERSION)],
    )


def connect(db_path: Path | str, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Abre (o crea) la base y aplica el esquema. Usa ':memory:' en tests."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=read_only)
    if not read_only:
        init_schema(con)
    return con
