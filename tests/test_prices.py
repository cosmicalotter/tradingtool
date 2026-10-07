from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd
import pytest

from tradingtool.prices.base import last_dates, load_bars, normalize_bars, store_bars
from tradingtool.prices.quality import check_bars, summarize
from tradingtool.prices.sources import (
    CsvSource,
    MassiveSource,
    PriceSourceError,
    TiingoSource,
    _ms_to_ny_date,
)
from tradingtool.prices.sync import (
    business_days,
    market_days_missing,
    repair_split_jumps,
    sync_market,
    sync_tickers,
)


def _df(ticker="AAA", start=date(2026, 1, 5), n=5, close=10.0, step=0.0):
    rows = []
    for d in business_days(start, start + timedelta(days=3 * n))[:n]:
        rows.append(
            {
                "ticker": ticker,
                "date": d,
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 1000,
            }
        )
        close += step
    return pd.DataFrame(rows)


def _ms(d: date) -> int:
    # Massive marca las barras diarias a medianoche de Nueva York (~04:00/05:00 UTC)
    return int(datetime(d.year, d.month, d.day, 5, 0, tzinfo=UTC).timestamp() * 1000)


# --------------------------------------------------------------------------- base


def test_normalize_and_store_upsert(con):
    df = _df()
    df.loc[0, "ticker"] = " aaa "
    assert store_bars(con, df, "test", True) == 5
    df2 = _df(close=11.0)
    store_bars(con, df2, "test2", True)
    out = load_bars(con, "AAA")
    assert len(out) == 5 and out["close"].eq(11.0).all()
    assert last_dates(con, ["AAA", "ZZZ"]) == {"AAA": out["date"].max()}
    with pytest.raises(PriceSourceError):
        normalize_bars(pd.DataFrame({"ticker": ["A"]}))
    assert normalize_bars(pd.DataFrame()).empty


def test_business_days():
    d = business_days(date(2026, 10, 2), date(2026, 10, 6))  # vie..mar
    assert d == [date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)]
    assert business_days(date(2026, 10, 6), date(2026, 10, 2)) == []


# --------------------------------------------------------------------------- calidad


def test_quality_detects_problems():
    df = _df(n=12)
    df.loc[2, "low"] = df.loc[2, "high"] + 1  # rango invertido (+cierre fuera)
    df.loc[4, "close"] = -1  # precio no positivo
    df.loc[7, "close"] = df.loc[6, "close"] * 3  # salto extremo
    df.loc[7, "high"] = df.loc[7, "close"]
    gap = df.iloc[[0]].copy()
    gap["date"] = df["date"].max() + timedelta(days=30)
    df = pd.concat([df, gap])
    kinds = summarize(check_bars(df))
    assert kinds["error:rango_invertido"] == 1
    assert kinds["error:precio_no_positivo"] == 1
    assert kinds.get("warning:salto_extremo", 0) >= 1
    assert kinds["warning:hueco"] == 1
    assert check_bars(pd.DataFrame()) == []
    assert check_bars(_df(n=10)) == []


# --------------------------------------------------------------------------- Massive


def test_ms_to_ny_date():
    assert _ms_to_ny_date(_ms(date(2026, 3, 10))) == date(2026, 3, 10)


def test_massive_grouped_and_range():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "/grouped/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "results": [
                        {
                            "T": "AAPL",
                            "o": 1,
                            "h": 2,
                            "l": 0.5,
                            "c": 1.5,
                            "v": 100,
                            "t": _ms(date(2026, 3, 10)),
                        },
                        {
                            "T": "MSFT",
                            "o": 3,
                            "h": 4,
                            "l": 2.5,
                            "c": 3.5,
                            "v": 200,
                            "t": _ms(date(2026, 3, 10)),
                        },
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "results": [
                    {"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100, "t": _ms(date(2026, 3, 9))},
                    {"o": 1, "h": 2, "l": 0.5, "c": 1.6, "v": 100, "t": _ms(date(2026, 3, 10))},
                ],
            },
        )

    src = MassiveSource("KEY", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    day = src.market_day(date(2026, 3, 10))
    assert list(day["ticker"]) == ["AAPL", "MSFT"] and day["date"].eq(date(2026, 3, 10)).all()
    bars = src.daily_bars("aapl", date(2026, 3, 9), date(2026, 3, 10))
    assert list(bars["close"]) == [1.5, 1.6] and bars["ticker"].eq("AAPL").all()
    assert seen[0].headers["Authorization"] == "Bearer KEY"
    assert "KEY" not in str(seen[0].url)  # la clave nunca va en la URL
    assert seen[0].url.params["adjusted"] == "true"


def test_massive_errors():
    with pytest.raises(PriceSourceError):
        MassiveSource("")
    src = MassiveSource(
        "K", transport=httpx.MockTransport(lambda r: httpx.Response(403)), sleep=lambda s: None
    )
    with pytest.raises(PriceSourceError, match="Clave"):
        src.market_day(date(2026, 3, 10))
    calls = []

    def flaky(request):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 2 else httpx.Response(200, json={"results": []})

    src2 = MassiveSource("K", transport=httpx.MockTransport(flaky), sleep=lambda s: None)
    assert src2.market_day(date(2026, 3, 10)).empty and len(calls) == 2


# --------------------------------------------------------------------------- Tiingo y CSV


def test_tiingo_uses_adjusted_fields_and_dash_symbols():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "date": "2026-03-10T00:00:00.000Z",
                    "open": 100,
                    "high": 110,
                    "low": 90,
                    "close": 105,
                    "volume": 10,
                    "adjOpen": 50,
                    "adjHigh": 55,
                    "adjLow": 45,
                    "adjClose": 52.5,
                    "adjVolume": 20,
                },
            ],
        )

    src = TiingoSource("TOK", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    df = src.daily_bars("BRK.B", date(2026, 3, 1), date(2026, 3, 10))
    assert "/BRK-B/" in seen[0].url.path
    assert seen[0].headers["Authorization"] == "Token TOK"
    assert df.iloc[0]["close"] == 52.5 and df.iloc[0]["volume"] == 20
    assert df.iloc[0]["ticker"] == "BRK.B"


def test_csv_source(tmp_path):
    _df("XYZ", n=5).drop(columns=["ticker"]).to_csv(tmp_path / "XYZ.csv", index=False)
    src = CsvSource(tmp_path)
    df = src.daily_bars("xyz", date(2026, 1, 6), date(2026, 1, 8))
    assert len(df) == 3
    assert src.daily_bars("NOPE", date(2026, 1, 1), date(2026, 1, 9)).empty


# --------------------------------------------------------------------------- sync


class FakeSource:
    name = "fake"
    adjusted = True

    def __init__(self, data: pd.DataFrame):
        self.data = data
        self.calls = []

    def daily_bars(self, ticker, start, end):
        self.calls.append((ticker, start, end))
        d = self.data[
            (self.data["ticker"] == ticker)
            & (self.data["date"] >= start)
            & (self.data["date"] <= end)
        ]
        return d.reset_index(drop=True)

    def market_day(self, day):
        self.calls.append(("*", day))
        return self.data[self.data["date"] == day].reset_index(drop=True)


def test_sync_tickers_is_incremental(con):
    data = pd.concat([_df("AAA", n=10), _df("BBB", n=10)])
    src = FakeSource(data)
    s1 = sync_tickers(con, src, ["AAA", "BBB"], date(2026, 1, 1), date(2026, 1, 9))
    assert s1.tickers_updated == 2
    src.calls.clear()
    sync_tickers(con, src, ["AAA"], date(2026, 1, 1), date(2026, 1, 20))
    assert src.calls[0][1] > date(2026, 1, 9)  # solo pide lo que falta


def test_sync_market_fetches_only_missing_days(con):
    days = business_days(date(2026, 1, 5), date(2026, 1, 9))
    rows = [
        {"ticker": f"T{i}", "date": d, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        for d in days
        for i in range(600)
    ]
    src = FakeSource(pd.DataFrame(rows))
    assert market_days_missing(con, days[0], days[-1]) == days
    st = sync_market(con, src, days[0], days[-1], max_days=3)
    assert st.days_fetched == 3
    assert market_days_missing(con, days[0], days[-1]) == days[:2]


def test_repair_split_jumps(con):
    hist = _df("SPL", start=date(2026, 1, 5), n=20, close=100.0)
    # la caché tiene la historia vieja sin ajustar + barras nuevas post-split (salto -50%)
    stale = hist.copy()
    stale.loc[stale.index[-5:], ["open", "high", "low", "close"]] = [50, 51, 49, 50]
    store_bars(con, stale, "fake", True)
    adjusted = hist.copy()
    adjusted[["open", "high", "low", "close"]] = [50, 51, 49, 50]
    src = FakeSource(adjusted)
    repaired = repair_split_jumps(
        con, src, since=date(2026, 1, 20), history_start=date(2026, 1, 1), end=date(2026, 2, 28)
    )
    assert repaired == ["SPL"]
    assert load_bars(con, "SPL")["close"].eq(50).all()


def test_holidays_are_remembered(con):
    days = business_days(
        date(2025, 11, 26), date(2025, 11, 28)
    )  # mié, jue (Acción de Gracias), vie
    rows = [
        {"ticker": f"T{i}", "date": d, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        for d in days
        if d != date(2025, 11, 27)
        for i in range(600)
    ]
    src = FakeSource(pd.DataFrame(rows))
    sync_market(con, src, days[0], days[-1])
    assert market_days_missing(con, days[0], days[-1]) == []
    src.calls.clear()
    sync_market(con, src, days[0], days[-1])
    assert src.calls == []


def test_eodhd_adjusts_ohlc_and_uses_dash_symbols():
    from tradingtool.prices.sources import EodhdSource

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "date": "2015-06-01",
                    "open": 100,
                    "high": 110,
                    "low": 90,
                    "close": 100,
                    "adjusted_close": 50,
                    "volume": 1000,
                },
            ],
        )

    src = EodhdSource("TOK", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    df = src.daily_bars("BRK.B", date(2015, 1, 1), date(2015, 12, 31))
    assert seen[0].url.path == "/api/eod/BRK-B.US"
    assert seen[0].url.params["from"] == "2015-01-01"
    row = df.iloc[0]
    assert (row["open"], row["high"], row["low"], row["close"]) == (50, 55, 45, 50)
    assert row["volume"] == 2000  # volumen ajustado inversamente
    with pytest.raises(PriceSourceError):
        EodhdSource("")


def test_httpx_urls_are_not_logged(tmp_path):
    import logging

    from tradingtool.logging_setup import setup_logging

    setup_logging(tmp_path)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_recent_empty_day_is_not_marked_as_holiday(con):
    d = date.today() - timedelta(days=1)
    src = FakeSource(
        pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume"])
    )
    sync_market(con, src, d, d)
    assert (
        con.execute("select count(*) from meta where key like 'sin_mercado:%'").fetchone()[0] == 0
    )
