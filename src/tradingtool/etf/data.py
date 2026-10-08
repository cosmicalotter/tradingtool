"""Datos de la rotación de ETFs: precios ajustados (retorno total) y efectivo (FRED).

Reglas:
- Los ETFs se guardan en ``etf_prices_daily``, separados de ``prices_daily``.
- Cada actualización RE-DESCARGA la historia completa de cada ETF. Con precios ajustados por
  dividendos, cada dividendo nuevo cambia todo el pasado: pegar trozos bajados en fechas
  distintas perdería el dividendo en la costura (y el retorno saldría más bajo de lo real).
- El efectivo (``CASH``) es un índice construido con la tasa de las letras del Tesoro a
  3 meses (FRED, serie DTB3), acumulada día a día, menos el costo de un ETF de letras.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import duckdb
import httpx
import numpy as np
import pandas as pd

from tradingtool.prices.base import (
    COLUMNS,
    PriceSource,
    PriceSourceAuthError,
    PriceSourceError,
    store_bars,
)

log = logging.getLogger(__name__)

ETF_TABLE = "etf_prices_daily"
CASH = "CASH"
CALENDAR = "SPY"  # los días hábiles del backtest son los días en que cotizó SPY
FRED_DTB3_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"
CASH_EXPENSE = 0.0007  # costo anual de un ETF de letras del Tesoro (IB01: 0,07%)
MAX_GAP_DAYS = 5  # días hábiles seguidos sin dato a partir de los cuales se avisa


@dataclass
class EtfSyncStats:
    rows: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- efectivo (FRED)


def parse_fred_csv(text: str) -> pd.Series:
    """Serie de tasas (% anual) indexada por fecha. FRED marca con '.' los días sin dato."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise PriceSourceError("FRED respondió un archivo vacío o inesperado")
    data: dict[date, float] = {}
    for row in reader:
        if len(row) < 2:
            continue
        try:
            d = date.fromisoformat(row[0].strip())
            v = float(row[1])
        except ValueError:
            continue
        data[d] = v
    if not data:
        raise PriceSourceError("FRED no devolvió tasas de la serie DTB3")
    return pd.Series(data, dtype=float).sort_index()


def tbill_index(rates: pd.Series, expense: float = CASH_EXPENSE) -> pd.Series:
    """Índice de valor del efectivo (empieza en 1) a partir de la tasa anual (%) diaria.

    Entre dos observaciones se acumula la tasa de la primera con base actual/360 (convención
    de las letras del Tesoro) y se descuenta el costo anual del ETF con base actual/365.
    """
    r = rates.dropna().sort_index()
    if r.empty:
        return pd.Series(dtype=float)
    stamps = pd.to_datetime(pd.Index(r.index))
    days = np.diff(stamps.values).astype("timedelta64[D]").astype(float)
    prev = r.to_numpy()[:-1]
    growth = 1.0 + prev / 100.0 * days / 360.0 - expense * days / 365.0
    values = np.concatenate([[1.0], np.cumprod(growth)])
    return pd.Series(values, index=r.index)


def fetch_fred_csv(
    url: str = FRED_DTB3_URL,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 3,
    timeout: float = 60.0,
) -> str:
    headers = {"User-Agent": "tradingtool (investigacion personal)"}
    last: Exception | None = None
    with httpx.Client(
        headers=headers, transport=transport, timeout=timeout, follow_redirects=True
    ) as client:
        for attempt in range(retries + 1):
            try:
                resp = client.get(url)
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code not in (429, 500, 502, 503, 504):
                    raise PriceSourceError(f"FRED respondió {resp.status_code}")
                last = PriceSourceError(f"HTTP {resp.status_code}")
            if attempt < retries:
                sleep(5.0 * (attempt + 1))
    raise PriceSourceError(
        f"No se pudo descargar la tasa del Tesoro de FRED ({last!r}). Reintenta más tarde."
    )


def cash_bars(index: pd.Series) -> pd.DataFrame:
    """El índice del efectivo en el formato de barras (open = high = low = close)."""
    df = pd.DataFrame({"date": list(index.index), "close": index.to_numpy()})
    df["ticker"] = CASH
    df["open"] = df["high"] = df["low"] = df["close"]
    df["volume"] = np.nan
    return df[COLUMNS]


def _replace_ticker(
    con: duckdb.DuckDBPyConnection, df: pd.DataFrame, ticker: str, source: str
) -> int:
    """Reemplaza toda la historia de un ticker de una sola vez (transacción)."""
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(f"DELETE FROM {ETF_TABLE} WHERE ticker = ?", [ticker])  # noqa: S608
        n = store_bars(con, df, source, True, table=ETF_TABLE)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n


def refresh_cash(
    con: duckdb.DuckDBPyConnection,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    rates = parse_fred_csv(fetch_fred_csv(transport=transport, sleep=sleep))
    return _replace_ticker(con, cash_bars(tbill_index(rates)), CASH, "fred")


# ----------------------------------------------------------------------------- ETFs


def refresh_etfs(
    con: duckdb.DuckDBPyConnection,
    source: PriceSource,
    tickers: list[str],
    start: date,
    end: date,
) -> EtfSyncStats:
    """Re-descarga la historia completa ajustada de cada ETF (ver el docstring del módulo)."""
    if not source.adjusted:
        raise PriceSourceError(f"la fuente {source.name} no entrega precios ajustados")
    stats = EtfSyncStats()
    for t in tickers:
        try:
            df = source.daily_bars(t, start, end)
        except PriceSourceAuthError:
            raise
        except PriceSourceError as exc:
            stats.errors.append(f"{t}: {exc}")
            log.warning("ETF %s: %s", t, exc)
            continue
        if df.empty:
            stats.errors.append(f"{t}: la fuente no devolvió datos")
            continue
        stats.rows[t] = _replace_ticker(con, df, t.upper(), source.name)
    return stats


def coverage(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Qué hay guardado: ticker, fuente, primera y última fecha, filas."""
    return con.execute(
        f"""
        SELECT ticker, any_value(source) AS fuente, min(date) AS desde, max(date) AS hasta,
               count(*) AS filas
        FROM {ETF_TABLE} GROUP BY ticker ORDER BY ticker
        """  # noqa: S608
    ).df()


def load_panel(
    con: duckdb.DuckDBPyConnection, tickers: list[str], end: date | None = None
) -> pd.DataFrame:
    """Cierres ajustados en formato ancho, uno por día hábil de SPY, hasta ``end`` inclusive.

    Los huecos se rellenan con el último precio conocido (nunca antes del primer dato de cada
    ETF). El efectivo usa también días de FRED que no coinciden con los de la bolsa.
    """
    wanted = sorted({t.upper() for t in [*tickers, CALENDAR]})
    df = con.execute(
        f"""
        SELECT ticker, date, close FROM {ETF_TABLE}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[])) AND (?::DATE IS NULL OR date <= ?::DATE)
        ORDER BY date
        """,  # noqa: S608
        [wanted, end, end],
    ).df()
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot(index="date", columns="ticker", values="close").sort_index()
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))
    if CALENDAR not in wide.columns:
        return pd.DataFrame()
    trading_days = wide.index[wide[CALENDAR].notna()]
    wide = wide.ffill().loc[trading_days]
    cols = [t.upper() for t in tickers if t.upper() in wide.columns]
    return wide[cols]


def data_warnings(con: duckdb.DuckDBPyConnection, tickers: list[str]) -> list[str]:
    """Avisos de calidad: tickers sin datos, huecos largos o datos desactualizados."""
    out: list[str] = []
    cov = coverage(con)
    have = {r.ticker: r for r in cov.itertuples()}
    cal = have.get(CALENDAR)
    for t in tickers:
        r = have.get(t)
        if r is None:
            out.append(f"{t}: sin datos (ejecuta: uv run tt etf-precios)")
            continue
        if cal is not None and t != CASH and (cal.hasta - r.hasta).days > 7:
            out.append(f"{t}: datos hasta {r.hasta}, SPY hasta {cal.hasta}")
    if cal is None:
        return out
    days = con.execute(
        f"SELECT ticker, date FROM {ETF_TABLE} WHERE ticker IN "  # noqa: S608
        "(SELECT unnest(?::VARCHAR[]))",
        [[t for t in tickers if t != CASH and t in have]],
    ).df()
    if days.empty:
        return out
    spy_days = pd.DatetimeIndex(
        pd.to_datetime(days.loc[days["ticker"] == CALENDAR, "date"])
    ).sort_values()
    for t, g in days.groupby("ticker"):
        if t == CALENDAR:
            continue
        mine = pd.DatetimeIndex(pd.to_datetime(g["date"]))
        span = spy_days[(spy_days >= mine.min()) & (spy_days <= mine.max())]
        missing = span.difference(mine)
        if len(missing) == 0:
            continue
        # racha más larga de días hábiles seguidos sin dato
        pos = np.searchsorted(span.values, missing.values)
        runs = np.split(pos, np.nonzero(np.diff(pos) != 1)[0] + 1)
        longest = max(len(x) for x in runs)
        if longest > MAX_GAP_DAYS:
            out.append(f"{t}: {len(missing)} días sin datos (racha más larga: {longest})")
    return out
