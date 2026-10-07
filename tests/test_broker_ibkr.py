"""Tests de seguridad del cliente IBKR de solo lectura (sin red: IB falso)."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingtool.broker import ibkr
from tradingtool.broker.ibkr import (
    BrokerConnectionError,
    IbkrReadOnly,
    LiveAccountRefusedError,
    check_accounts,
    is_paper_account,
)
from tradingtool.settings import Settings


class FakeIB:
    def __init__(self, accounts=("DU1234567",), fail_connect=False):
        self.accounts = list(accounts)
        self.fail_connect = fail_connect
        self.connected = False
        self.connect_kwargs = None

    def connect(self, host, port, clientId, timeout, readonly):
        if self.fail_connect:
            raise ConnectionRefusedError("refused")
        self.connect_kwargs = dict(
            host=host, port=port, clientId=clientId, timeout=timeout, readonly=readonly
        )
        self.connected = True

    def isConnected(self):
        return self.connected

    def disconnect(self):
        self.connected = False

    def managedAccounts(self):
        return self.accounts

    def accountSummary(self, account):
        return [
            SimpleNamespace(tag="NetLiquidation", value="1000.5", currency="USD"),
            SimpleNamespace(tag="TotalCashValue", value="250", currency="USD"),
            SimpleNamespace(tag="Cushion", value="0.9", currency=""),
            SimpleNamespace(tag="BuyingPower", value="nan", currency="USD"),
        ]

    def positions(self, account):
        c = SimpleNamespace(
            symbol="VWRA", secType="STK", currency="USD", primaryExchange="LSEETF", exchange=""
        )
        return [SimpleNamespace(account=account, contract=c, position=3, avgCost=150.2)]


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_is_paper_account():
    assert is_paper_account("DU1234567")
    assert is_paper_account(" du999 ")
    assert not is_paper_account("U1234567")
    assert not is_paper_account("F1234567")


def test_check_accounts_refuses_live_by_default():
    with pytest.raises(LiveAccountRefusedError):
        check_accounts(["U1234567"], "", allow_live_readonly=False)
    assert check_accounts(["U1234567"], "", allow_live_readonly=True) == "U1234567"


def test_check_accounts_wanted_must_exist():
    with pytest.raises(BrokerConnectionError):
        check_accounts(["DU1"], "DU2", allow_live_readonly=False)
    with pytest.raises(BrokerConnectionError):
        check_accounts([], "", allow_live_readonly=False)


def test_connect_is_readonly_and_snapshot():
    fake = FakeIB()
    with IbkrReadOnly(_settings(), ib_factory=lambda: fake) as broker:
        assert fake.connect_kwargs["readonly"] is True
        assert fake.connect_kwargs["port"] == 4002
        snap = broker.snapshot()
    assert not fake.connected
    assert snap.is_paper
    assert snap.value("NetLiquidation") == 1000.5
    assert snap.value("BuyingPower") is None  # nan -> None
    assert snap.value("Cushion") is None  # no pedido
    assert snap.positions[0].symbol == "VWRA"
    assert snap.positions[0].exchange == "LSEETF"


def test_live_account_disconnects_and_raises():
    fake = FakeIB(accounts=["U7654321"])
    with pytest.raises(LiveAccountRefusedError):
        IbkrReadOnly(_settings(), ib_factory=lambda: fake).connect()
    assert not fake.connected


def test_connection_error_message():
    fake = FakeIB(fail_connect=True)
    with pytest.raises(BrokerConnectionError, match="4002"):
        IbkrReadOnly(_settings(), ib_factory=lambda: fake).connect()


def test_module_has_no_order_capability():
    """Regla no negociable: en Fase 1 ningún módulo del broker puede enviar órdenes."""
    forbidden = {
        "placeOrder",
        "cancelOrder",
        "reqGlobalCancel",
        "MarketOrder",
        "LimitOrder",
        "StopOrder",
        "bracketOrder",
        "whatIfOrder",
    }
    broker_dir = Path(ibkr.__file__).parent
    for py in broker_dir.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not (names & forbidden), f"{py.name} usa {names & forbidden}"
