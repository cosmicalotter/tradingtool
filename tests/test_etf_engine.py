"""Simulación de la rotación: ejecución sin mirar al futuro, costos, banda y métricas."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tradingtool.etf.engine import (
    CostModel,
    Rebalance,
    build_schedule,
    month_ends,
    rebalance_weights,
    simulate,
)
from tradingtool.etf.metrics import (
    bootstrap_vs,
    contributions_value,
    max_drawdown,
    period_metrics,
    period_returns,
    sharpe,
    window,
)
from tradingtool.etf.strategies import StrategySpec

FREE = CostModel(min_fee_usd=0.0, fee_rate=0.0, slippage_bps=0.0, capital_usd=1_000)


def _flat_prices(dates: pd.DatetimeIndex, **cols: list[float]) -> pd.DataFrame:
    return pd.DataFrame(cols, index=dates, dtype=float)


# ----------------------------------------------------------------------------- costos


def test_order_cost_minimum_fee_dominates_small_orders():
    c = CostModel(min_fee_usd=1.90, fee_rate=0.0005, slippage_bps=10, capital_usd=5_000)
    # orden de 25% = US$1.250: comisión mínima 1,90 (> 0,05% = 0,625) + 10 pb de 25%
    assert c.order_cost(0.25) == pytest.approx(1.90 / 5_000 + 0.25 * 0.001)
    big = CostModel(min_fee_usd=1.90, fee_rate=0.0005, slippage_bps=0, capital_usd=1_000_000)
    assert big.order_cost(1.0) == pytest.approx(0.0005)  # el % supera al mínimo
    assert c.order_cost(0.0) == 0.0


def test_month_ends_are_last_trading_day_of_each_month():
    idx = pd.DatetimeIndex(["2024-01-30", "2024-01-31", "2024-02-28", "2024-02-29", "2024-03-01"])
    assert list(month_ends(idx).strftime("%Y-%m-%d")) == ["2024-01-31", "2024-02-29", "2024-03-01"]


# ----------------------------------------------------------------------------- calendario


def test_schedule_never_sees_data_after_the_signal_month():
    dates = pd.bdate_range("2024-01-01", "2024-06-28")
    prices = _flat_prices(dates, A=list(np.linspace(100, 200, len(dates))))
    seen: list[pd.Timestamp] = []

    def spy_fn(monthly: pd.DataFrame):
        seen.append(monthly.index[-1])
        return {"A": 1.0}

    spec = StrategySpec("x", "x", "principal", spy_fn, ("A",), exec_lag=1)
    sched = build_schedule(prices, spec)
    ends = month_ends(dates)
    assert seen == list(ends)  # cada llamada termina exactamente en su fin de mes
    for r in sched:
        pos = dates.get_loc(r.signal_date)
        assert r.exec_date == dates[pos + 1]  # T+1
    # el último fin de mes no tiene día siguiente en los datos: no se ejecuta
    assert sched[-1].signal_date == ends[-2]


def test_changing_future_prices_does_not_change_past_signals():
    dates = pd.bdate_range("2023-01-02", "2024-12-31")
    rng = np.random.default_rng(0)
    base = pd.DataFrame(
        {
            a: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(dates))))
            for a in ("SPY", "EFA", "EEM", "IEF", "CASH")
        },
        index=dates,
    )
    from tradingtool.config import EtfConfig
    from tradingtool.etf.strategies import build_specs

    spec = build_specs(EtfConfig())[0]
    before = build_schedule(base, spec)
    shocked = base.copy()
    shocked.loc[shocked.index >= "2024-07-01", "EEM"] *= 3.0  # el "futuro" cambia mucho
    after = build_schedule(shocked, spec)
    old = [r for r in before if r.signal_date < pd.Timestamp("2024-07-01")]
    new = [r for r in after if r.signal_date < pd.Timestamp("2024-07-01")]
    assert old == new


# ----------------------------------------------------------------------------- simulación


def test_new_weights_earn_returns_only_after_the_execution_close():
    dates = pd.bdate_range("2024-01-29", "2024-02-09")
    a = [100.0] * len(dates)
    b = [100.0] * len(dates)
    # B salta +10% el 2024-02-01 (T+1 de la señal del 31-ene) y +10% el 2024-02-02.
    i1, i2 = dates.get_loc(pd.Timestamp("2024-02-01")), dates.get_loc(pd.Timestamp("2024-02-02"))
    for i in range(i1, len(dates)):
        b[i] = 110.0
    for i in range(i2, len(dates)):
        b[i] = 121.0
    prices = _flat_prices(dates, A=a, B=b)
    sched = [
        Rebalance(dates[0], dates[0], {"A": 1.0}),
        Rebalance(pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-01"), {"B": 1.0}),
    ]
    sim = simulate(prices, sched, FREE, band=0.05)
    eq = sim.equity
    # el salto del día de ejecución (01-feb) se lo pierde: aún tenía A al cierre anterior
    assert eq[pd.Timestamp("2024-02-01")] == pytest.approx(1.0)
    # el del día siguiente sí lo gana
    assert eq[pd.Timestamp("2024-02-02")] == pytest.approx(1.10)
    assert eq.iloc[0] == 1.0 and eq.index[0] < dates[0]  # punto base antes del inicio


def test_weights_drift_and_band_avoids_tiny_trades():
    dates = pd.bdate_range("2024-01-01", "2024-03-29")
    n = len(dates)
    spy = list(np.linspace(100, 104, n))  # sube poco: la deriva no supera la banda
    ief = [100.0] * n
    prices = _flat_prices(dates, SPY=spy, IEF=ief)
    ends = month_ends(dates)
    sched = [Rebalance(m, dates[dates.get_loc(m) + 1], {"SPY": 0.6, "IEF": 0.4}) for m in ends[:-1]]
    sim = simulate(prices, sched, FREE, band=0.05)
    assert len(sim.trades) == 2  # solo la compra inicial de cada ETF
    last = sim.weights.iloc[-1]
    assert last["SPY"] > 0.6  # derivó con el precio
    assert last.sum() == pytest.approx(1.0)


def test_costs_are_charged_on_every_order():
    dates = pd.bdate_range("2024-01-01", "2024-01-10")
    prices = _flat_prices(dates, A=[100.0] * len(dates), B=[100.0] * len(dates))
    c = CostModel(min_fee_usd=2.0, fee_rate=0.0, slippage_bps=0.0, capital_usd=1_000)
    sched = [
        Rebalance(dates[0], dates[1], {"A": 1.0}),
        Rebalance(dates[2], dates[3], {"B": 1.0}),
    ]
    sim = simulate(prices, sched, c, band=0.05)
    # compra A (0,2%), vende A y compra B (2 x 0,2%): 3 órdenes
    assert len(sim.trades) == 3
    assert sim.equity.iloc[-1] == pytest.approx((1 - 0.002) * (1 - 0.004))


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        ([0.62, 0.38], [0.6, 0.4], [0.62, 0.38]),  # dentro de la banda: no se toca
        ([0.0, 0.0], [0.6, 0.4], [0.6, 0.4]),  # compra inicial
        ([0.5, 0.5, 0.0], [0.5, 0.0, 0.5], [0.5, 0.0, 0.5]),  # salir de B, entrar a C
        ([0.52, 0.48, 0.0], [0.25, 0.5, 0.25], [0.26, 0.48, 0.26]),  # B se mantiene
        ([0.97, 0.03], [1.0, 0.0], [1.0, 0.0]),  # salir del todo aunque sea poco
    ],
)
def test_rebalance_band_rules(current, target, expected):
    out = rebalance_weights(np.array(current), np.array(target), band=0.05)
    assert out == pytest.approx(np.array(expected))
    assert out.sum() == pytest.approx(1.0)


# ----------------------------------------------------------------------------- métricas


def test_window_uses_previous_close_as_base():
    s = pd.Series(
        [1.0, 1.1, 1.2, 1.3],
        index=pd.to_datetime(["2014-12-30", "2014-12-31", "2015-01-02", "2015-01-05"]),
    )
    w = window(s, pd.Timestamp("2015-01-01"), pd.Timestamp("2015-12-31"))
    assert list(w.index.strftime("%Y-%m-%d")) == ["2014-12-31", "2015-01-02", "2015-01-05"]
    assert window(s, pd.Timestamp("2016-01-01"), pd.Timestamp("2016-12-31")) is None


def test_period_returns_start_from_the_base():
    idx = pd.to_datetime(["2014-12-31", "2015-01-15", "2015-01-30", "2015-02-27"])
    curve = pd.Series([1.0, 1.05, 1.10, 1.21], index=idx)
    m = period_returns(curve, "M")
    assert list(m.round(6)) == [0.10, 0.10]
    y = period_returns(curve, "Y")
    assert y.iloc[0] == pytest.approx(0.21)


def test_max_drawdown_and_sharpe():
    curve = pd.Series([1.0, 1.2, 0.9, 1.3], index=pd.bdate_range("2024-01-01", periods=4))
    assert max_drawdown(curve) == pytest.approx(0.9 / 1.2 - 1)
    r = np.array([0.02, 0.0, 0.02, 0.0])
    c = np.zeros(4)
    assert sharpe(r, c) == pytest.approx(0.01 / np.std(r, ddof=1) * math.sqrt(12))
    assert math.isnan(sharpe(np.array([0.01, 0.01]), np.zeros(2)))  # sin variación


def test_period_metrics_on_a_known_path():
    dates = pd.bdate_range("2014-12-01", "2016-12-30")
    growth = 1.10 ** (1 / 252)
    prices = pd.DataFrame(
        {"A": 100 * growth ** np.arange(len(dates)), "CASH": np.ones(len(dates))}, index=dates
    )
    sched = [Rebalance(dates[0], dates[1], {"A": 1.0})]
    sim = simulate(prices, sched, FREE, band=0.05)
    m = period_metrics(
        sim,
        prices["CASH"],
        pd.Timestamp("2015-01-01"),
        pd.Timestamp("2015-12-31"),
        frozenset({"A"}),
    )
    assert m.months == 12
    assert m.cagr == pytest.approx(0.10, abs=0.01)
    assert m.max_dd == pytest.approx(0.0, abs=1e-12)
    assert m.risk_exposure == pytest.approx(1.0)
    assert m.trades_per_year == 0  # la compra fue antes del periodo


def test_bootstrap_identical_series_has_zero_difference_and_is_reproducible():
    idx = pd.period_range("2015-01", periods=60, freq="M")
    rng = np.random.default_rng(3)
    s = pd.Series(rng.normal(0.01, 0.04, 60), index=idx)
    cash = pd.Series(0.001, index=idx)
    ci = bootstrap_vs(s, s, cash, samples=300, mean_block=6, seed=1)
    assert ci.cagr_diff == (0.0, 0.0)
    other = pd.Series(rng.normal(0.005, 0.03, 60), index=idx)
    a = bootstrap_vs(s, other, cash, samples=300, seed=5)
    b = bootstrap_vs(s, other, cash, samples=300, seed=5)
    assert a == b
    assert a.cagr_diff[0] <= a.cagr_diff[1]


def test_contributions_value():
    m = pd.Series([0.0, 0.0, 0.0])
    assert contributions_value(m, 400) == pytest.approx(1200)
    m = pd.Series([0.10, 0.10])
    assert contributions_value(m, 100) == pytest.approx(((100 * 1.1) + 100) * 1.1)
