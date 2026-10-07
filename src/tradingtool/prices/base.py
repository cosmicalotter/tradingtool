"""Contrato común de fuentes de precios diarios y guardado en la caché local (DuckDB).

Todas las fuentes devuelven un DataFrame con columnas exactas:
``ticker, date, open, high, low, close, volume`` (fecha como ``datetime.date``), con precios
AJUSTADOS por splits (y dividendos si la fuente lo permite), ordenado por ticker y fecha.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import duckdb
import pandas as pd

COLUMNS = ["ticker", "date", "open", "high", "low", "close", "volume"]


class PriceSourceError(RuntimeError):
    pass


@runtime_checkable
class PriceSource(Protocol):
    name: str
    adjusted: bool

    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Barras diarias de un ticker entre start y end (incluidos)."""
        ...


@runtime_checkable
class MarketSnapshotSource(Protocol):
    """Fuente capaz de traer TODO el mercado de EE. UU. para un día en una sola llamada."""

    name: str
    adjusted: bool

    def market_day(self, day: date) -> pd.DataFrame: ...


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Valida y normaliza columnas/tipos; descarta filas sin fecha o sin cierre."""
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise PriceSourceError(f"faltan columnas en barras de precios: {missing}")
    out = df[COLUMNS].copy()
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["date", "close"])
    out = out.drop_duplicates(subset=["ticker", "date"], keep="last")
    return out.sort_values(["ticker", "date"]).reset_index(drop=True)


def store_bars(
    con: duckdb.DuckDBPyConnection, df: pd.DataFrame, source: str, adjusted: bool
) -> int:
    """Inserta o reemplaza barras en ``prices_daily``. Devuelve filas escritas."""
    df = normalize_bars(df)
    if df.empty:
        return 0
    df = df.assign(source=source, adjusted=adjusted)
    con.register("tt_new_bars", df)
    try:
        con.execute(
            """
            INSERT INTO prices_daily (ticker, date, open, high, low, close, volume, source,
                adjusted)
            SELECT ticker, date, open, high, low, close, volume, source, adjusted
            FROM tt_new_bars
            ON CONFLICT (ticker, date) DO UPDATE SET
                open = excluded.open, high = excluded.high, low = excluded.low,
                close = excluded.close, volume = excluded.volume, source = excluded.source,
                adjusted = excluded.adjusted
            """
        )
    finally:
        con.unregister("tt_new_bars")
    return len(df)


def last_dates(con: duckdb.DuckDBPyConnection, tickers: list[str]) -> dict[str, date]:
    if not tickers:
        return {}
    rows = con.execute(
        "SELECT ticker, max(date) FROM prices_daily "
        "WHERE ticker IN (SELECT unnest(?::VARCHAR[])) GROUP BY ticker",
        [tickers],
    ).fetchall()
    return {t: d for t, d in rows}


def load_bars(
    con: duckdb.DuckDBPyConnection, ticker: str, start: date | None = None, end: date | None = None
) -> pd.DataFrame:
    sql = "SELECT ticker, date, open, high, low, close, volume FROM prices_daily WHERE ticker = ?"
    params: list[object] = [ticker.upper()]
    if start is not None:
        sql += " AND date >= ?"
        params.append(start)
    if end is not None:
        sql += " AND date <= ?"
        params.append(end)
    df = con.execute(sql + " ORDER BY date", params).df()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df
