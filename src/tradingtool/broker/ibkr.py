"""Conexión a IBKR en SOLO LECTURA (Fase 1).

Garantías de seguridad (verificadas por tests):
- Se conecta con ``readonly=True`` (la API rechaza órdenes en ese modo).
- Este módulo no expone ninguna función para enviar, modificar ni cancelar órdenes.
- Por defecto rechaza cuentas reales: solo acepta cuentas paper (ID que empieza por "D",
  p. ej. "DU1234567"). Las cuentas reales empiezan por "U", "F" o "I".
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tradingtool.settings import Settings

log = logging.getLogger(__name__)

PAPER_PREFIX = "D"  # DU = paper individual, DF/DI = paper de asesores/brokers


class LiveAccountRefusedError(RuntimeError):
    """Se intentó usar una cuenta real sin autorización explícita."""


class BrokerConnectionError(RuntimeError):
    """No fue posible conectar con TWS / IB Gateway."""


def is_paper_account(account_id: str) -> bool:
    return account_id.strip().upper().startswith(PAPER_PREFIX)


@dataclass(frozen=True)
class PositionRow:
    account: str
    symbol: str
    sec_type: str
    currency: str
    exchange: str
    quantity: float
    avg_cost: float


@dataclass(frozen=True)
class AccountSnapshot:
    account: str
    is_paper: bool
    values: dict[str, tuple[float | None, str]]  # tag -> (valor, moneda)
    positions: tuple[PositionRow, ...] = field(default_factory=tuple)

    def value(self, tag: str) -> float | None:
        v = self.values.get(tag)
        return v[0] if v else None


SUMMARY_TAGS = (
    "NetLiquidation",
    "TotalCashValue",
    "BuyingPower",
    "AvailableFunds",
    "GrossPositionValue",
    "UnrealizedPnL",
    "RealizedPnL",
)


def _to_float(raw: Any) -> float | None:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if math.isfinite(val) else None


def check_accounts(accounts: list[str], wanted: str, allow_live_readonly: bool) -> str:
    """Elige la cuenta a usar y aplica la regla paper-only. Devuelve el ID elegido."""
    if not accounts:
        raise BrokerConnectionError("IBKR no devolvió ninguna cuenta gestionada.")
    if wanted:
        if wanted not in accounts:
            raise BrokerConnectionError(
                f"La cuenta {wanted!r} no está entre las cuentas de esta sesión: {accounts}"
            )
        account = wanted
    else:
        account = accounts[0]
    if not is_paper_account(account) and not allow_live_readonly:
        raise LiveAccountRefusedError(
            f"La cuenta {account} parece REAL (no empieza por 'D'). Esta fase solo permite "
            "cuentas paper. Inicia sesión en IB Gateway/TWS con tu usuario paper."
        )
    return account


class IbkrReadOnly:
    """Cliente de solo lectura. Úsalo como context manager::

    with IbkrReadOnly(settings) as broker:
        snap = broker.snapshot()
    """

    def __init__(self, settings: Settings, ib_factory: Callable[[], Any] | None = None):
        self._settings = settings
        if ib_factory is None:
            from ib_async import IB

            ib_factory = IB
        self._ib = ib_factory()
        self.account: str | None = None

    # -- conexión -----------------------------------------------------------------
    def connect(self, timeout: float = 10.0) -> IbkrReadOnly:
        s = self._settings
        try:
            self._ib.connect(
                s.ibkr_host,
                s.ibkr_port,
                clientId=s.ibkr_client_id,
                timeout=timeout,
                readonly=True,
            )
        except Exception as exc:  # ConnectionRefusedError, TimeoutError, etc.
            raise BrokerConnectionError(
                f"No se pudo conectar a {s.ibkr_host}:{s.ibkr_port}. ¿Está abierto IB Gateway "
                f"(paper=4002) o TWS (paper=7497) con la API habilitada? Detalle: {exc!r}"
            ) from exc
        try:
            self.account = check_accounts(
                list(self._ib.managedAccounts()), s.ibkr_account, s.ibkr_allow_live_readonly
            )
        except Exception:
            self.disconnect()
            raise
        log.info("Conectado a IBKR (solo lectura) cuenta=%s", self.account)
        return self

    def disconnect(self) -> None:
        try:
            if self._ib.isConnected():
                self._ib.disconnect()
        except Exception:
            log.warning("Error al desconectar de IBKR", exc_info=True)

    def __enter__(self) -> IbkrReadOnly:
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    # -- lectura ------------------------------------------------------------------
    def snapshot(self) -> AccountSnapshot:
        if self.account is None:
            raise BrokerConnectionError("No conectado.")
        values: dict[str, tuple[float | None, str]] = {}
        for av in self._ib.accountSummary(self.account):
            if av.tag in SUMMARY_TAGS and (
                av.currency in ("USD", "BASE", "") or av.tag not in values
            ):
                values[av.tag] = (_to_float(av.value), av.currency)
        positions = tuple(
            PositionRow(
                account=p.account,
                symbol=getattr(p.contract, "symbol", ""),
                sec_type=getattr(p.contract, "secType", ""),
                currency=getattr(p.contract, "currency", ""),
                exchange=getattr(p.contract, "primaryExchange", "")
                or getattr(p.contract, "exchange", ""),
                quantity=float(p.position),
                avg_cost=float(p.avgCost),
            )
            for p in self._ib.positions(self.account)
        )
        return AccountSnapshot(
            account=self.account,
            is_paper=is_paper_account(self.account),
            values=values,
            positions=positions,
        )
