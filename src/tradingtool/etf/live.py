"""Señal mensual de la rotación (día a día) y su registro inmutable en la base.

La recomendación de cada mes se guarda la PRIMERA vez que se calcula y no se reescribe: así el
seguimiento en papel es honesto (no se puede "corregir" el pasado con datos posteriores).
Nada de este módulo envía órdenes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import duckdb
import pandas as pd

from tradingtool.config import EtfConfig
from tradingtool.etf.engine import month_ends
from tradingtool.etf.report import ETF_VERDICT_KEY, EtfDataError
from tradingtool.etf.strategies import HorizonPick, Weights, dual_momentum_picks

MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


@dataclass(frozen=True)
class LiveSignal:
    as_of: date  # cierre de fin de mes usado para la señal
    execute_from: date  # primer día hábil siguiente (aprox.: no conoce feriados)
    weights: Weights
    picks: list[HorizonPick]
    data_last: date  # último día con datos
    stale: bool  # True si los datos no cubren el último mes completo
    stale_reason: str = ""


def _expected_month(today: date) -> tuple[int, int]:
    """Último mes calendario completo antes de ``today``."""
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def compute_signal(prices: pd.DataFrame, cfg: EtfConfig, today: date) -> LiveSignal:
    """Pesos de ``rotacion-v1`` con el último mes COMPLETO de datos (nunca el mes en curso)."""
    if prices.empty:
        raise EtfDataError("No hay precios de ETFs. Ejecuta: uv run tt etf-precios")
    ends = month_ends(pd.DatetimeIndex(prices.index))
    complete = ends[[(m.year, m.month) < (today.year, today.month) for m in ends]]
    if len(complete) == 0:
        raise EtfDataError("Todavía no hay un mes completo de datos.")
    m = complete[-1]
    monthly = prices.loc[ends[ends <= m]]
    picks = dual_momentum_picks(
        monthly,
        cfg.risk_assets,
        cfg.defensive_assets,
        cfg.cash_asset,
        cfg.horizons_months,
    )
    if picks is None:
        raise EtfDataError(
            "Faltan datos o historia (12 meses) de algún ETF. Ejecuta: uv run tt etf-precios"
        )
    weights: Weights = {}
    for p in picks:
        weights[p.pick] = weights.get(p.pick, 0.0) + 1.0 / len(picks)
    last_bday = (m + pd.offsets.BMonthEnd(0)).normalize()
    stale_reason = ""
    if (m.year, m.month) != _expected_month(today):
        y, mo = _expected_month(today)
        stale_reason = (
            f"tus datos llegan hasta {prices.index[-1]:%Y-%m-%d} y falta el cierre de "
            f"{MONTHS_ES[mo - 1]} de {y}"
        )
    elif m.normalize() < last_bday - pd.Timedelta(days=3):
        stale_reason = f"los datos de {MONTHS_ES[m.month - 1]} terminan el {m:%Y-%m-%d}"
    execute_from = (m + pd.offsets.BDay(1)).date()
    return LiveSignal(
        as_of=m.date(),
        execute_from=execute_from,
        weights=weights,
        picks=picks,
        data_last=pd.Timestamp(prices.index[-1]).date(),
        stale=bool(stale_reason),
        stale_reason=stale_reason,
    )


def record_recommendation(
    con: duckdb.DuckDBPyConnection, sig: LiveSignal, cfg: EtfConfig
) -> Weights | None:
    """Guarda la recomendación del mes si es la primera. Devuelve la ya guardada (si existía)."""
    key = [sig.as_of, cfg.strategy_version, cfg.rules_hash()]
    row = con.execute(
        "SELECT weights FROM etf_recommendations "
        "WHERE as_of_date = ? AND strategy_version = ? AND rules_hash = ?",
        key,
    ).fetchone()
    if row is not None:
        return json.loads(row[0])
    detail = [
        {"meses": p.months, "elige": p.pick, "riesgo": p.risk_on, "retornos": p.returns}
        for p in sig.picks
    ]
    con.execute(
        "INSERT INTO etf_recommendations (as_of_date, strategy_version, rules_hash, weights, "
        "detail, data_last_date) VALUES (?, ?, ?, ?, ?, ?)",
        [*key, json.dumps(sig.weights), json.dumps(detail), sig.data_last],
    )
    return None


def previous_recommendation(
    con: duckdb.DuckDBPyConnection, cfg: EtfConfig, before: date
) -> tuple[date, Weights] | None:
    row = con.execute(
        "SELECT as_of_date, weights FROM etf_recommendations "
        "WHERE strategy_version = ? AND rules_hash = ? AND as_of_date < ? "
        "ORDER BY as_of_date DESC LIMIT 1",
        [cfg.strategy_version, cfg.rules_hash(), before],
    ).fetchone()
    return (row[0], json.loads(row[1])) if row else None


def recommendation_history(con: duckdb.DuckDBPyConnection, limit: int = 24) -> pd.DataFrame:
    return con.execute(
        "SELECT as_of_date, strategy_version, rules_hash, weights, created_at "
        "FROM etf_recommendations ORDER BY as_of_date DESC, created_at DESC LIMIT ?",
        [limit],
    ).df()


def stored_verdict(con: duckdb.DuckDBPyConnection) -> dict | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", [ETF_VERDICT_KEY]).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def save_verdict(con: duckdb.DuckDBPyConnection, payload: dict) -> None:
    con.execute(
        "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [ETF_VERDICT_KEY, json.dumps(payload, ensure_ascii=False)],
    )


def changes(previous: Weights | None, current: Weights) -> list[tuple[str, float, float]]:
    """(ticker, peso anterior, peso nuevo) de los que cambian más de 1 punto."""
    prev = previous or {}
    out = []
    for t in sorted(set(prev) | set(current)):
        a, b = prev.get(t, 0.0), current.get(t, 0.0)
        if abs(a - b) > 0.01:
            out.append((t, a, b))
    return out
