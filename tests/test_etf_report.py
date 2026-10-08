"""Informe y veredicto pre-registrado de la rotación de ETFs."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from etf_synth import random_prices, regime_prices
from tradingtool.config import EtfConfig, EtfVerdictCfg
from tradingtool.etf.metrics import PeriodMetrics
from tradingtool.etf.report import (
    FAIL,
    INSUFFICIENT,
    PASS,
    EtfDataError,
    evaluate_verdict,
    meets_core,
    run_report,
)
from tradingtool.etf.strategies import PRINCIPAL


def _m(sharpe=0.8, max_dd=-0.2, cagr=0.08, months=120) -> PeriodMetrics:
    empty = pd.Series(dtype=float)
    idx = pd.period_range("2015-01", periods=months, freq="M")
    return PeriodMetrics(
        start=pd.Timestamp("2014-12-31"),
        end=pd.Timestamp("2024-12-31"),
        months=months,
        cagr=cagr,
        vol=0.1,
        sharpe=sharpe,
        max_dd=max_dd,
        worst_year=-0.1,
        pct_months_up=0.6,
        trades_per_year=10,
        cost_per_year=0.005,
        risk_exposure=0.8,
        monthly=pd.Series(0.0, index=idx),
        cash_monthly=pd.Series(0.0, index=idx),
        yearly=empty,
        curve=empty,
    )


V = EtfVerdictCfg()
SPY = _m(sharpe=0.7, max_dd=-0.34, cagr=0.13)
BAL = _m(sharpe=0.75, max_dd=-0.2, cagr=0.085)
GOOD = _m(sharpe=0.9, max_dd=-0.15, cagr=0.09)


def test_verdict_pass_when_everything_holds():
    verdict, crit = evaluate_verdict(GOOD, SPY, BAL, [GOOD] * 6, GOOD, SPY, V)
    assert verdict == PASS
    assert all(c.passed for c in crit)


@pytest.mark.parametrize(
    ("principal", "label"),
    [
        (_m(sharpe=0.6, max_dd=-0.15, cagr=0.09), "1."),  # peor Sharpe que SPY
        (_m(sharpe=0.9, max_dd=-0.25, cagr=0.09), "2."),  # cae más que 60% de SPY
        (_m(sharpe=0.9, max_dd=-0.15, cagr=0.07), "3."),  # rinde menos que el 60/40
    ],
)
def test_verdict_fails_on_each_core_criterion(principal, label):
    verdict, crit = evaluate_verdict(principal, SPY, BAL, [GOOD] * 6, GOOD, SPY, V)
    assert verdict == FAIL
    failed = [c.label for c in crit if c.passed is False]
    assert failed and failed[0].startswith(label)


def test_verdict_needs_robust_neighbors_and_design_consistency():
    bad = _m(sharpe=0.5, max_dd=-0.3)
    verdict, crit = evaluate_verdict(GOOD, SPY, BAL, [GOOD] * 3 + [bad] * 3, GOOD, SPY, V)
    assert verdict == FAIL and any(c.label.startswith("4.") and not c.passed for c in crit)
    verdict, _ = evaluate_verdict(GOOD, SPY, BAL, [GOOD] * 4 + [bad] * 2, GOOD, SPY, V)
    assert verdict == PASS
    verdict, crit = evaluate_verdict(GOOD, SPY, BAL, [GOOD] * 6, bad, SPY, V)
    assert verdict == FAIL and any(c.label.startswith("5.") and not c.passed for c in crit)


def test_verdict_insufficient_without_enough_months_or_design_data():
    short = _m(sharpe=0.9, max_dd=-0.15, cagr=0.09, months=60)
    verdict, _ = evaluate_verdict(short, SPY, BAL, [GOOD] * 6, GOOD, SPY, V)
    assert verdict == INSUFFICIENT
    verdict, _ = evaluate_verdict(GOOD, SPY, BAL, [GOOD] * 6, None, None, V)
    assert verdict == INSUFFICIENT
    assert meets_core(_m(sharpe=math.nan), SPY, 0.6) is None


def test_report_on_random_walks_runs_and_starts_together():
    prices = random_prices()
    rep = run_report(prices, EtfConfig(), "validacion")
    assert rep.verdict in (PASS, FAIL, INSUFFICIENT)
    # inicio común: cuando GTAA5 tiene 10 meses de DBC (desde feb-2006)
    assert rep.first_signal == pd.Timestamp("2006-11-30")
    p = rep.metrics(PRINCIPAL)
    assert p.months == 120 and str(p.monthly.index[0]) == "2015-01"
    assert len(rep.neighbors) == 6
    assert len(rep.cost_sensitivity) == 3
    small, mid, big = (c[1] for c in rep.cost_sensitivity)
    assert small < mid < big  # cuenta más chica = la comisión mínima pesa más
    assert list(rep.yearly.index) == list(range(2015, 2025))
    assert rep.contributed == pytest.approx(400 * 120)


def test_report_without_long_history_is_insufficient():
    prices = random_prices(start="2016-01-04")
    rep = run_report(prices, EtfConfig(), "validacion")
    assert rep.verdict == INSUFFICIENT


def test_report_requires_cash_and_benchmark():
    prices = random_prices().drop(columns=["CASH"])
    with pytest.raises(EtfDataError):
        run_report(prices, EtfConfig(), "validacion")


def test_rotation_sidesteps_a_slow_crash():
    """En un mercado con tendencias claras la regla sale a tiempo (verifica la lógica)."""
    rep = run_report(regime_prices(), EtfConfig(), "diseno")
    p, spy = rep.metrics(PRINCIPAL), rep.metrics("benchmark")
    assert spy.max_dd < -0.35
    assert abs(p.max_dd) < 0.5 * abs(spy.max_dd)
    assert p.risk_exposure < 0.9  # se refugió durante la caída
    assert rep.yearly.loc[2008].iloc[0] > rep.yearly.loc[2008].iloc[1]  # 2008: mejor que SPY
