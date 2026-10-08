"""Métricas por periodo, intervalos bootstrap y simulación de aportes mensuales.

Definiciones (todas con datos netos de costos):
- Rendimiento anual compuesto (CAGR): (valor final / valor inicial)^(1/años) - 1.
- Volatilidad: desviación estándar de los retornos mensuales x raíz de 12.
- Sharpe: promedio del retorno mensual por encima del efectivo / su desviación x raíz de 12.
- Peor caída (máximo *drawdown*): la mayor baja desde un máximo previo, con valores diarios.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradingtool.etf.engine import SimResult


@dataclass(frozen=True)
class PeriodMetrics:
    start: pd.Timestamp  # punto base (último cierre antes del periodo o inicio de la estrategia)
    end: pd.Timestamp
    months: int
    cagr: float
    vol: float
    sharpe: float
    max_dd: float  # negativo: -0,25 = cayó 25% desde un máximo
    worst_year: float
    pct_months_up: float
    trades_per_year: float
    cost_per_year: float  # suma de costos (fracción de la cartera) por año
    risk_exposure: float  # peso promedio en activos de riesgo
    monthly: pd.Series  # retornos mensuales (índice: periodo mensual)
    cash_monthly: pd.Series
    yearly: pd.Series  # retornos por año calendario (el primero y el último pueden ser parciales)
    curve: pd.Series  # valor diario normalizado a 1 en la base


def window(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series | None:
    """La serie dentro de [start, end] con un punto base al inicio (el último valor previo).

    Si la serie empieza dentro del periodo, su propio primer punto hace de base.
    """
    inside = series[(series.index >= start) & (series.index <= end)]
    if inside.empty:
        return None
    before = series[series.index < start]
    if before.empty:
        return inside
    return pd.concat([before.iloc[[-1]], inside])


def period_returns(curve: pd.Series, freq: str) -> pd.Series:
    """Retornos por mes (``"M"``) o año (``"Y"``); el primero se mide desde la base."""
    rest = curve.iloc[1:]
    if rest.empty:
        return pd.Series(dtype=float)
    ends = rest.groupby(rest.index.to_period(freq)).last()
    values = np.concatenate([[curve.iloc[0]], ends.to_numpy(dtype=float)])
    return pd.Series(values[1:] / values[:-1] - 1.0, index=ends.index)


def sharpe(monthly: np.ndarray, cash: np.ndarray) -> float:
    ex = np.asarray(monthly, dtype=float) - np.asarray(cash, dtype=float)
    if len(ex) < 2:
        return math.nan
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * math.sqrt(12)) if sd > 0 else math.nan


def cagr_from_monthly(monthly: np.ndarray) -> float:
    m = np.asarray(monthly, dtype=float)
    if len(m) == 0:
        return math.nan
    return float(np.prod(1.0 + m) ** (12.0 / len(m)) - 1.0)


def max_drawdown(curve: pd.Series) -> float:
    return float((curve / curve.cummax() - 1.0).min())


def period_metrics(
    sim: SimResult,
    cash_index: pd.Series,
    start: pd.Timestamp,
    end: pd.Timestamp,
    risky: frozenset[str],
) -> PeriodMetrics | None:
    curve = window(sim.equity, start, end)
    if curve is None or len(curve) < 2:
        return None
    curve = curve / curve.iloc[0]
    cash_curve = cash_index.reindex(curve.index).ffill().bfill()
    monthly = period_returns(curve, "M")
    cash_m = period_returns(cash_curve, "M").reindex(monthly.index)
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    cagr = float(curve.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else math.nan
    yearly = period_returns(curve, "Y")
    t = sim.trades
    in_window = t[(t["date"] > curve.index[0]) & (t["date"] <= curve.index[-1])]
    w = sim.weights.reindex(curve.index[1:]).fillna(0.0)
    risk_cols = [c for c in w.columns if c in risky]
    exposure = float(w[risk_cols].sum(axis=1).mean()) if risk_cols else 0.0
    return PeriodMetrics(
        start=curve.index[0],
        end=curve.index[-1],
        months=len(monthly),
        cagr=cagr,
        vol=float(monthly.std(ddof=1) * math.sqrt(12)) if len(monthly) > 1 else math.nan,
        sharpe=sharpe(monthly.to_numpy(), cash_m.to_numpy()),
        max_dd=max_drawdown(curve),
        worst_year=float(yearly.min()) if len(yearly) else math.nan,
        pct_months_up=float((monthly > 0).mean()) if len(monthly) else math.nan,
        trades_per_year=len(in_window) / years if years > 0 else math.nan,
        cost_per_year=float(in_window["cost"].sum()) / years if years > 0 else math.nan,
        risk_exposure=exposure,
        monthly=monthly,
        cash_monthly=cash_m,
        yearly=yearly,
        curve=curve,
    )


# ----------------------------------------------------------------------------- bootstrap


def stationary_bootstrap_indices(
    n: int, samples: int, mean_block: float, rng: np.random.Generator
) -> np.ndarray:
    """Índices del bootstrap estacionario (Politis y Romano, 1994): bloques de largo aleatorio
    (promedio ``mean_block``) para respetar la dependencia entre meses seguidos."""
    idx = np.empty((samples, n), dtype=np.int64)
    idx[:, 0] = rng.integers(n, size=samples)
    jumps = rng.random((samples, n)) < 1.0 / mean_block
    fresh = rng.integers(n, size=(samples, n))
    for t in range(1, n):
        idx[:, t] = np.where(jumps[:, t], fresh[:, t], (idx[:, t - 1] + 1) % n)
    return idx


@dataclass(frozen=True)
class BootstrapCI:
    cagr_diff: tuple[float, float]  # intervalo 90% de (CAGR estrategia - CAGR comparador)
    sharpe_diff: tuple[float, float]
    p_cagr_better: float  # fracción de remuestreos en que la estrategia rinde más
    p_sharpe_better: float


def bootstrap_vs(
    strategy: pd.Series,
    benchmark: pd.Series,
    cash: pd.Series,
    samples: int = 2000,
    mean_block: float = 6.0,
    seed: int = 7,
) -> BootstrapCI | None:
    """Incertidumbre de la diferencia con el comparador, remuestreando los MISMOS meses."""
    df = pd.concat([strategy, benchmark, cash], axis=1).dropna()
    n = len(df)
    if n < 12:
        return None
    s, b, c = (df.iloc[:, i].to_numpy(dtype=float) for i in range(3))
    idx = stationary_bootstrap_indices(n, samples, mean_block, np.random.default_rng(seed))
    S, B, C = s[idx], b[idx], c[idx]

    def cagr(x: np.ndarray) -> np.ndarray:
        return np.prod(1.0 + x, axis=1) ** (12.0 / n) - 1.0

    def shp(x: np.ndarray) -> np.ndarray:
        ex = x - C
        sd = ex.std(axis=1, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(sd > 0, ex.mean(axis=1) / sd * math.sqrt(12), np.nan)

    d_cagr = cagr(S) - cagr(B)
    d_sh = shp(S) - shp(B)
    d_sh = d_sh[~np.isnan(d_sh)]
    return BootstrapCI(
        cagr_diff=(float(np.percentile(d_cagr, 5)), float(np.percentile(d_cagr, 95))),
        sharpe_diff=(float(np.percentile(d_sh, 5)), float(np.percentile(d_sh, 95)))
        if len(d_sh)
        else (math.nan, math.nan),
        p_cagr_better=float((d_cagr > 0).mean()),
        p_sharpe_better=float((d_sh > 0).mean()) if len(d_sh) else math.nan,
    )


# ----------------------------------------------------------------------------- aportes


def contributions_value(monthly: pd.Series, amount: float) -> float:
    """Valor final aportando ``amount`` al inicio de cada mes y reinvirtiendo todo.

    Simplificación: usa los retornos netos de la simulación (costos con la cuenta supuesta);
    no incluye impuestos.
    """
    v = 0.0
    for r in monthly.to_numpy(dtype=float):
        v = (v + amount) * (1.0 + r)
    return v
