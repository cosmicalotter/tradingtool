"""Datos de la rotación (FRED, historia completa de ETFs, panel), señal mensual y limitador."""

from __future__ import annotations

import json
from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest

from etf_synth import random_prices
from tradingtool.config import EtfConfig
from tradingtool.etf.data import (
    CASH,
    ETF_TABLE,
    cash_bars,
    data_warnings,
    fetch_fred_csv,
    load_panel,
    parse_fred_csv,
    refresh_cash,
    refresh_etfs,
    tbill_index,
)
from tradingtool.etf.live import (
    changes,
    compute_signal,
    previous_recommendation,
    record_recommendation,
    save_verdict,
    stored_verdict,
)
from tradingtool.prices.base import PriceSourceError, store_bars
from tradingtool.prices.sources import BurstRateLimiter

FRED_TEXT = "observation_date,DTB3\n2024-01-02,5.00\n2024-01-03,.\n2024-01-04,5.10\n"


# ----------------------------------------------------------------------------- FRED


def test_parse_fred_csv_skips_missing_days():
    s = parse_fred_csv(FRED_TEXT)
    assert list(s.index) == [date(2024, 1, 2), date(2024, 1, 4)]
    assert s.iloc[-1] == 5.10
    with pytest.raises(PriceSourceError):
        parse_fred_csv("<html>error</html>")


def test_tbill_index_accrues_actual_360_minus_fund_cost():
    rates = pd.Series({date(2024, 1, 1): 3.6, date(2024, 12, 26): 3.6})  # 360 días
    idx = tbill_index(rates, expense=0.0007)
    assert idx.iloc[0] == 1.0
    assert idx.iloc[1] == pytest.approx(1 + 0.036 - 0.0007 * 360 / 365)


def test_fetch_fred_retries_server_errors():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, text=FRED_TEXT)

    text = fetch_fred_csv(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert text == FRED_TEXT and len(calls) == 2
    assert "tradingtool" in calls[0].headers["User-Agent"]


def test_refresh_cash_stores_index(con):
    n = refresh_cash(
        con,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=FRED_TEXT)),
        sleep=lambda s: None,
    )
    assert n == 2
    rows = con.execute(f"SELECT close, source FROM {ETF_TABLE} WHERE ticker = 'CASH'").fetchall()
    assert rows[0] == (1.0, "fred") and rows[1][0] > 1.0


# ----------------------------------------------------------------------------- ETFs


class _FakeSource:
    name = "fake"
    adjusted = True

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames

    def daily_bars(self, ticker, start, end):
        return self.frames.get(ticker, pd.DataFrame())


def _bars(ticker: str, dates, closes) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ticker,
            "date": list(dates),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 1.0,
        }
    )


def test_refresh_replaces_the_whole_history(con):
    old = _bars("SPY", pd.bdate_range("2020-01-01", periods=3).date, [1.0, 2.0, 3.0])
    store_bars(con, old, "viejo", True, table=ETF_TABLE)
    new = _bars("SPY", pd.bdate_range("2020-01-02", periods=2).date, [9.0, 9.5])
    st = refresh_etfs(
        con, _FakeSource({"SPY": new}), ["SPY", "EFA"], date(2000, 1, 1), date.today()
    )
    assert st.rows == {"SPY": 2}
    assert st.errors and "EFA" in st.errors[0]
    rows = con.execute(f"SELECT date, close, source FROM {ETF_TABLE} ORDER BY date").fetchall()
    assert [r[1] for r in rows] == [9.0, 9.5]  # el 2020-01-01 viejo ya no está
    assert {r[2] for r in rows} == {"fake"}
    # la caché de acciones no se toca
    assert con.execute("SELECT count(*) FROM prices_daily").fetchone()[0] == 0


def test_refresh_rejects_sources_without_dividend_adjustment(con):
    src = _FakeSource({})
    src.adjusted = False
    with pytest.raises(PriceSourceError):
        refresh_etfs(con, src, ["SPY"], date(2000, 1, 1), date.today())


def test_load_panel_uses_spy_calendar_and_forward_fills(con):
    days = pd.bdate_range("2024-03-25", "2024-04-05")
    spy_days = [d for d in days if d != pd.Timestamp("2024-03-29")]  # Viernes Santo
    store_bars(
        con,
        _bars("SPY", [d.date() for d in spy_days], list(range(1, len(spy_days) + 1))),
        "x",
        True,
        table=ETF_TABLE,
    )
    efa_days = [d for d in spy_days if d != pd.Timestamp("2024-04-02")]  # hueco de 1 día
    store_bars(
        con,
        _bars("EFA", [d.date() for d in efa_days], [10.0] * len(efa_days)),
        "x",
        True,
        table=ETF_TABLE,
    )
    store_bars(
        con,
        cash_bars(pd.Series({d.date(): 1.0 + i / 100 for i, d in enumerate(days)})),
        "fred",
        True,
        table=ETF_TABLE,
    )
    p = load_panel(con, ["SPY", "EFA", "CASH"])
    assert pd.Timestamp("2024-03-29") not in p.index  # solo días en que cotizó SPY
    assert p.loc["2024-04-02", "EFA"] == 10.0  # relleno con el último precio
    assert p.loc["2024-04-01", "CASH"] == pytest.approx(1.05)  # incluye el dato del 29-mar
    cut = load_panel(con, ["SPY"], end=date(2024, 3, 28))
    assert cut.index[-1] == pd.Timestamp("2024-03-28")


def test_data_warnings_flag_missing_tickers_and_long_gaps(con):
    days = pd.bdate_range("2024-01-01", "2024-03-29")
    store_bars(con, _bars("SPY", days.date, [1.0] * len(days)), "x", True, table=ETF_TABLE)
    gap = days[(days < "2024-02-01") | (days > "2024-02-20")]
    store_bars(con, _bars("EFA", gap.date, [1.0] * len(gap)), "x", True, table=ETF_TABLE)
    w = data_warnings(con, ["SPY", "EFA", "EEM"])
    assert any(x.startswith("EEM: sin datos") for x in w)
    assert any(x.startswith("EFA:") and "racha" in x for x in w)


# ----------------------------------------------------------------------------- señal mensual


def test_signal_uses_last_complete_month_and_flags_stale_data():
    prices = random_prices(end="2026-10-07")
    sig = compute_signal(prices, EtfConfig(), date(2026, 10, 8))
    assert sig.as_of == date(2026, 9, 30)
    assert sig.execute_from == date(2026, 10, 1)
    assert not sig.stale
    assert sum(sig.weights.values()) == pytest.approx(1.0)
    assert len(sig.picks) == 4
    # Un mes después sin actualizar: falta el cierre de octubre
    late = compute_signal(prices, EtfConfig(), date(2026, 11, 3))
    assert late.stale and "octubre" in late.stale_reason
    # Datos cortados a mitad de mes: el mes en curso nunca se usa
    mid = compute_signal(prices[prices.index <= "2026-09-15"], EtfConfig(), date(2026, 9, 16))
    assert mid.as_of == date(2026, 8, 31)


def test_recommendations_are_frozen_once_saved(con):
    cfg = EtfConfig()
    prices = random_prices(end="2026-10-07")
    sig = compute_signal(prices, cfg, date(2026, 10, 8))
    assert record_recommendation(con, sig, cfg) is None  # primera vez: se guarda
    altered = sig.__class__(**{**sig.__dict__, "weights": {"CASH": 1.0}})
    assert record_recommendation(con, altered, cfg) == sig.weights  # se conserva la primera
    assert con.execute("SELECT count(*) FROM etf_recommendations").fetchone()[0] == 1
    older = compute_signal(prices, cfg, date(2026, 9, 2))
    record_recommendation(con, older, cfg)
    prev = previous_recommendation(con, cfg, sig.as_of)
    assert prev is not None and prev[0] == date(2026, 8, 31)


def test_changes_lists_only_material_moves():
    assert changes({"SPY": 0.75, "IEF": 0.25}, {"SPY": 0.75, "IEF": 0.25}) == []
    assert changes({"SPY": 1.0}, {"SPY": 0.5, "EFA": 0.5}) == [("EFA", 0.0, 0.5), ("SPY", 1.0, 0.5)]


def test_verdict_roundtrip(con):
    assert stored_verdict(con) is None
    save_verdict(con, {"veredicto": "NO PASA", "reglas": "abc"})
    save_verdict(con, {"veredicto": "PASA", "reglas": "abc"})
    assert stored_verdict(con)["veredicto"] == "PASA"
    raw = con.execute("SELECT value FROM meta WHERE key = 'etf_veredicto'").fetchone()[0]
    assert json.loads(raw)["reglas"] == "abc"


# ----------------------------------------------------------------------------- limitador


def test_burst_limiter_allows_bursts_then_waits_for_the_window():
    now = [0.0]
    waits: list[float] = []

    def sleep(s: float) -> None:
        waits.append(s)
        now[0] += s

    lim = BurstRateLimiter(3, 3600.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        lim.wait()
    assert waits == []  # ráfaga permitida
    now[0] = 100.0
    lim.wait()
    assert waits == [pytest.approx(3500.0)]  # espera a que se libere el cupo


def test_tiingo_rate_limit_message():
    from tradingtool.prices.sources import TiingoSource

    src = TiingoSource(
        "K", transport=httpx.MockTransport(lambda r: httpx.Response(429)), sleep=lambda s: None
    )
    with pytest.raises(PriceSourceError, match="429"):
        src.daily_bars("SPY", date(2024, 1, 1), date(2024, 1, 31))


def test_cash_bars_format():
    df = cash_bars(pd.Series({date(2024, 1, 2): 1.0, date(2024, 1, 3): 1.0002}))
    assert list(df["ticker"].unique()) == [CASH]
    assert np.allclose(df["open"], df["close"])
