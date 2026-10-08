"""Simulación diaria de una cartera que sigue un calendario de pesos objetivo.

Convenciones (verificadas por tests):
- La señal de un mes usa solo cierres hasta el último día hábil de ese mes.
- Se ejecuta al cierre del día hábil número ``exec_lag`` posterior (T+1 por defecto). El primer
  retorno con los pesos nuevos es el del día siguiente a la ejecución.
- Entre rebalanceos los pesos "derivan" con los precios (comprar y mantener dentro del mes).
- Banda de no operación: no se toca un activo si su peso está a menos de ``band`` del objetivo,
  salvo entradas y salidas completas.
- Costo de cada orden: el mayor entre la comisión mínima y el % del valor, más el spread y
  deslizamiento. Se supone un tamaño de cuenta fijo, así la comisión mínima pesa igual en todo
  el periodo (con aportes, en la práctica pesa menos a medida que la cuenta crece).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingtool.config import EtfCostsCfg
from tradingtool.etf.strategies import StrategySpec, Weights

EPS = 1e-9


@dataclass(frozen=True)
class CostModel:
    min_fee_usd: float = 1.90
    fee_rate: float = 0.0005
    slippage_bps: float = 10.0
    capital_usd: float = 5_000.0

    @classmethod
    def from_config(cls, c: EtfCostsCfg, capital_usd: float | None = None) -> CostModel:
        return cls(c.min_fee_usd, c.fee_rate, c.slippage_bps, capital_usd or c.capital_usd)

    def order_cost(self, traded_fraction: float) -> float:
        """Costo de una orden que mueve ``traded_fraction`` de la cartera (como fracción)."""
        if traded_fraction <= EPS:
            return 0.0
        value = traded_fraction * self.capital_usd
        fee = max(self.min_fee_usd, self.fee_rate * value)
        return fee / self.capital_usd + traded_fraction * self.slippage_bps / 10_000.0


def month_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Último día con datos de cada mes calendario."""
    if len(index) == 0:
        return pd.DatetimeIndex([])
    s = pd.Series(index, index=index)
    last = s.groupby([index.year, index.month]).max()
    return pd.DatetimeIndex(last.to_numpy())


def normalize_weights(w: Weights) -> Weights:
    clean = {k: float(v) for k, v in w.items() if v > EPS}
    total = sum(clean.values())
    if total <= 0:
        raise ValueError("pesos objetivo vacíos")
    return {k: v / total for k, v in clean.items()}


@dataclass(frozen=True)
class Rebalance:
    signal_date: pd.Timestamp  # último día hábil del mes (datos usados hasta aquí)
    exec_date: pd.Timestamp  # día en que se opera, al cierre
    weights: Weights


def build_schedule(
    prices: pd.DataFrame, spec: StrategySpec, ends: pd.DatetimeIndex | None = None
) -> list[Rebalance]:
    """Pesos objetivo de cada mes según la estrategia, con su fecha de ejecución."""
    ends = month_ends(pd.DatetimeIndex(prices.index)) if ends is None else ends
    cols = [a for a in spec.assets if a in prices.columns]
    if len(cols) < len(spec.assets):
        return []
    monthly = prices.loc[ends, cols]
    out: list[Rebalance] = []
    for i, m in enumerate(ends):
        w = spec.fn(monthly.iloc[: i + 1])
        if w is None:
            continue
        pos = prices.index.get_loc(m) + spec.exec_lag
        if pos >= len(prices.index):
            break
        out.append(Rebalance(m, prices.index[pos], normalize_weights(w)))
    return out


def rebalance_weights(current: np.ndarray, target: np.ndarray, band: float) -> np.ndarray:
    """Pesos después de operar, respetando la banda de no operación.

    Se opera un activo si su diferencia con el objetivo es ≥ ``band``, o si hay que entrar
    (peso 0 → objetivo) o salir del todo (objetivo 0). Los que se operan reparten lo que dejan
    libre los que no se tocan; si eso los aleja del objetivo más que la banda, se rebalancea
    todo.
    """
    trade = (
        (np.abs(target - current) >= band - EPS)
        | ((target <= EPS) & (current > EPS))
        | ((current <= EPS) & (target > EPS))
    )
    if not trade.any():
        return current.copy()
    kept = ~trade
    free = 1.0 - current[kept].sum()
    tsum = target[trade].sum()
    if tsum > EPS:
        new = current.copy()
        new[trade] = target[trade] * (free / tsum)
        if np.all(np.abs(new[trade] - target[trade]) < band):
            new[kept] = current[kept]
            return new
    return target.copy()


@dataclass
class SimResult:
    key: str
    equity: pd.Series  # valor neto; el primer punto (1,0) es el día anterior al inicio
    weights: pd.DataFrame  # pesos al cierre de cada día (después de operar)
    trades: pd.DataFrame  # date, ticker, delta, cost
    schedule: list[Rebalance] = field(default_factory=list)
    nan_days: int = 0  # días con un activo en cartera sin precio (se tomó retorno 0)

    @property
    def start(self) -> pd.Timestamp:
        return self.schedule[0].exec_date


def simulate(
    prices: pd.DataFrame,
    schedule: list[Rebalance],
    costs: CostModel,
    band: float,
    key: str = "",
) -> SimResult:
    """Valor diario de la cartera (neto de costos) siguiendo ``schedule``."""
    if not schedule:
        raise ValueError(f"la estrategia {key or '?'} no tiene ninguna señal con estos datos")
    dates = pd.DatetimeIndex(prices.index)
    tickers = list(prices.columns)
    col = {t: i for i, t in enumerate(tickers)}
    values = prices.to_numpy(dtype=float)
    rets = np.full_like(values, np.nan)
    rets[1:] = values[1:] / values[:-1] - 1.0
    by_pos = {dates.get_loc(r.exec_date): r for r in schedule}
    p0 = min(by_pos)

    n_days = len(dates) - p0
    equity = np.empty(n_days)
    weights = np.zeros((n_days, len(tickers)))
    trades: list[tuple[pd.Timestamp, str, float, float]] = []
    w = np.zeros(len(tickers))
    value = 1.0
    nan_days = 0
    for k, i in enumerate(range(p0, len(dates))):
        if i > p0:
            held = w > EPS
            if held.any():
                r = rets[i, held]
                if np.isnan(r).any():
                    nan_days += 1
                    r = np.nan_to_num(r)
                growth = 1.0 + float(w[held] @ r)
                value *= growth
                w[held] = w[held] * (1.0 + r) / growth
        reb = by_pos.get(i)
        if reb is not None:
            target = np.zeros(len(tickers))
            for t, x in reb.weights.items():
                target[col[t]] = x
            new = rebalance_weights(w, target, band)
            cost = 0.0
            for j in np.nonzero(np.abs(new - w) > EPS)[0]:
                c = costs.order_cost(abs(new[j] - w[j]))
                trades.append((dates[i], tickers[j], float(new[j] - w[j]), c))
                cost += c
            value *= 1.0 - cost
            w = new
        equity[k] = value
        weights[k] = w

    idx = dates[p0:]
    eq = pd.Series(equity, index=idx, name=key)
    base_date = dates[p0 - 1] if p0 > 0 else idx[0] - pd.Timedelta(days=1)
    eq = pd.concat([pd.Series([1.0], index=pd.DatetimeIndex([base_date])), eq])
    return SimResult(
        key=key,
        equity=eq,
        weights=pd.DataFrame(weights, index=idx, columns=tickers),
        trades=pd.DataFrame(trades, columns=["date", "ticker", "delta", "cost"]),
        schedule=schedule,
        nan_days=nan_days,
    )
