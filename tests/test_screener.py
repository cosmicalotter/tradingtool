"""Tests del screener insider-v1 y de la clasificación rutinario/oportunista."""

from __future__ import annotations

from datetime import date, timedelta
from itertools import count

import pandas as pd
import pytest

from tradingtool.config import AppConfig, ClusterCfg, ScreenerConfig, TransactionCfg
from tradingtool.insiders.classify import (
    OPPORTUNISTIC,
    ROUTINE,
    UNCLASSIFIED,
    classify_from_trades,
    classify_insiders,
)
from tradingtool.insiders.screener import candidate_tickers, flag, is_common_stock, screen
from tradingtool.insiders.store import upsert_filings
from tradingtool.models import Form4Filing, InsiderTransaction, ReportingOwner

_acc = count(1)


def _filing(
    *,
    issuer="0000000100",
    ticker="ACME",
    filing_date=date(2026, 3, 10),
    tx_date=None,
    owner="0000000900",
    owner_name="Jane Doe",
    director=True,
    officer=False,
    ten_pct=False,
    title=None,
    code="P",
    shares=1000.0,
    price=20.0,
    owned_after=11000.0,
    aff=False,
    mentions=False,
    form_type="4",
    security="Common Stock",
    extra_tx=(),
):
    tx_date = tx_date or filing_date - timedelta(days=2)
    txs = [
        InsiderTransaction(
            0,
            False,
            security,
            tx_date,
            code,
            shares,
            price,
            "A" if code == "P" else "D",
            owned_after,
            "D",
        )
    ]
    for i, t in enumerate(extra_tx, 1):
        txs.append(
            InsiderTransaction(
                i,
                False,
                security,
                t[0],
                t[1],
                100.0,
                price,
                "A" if t[1] == "P" else "D",
                owned_after,
                "D",
            )
        )
    return Form4Filing(
        accession=f"0000000001-26-{next(_acc):06d}",
        source="fixture",
        form_type=form_type,
        filing_date=filing_date,
        acceptance_ts=None,
        period_of_report=tx_date,
        issuer_cik=issuer,
        issuer_name="Acme Corp",
        issuer_ticker=ticker,
        aff10b5one=aff,
        owners=(
            ReportingOwner(
                owner,
                owner_name,
                is_director=director,
                is_officer=officer,
                is_ten_pct_owner=ten_pct,
                officer_title=title,
            ),
        ),
        transactions=tuple(txs),
        mentions_10b5_1=mentions,
    )


def _prices(con, ticker="ACME", end=date(2026, 3, 31), n=60, close=20.0, volume=200_000):
    rows, d = [], end
    while len(rows) < n:
        if d.weekday() < 5:
            rows.append(
                {
                    "ticker": ticker,
                    "date": d,
                    "open": close,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "close": close,
                    "volume": volume,
                    "source": "test",
                    "adjusted": True,
                }
            )
        d -= timedelta(days=1)
    df = pd.DataFrame(rows)
    con.register("tmp_px", df)
    con.execute(
        "INSERT OR REPLACE INTO prices_daily SELECT ticker, date, open, high, low, close, "
        "volume, source, adjusted FROM tmp_px"
    )
    con.unregister("tmp_px")


def _one(con, cfg=None, day=date(2026, 3, 10)):
    sigs = screen(con, day, day, cfg or AppConfig())
    assert len(sigs) == 1, sigs
    return sigs[0]


# --------------------------------------------------------------------------- utilidades


def test_flag_handles_numpy_and_na():
    import numpy as np

    assert flag(np.True_) is True
    assert flag(np.False_) is False
    assert flag(pd.NA) is None
    assert flag(None) is None
    assert flag(float("nan")) is None


def test_is_common_stock():
    assert is_common_stock("Common Stock")
    assert is_common_stock("Class A Common Stock")
    assert is_common_stock("Ordinary Shares")
    assert not is_common_stock("Series A Preferred Stock")
    assert not is_common_stock("Warrants to purchase Common Stock")
    assert not is_common_stock("Common Units")
    assert not is_common_stock("American Depositary Shares")
    assert not is_common_stock(None)


def test_classify_from_trades():
    assert classify_from_trades([(2023, 5), (2024, 5), (2025, 5)], 2026) == ROUTINE
    assert classify_from_trades([(2023, 5), (2024, 6), (2025, 5)], 2026) == OPPORTUNISTIC
    assert classify_from_trades([(2024, 5), (2025, 5)], 2026) == UNCLASSIFIED
    assert classify_from_trades([(2019, 5), (2024, 5), (2025, 5)], 2026) == UNCLASSIFIED


# --------------------------------------------------------------------------- caso base


def test_valid_director_purchase_passes(con):
    upsert_filings(con, [_filing()])
    _prices(con)
    s = _one(con)
    assert s.passed, s.reasons
    assert s.ticker == "ACME" and s.as_of_date == date(2026, 3, 10)
    f = s.features
    assert f["n_insiders"] == 1 and f["total_value"] == pytest.approx(20_000)
    assert f["pct_increase_max"] == pytest.approx(1000 / 10000)
    assert f["adv20"] == pytest.approx(20.0 * 200_000)
    assert f["price_last_date"] <= "2026-03-10"
    assert f["insiders"][0]["clase"] == UNCLASSIFIED
    assert s.score > 0
    assert any("compraron" in r for r in s.reasons)
    # determinista
    assert (
        screen(con, date(2026, 3, 10), date(2026, 3, 10), AppConfig())[0].signal_id == s.signal_id
    )


def test_non_purchase_codes_do_not_create_signals(con):
    upsert_filings(con, [_filing(code="S")])
    assert screen(con, date(2026, 3, 10), date(2026, 3, 10), AppConfig()) == []


# --------------------------------------------------------------------------- bloqueos


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"aff": True}, "10b5-1"),
        ({"aff": None, "mentions": True}, "nota al pie"),
        ({"director": False, "ten_pct": True}, "Rol del insider"),
        ({"shares": 100.0, "price": 20.0}, "menor al mínimo"),
        ({"form_type": "4/A"}, "enmienda"),
        ({"tx_date": date(2026, 2, 1)}, "días después"),
        ({"security": "Series B Preferred Stock"}, "No es acción común"),
        ({"price": 1.0, "shares": 50_000.0}, "Precio pagado"),
        ({"ticker": None}, "ticker"),
    ],
)
def test_blocking_rules(con, kwargs, fragment):
    upsert_filings(con, [_filing(**kwargs)])
    _prices(con)
    s = _one(con)
    assert not s.passed
    assert any(fragment in r for r in s.reasons), s.reasons


def test_10b5_1_false_with_mention_still_passes(con):
    # la casilla explícita (False) manda sobre la mención en notas
    upsert_filings(con, [_filing(aff=False, mentions=True)])
    _prices(con)
    assert _one(con).passed


def test_liquidity_and_missing_prices(con):
    upsert_filings(con, [_filing()])
    s = _one(con)
    assert not s.passed and any("Sin datos de precio" in r for r in s.reasons)
    _prices(con, volume=1_000)  # 20 USD x 1000 = 20 mil/día
    s = _one(con)
    assert not s.passed and any("Liquidez baja" in r for r in s.reasons)


# --------------------------------------------------------------------------- cluster


def test_cluster_counts_distinct_insiders_in_window(con):
    upsert_filings(
        con,
        [
            _filing(filing_date=date(2026, 3, 2), owner="0000000901"),
            _filing(filing_date=date(2026, 3, 10), owner="0000000902", officer=True, title="CFO"),
        ],
    )
    _prices(con)
    cfg = AppConfig(screener=ScreenerConfig(cluster=ClusterCfg(window_days=30, min_insiders=2)))
    sigs = {s.as_of_date: s for s in screen(con, date(2026, 3, 1), date(2026, 3, 10), cfg)}
    assert not sigs[date(2026, 3, 2)].passed  # solo 1 insider hasta esa fecha
    late = sigs[date(2026, 3, 10)]
    assert late.passed and late.features["n_insiders_window"] == 2
    assert late.features["any_officer"]


# --------------------------------------------------------------------------- clasificación


def _history(owner, months, issuer="0000000100"):
    """Operaciones previas del insider (presentadas a tiempo) en los meses dados."""
    out = []
    for y, m in months:
        d = date(y, m, 15)
        out.append(
            _filing(
                issuer=issuer, owner=owner, filing_date=d + timedelta(days=2), tx_date=d, code="S"
            )
        )
    return out


def test_routine_insider_is_blocked_and_opportunistic_counted(con):
    upsert_filings(con, _history("0000000901", [(2023, 3), (2024, 3), (2025, 3)]))
    upsert_filings(con, _history("0000000902", [(2023, 3), (2024, 7), (2025, 11)]))
    upsert_filings(
        con,
        [
            _filing(filing_date=date(2026, 3, 10), owner="0000000901"),
            _filing(filing_date=date(2026, 3, 10), owner="0000000902"),
        ],
    )
    _prices(con)
    s = _one(con)
    ins = {i["cik"]: i for i in s.features["insiders"]}
    assert ins["0000000901"]["clase"] == ROUTINE and not ins["0000000901"]["valida"]
    assert ins["0000000902"]["clase"] == OPPORTUNISTIC and ins["0000000902"]["valida"]
    assert s.passed and s.features["opportunistic_count"] == 1
    assert s.features["routine_count"] == 1


def test_opportunistic_only_mode_blocks_unclassified(con):
    upsert_filings(con, [_filing()])
    _prices(con)
    cfg = AppConfig(
        screener=ScreenerConfig(classification={"mode": "opportunistic_only", "lookback_years": 3})
    )
    s = _one(con, cfg)
    assert not s.passed and any("sin historia" in r for r in s.reasons)


def test_classification_ignores_filings_after_start_of_year(con):
    # Historia "rutinaria", pero el filing de 2025 se presentó tarde (en 2026): no es pública
    # al inicio del año del evento, así que el insider queda no clasificable.
    hist = _history("0000000901", [(2023, 3), (2024, 3)])
    hist.append(
        _filing(
            owner="0000000901", filing_date=date(2026, 1, 5), tx_date=date(2025, 3, 15), code="S"
        )
    )
    upsert_filings(con, hist)
    got = classify_insiders(con, [("0000000901", "0000000100")], date(2026, 3, 10))
    assert got[("0000000901", "0000000100")] == UNCLASSIFIED


# --------------------------------------------------------------------------- sin mirar al futuro


def test_price_features_ignore_future_bars(con):
    upsert_filings(con, [_filing()])
    _prices(con, end=date(2026, 3, 10), volume=200_000)
    future = pd.DataFrame(
        [
            {
                "ticker": "ACME",
                "date": date(2026, 3, 11) + timedelta(days=i),
                "open": 99.0,
                "high": 99.0,
                "low": 99.0,
                "close": 99.0,
                "volume": 9e9,
                "source": "t",
                "adjusted": True,
            }
            for i in range(5)
        ]
    )
    con.register("fut", future)
    con.execute("INSERT INTO prices_daily SELECT * FROM fut")
    s = _one(con)
    assert s.features["price_last"] == pytest.approx(20.0)
    assert s.features["adv20"] == pytest.approx(20.0 * 200_000)


def test_max_filing_lag_configurable(con):
    upsert_filings(con, [_filing(tx_date=date(2026, 2, 20))])  # 18 días
    _prices(con)
    cfg = AppConfig(screener=ScreenerConfig(transactions=TransactionCfg(max_filing_lag_days=30)))
    assert _one(con, cfg).passed
    assert not _one(con).passed  # por defecto 10


def test_candidate_tickers(con):
    upsert_filings(con, [_filing(), _filing(issuer="0000000200", ticker="BETA", code="S")])
    assert candidate_tickers(con, date(2026, 3, 1), date(2026, 3, 31)) == ["ACME"]
