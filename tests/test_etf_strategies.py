"""Reglas de la rotación de ETFs (funciones puras)."""

from __future__ import annotations

import math

import pytest

from etf_synth import monthly_frame
from tradingtool.config import EtfConfig
from tradingtool.etf.strategies import (
    LITERATURE,
    NEIGHBOR,
    PRINCIPAL,
    REFERENCE,
    build_specs,
    dual_momentum,
    dual_momentum_picks,
    fixed_weights,
    gem,
    gtaa,
    momentum,
    required_tickers,
    sma_timing,
)

RISK = ("SPY", "EFA", "EEM")
DEF = ("IEF", "CASH")
H = (1, 3, 6, 12)


def _linear(start: float, step: float, n: int = 13) -> list[float]:
    return [start + step * i for i in range(n)]


def test_momentum_is_total_return_over_h_months():
    m = monthly_frame({"A": [100, 110, 121]})
    assert momentum(m, 1)["A"] == pytest.approx(0.10)
    assert momentum(m, 2)["A"] == pytest.approx(0.21)
    assert math.isnan(momentum(m, 3)["A"])  # falta historia


def test_dual_momentum_picks_best_risk_asset_when_it_beats_cash():
    m = monthly_frame(
        {
            "SPY": _linear(100, 1),
            "EFA": _linear(100, 0.5),
            "EEM": _linear(100, 2),  # el mejor en todos los horizontes
            "IEF": _linear(100, 0.2),
            "CASH": _linear(100, 0.1),
        }
    )
    assert dual_momentum(m, risk=RISK, defensive=DEF, cash="CASH", horizons=H) == {"EEM": 1.0}


def test_dual_momentum_goes_to_best_refuge_when_stocks_lose_to_cash():
    falling = _linear(100, -2)
    m = monthly_frame(
        {
            "SPY": falling,
            "EFA": falling,
            "EEM": falling,
            "IEF": _linear(100, 1),  # los bonos suben
            "CASH": _linear(100, 0.1),
        }
    )
    assert dual_momentum(m, risk=RISK, defensive=DEF, cash="CASH", horizons=H) == {"IEF": 1.0}
    m["IEF"] = _linear(100, -1)  # bonos cayendo (como en 2022): efectivo
    assert dual_momentum(m, risk=RISK, defensive=DEF, cash="CASH", horizons=H) == {"CASH": 1.0}


def test_each_horizon_decides_an_equal_slice():
    # SPY sube 12 meses, pero cae el último mes: el horizonte de 1 mes va a defensivo.
    spy = [*_linear(100, 2, 12), 120.0]
    m = monthly_frame(
        {
            "SPY": spy,
            "EFA": _linear(100, -1),
            "EEM": _linear(100, -1),
            "IEF": _linear(100, 0.3),
            "CASH": _linear(100, 0.1),
        }
    )
    picks = dual_momentum_picks(m, RISK, DEF, "CASH", H)
    assert [p.pick for p in picks] == ["IEF", "SPY", "SPY", "SPY"]
    assert [p.risk_on for p in picks] == [False, True, True, True]
    w = dual_momentum(m, risk=RISK, defensive=DEF, cash="CASH", horizons=H)
    assert w == pytest.approx({"SPY": 0.75, "IEF": 0.25})


def test_dual_momentum_needs_full_history_and_all_assets():
    rows = {a: _linear(100, 1, 12) for a in (*RISK, *DEF)}  # 12 filas: falta 1 para 12 meses
    assert (
        dual_momentum(monthly_frame(rows), risk=RISK, defensive=DEF, cash="CASH", horizons=H)
        is None
    )
    rows = {a: _linear(100, 1) for a in ("SPY", "EFA", "IEF", "CASH")}  # sin EEM
    assert (
        dual_momentum(monthly_frame(rows), risk=RISK, defensive=DEF, cash="CASH", horizons=H)
        is None
    )


def test_ties_go_to_the_first_asset_in_the_list():
    same = _linear(100, 1)
    m = monthly_frame({"SPY": same, "EFA": same, "EEM": same, "IEF": same, "CASH": _linear(100, 0)})
    assert dual_momentum(m, risk=RISK, defensive=DEF, cash="CASH", horizons=H) == {"SPY": 1.0}


def test_gem_rules():
    base = {"AGG": _linear(100, 0.2), "CASH": _linear(100, 0.1)}
    m = monthly_frame({"SPY": _linear(100, 2), "EFA": _linear(100, 1), **base})
    assert gem(m) == {"SPY": 1.0}
    m = monthly_frame({"SPY": _linear(100, 1), "EFA": _linear(100, 2), **base})
    assert gem(m) == {"EFA": 1.0}
    # absoluto: EE. UU. pierde contra el efectivo → bonos (aunque EFA suba)
    m = monthly_frame({"SPY": _linear(100, -1), "EFA": _linear(100, 2), **base})
    assert gem(m) == {"AGG": 1.0}


def test_sma_timing_and_gtaa():
    up, down = _linear(100, 1, 10), _linear(100, -1, 10)
    m = monthly_frame({"SPY": up, "CASH": _linear(100, 0.1, 10)})
    assert sma_timing(m) == {"SPY": 1.0}
    m = monthly_frame({"SPY": down, "CASH": _linear(100, 0.1, 10)})
    assert sma_timing(m) == {"CASH": 1.0}
    assert sma_timing(monthly_frame({"SPY": up[:9], "CASH": up[:9]})) is None
    rows = {"SPY": up, "EFA": up, "IEF": down, "VNQ": down, "DBC": up, "CASH": up}
    w = gtaa(monthly_frame(rows))
    assert w == pytest.approx({"SPY": 0.2, "EFA": 0.2, "DBC": 0.2, "CASH": 0.4})


def test_fixed_weights_need_data_on_the_signal_month():
    m = monthly_frame({"SPY": [1.0, 2.0], "IEF": [1.0, float("nan")]})
    assert fixed_weights(m, weights=(("SPY", 0.6), ("IEF", 0.4))) is None
    assert fixed_weights(m, weights=(("SPY", 1.0),)) == {"SPY": 1.0}


def test_preregistered_catalog():
    specs = build_specs(EtfConfig())
    roles = [s.role for s in specs]
    assert roles.count(PRINCIPAL) == 1 and specs[0].role == PRINCIPAL
    assert roles.count(NEIGHBOR) == 6
    assert roles.count(LITERATURE) == 3
    assert roles.count(REFERENCE) == 2
    assert len({s.key for s in specs}) == len(specs)
    lag5 = next(s for s in specs if s.key == "vecino_t5")
    assert lag5.exec_lag == 5
    assert set(required_tickers(EtfConfig())) == {"SPY", "EFA", "EEM", "IEF", "AGG", "VNQ", "DBC"}
