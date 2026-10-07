import math

import pandas as pd
import pytest

from tradingtool.config import CostsConfig, RiskConfig
from tradingtool.risk.costs import estimate_order_cost, estimate_roundtrip_cost, slippage_bps
from tradingtool.risk.sizing import atr, avg_dollar_volume, size_position


def test_tiered_min_commission_applies():
    cfg = CostsConfig()
    c = estimate_order_cost(cfg, "buy", 10, 50.0, adv_usd=100e6)
    # 10 * 0.0035 = 0.035 < mínimo 0.35
    assert c.commission == pytest.approx(0.35)
    assert c.fees == pytest.approx(10 * 0.0032)
    assert c.slippage == pytest.approx(500 * 5 / 10_000)


def test_commission_capped_at_one_percent_of_value():
    cfg = CostsConfig()
    c = estimate_order_cost(cfg, "buy", 1, 10.0, adv_usd=100e6)  # valor 10 USD
    assert c.commission == pytest.approx(0.10)  # 1% de 10 < mínimo 0.35


def test_fixed_plan():
    cfg = CostsConfig(plan="fixed")
    c = estimate_order_cost(cfg, "buy", 100, 50.0, adv_usd=100e6)
    assert c.commission == pytest.approx(1.0)  # max(0.5, 1.0)
    assert c.fees == 0


def test_sell_adds_regulatory_fees():
    cfg = CostsConfig()
    buy = estimate_order_cost(cfg, "buy", 100, 20.0, adv_usd=100e6)
    sell = estimate_order_cost(cfg, "sell", 100, 20.0, adv_usd=100e6)
    assert sell.fees > buy.fees


def test_slippage_tiers_and_unknown_adv_is_conservative():
    cfg = CostsConfig()
    assert slippage_bps(cfg, None) == 50
    assert slippage_bps(cfg, 500_000) == 50
    assert slippage_bps(cfg, 2_000_000) == 25
    assert slippage_bps(cfg, 1e9) == 5


def test_invalid_inputs():
    cfg = CostsConfig()
    with pytest.raises(ValueError):
        estimate_order_cost(cfg, "hold", 1, 1.0)
    with pytest.raises(ValueError):
        estimate_order_cost(cfg, "buy", 0, 1.0)


def test_roundtrip_pct_small_vs_large_position():
    cfg = CostsConfig()
    _, small = estimate_roundtrip_cost(cfg, 2, 50.0, adv_usd=100e6)  # 100 USD
    _, large = estimate_roundtrip_cost(cfg, 60, 50.0, adv_usd=100e6)  # 3000 USD
    assert small > large
    assert small > 0.005  # > 0.5% con 100 USD


def test_size_position_basic():
    risk = RiskConfig(capital_usd=10_000, risk_per_trade_pct=1.0, max_position_pct=50)
    r = size_position(risk, CostsConfig(), entry=50.0, stop=45.0, adv_usd=100e6)
    assert r.shares == 20  # 100 USD / 5
    assert r.risk_usd == pytest.approx(100.0)
    assert r.ok


def test_size_position_capped_by_max_position():
    risk = RiskConfig(capital_usd=10_000, risk_per_trade_pct=1.0, max_position_pct=10)
    r = size_position(risk, CostsConfig(), entry=50.0, stop=49.0, adv_usd=100e6)
    assert r.shares == 20  # tope 1000 USD / 50
    assert any("tope" in w for w in r.warnings)


def test_size_position_too_small_is_not_ok():
    risk = RiskConfig(capital_usd=300, risk_per_trade_pct=0.5)
    r = size_position(risk, CostsConfig(), entry=40.0, stop=38.0, adv_usd=100e6)
    # riesgo 1.5 USD / 2 por acción = 0 acciones
    assert r.shares == 0 and not r.ok


def test_size_position_rejects_when_costs_too_high():
    risk = RiskConfig(capital_usd=1_000, risk_per_trade_pct=1.0, max_position_pct=100)
    r = size_position(risk, CostsConfig(), entry=10.0, stop=9.0, adv_usd=100e6)
    # 10 acciones x 10 = 100 USD -> mínimos de comisión hacen > 0.3%
    assert r.shares == 10
    assert not r.ok
    assert any("Costo ida y vuelta" in w for w in r.warnings)


def test_size_position_validates_stop():
    with pytest.raises(ValueError):
        size_position(RiskConfig(), CostsConfig(), entry=10.0, stop=11.0)


def _bars(n=30):
    rows = []
    for i in range(n):
        c = 100 + i
        rows.append(
            {
                "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
                "open": c,
                "high": c + 2,
                "low": c - 2,
                "close": c,
                "volume": 1000,
            }
        )
    return pd.DataFrame(rows)


def test_atr_and_adv():
    b = _bars()
    a = atr(b, 14)
    # rango verdadero = max(4, |102+i - (99+i)|=3, ...) = 4 constante
    assert a == pytest.approx(4.0)
    assert atr(b.head(10), 14) is None
    adv = avg_dollar_volume(b, 20)
    assert adv == pytest.approx(sum((100 + i) * 1000 for i in range(10, 30)) / 20)
    assert avg_dollar_volume(b.head(5), 20) is None
    assert not math.isnan(a)
