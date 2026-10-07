"""Modelo de costos de IBKR Pro para acciones/ETF de EE. UU. (estimación conservadora).

Componentes por orden:
- Comisión del plan (tiered o fixed) con mínimo por orden y tope % del valor.
- Tiered: tasas de bolsa/compensación aproximadas por acción (asumimos tomar liquidez).
- Ventas: tarifa SEC (Sección 31) sobre el valor vendido y FINRA TAF por acción.
- Spread + slippage por lado según la liquidez (ADV en USD).

Las tarifas exactas cambian; los valores viven en `config/costs.yaml`. Es preferible
sobreestimar costos que subestimarlos.
"""

from __future__ import annotations

from tradingtool.config import CostsConfig
from tradingtool.models import CostEstimate


def slippage_bps(cfg: CostsConfig, adv_usd: float | None) -> float:
    """bps por lado según ADV. Sin ADV conocido se usa el tramo más ilíquido (conservador)."""
    if adv_usd is None:
        return cfg.slippage_tiers[0].bps_per_side
    for tier in cfg.slippage_tiers:
        if tier.max_adv_usd is None or adv_usd <= tier.max_adv_usd:
            return tier.bps_per_side
    return cfg.slippage_tiers[-1].bps_per_side  # pragma: no cover - validado en config


def estimate_order_cost(
    cfg: CostsConfig,
    side: str,
    shares: int,
    price: float,
    adv_usd: float | None = None,
) -> CostEstimate:
    if side not in ("buy", "sell"):
        raise ValueError("side debe ser 'buy' o 'sell'")
    if shares <= 0 or price <= 0:
        raise ValueError("shares y price deben ser positivos")
    value = shares * price
    plan = cfg.tiered if cfg.plan == "tiered" else cfg.fixed

    commission = max(plan.per_share * shares, plan.min_per_order)
    commission = min(commission, plan.max_pct_of_value / 100.0 * value)

    fees = plan.exchange_clearing_per_share * shares
    if side == "sell":
        reg = cfg.regulatory
        fees += reg.sec_fee_rate_on_sales * value
        fees += min(reg.finra_taf_per_share_sold * shares, reg.finra_taf_max_per_order)

    slippage = slippage_bps(cfg, adv_usd) / 10_000.0 * value
    return CostEstimate(
        side=side,
        shares=shares,
        price=price,
        commission=round(commission, 4),
        fees=round(fees, 4),
        slippage=round(slippage, 4),
    )


def estimate_roundtrip_cost(
    cfg: CostsConfig,
    shares: int,
    entry: float,
    exit_price: float | None = None,
    adv_usd: float | None = None,
) -> tuple[float, float]:
    """Costo total ida y vuelta (USD, fracción del valor de entrada)."""
    exit_price = entry if exit_price is None else exit_price
    buy = estimate_order_cost(cfg, "buy", shares, entry, adv_usd)
    sell = estimate_order_cost(cfg, "sell", shares, exit_price, adv_usd)
    total = buy.total + sell.total
    return total, total / (shares * entry)
