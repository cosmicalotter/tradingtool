"""Tamaño de posición por riesgo fijo, con acciones enteras (la API de IBKR no admite fracciones).

Reglas:
1. Riesgo por trade = capital x riesgo% ; acciones = floor(riesgo / (entrada - stop)).
2. Tope por posición: valor <= capital x max_position%.
3. Si el costo ida y vuelta supera el máximo configurado, la operación se marca como NO ok
   (con capital pequeño, este es el filtro que más trades descarta, y está bien que así sea).
"""

from __future__ import annotations

import math

import pandas as pd

from tradingtool.config import CostsConfig, RiskConfig
from tradingtool.models import SizingResult
from tradingtool.risk.costs import estimate_roundtrip_cost


def atr(bars: pd.DataFrame, period: int = 14) -> float | None:
    """ATR de Wilder sobre barras ordenadas por fecha (columnas high, low, close).

    Devuelve None si no hay suficientes datos.
    """
    if bars is None or len(bars) < period + 1:
        return None
    df = bars.sort_values("date") if "date" in bars.columns else bars
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(
        axis=1
    )
    tr = tr.iloc[1:]
    value = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().iloc[-1]
    return float(value) if pd.notna(value) and value > 0 else None


def avg_dollar_volume(bars: pd.DataFrame, window: int = 20) -> float | None:
    if bars is None or len(bars) < window:
        return None
    df = bars.sort_values("date") if "date" in bars.columns else bars
    dv = (df["close"].astype(float) * df["volume"].astype(float)).tail(window)
    value = dv.mean()
    return float(value) if pd.notna(value) else None


def size_position(
    risk_cfg: RiskConfig,
    costs_cfg: CostsConfig,
    entry: float,
    stop: float,
    capital_usd: float | None = None,
    adv_usd: float | None = None,
) -> SizingResult:
    capital = risk_cfg.capital_usd if capital_usd is None else capital_usd
    warnings: list[str] = []
    if entry <= 0 or stop <= 0:
        raise ValueError("entrada y stop deben ser positivos")
    if stop >= entry:
        raise ValueError("para una compra, el stop debe estar por debajo de la entrada")

    risk_per_share = entry - stop
    risk_budget = capital * risk_cfg.risk_per_trade_pct / 100.0
    shares = math.floor(risk_budget / risk_per_share)

    max_value = capital * risk_cfg.max_position_pct / 100.0
    if shares * entry > max_value:
        shares = math.floor(max_value / entry)
        warnings.append(
            f"Tamaño limitado por el tope de {risk_cfg.max_position_pct:.0f}% del capital "
            "por posición."
        )

    ok = True
    if shares < 1:
        ok = False
        warnings.append(
            "Con este capital y este stop no alcanza ni para 1 acción respetando el riesgo máximo."
        )
        return SizingResult(
            shares=0,
            entry=entry,
            stop=stop,
            risk_per_share=risk_per_share,
            position_value=0.0,
            risk_usd=0.0,
            risk_pct_of_capital=0.0,
            roundtrip_cost=0.0,
            roundtrip_cost_pct=0.0,
            r_multiple_cost=0.0,
            warnings=tuple(warnings),
            ok=False,
        )

    position_value = shares * entry
    risk_usd = shares * risk_per_share
    rt_cost, rt_pct = estimate_roundtrip_cost(costs_cfg, shares, entry, stop, adv_usd)
    r_cost = rt_cost / risk_usd if risk_usd > 0 else math.inf

    if rt_pct * 100 > risk_cfg.max_roundtrip_cost_pct:
        ok = False
        warnings.append(
            f"Costo ida y vuelta estimado {rt_pct * 100:.2f}% supera el máximo "
            f"{risk_cfg.max_roundtrip_cost_pct:.2f}%: la posición es demasiado pequeña para "
            "que valga la pena."
        )
    if r_cost > 0.2:
        warnings.append(f"Los costos se comen {r_cost:.2f}R del riesgo (más de 0.2R).")

    return SizingResult(
        shares=shares,
        entry=entry,
        stop=stop,
        risk_per_share=risk_per_share,
        position_value=position_value,
        risk_usd=risk_usd,
        risk_pct_of_capital=risk_usd / capital * 100.0,
        roundtrip_cost=rt_cost,
        roundtrip_cost_pct=rt_pct * 100.0,
        r_multiple_cost=r_cost,
        warnings=tuple(warnings),
        ok=ok,
    )
