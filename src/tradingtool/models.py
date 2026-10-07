"""Modelos de dominio compartidos entre módulos (contratos estables).

Convenciones:
- Fechas como ``datetime.date``; marcas de tiempo como ``datetime.datetime`` (UTC o naive ET,
  indicado en el campo).
- Valores desconocidos = ``None`` (nunca 0 inventado).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class ReportingOwner:
    """Persona o entidad que reporta en un Form 4 (un filing puede tener varias)."""

    owner_cik: str
    owner_name: str
    is_director: bool = False
    is_officer: bool = False
    is_ten_pct_owner: bool = False
    is_other: bool = False
    officer_title: str | None = None


@dataclass(frozen=True)
class InsiderTransaction:
    """Una línea de transacción (tabla no derivada o derivada) de un Form 4."""

    seq: int  # orden dentro del filing (0, 1, 2, ...)
    is_derivative: bool
    security_title: str | None
    transaction_date: date | None
    transaction_code: str | None  # "P" compra en mercado abierto, "S" venta, ...
    shares: float | None
    price_per_share: float | None
    acquired_disposed: str | None  # "A" adquirido / "D" dispuesto
    shares_owned_after: float | None
    direct_indirect: str | None  # "D" directa / "I" indirecta
    equity_swap: bool | None = None
    footnote_ids: tuple[str, ...] = ()

    @property
    def value_usd(self) -> float | None:
        if self.shares is None or self.price_per_share is None:
            return None
        return self.shares * self.price_per_share


@dataclass(frozen=True)
class Form4Filing:
    """Un filing de Form 4 / 4/A normalizado (venga de EDGAR diario o del dataset trimestral)."""

    accession: str  # formato 0000000000-00-000000
    source: str  # "edgar_daily" | "sec_bulk" | "fixture"
    form_type: str  # "4" | "4/A"
    filing_date: date
    acceptance_ts: datetime | None  # hora de aceptación EDGAR (America/New_York, naive)
    period_of_report: date | None
    issuer_cik: str
    issuer_name: str | None
    issuer_ticker: str | None
    aff10b5one: bool | None  # casilla "plan 10b5-1" (Form 4 desde 2023); None = no informado
    owners: tuple[ReportingOwner, ...] = ()
    transactions: tuple[InsiderTransaction, ...] = ()
    footnotes: dict[str, str] = field(default_factory=dict)

    @property
    def primary_owner(self) -> ReportingOwner | None:
        return self.owners[0] if self.owners else None


@dataclass(frozen=True)
class Signal:
    """Idea generada por el screener (pase o no los filtros).

    Las ideas bloqueadas también se guardan: sirven como contrafactuales.
    """

    signal_id: str
    strategy_version: str
    config_hash: str
    as_of_date: date  # fecha en que la información fue pública (filing_date del último filing)
    ticker: str | None
    issuer_cik: str
    issuer_name: str | None
    score: float
    passed: bool
    reasons: tuple[str, ...]  # por qué pasó o por qué se bloqueó (texto en español)
    features: dict[str, Any]
    accessions: tuple[str, ...]


@dataclass(frozen=True)
class CostEstimate:
    side: str  # "buy" | "sell"
    shares: int
    price: float
    commission: float
    fees: float
    slippage: float

    @property
    def total(self) -> float:
        return self.commission + self.fees + self.slippage

    @property
    def pct_of_value(self) -> float:
        value = self.shares * self.price
        return self.total / value if value > 0 else float("inf")


@dataclass(frozen=True)
class SizingResult:
    shares: int
    entry: float
    stop: float
    risk_per_share: float
    position_value: float
    risk_usd: float
    risk_pct_of_capital: float
    roundtrip_cost: float
    roundtrip_cost_pct: float
    r_multiple_cost: float  # costo ida y vuelta expresado en R
    warnings: tuple[str, ...]
    ok: bool  # False si alguna regla dura impide la operación
