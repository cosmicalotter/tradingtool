"""Tests del panel: consultas, presentación, gráficos y la app Streamlit (AppTest).

Sin red: base DuckDB temporal con señales, precios, decisiones y resultados falsos.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tradingtool.config import AppConfig
from tradingtool.db import connect
from tradingtool.journal import journal as j
from tradingtool.models import Signal
from tradingtool.settings import Settings
from tradingtool.ui import charts, launch
from tradingtool.ui import presenters as p
from tradingtool.ui import queries as q

APP_PATH = Path(__file__).parents[1] / "src" / "tradingtool" / "ui" / "app.py"
ACC = "0001234567-26-000001"


# ------------------------------------------------------------------------------ datos falsos


def _bdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DATES = _bdays(date(2026, 1, 1), 40)


def _insert_prices(con, ticker, dates, start_price=10.0, step=0.1):
    df = pd.DataFrame(
        [
            {
                "ticker": ticker,
                "date": d,
                "open": start_price + i * step,
                "high": start_price + i * step + 0.5,
                "low": start_price + i * step - 0.5,
                "close": start_price + i * step + 0.05,
                "volume": 1e5,
                "source": "test",
                "adjusted": True,
            }
            for i, d in enumerate(dates)
        ]
    )
    con.register("tmp_p", df)
    con.execute(
        "INSERT INTO prices_daily SELECT ticker, date, open, high, low, close, volume, "
        "source, adjusted FROM tmp_p"
    )
    con.unregister("tmp_p")


def _signal(sid, ticker, as_of, passed=True, features=None, reasons=None):
    return Signal(
        signal_id=sid,
        strategy_version="insider-v1",
        config_hash="cfg123",
        as_of_date=as_of,
        ticker=ticker,
        issuer_cik="0000320193",
        issuer_name=f"{ticker} Corp",
        score=2.5 if passed else 0.4,
        passed=passed,
        reasons=tuple(reasons or ["Compra del CEO por $250,000"]),
        features=features if features is not None else {},
        accessions=(ACC,),
    )


FEATURES = {
    "n_insiders": 2,
    "total_value": 250000.0,
    "roles": ["director", "officer"],
    "price_last": 10.5,
    "atr14": 0.4,
    "adv20": 2_500_000.0,
    "algo_nuevo": {"x": 1},
}


def populate(con) -> None:
    rid = j.start_run(con, "screener", config_hash="cfg123")
    j.record_signals(
        con,
        rid,
        [
            _signal("s-acme", "ACME", DATES[3], True, FEATURES),
            _signal("s-beta", "BETA", DATES[4], False, {}, ["Valor comprado muy bajo"]),
        ],
    )
    j.finish_run(con, rid, "ok", "2 señales")
    _insert_prices(con, "ACME", DATES)
    _insert_prices(con, "BETA", DATES, start_price=20.0, step=-0.1)
    _insert_prices(con, "SPY", DATES, start_price=100.0, step=0.0)
    j.record_decision(con, "s-acme", "approve", "me gusta", planned_shares=5, planned_entry=10.5)
    j.update_outcomes(con, horizons=(5, 21), benchmark_ticker="SPY")


@pytest.fixture
def dcon():
    c = connect(":memory:")
    populate(c)
    yield c
    c.close()


@pytest.fixture
def data_dirs(tmp_path: Path):
    data_dir, config_dir = tmp_path / "data", tmp_path / "config"
    config_dir.mkdir()
    return data_dir, config_dir


def _make_db(data_dir: Path, with_data: bool) -> Path:
    path = data_dir / "tradingtool.duckdb"
    con = connect(path)
    if with_data:
        populate(con)
    con.close()
    return path


# ------------------------------------------------------------------------------ consultas


def test_latest_signals_labels_and_filter(dcon):
    df = q.latest_signals(dcon)
    assert list(df["signal_id"]) == ["s-beta", "s-acme"]  # más reciente primero
    row = df.set_index("signal_id").loc["s-acme"]
    assert row["estado"] == "Pasa"
    assert row["decision_label"] == "Aprobada"
    assert df.set_index("signal_id").loc["s-beta", "decision_label"] == "Sin decisión"
    assert df.set_index("signal_id").loc["s-beta", "estado"] == "Bloqueada"
    only = q.latest_signals(dcon, only_passed=True)
    assert list(only["signal_id"]) == ["s-acme"]
    assert q.latest_signals(dcon, start=DATES[10]).empty
    assert q.signal_date_bounds(dcon) == (DATES[3], DATES[4])


def test_latest_signals_empty_db(con):
    df = q.latest_signals(con)
    assert df.empty and "decision_label" in df.columns
    assert q.signal_date_bounds(con) == (None, None)


def test_signal_detail_parses_json(dcon):
    d = q.signal_detail(dcon, "s-acme")
    assert d is not None
    assert d.as_of_date == DATES[3]
    assert d.reasons == ["Compra del CEO por $250,000"]
    assert d.features["price_last"] == 10.5
    assert d.accessions == [ACC]
    assert d.last_decision["decision"] == "approve"
    assert d.last_decision["planned_shares"] == 5
    (acc, url), = d.filing_links
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000123456726000001/"
        "0001234567-26-000001-index.htm"
    )
    assert q.signal_detail(dcon, "no-existe") is None


def test_signal_detail_robust_to_bad_json(con):
    con.execute(
        "INSERT INTO signals (signal_id, strategy_version, config_hash, as_of_date, ticker, "
        "issuer_cik, score, passed, reasons, features, accessions) VALUES "
        "('x', 'v', 'h', '2026-01-05', NULL, '12', NULL, false, ?, ?, NULL)",
        [json.dumps("una sola razón"), json.dumps([1, 2])],
    )
    d = q.signal_detail(con, "x")
    assert d.reasons == ["una sola razón"]
    assert d.features == {}
    assert d.accessions == []
    assert d.ticker is None and d.score is None
    assert q.parse_reasons('{"a": 1}') == ["a: 1"]
    assert q.parse_reasons("no es json") == ["no es json"]
    assert q.parse_reasons(None) == [] and q.parse_reasons("[]") == []
    assert q.parse_accessions('"0001234567-26-000001"') == [ACC]
    assert q.parse_features(None) == {}


def test_price_bars_until_date(dcon):
    bars = q.price_bars(dcon, "ACME", end=DATES[3])
    assert len(bars) == 4
    assert bars["date"].iloc[-1] == pd.Timestamp(DATES[3])
    assert list(bars.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(q.price_bars(dcon, "ACME", start=DATES[38])) == 2
    assert q.price_bars(dcon, None).empty
    assert q.price_bars(dcon, "NOPE").empty


def test_outcome_summary_and_history(dcon):
    s = q.outcome_summary(dcon, 5)
    assert list(s["grupo"]) == ["aprobada", "bloqueada"]  # orden fijo
    assert (s["n"] == 1).all()
    assert q.outcome_summary(dcon, 999).empty
    assert q.pending_outcomes(dcon, 5) == 0
    assert q.pending_outcomes(dcon, 63) == 2

    j.record_decision(dcon, "s-acme", "reject", "cambié de opinión")
    h = q.decisions_history(dcon, 5)
    assert list(h["decision"]) == ["reject", "approve"]
    assert list(h["vigente"]) == [True, False]
    assert h["ret"].notna().all()
    # Las decisiones se tomaron "hoy", mucho después de la entrada: retrospectiva.
    assert list(h["decidida_tarde"]) == [True, True]
    assert h.loc[1, "planned_shares"] == 5
    assert q.decisions_history(dcon, 999)["ret"].isna().all()


def test_data_status_runs_and_versions(dcon):
    st_df = q.data_status(dcon, today=DATES[-1]).set_index("tabla")
    assert st_df.loc["signals", "filas"] == 2
    assert st_df.loc["prices_daily", "filas"] == 120
    assert st_df.loc["prices_daily", "dias_desde"] == 0
    assert st_df.loc["insider_filings", "filas"] == 0
    assert st_df.loc["insider_owners", "ultimo_dato"] is None
    runs = q.last_runs(dcon)
    assert len(runs) == 1 and runs.loc[0, "status"] == "ok"
    assert runs.loc[0, "duracion_s"] >= 0
    v = q.strategy_versions(dcon)
    assert v.loc[0, "n"] == 2 and v.loc[0, "config_hash"] == "cfg123"
    cov = q.price_coverage(dcon)
    assert cov == {"tickers": 3, "first": DATES[0], "last": DATES[-1]}


def test_sec_filing_index_url():
    assert q.sec_filing_index_url("0000950170-24-012345", "0001045810") == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000095017024012345/"
        "0000950170-24-012345-index.htm"
    )
    assert q.sec_filing_index_url("000095017024012345", 1045810).endswith(
        "/1045810/000095017024012345/0000950170-24-012345-index.htm"
    )
    assert q.sec_filing_index_url("a-1", "123") is None
    assert q.sec_filing_index_url(ACC, None) is None
    assert q.sec_filing_index_url(ACC, "abc") is None


def test_record_panel_decision(dcon):
    with pytest.raises(q.DecisionInputError, match="motivo"):
        q.record_panel_decision(dcon, "s-beta", "reject", "  ")
    with pytest.raises(q.DecisionInputError):
        q.record_panel_decision(dcon, "s-beta", "buy_now")
    with pytest.raises(q.DecisionInputError):
        q.record_panel_decision(dcon, "nope", "approve")
    with pytest.raises(q.DecisionInputError):
        q.record_panel_decision(dcon, "s-beta", "approve", "x" * 400)
    did = q.record_panel_decision(
        dcon,
        "s-beta",
        "reject",
        "  muy   poco líquida ",
        planned_shares=0,
        planned_entry=float("nan"),
        planned_stop=9.0,
    )
    row = dcon.execute(
        "SELECT decision, reason, planned_shares, planned_entry, planned_stop, decided_by "
        "FROM decisions WHERE decision_id = ?",
        [did],
    ).fetchone()
    assert row == ("reject", "muy poco líquida", None, None, 9.0, "human")


def test_open_db_errors(tmp_path, monkeypatch):
    missing = tmp_path / "nada" / "tt.duckdb"
    with pytest.raises(q.DbMissingError, match="uv run tt iniciar"), q.open_db(missing):
        pass

    path = tmp_path / "tt.duckdb"
    writer = connect(path)  # otra conexión de escritura abierta en este proceso
    try:
        with pytest.raises(q.DbBusyError, match="ocupada"), q.open_db(path):
            pass
    finally:
        writer.close()
    with q.open_db(path) as con:  # ya libre
        assert q.latest_signals(con).empty

    def _locked(*a, **k):
        raise duckdb.IOException('IO Error: Could not set lock on file "x": Conflicting lock')

    monkeypatch.setattr(q, "connect", _locked)
    with pytest.raises(q.DbBusyError), q.open_db(path):
        pass


def test_open_db_schema_error(tmp_path):
    path = tmp_path / "vacia.duckdb"
    duckdb.connect(str(path)).close()  # archivo DuckDB sin tablas
    with pytest.raises(q.DbSchemaError, match="tt iniciar"), q.open_db(path) as con:
        q.latest_signals(con)


def test_open_db_write_records_decision(tmp_path):
    path = tmp_path / "tt.duckdb"
    c = connect(path)
    populate(c)
    c.close()
    with q.open_db(path, read_only=False) as con:
        q.record_panel_decision(con, "s-beta", "skip")
    with q.open_db(path) as con:
        assert q.signal_detail(con, "s-beta").last_decision["decision"] == "skip"


# ------------------------------------------------------------------------------ presentación


def test_feature_rows_robust():
    feats = {
        "n_insiders": "3",
        "total_value": None,
        "titles": ["CEO", None, "CFO"],
        "roles": {"director": 2},
        "pct_increase_max": 12.345,
        "filing_lag_max": 2,
        "price_last": "no-numero",
        "zeta": [1.5, 2],
        "alfa_raro": float("nan"),
    }
    rows = {r.key: r for r in p.feature_rows(feats)}
    assert rows["n_insiders"].value == "3"
    assert rows["total_value"].value == p.DASH
    assert rows["titles"].value == "CEO, CFO"
    assert rows["roles"].value == "director (2)"
    assert rows["pct_increase_max"].value == "12.3%"
    assert rows["filing_lag_max"].value == "2 días"
    assert rows["price_last"].value == "no-numero"
    assert rows["zeta"].label == "Zeta" and rows["zeta"].value == "1.50, 2"
    assert rows["alfa_raro"].value == p.DASH
    keys = [r.key for r in p.feature_rows(feats)]
    assert keys.index("n_insiders") < keys.index("alfa_raro")  # conocidas primero
    assert p.feature_rows(None) == [] and p.feature_rows("x") == []
    assert [r.key for r in p.key_metric_rows(FEATURES)] == [
        "n_insiders",
        "total_value",
        "price_last",
        "adv20",
    ]


def test_sizing_defaults():
    d = p.sizing_defaults(FEATURES, 2.5)
    assert d.entry == 10.5 and d.stop == pytest.approx(9.5) and d.adv == 2_500_000.0
    assert p.sizing_defaults({"price_last": 10}, 2.5).stop is None
    assert p.sizing_defaults({"price_last": 1, "atr14": 1}, 2.5).stop is None  # stop <= 0
    assert p.sizing_defaults({"atr14": 1}, 2.5) == p.SizingDefaults(None, None, None, 1.0)
    assert p.sizing_defaults(None, 2.5).entry is None


def test_compute_sizing(app_config):
    res, msg = p.compute_sizing(app_config, 1000, 0.5, 10.0, 9.0, 2_000_000)
    assert msg is None and res.shares == 5 and res.risk_usd == pytest.approx(5.0)
    res, msg = p.compute_sizing(app_config, 1000, 0.5, None, 9.0)
    assert res is None and "entrada" in msg
    res, msg = p.compute_sizing(app_config, 1000, 0.5, 9.0, 10.0)
    assert res is None and "debajo" in msg


def test_summary_display_and_small_sample(dcon):
    s = q.outcome_summary(dcon, 5)
    disp = p.summary_for_display(s, "SPY")
    assert list(disp.columns)[:3] == ["Grupo", "Ideas (n)", "Retorno medio"]
    assert "Exceso medio vs SPY" in disp.columns
    assert disp.loc[0, "Grupo"] == "Aprobadas"
    raw = s.loc[0, "ret_medio"]
    assert disp.loc[0, "Retorno medio"] == pytest.approx(round(raw * 100, 2))
    assert p.small_sample_groups(s) == ["Aprobadas", "Bloqueadas (contrafactuales)"]
    assert p.small_sample_groups(pd.DataFrame({"grupo": ["aprobada"], "n": [30]})) == []
    assert p.summary_for_display(pd.DataFrame()).empty


def test_freshness_warnings(dcon):
    status = q.data_status(dcon, today=DATES[-1] + timedelta(days=10))
    w = " ".join(p.freshness_warnings(status))
    assert "sec-historico" in w  # sin Form 4
    assert "uv run tt precios" in w  # precios viejos
    assert "uv run tt screener" in w
    assert p.freshness_warnings(pd.DataFrame()) == []


def test_text_helpers():
    assert p.md_escape("$5 *x* [a](b)") == "\\$5 \\*x\\* \\[a\\](b)"
    assert p.horizon_label(21) == "21 días hábiles (~1 mes)"
    assert p.horizon_label(7) == "7 días hábiles"
    assert p.fmt_money(-1234.5) == "-US$1,234.50"
    assert p.fmt_money_compact(2_500_000) == "US$2.5 millones"
    assert p.fmt_pct(0.1234) == "12.3%" and p.fmt_pct(None) == p.DASH
    assert p.days_ago_text(0) == "hoy" and p.days_ago_text(3) == "hace 3 días"


class FakeIB:
    def __init__(self, accounts=("DU1234567",), fail_connect=False):
        self.accounts, self.fail_connect, self.connected = list(accounts), fail_connect, False

    def connect(self, host, port, clientId, timeout, readonly):
        assert readonly is True
        if self.fail_connect:
            raise ConnectionRefusedError("refused")
        self.connected = True

    def isConnected(self):
        return self.connected

    def disconnect(self):
        self.connected = False

    def managedAccounts(self):
        return self.accounts

    def accountSummary(self, account):
        return [SimpleNamespace(tag="NetLiquidation", value="1000.5", currency="USD")]

    def positions(self, account):
        c = SimpleNamespace(
            symbol="VWRA", secType="STK", currency="USD", primaryExchange="LSEETF", exchange=""
        )
        return [SimpleNamespace(account=account, contract=c, position=3, avgCost=150.2)]


def test_probe_ibkr_ok_and_errors():
    s = Settings(_env_file=None)
    ok = p.probe_ibkr(s, ib_factory=FakeIB)
    assert ok.ok and "DU1234567" in ok.message and "paper" in ok.message
    vals = p.snapshot_values_table(ok.snapshot)
    assert vals.loc[0, "Concepto"] == "Valor total de la cuenta"
    assert p.snapshot_positions_table(ok.snapshot).loc[0, "Símbolo"] == "VWRA"

    down = p.probe_ibkr(s, ib_factory=lambda: FakeIB(fail_connect=True))
    assert not down.ok and "No se pudo conectar" in down.message

    live = p.probe_ibkr(s, ib_factory=lambda: FakeIB(accounts=("U1234567",)))
    assert not live.ok and "REAL" in live.message

    def boom():
        raise RuntimeError("explotó")

    weird = p.probe_ibkr(s, ib_factory=boom)
    assert not weird.ok and "explotó" in weird.message


# ------------------------------------------------------------------------------ gráficos


def test_charts_build_valid_specs(dcon):
    bars = q.price_bars(dcon, "ACME")
    spec = charts.price_chart(bars, DATES[3]).to_dict()
    assert spec["layer"]
    assert charts.price_chart(pd.DataFrame(columns=["date", "close"]), DATES[3]) is None
    assert charts.price_chart(bars, None, dark=True).to_dict()["layer"]

    s = q.outcome_summary(dcon, 5)
    long = charts.outcome_long(s, "SPY")
    assert set(long["medida"]) == {"Retorno medio", "Exceso medio vs SPY"}
    assert charts.outcome_chart(s, "SPY").to_dict()["layer"]
    assert charts.outcome_chart(pd.DataFrame(), "SPY") is None


def test_launch_command_is_local_only():
    cmd = launch.panel_command(port=8600)
    assert cmd[cmd.index("--server.address") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--server.port") + 1] == "8600"
    assert launch.app_path().exists()


# ------------------------------------------------------------------------------ app (AppTest)


def _run_app(monkeypatch, data_dir: Path, config_dir: Path) -> AppTest:
    monkeypatch.setenv("TT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TT_CONFIG_DIR", str(config_dir))
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    return at


def _texts(at: AppTest) -> str:
    parts = []
    for group in (at.title, at.markdown, at.info, at.warning, at.error, at.success, at.caption):
        parts += [str(e.value) for e in group]
    parts += [str(e.value) for e in at.subheader]
    return "\n".join(parts)


def test_app_without_db_shows_onboarding(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert not at.exception
    text = _texts(at)
    assert "uv run tt iniciar" in text
    assert "NO envía órdenes" in text
    assert len(at.tabs) == 4
    assert not (data_dir / "tradingtool.duckdb").exists()  # el panel no crea la base


def test_app_with_empty_db(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    _make_db(data_dir, with_data=False)
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert not at.exception
    text = _texts(at)
    assert "Todavía no hay ideas" in text
    assert "Aún no hay resultados" in text


def test_app_with_data_lists_signal_and_records_decision(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    db_path = _make_db(data_dir, with_data=True)
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert not at.exception
    sel = at.selectbox(key="ideas_sel")
    assert sel.value == "s-acme"
    assert "ACME" in sel.format_func(sel.value)
    text = _texts(at)
    assert "ACME · ACME Corp" in text
    assert "Compra del CEO por" in text
    assert "000123456726000001" in text  # enlace a la SEC
    assert at.number_input(key="ideas_s-acme_entrada").value == pytest.approx(10.5)
    assert at.number_input(key="ideas_s-acme_stop").value == pytest.approx(9.5)

    # Rechazar sin motivo: error y nada se guarda.
    at.button(key="btn_reject_s-acme").click().run()
    assert not at.exception
    assert any("motivo" in str(e.value) for e in at.error)

    # Aprobar con nota: se guarda con el plan (acciones, entrada, stop).
    at.text_input(key="reason_s-acme_0").input("cluster de directores")
    at.button(key="btn_approve_s-acme").click().run()
    assert not at.exception
    assert any("Decisión guardada" in str(e.value) for e in at.success)

    with q.open_db(db_path) as con:
        rows = con.execute(
            "SELECT decision, reason, planned_shares, planned_entry, planned_stop "
            "FROM decisions WHERE signal_id = 's-acme' ORDER BY decided_at"
        ).fetchall()
    assert len(rows) == 2  # la del fixture + la del panel
    decision, reason, shares, entry, stop = rows[-1]
    assert (decision, reason) == ("approve", "cluster de directores")
    assert shares and shares > 0
    assert entry == pytest.approx(10.5) and stop == pytest.approx(9.5)


def test_app_shows_blocked_when_toggle_off(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    _make_db(data_dir, with_data=True)
    at = _run_app(monkeypatch, data_dir, config_dir)
    at.toggle(key="ideas_solo_pasan").set_value(False).run()
    assert not at.exception
    assert set(at.selectbox(key="ideas_sel").options) >= {"s-acme", "s-beta"}
