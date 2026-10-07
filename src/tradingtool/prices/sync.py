"""Sincronización incremental de precios hacia la caché local.

Problema clásico: con precios "ajustados", un split futuro cambia TODA la historia anterior.
Si se guardan barras día a día, después de un split aparece un salto falso (p. ej. -50%).
Solución: tras cada sincronización se buscan saltos extremos recientes y, para esos tickers,
se vuelve a descargar la historia completa ajustada.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
import numpy as np

from tradingtool.prices.base import (
    MarketSnapshotSource,
    PriceSource,
    PriceSourceError,
    last_dates,
    load_bars,
    store_bars,
)
from tradingtool.prices.quality import check_bars

log = logging.getLogger(__name__)


@dataclass
class PriceSyncStats:
    source: str
    rows_written: int = 0
    tickers_updated: int = 0
    days_fetched: int = 0
    repaired: list[str] = field(default_factory=list)
    first_day: date | None = None  # primer día realmente descargado (costura para splits)
    errors: list[str] = field(default_factory=list)


def business_days(start: date, end: date) -> list[date]:
    if start > end:
        return []
    days = np.arange(
        np.datetime64(start, "D"),
        np.datetime64(end, "D") + np.timedelta64(1, "D"),
        dtype="datetime64[D]",
    )
    mask = np.is_busday(days)
    return [d.astype(object) for d in days[mask]]


def sync_tickers(
    con: duckdb.DuckDBPyConnection,
    source: PriceSource,
    tickers: list[str],
    start: date,
    end: date,
    full: bool = False,
) -> PriceSyncStats:
    """Descarga por ticker solo lo que falta (desde su última fecha guardada)."""
    stats = PriceSyncStats(source=source.name)
    last = {} if full else last_dates(con, tickers)
    for t in sorted(set(tickers)):
        s = start
        if t in last and last[t] is not None:
            s = max(start, last[t] + timedelta(days=1))
        if s > end:
            continue
        try:
            df = source.daily_bars(t, s, end)
        except PriceSourceError as exc:
            stats.errors.append(f"{t}: {exc}")
            log.warning("Precios de %s: %s", t, exc)
            continue
        n = store_bars(con, df, source.name, source.adjusted)
        if n:
            stats.rows_written += n
            stats.tickers_updated += 1
            if stats.first_day is None or s < stats.first_day:
                stats.first_day = s
    return stats


NO_MARKET_PREFIX = "sin_mercado:"


def market_days_missing(
    con: duckdb.DuckDBPyConnection, start: date, end: date, min_tickers: int = 500
) -> list[date]:
    """Días hábiles para los que la caché no tiene una foto completa del mercado.

    Excluye los días ya consultados que resultaron sin mercado (feriados de EE. UU.).
    """
    holidays = {
        r[0][len(NO_MARKET_PREFIX) :]
        for r in con.execute(
            "SELECT key FROM meta WHERE key LIKE ?", [NO_MARKET_PREFIX + "%"]
        ).fetchall()
    }
    have = {
        d
        for d, n in con.execute(
            "SELECT date, count(*) FROM prices_daily WHERE date BETWEEN ? AND ? GROUP BY date",
            [start, end],
        ).fetchall()
        if n >= min_tickers
    }
    return [d for d in business_days(start, end) if d not in have and str(d) not in holidays]


def sync_market(
    con: duckdb.DuckDBPyConnection,
    source: MarketSnapshotSource,
    start: date,
    end: date,
    max_days: int | None = None,
) -> PriceSyncStats:
    """Trae el mercado completo día por día (una llamada por día) para los días que faltan."""
    stats = PriceSyncStats(source=source.name)
    days = market_days_missing(con, start, end)
    if max_days is not None and len(days) > max_days:
        log.info("Limitando a los últimos %d de %d días faltantes", max_days, len(days))
        days = days[-max_days:]
    for i, d in enumerate(days, 1):
        try:
            df = source.market_day(d)
        except PriceSourceError as exc:
            stats.errors.append(f"{d}: {exc}")
            log.warning("Mercado %s: %s", d, exc)
            continue
        if df.empty and d <= date.today() - timedelta(days=4):
            # Día hábil sin datos (ya viejo, así que no es "aún no publicado"): feriado.
            # Se recuerda para no volver a pedirlo.
            con.execute(
                "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                [f"{NO_MARKET_PREFIX}{d}", source.name],
            )
        stats.rows_written += store_bars(con, df, source.name, source.adjusted)
        stats.days_fetched += 1
        if not df.empty and (stats.first_day is None or d < stats.first_day):
            stats.first_day = d
        if i % 20 == 0:
            log.info("Precios: %d/%d días", i, len(days))
    return stats


def relevant_tickers(con: duckdb.DuckDBPyConnection, extra: Iterable[str] = ()) -> list[str]:
    """Tickers que importan para la estrategia: los de las señales + benchmarks."""
    rows = con.execute("SELECT DISTINCT ticker FROM signals WHERE ticker IS NOT NULL").fetchall()
    return sorted({r[0] for r in rows} | {t for t in extra if t})


def repair_split_jumps(
    con: duckdb.DuckDBPyConnection,
    source: PriceSource,
    tickers: list[str],
    boundary: date | None,
    history_start: date,
    end: date,
    max_tickers: int = 25,
) -> list[str]:
    """Re-descarga la historia de tickers con un salto sospechoso JUSTO en la costura entre lo
    que ya estaba en la caché (antes de ``boundary``) y lo recién descargado.

    Una descarga hecha de una sola vez ya viene ajustada de forma consistente, así que no hay
    nada que reparar; el problema solo aparece cuando un split ocurre entre dos descargas.
    """
    if boundary is None or not tickers:
        return []
    suspects = [
        r[0]
        for r in con.execute(
            """
            WITH t AS (SELECT unnest(?::VARCHAR[]) AS ticker),
            old AS (
                SELECT p.ticker, arg_max(p.close, p.date) AS c
                FROM prices_daily p JOIN t USING (ticker)
                WHERE p.date < ? GROUP BY p.ticker
            ),
            new AS (
                SELECT p.ticker, arg_min(p.close, p.date) AS c
                FROM prices_daily p JOIN t USING (ticker)
                WHERE p.date >= ? AND p.date <= ? GROUP BY p.ticker
            )
            SELECT old.ticker FROM old JOIN new USING (ticker)
            WHERE old.c > 0 AND (new.c / old.c > 1.8 OR new.c / old.c < 0.55)
            ORDER BY 1
            """,
            [tickers, boundary, boundary, end],
        ).fetchall()
    ]
    repaired = []
    for t in suspects[:max_tickers]:
        try:
            df = source.daily_bars(t, history_start, end)
        except PriceSourceError as exc:
            log.warning("No se pudo reparar %s: %s", t, exc)
            continue
        if df.empty:
            continue
        con.execute(
            "DELETE FROM prices_daily WHERE ticker = ? AND date BETWEEN ? AND ?",
            [t, history_start, end],
        )
        store_bars(con, df, source.name, source.adjusted)
        repaired.append(t)
    if len(suspects) > max_tickers:
        log.warning(
            "%d tickers con posibles splits; se repararon %d (ejecuta de nuevo para seguir)",
            len(suspects),
            max_tickers,
        )
    return repaired


def quality_report(con: duckdb.DuckDBPyConnection, tickers: list[str], since: date) -> list:
    issues = []
    for t in tickers:
        issues.extend(check_bars(load_bars(con, t, start=since)))
    return issues
