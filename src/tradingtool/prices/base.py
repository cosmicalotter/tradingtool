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

# Tablas con el mismo formato de barras. La de ETFs va aparte porque guarda precios ajustados
# por dividendos (retorno total) y no debe mezclarse con la caché de acciones individuales.
PRICE_TABLES = ("prices_daily", "etf_prices_daily")


def _table(name: str) -> str:
    if name not in PRICE_TABLES:
        raise ValueError(f"tabla de precios desconocida: {name}")
    return name


class PriceSourceError(RuntimeError):
    pass


class PriceSourceAuthError(PriceSourceError):
    """El proveedor rechazó las claves (401): no tiene sentido seguir con más tickers."""


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
    con: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    source: str,
    adjusted: bool,
    table: str = "prices_daily",
) -> int:
    """Inserta o reemplaza barras en ``table`` (por defecto ``prices_daily``)."""
    table = _table(table)
    df = normalize_bars(df)
    if df.empty:
        return 0
    df = df.assign(source=source, adjusted=adjusted)
    con.register("tt_new_bars", df)
    try:
        con.execute(
            f"""
            INSERT INTO {table} (ticker, date, open, high, low, close, volume, source,
                adjusted)
            SELECT ticker, date, open, high, low, close, volume, source, adjusted
            FROM tt_new_bars
            ON CONFLICT (ticker, date) DO UPDATE SET
                open = excluded.open, high = excluded.high, low = excluded.low,
                close = excluded.close, volume = excluded.volume, source = excluded.source,
                adjusted = excluded.adjusted
            """  # noqa: S608 (tabla validada por _table)
        )
    finally:
        con.unregister("tt_new_bars")
    return len(df)


def last_dates(
    con: duckdb.DuckDBPyConnection, tickers: list[str], table: str = "prices_daily"
) -> dict[str, date]:
    if not tickers:
        return {}
    rows = con.execute(
        f"SELECT ticker, max(date) FROM {_table(table)} "  # noqa: S608 (tabla validada)
        "WHERE ticker IN (SELECT unnest(?::VARCHAR[])) GROUP BY ticker",
        [tickers],
    ).fetchall()
    return {t: d for t, d in rows}


def load_bars(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    table: str = "prices_daily",
) -> pd.DataFrame:
    cols = "ticker, date, open, high, low, close, volume"
    sql = f"SELECT {cols} FROM {_table(table)} WHERE ticker = ?"  # noqa: S608
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
