"""Configuración de estrategia, riesgo y costos (archivos YAML versionados en `config/`).

Cada archivo se valida con pydantic. El hash de la configuración se guarda con cada
ejecución para poder reproducir cualquier resultado.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingtool.ids import stable_hash


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- screener (estrategia)


class UniverseCfg(_Strict):
    min_price: float = Field(2.0, ge=0)
    min_avg_dollar_volume: float = Field(250_000, ge=0)  # promedio de N días, USD
    adv_window_days: int = Field(20, ge=1)


class TransactionCfg(_Strict):
    codes: tuple[str, ...] = ("P",)  # P = compra en mercado abierto
    min_value_usd: float = Field(10_000, ge=0)  # valor mínimo comprado por el insider
    exclude_10b5_1: bool = True  # excluir compras marcadas como plan 10b5-1
    max_filing_lag_days: int = Field(10, ge=0)  # días calendario entre transacción y filing
    common_stock_only: bool = True  # solo tabla no derivada (acciones comunes)
    include_amendments: bool = False  # 4/A


class InsiderRolesCfg(_Strict):
    include_officers: bool = True
    include_directors: bool = True
    include_ten_pct_owners_only: bool = False  # dueños >10% sin cargo (suelen ser fondos)
    include_other: bool = False


class ClassificationCfg(_Strict):
    lookback_years: int = Field(3, ge=1)
    # opportunistic_only: solo insiders clasificados como oportunistas (CMP 2012)
    # exclude_routine: oportunistas + no clasificables (excluye rutinarios)
    # all: sin filtro
    mode: Literal["opportunistic_only", "exclude_routine", "all"] = "exclude_routine"


class ClusterCfg(_Strict):
    window_days: int = Field(30, ge=1)  # ventana para contar insiders distintos comprando
    min_insiders: int = Field(1, ge=1)


class ScoringCfg(_Strict):
    w_log_value: float = 1.0
    w_cluster: float = 1.0
    w_officer: float = 0.5
    w_ownership_increase: float = 1.0
    w_opportunistic: float = 0.5


class ScreenerConfig(_Strict):
    strategy_version: str = "insider-v1"
    universe: UniverseCfg = UniverseCfg()
    transactions: TransactionCfg = TransactionCfg()
    roles: InsiderRolesCfg = InsiderRolesCfg()
    classification: ClassificationCfg = ClassificationCfg()
    cluster: ClusterCfg = ClusterCfg()
    scoring: ScoringCfg = ScoringCfg()
    lookback_days_for_events: int = Field(7, ge=1)  # filings recientes que se evalúan cada día


# ---------------------------------------------------------------- riesgo


class RiskConfig(_Strict):
    capital_usd: float = Field(1_000, gt=0)  # capital del satélite (paper)
    risk_per_trade_pct: float = Field(0.5, gt=0, le=2.0)  # % del capital arriesgado por trade
    max_position_pct: float = Field(25, gt=0, le=100)
    max_positions: int = Field(5, ge=1)
    stop_atr_multiple: float = Field(2.5, gt=0)
    atr_period: int = Field(14, ge=2)
    max_roundtrip_cost_pct: float = Field(0.30, gt=0)  # costo ida y vuelta máximo (% del valor)


# ---------------------------------------------------------------- costos


class CommissionPlan(_Strict):
    per_share: float
    min_per_order: float
    max_pct_of_value: float  # tope de comisión como % del valor de la orden (1.0 = 1%)
    # Tasas aproximadas de bolsa + compensación por acción (solo plan tiered).
    exchange_clearing_per_share: float = 0.0


class RegulatoryFees(_Strict):
    # Tarifa SEC (Sección 31) sobre el valor VENDIDO. Cambia cada año: verificar.
    sec_fee_rate_on_sales: float = 0.0000278
    # FINRA TAF por acción vendida, con tope por orden. Verificar periódicamente.
    finra_taf_per_share_sold: float = 0.000166
    finra_taf_max_per_order: float = 8.30


class SlippageTier(_Strict):
    max_adv_usd: float | None  # None = sin tope
    bps_per_side: float


class CostsConfig(_Strict):
    plan: Literal["tiered", "fixed"] = "tiered"
    tiered: CommissionPlan = CommissionPlan(
        per_share=0.0035,
        min_per_order=0.35,
        max_pct_of_value=1.0,
        exchange_clearing_per_share=0.0032,
    )
    fixed: CommissionPlan = CommissionPlan(
        per_share=0.005,
        min_per_order=1.00,
        max_pct_of_value=1.0,
    )
    regulatory: RegulatoryFees = RegulatoryFees()
    # Spread + slippage por lado según liquidez (ADV en USD). Conservador a propósito.
    slippage_tiers: tuple[SlippageTier, ...] = (
        SlippageTier(max_adv_usd=1_000_000, bps_per_side=50),
        SlippageTier(max_adv_usd=5_000_000, bps_per_side=25),
        SlippageTier(max_adv_usd=50_000_000, bps_per_side=10),
        SlippageTier(max_adv_usd=None, bps_per_side=5),
    )

    @model_validator(mode="after")
    def _last_tier_unbounded(self) -> CostsConfig:
        if not self.slippage_tiers or self.slippage_tiers[-1].max_adv_usd is not None:
            raise ValueError("el último tramo de slippage debe tener max_adv_usd: null")
        return self


# ---------------------------------------------------------------- resultados


class OutcomesConfig(_Strict):
    horizons_days: tuple[int, ...] = (5, 10, 21, 63, 126)  # días hábiles


class AppConfig(_Strict):
    screener: ScreenerConfig = ScreenerConfig()
    risk: RiskConfig = RiskConfig()
    costs: CostsConfig = CostsConfig()
    outcomes: OutcomesConfig = OutcomesConfig()

    def config_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))

    def screener_hash(self) -> str:
        return stable_hash(self.screener.model_dump(mode="json"))


_FILES = {
    "screener": ("screener.yaml", ScreenerConfig),
    "risk": ("risk.yaml", RiskConfig),
    "costs": ("costs.yaml", CostsConfig),
    "outcomes": ("outcomes.yaml", OutcomesConfig),
}


def load_config(config_dir: Path | str) -> AppConfig:
    """Carga los YAML presentes; los que falten usan valores por defecto."""
    config_dir = Path(config_dir)
    parts = {}
    for key, (fname, model) in _FILES.items():
        path = config_dir / fname
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            parts[key] = model.model_validate(data)
    return AppConfig(**parts)
