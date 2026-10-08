"""Reglas de rotación de ETFs como funciones puras (sin red ni base de datos).

Cada estrategia recibe ``monthly``: cierres AJUSTADOS (retorno total) del último día hábil de
cada mes, con una fila por mes hasta el mes de la señal INCLUSIVE y nunca después. Devuelve
los pesos objetivo ``{ticker: peso}`` (suman 1), o ``None`` si aún no hay historia suficiente.
Como la función no recibe datos posteriores a la señal, no puede mirar al futuro.

Pre-registro y justificación: docs/ETF-ROTACION.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import numpy as np
import pandas as pd

from tradingtool.config import EtfConfig

Weights = dict[str, float]
StrategyFn = Callable[[pd.DataFrame], Weights | None]

PRINCIPAL = "principal"
NEIGHBOR = "vecino"
LITERATURE = "literatura"
REFERENCE = "referencia"

LABELS = {
    "SPY": "Acciones EE. UU. (S&P 500)",
    "EFA": "Acciones de desarrollados fuera de EE. UU.",
    "EEM": "Acciones emergentes",
    "IEF": "Bonos del Tesoro EE. UU. 7-10 años",
    "AGG": "Bonos EE. UU. (agregado)",
    "VNQ": "Inmobiliario EE. UU. (REITs)",
    "DBC": "Materias primas",
    "CASH": "Efectivo (letras del Tesoro)",
}

# Activos "de riesgo" para medir la exposición promedio (el resto es defensivo).
RISKY = frozenset({"SPY", "EFA", "EEM", "VNQ", "DBC"})

# Estrategias de la literatura, con sus parámetros originales.
GEM_ASSETS = ("SPY", "EFA", "AGG")
GTAA_ASSETS = ("SPY", "EFA", "IEF", "VNQ", "DBC")
NEIGHBOR_HORIZONS = ((3, 6, 12), (1, 3, 6), (1, 3, 6, 9, 12))
NEIGHBOR_LAG = 5


# ----------------------------------------------------------------------------- piezas


def momentum(monthly: pd.DataFrame, months: int) -> pd.Series:
    """Retorno total de ``months`` meses al último cierre mensual (NaN si falta historia)."""
    if len(monthly) <= months:
        return pd.Series(np.nan, index=monthly.columns, dtype=float)
    return monthly.iloc[-1] / monthly.iloc[-1 - months] - 1.0


def _best(returns: pd.Series, assets: tuple[str, ...]) -> str:
    """El de mayor retorno; ante un empate gana el que aparece primero en la lista."""
    return max(assets, key=lambda a: returns[a])


def _sma_above(monthly: pd.DataFrame, asset: str, months: int) -> bool | None:
    if len(monthly) < months:
        return None
    window = monthly[asset].iloc[-months:]
    if window.isna().any():
        return None
    return bool(window.iloc[-1] > window.mean())


def _available(monthly: pd.DataFrame, assets: tuple[str, ...]) -> bool:
    return all(a in monthly.columns and not np.isnan(monthly[a].iloc[-1]) for a in assets)


# ----------------------------------------------------------------------------- estrategias


@dataclass(frozen=True)
class HorizonPick:
    months: int
    pick: str
    returns: dict[str, float]  # retorno de cada activo en ese horizonte
    risk_on: bool  # True si el mejor activo de riesgo le ganó al efectivo


def dual_momentum_picks(
    monthly: pd.DataFrame,
    risk: tuple[str, ...],
    defensive: tuple[str, ...],
    cash: str,
    horizons: tuple[int, ...],
) -> list[HorizonPick] | None:
    """La elección de cada horizonte (para explicar la señal paso a paso)."""
    assets = tuple(dict.fromkeys([*risk, *defensive, cash]))
    if not all(a in monthly.columns for a in assets):
        return None
    out = []
    for h in horizons:
        r = momentum(monthly[list(assets)], h)
        if r.isna().any():
            return None
        best = _best(r, risk)
        risk_on = bool(r[best] > r[cash])
        pick = best if risk_on else _best(r, defensive)
        out.append(HorizonPick(h, pick, {a: float(r[a]) for a in assets}, risk_on))
    return out


def dual_momentum(
    monthly: pd.DataFrame,
    *,
    risk: tuple[str, ...],
    defensive: tuple[str, ...],
    cash: str,
    horizons: tuple[int, ...],
) -> Weights | None:
    """rotacion-v1: en cada horizonte, el mejor activo de riesgo si supera al efectivo; si no,
    el mejor defensivo. Cada horizonte decide una parte igual de la cartera."""
    picks = dual_momentum_picks(monthly, risk, defensive, cash, horizons)
    if picks is None:
        return None
    w: Weights = {}
    for p in picks:
        w[p.pick] = w.get(p.pick, 0.0) + 1.0 / len(picks)
    return w


def gem(
    monthly: pd.DataFrame,
    *,
    us: str = "SPY",
    intl: str = "EFA",
    bonds: str = "AGG",
    cash: str = "CASH",
    lookback: int = 12,
) -> Weights | None:
    """GEM (Antonacci, 2014): si EE. UU. supera al efectivo en 12 meses, el mejor entre EE. UU.
    y el resto del mundo; si no, bonos agregados."""
    assets = [us, intl, bonds, cash]
    if not all(a in monthly.columns for a in assets):
        return None
    r = momentum(monthly[assets], lookback)
    if r.isna().any():
        return None
    if r[us] > r[cash]:
        return {us: 1.0} if r[us] >= r[intl] else {intl: 1.0}
    return {bonds: 1.0}


def sma_timing(
    monthly: pd.DataFrame, *, asset: str = "SPY", cash: str = "CASH", months: int = 10
) -> Weights | None:
    """Tendencia (Faber, 2007): el activo si su cierre mensual supera su promedio de 10 meses;
    si no, efectivo."""
    if not _available(monthly, (asset, cash)):
        return None
    above = _sma_above(monthly, asset, months)
    if above is None:
        return None
    return {asset: 1.0} if above else {cash: 1.0}


def gtaa(
    monthly: pd.DataFrame,
    *,
    assets: tuple[str, ...] = GTAA_ASSETS,
    cash: str = "CASH",
    months: int = 10,
) -> Weights | None:
    """GTAA5 (Faber, 2007): partes iguales; cada activo bajo su promedio de 10 meses pasa su
    parte a efectivo."""
    if not _available(monthly, (*assets, cash)):
        return None
    w: Weights = {}
    for a in assets:
        above = _sma_above(monthly, a, months)
        if above is None:
            return None
        key = a if above else cash
        w[key] = w.get(key, 0.0) + 1.0 / len(assets)
    return w


def fixed_weights(
    monthly: pd.DataFrame, *, weights: tuple[tuple[str, float], ...]
) -> Weights | None:
    """Cartera fija (comprar y mantener / 60-40)."""
    if not _available(monthly, tuple(a for a, _ in weights)):
        return None
    return dict(weights)


# ----------------------------------------------------------------------------- catálogo


@dataclass(frozen=True)
class StrategySpec:
    key: str
    name: str
    role: str  # principal | vecino | literatura | referencia
    fn: StrategyFn
    assets: tuple[str, ...]  # activos que necesita (para saber qué datos faltan)
    exec_lag: int = 1


def _h(horizons: tuple[int, ...]) -> str:
    return "-".join(str(h) for h in horizons)


def build_specs(cfg: EtfConfig) -> list[StrategySpec]:
    """Todas las estrategias del informe, en el orden en que se muestran (pre-registradas)."""
    risk, defensive, cash = cfg.risk_assets, cfg.defensive_assets, cfg.cash_asset
    base_assets = tuple(dict.fromkeys([*risk, *defensive, cash]))

    def dm(
        risk: tuple[str, ...] = risk,
        defensive: tuple[str, ...] = defensive,
        horizons: tuple[int, ...] = cfg.horizons_months,
    ) -> StrategyFn:
        return partial(dual_momentum, risk=risk, defensive=defensive, cash=cash, horizons=horizons)

    specs = [
        StrategySpec(
            PRINCIPAL,
            f"★ Rotación (momentum {_h(cfg.horizons_months)})",
            PRINCIPAL,
            dm(),
            base_assets,
            cfg.execution_lag_days,
        )
    ]
    for hs in NEIGHBOR_HORIZONS:
        if tuple(hs) != tuple(cfg.horizons_months):
            specs.append(
                StrategySpec(
                    f"vecino_h{_h(hs)}",
                    f"Vecino: horizontes {_h(hs)}",
                    NEIGHBOR,
                    dm(horizons=hs),
                    base_assets,
                    cfg.execution_lag_days,
                )
            )
    lag = NEIGHBOR_LAG if cfg.execution_lag_days != NEIGHBOR_LAG else NEIGHBOR_LAG * 2
    specs.append(
        StrategySpec(
            f"vecino_t{lag}",
            f"Vecino: ejecutar {lag} días después",
            NEIGHBOR,
            dm(),
            base_assets,
            lag,
        )
    )
    if len(risk) >= 2:
        fewer = risk[:-1]
        specs.append(
            StrategySpec(
                f"vecino_sin_{risk[-1].lower()}",
                f"Vecino: sin {risk[-1]}",
                NEIGHBOR,
                dm(risk=fewer),
                tuple(dict.fromkeys([*fewer, *defensive, cash])),
                cfg.execution_lag_days,
            )
        )
    if tuple(defensive) != (cash,):
        specs.append(
            StrategySpec(
                "vecino_solo_efectivo",
                "Vecino: refugio solo efectivo",
                NEIGHBOR,
                dm(defensive=(cash,)),
                tuple(dict.fromkeys([*risk, cash])),
                cfg.execution_lag_days,
            )
        )
    specs += [
        StrategySpec(
            "gem",
            "GEM (Antonacci 2014)",
            LITERATURE,
            partial(gem, cash=cash),
            (*GEM_ASSETS, cash),
        ),
        StrategySpec(
            "sma10",
            "Tendencia SMA10 sobre SPY (Faber 2007)",
            LITERATURE,
            partial(sma_timing, asset="SPY", cash=cash),
            ("SPY", cash),
        ),
        StrategySpec(
            "gtaa5",
            "GTAA5 (Faber 2007)",
            LITERATURE,
            partial(gtaa, cash=cash),
            (*GTAA_ASSETS, cash),
        ),
        StrategySpec(
            "benchmark",
            f"Comprar y mantener {cfg.benchmark}",
            REFERENCE,
            partial(fixed_weights, weights=((cfg.benchmark, 1.0),)),
            (cfg.benchmark,),
        ),
        StrategySpec(
            "balanced",
            "/".join(f"{w * 100:.0f}" for w in cfg.balanced_benchmark.values())
            + " "
            + "/".join(cfg.balanced_benchmark),
            REFERENCE,
            partial(fixed_weights, weights=tuple(cfg.balanced_benchmark.items())),
            tuple(cfg.balanced_benchmark),
        ),
    ]
    return specs


def required_tickers(cfg: EtfConfig) -> list[str]:
    """ETFs que hay que descargar (sin el efectivo, que viene de FRED)."""
    tickers: list[str] = []
    for s in build_specs(cfg):
        tickers += [a for a in s.assets if a != cfg.cash_asset]
    return list(dict.fromkeys(tickers))
