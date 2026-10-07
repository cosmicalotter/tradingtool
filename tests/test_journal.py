from datetime import date, timedelta

import pandas as pd
import pytest

from tradingtool.journal import journal as j
from tradingtool.models import Signal


def _signal(sid="s1", ticker="ACME", as_of=date(2026, 1, 5), passed=True):
    return Signal(
        signal_id=sid,
        strategy_version="insider-v1",
        config_hash="abc",
        as_of_date=as_of,
        ticker=ticker,
        issuer_cik="123",
        issuer_name="Acme",
        score=1.5,
        passed=passed,
        reasons=("compra de director",),
        features={"value": 50000},
        accessions=("a-1",),
    )


def _bdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _insert_prices(con, ticker, dates, start_price=10.0, step=0.1):
    rows = []
    for i, d in enumerate(dates):
        p = start_price + i * step
        rows.append(
            {
                "ticker": ticker,
                "date": d,
                "open": p,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p + 0.05,
                "volume": 1e5,
                "source": "test",
                "adjusted": True,
            }
        )
    df = pd.DataFrame(rows)
    con.register("tmp_p", df)
    con.execute(
        "INSERT INTO prices_daily SELECT ticker, date, open, high, low, close, volume, "
        "source, adjusted FROM tmp_p"
    )
    con.unregister("tmp_p")


def test_run_lifecycle(con):
    rid = j.start_run(con, "screen", config_hash="h", params={"x": 1})
    j.finish_run(con, rid, "ok", "nota")
    row = con.execute("select kind, status, notes, config_hash from runs").fetchone()
    assert row == ("screen", "ok", "nota", "h")


def test_record_signals_upsert_keeps_decisions(con):
    j.record_signals(con, "r1", [_signal()])
    j.record_decision(con, "s1", "approve", "me gusta")
    j.record_signals(con, "r2", [_signal()])  # re-ejecución
    df = j.list_signals(con)
    assert len(df) == 1
    assert df.loc[0, "decision"] == "approve"
    assert df.loc[0, "run_id"] == "r2"


def test_decision_validation(con):
    j.record_signals(con, None, [_signal()])
    with pytest.raises(ValueError):
        j.record_decision(con, "s1", "buy_now")
    with pytest.raises(KeyError):
        j.record_decision(con, "nope", "approve")


def test_latest_decision_wins(con):
    j.record_signals(con, None, [_signal()])
    j.record_decision(con, "s1", "approve")
    j.record_decision(con, "s1", "reject", "cambié de opinión")
    assert j.list_signals(con).loc[0, "decision"] == "reject"


def test_outcomes_entry_is_next_open_and_no_lookahead(con):
    dates = _bdays(date(2026, 1, 1), 40)
    _insert_prices(con, "ACME", dates)
    _insert_prices(con, "SPY", dates, start_price=100.0, step=0.0)
    as_of = dates[3]
    j.record_signals(con, None, [_signal(as_of=as_of)])
    n = j.update_outcomes(con, horizons=(5,), benchmark_ticker="SPY")
    assert n == 1
    o = con.execute("select * from outcomes").df().iloc[0]
    assert pd.Timestamp(o["entry_date"]).date() == dates[4]  # día hábil siguiente
    assert o["entry_price"] == pytest.approx(10.0 + 4 * 0.1)  # apertura
    assert pd.Timestamp(o["exit_date"]).date() == dates[8]  # 5º día contando la entrada
    assert o["exit_price"] == pytest.approx(10.0 + 8 * 0.1 + 0.05)
    assert o["status"] == "complete"
    assert o["bench_ret"] == pytest.approx(100.05 / 100.0 - 1)
    # idempotente
    assert j.update_outcomes(con, horizons=(5,), benchmark_ticker="SPY") == 0


def test_outcome_pending_when_horizon_not_reached(con):
    dates = _bdays(date(2026, 1, 1), 6)
    _insert_prices(con, "ACME", dates)
    j.record_signals(con, None, [_signal(as_of=dates[2])])
    assert j.update_outcomes(con, horizons=(5,), benchmark_ticker=None) == 0


def test_outcome_truncated_when_ticker_stops_trading(con):
    dates = _bdays(date(2026, 1, 1), 40)
    _insert_prices(con, "ACME", dates[:8])  # deja de cotizar
    _insert_prices(con, "SPY", dates, start_price=100.0, step=0.0)
    j.record_signals(con, None, [_signal(as_of=dates[2])])
    assert j.update_outcomes(con, horizons=(21,), benchmark_ticker="SPY") == 1
    o = con.execute("select status, bars_held from outcomes").fetchone()
    assert o == ("truncated", 5)


def test_outcome_summary_groups(con):
    dates = _bdays(date(2026, 1, 1), 40)
    _insert_prices(con, "ACME", dates)
    _insert_prices(con, "SPY", dates, start_price=100.0, step=0.0)
    j.record_signals(
        con,
        None,
        [
            _signal("a", as_of=dates[1]),
            _signal("b", as_of=dates[2]),
            _signal("c", as_of=dates[3], passed=False),
            _signal("d", as_of=dates[4]),
        ],
    )
    j.record_decision(con, "a", "approve")
    j.record_decision(con, "b", "reject")
    j.update_outcomes(con, horizons=(5,), benchmark_ticker="SPY")
    s = j.outcome_summary(con, 5).set_index("grupo")
    assert set(s.index) == {"aprobada", "rechazada", "bloqueada", "sin decisión"}
    assert (s["n"] == 1).all()


def test_no_outcome_when_first_bar_is_far_after_signal(con):
    dates = _bdays(date(2026, 3, 1), 40)
    _insert_prices(con, "ACME", dates)
    # señal en enero, pero la primera barra es de marzo: entrada falsa -> no se mide
    j.record_signals(con, None, [_signal(as_of=date(2026, 1, 15))])
    assert j.update_outcomes(con, horizons=(5,), benchmark_ticker=None) == 0


def test_secondary_benchmark(con):
    dates = _bdays(date(2026, 1, 1), 40)
    _insert_prices(con, "ACME", dates)
    _insert_prices(con, "SPY", dates, start_price=100.0, step=0.0)
    _insert_prices(con, "IWM", dates, start_price=50.0, step=0.5)
    j.record_signals(con, None, [_signal(as_of=dates[3])])
    j.update_outcomes(con, horizons=(5,), benchmark_ticker="SPY", secondary_ticker="IWM")
    o = con.execute("select ret, bench2_ret, excess2_ret from outcomes").fetchone()
    # IWM: apertura día 4 = 52.0 ; cierre día 8 = 54.05
    assert o[1] == pytest.approx(54.05 / 52.0 - 1)
    assert o[2] == pytest.approx(o[0] - o[1])
