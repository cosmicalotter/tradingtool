"""Tests del panel: consultas, presentación, gráficos y la app Streamlit (AppTest).

Sin red: base DuckDB temporal con señales, precios, decisiones y resultados falsos.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tradingtool.config import AppConfig
from tradingtool.db import connect
from tradingtool.journal import journal as j
from tradingtool.models import Signal
from tradingtool.risk.sizing import size_position
from tradingtool.settings import Settings
from tradingtool.ui import charts
from tradingtool.ui import presenters as p
from tradingtool.ui import queries as q

UI_DIR = Path(__file__).parents[1] / "src" / "tradingtool" / "ui"
APP_PATH = UI_DIR / "app.py"
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


def _signal(sid, ticker, as_of, passed=True, features=None, reasons=None, origin="live"):
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
        origin=origin,
    )


# Mismas claves que produce insiders/screener.py (más una desconocida, "algo_nuevo").
FEATURES = {
    "n_insiders": 2,
    "n_insiders_window": 3,
    "total_value": 250000.0,
    "max_insider_value": 150000.0,
    "any_officer": True,
    "any_director": True,
    "opportunistic_count": 1,
    "routine_count": 1,
    "unclassified_count": 1,
    "pct_increase_max": None,
    "new_position": True,
    "filing_lag_max": 2,
    "insider_price_max": 10.4,
    "insiders": [
        {
            "nombre": "John Doe",
            "cik": "0002222222",
            "cargo": "Director",
            "valor": 100000.0,
            "acciones": 10000.0,
            "aumento_participacion": None,
            "posicion_nueva": True,
            "clase": "no_clasificable",
            "valida": True,
            "motivos": [],
        },
        {
            "nombre": "Rich Habit",
            "cik": "0003333333",
            "cargo": "CFO",
            "valor": 20000.0,
            "acciones": 2000.0,
            "aumento_participacion": 0.0125,
            "posicion_nueva": False,
            "clase": "rutinario",
            "valida": False,
            "motivos": ["Insider rutinario (compra habitual en el mismo mes cada año)"],
        },
        {
            "nombre": "Jane Roe",
            "cik": "0001111111",
            "cargo": "CEO",
            "valor": 150000.0,
            "acciones": 15000.0,
            "aumento_participacion": 0.25,
            "posicion_nueva": False,
            "clase": "oportunista",
            "valida": True,
            "motivos": [],
        },
    ],
    "n_purchase_rows": 3,
    "transaction_dates": [str(DATES[1]), str(DATES[2])],
    "price_last": 10.5,
    "price_last_date": str(DATES[3]),
    "adv20": 2_500_000.0,
    "atr14": 0.4,
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
    ((_acc, url),) = d.filing_links
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


def _decision_row(con, did):
    return con.execute(
        "SELECT signal_id, decision, reason, planned_shares, planned_entry, planned_stop, "
        "decided_by FROM decisions WHERE decision_id = ?",
        [did],
    ).fetchone()


def test_record_panel_decision(dcon):
    with pytest.raises(q.DecisionInputError, match="motivo"):
        q.record_panel_decision(dcon, "s-acme", "reject", "  ")
    with pytest.raises(q.DecisionInputError):
        q.record_panel_decision(dcon, "s-acme", "buy_now")
    with pytest.raises(q.DecisionInputError, match="ya no existe"):
        q.record_panel_decision(dcon, "nope", "approve")
    with pytest.raises(q.DecisionInputError):
        q.record_panel_decision(dcon, "s-acme", "approve", "x" * 400)
    # Las bloqueadas no se deciden (son contrafactuales).
    with pytest.raises(q.DecisionInputError, match="no pasó los filtros"):
        q.record_panel_decision(dcon, "s-beta", "skip")
    # Stop por encima de la entrada: plan inválido, no se guarda.
    with pytest.raises(q.DecisionInputError, match="stop"):
        q.record_panel_decision(dcon, "s-acme", "approve", planned_entry=10.0, planned_stop=11.0)
    n_before = dcon.execute("SELECT count(*) FROM decisions").fetchone()[0]
    assert n_before == 1  # solo la del fixture

    # Aprobar: el plan se limpia (0 acciones / NaN -> vacío) y se guarda tal cual.
    did = q.record_panel_decision(
        dcon,
        "s-acme",
        "approve",
        "  cluster   de directores ",
        planned_shares=0,
        planned_entry=float("nan"),
        planned_stop=9.0,
    )
    assert _decision_row(dcon, did) == (
        "s-acme",
        "approve",
        "cluster de directores",
        None,
        None,
        9.0,
        "human",
    )
    did = q.record_panel_decision(
        dcon, "s-acme", "approve", planned_shares=5.0, planned_entry=10.5, planned_stop=9.5
    )
    assert _decision_row(dcon, did)[3:6] == (5, 10.5, 9.5)
    did = q.record_panel_decision(dcon, "s-acme", "approve", planned_shares=5.7)
    assert _decision_row(dcon, did)[3] is None  # nunca redondea acciones en silencio

    # Rechazar u omitir: no hay plan aunque la calculadora tenga valores.
    did = q.record_panel_decision(
        dcon, "s-acme", "reject", "muy poco líquida", 5, planned_entry=10.5, planned_stop=9.5
    )
    assert _decision_row(dcon, did) == (
        "s-acme",
        "reject",
        "muy poco líquida",
        None,
        None,
        None,
        "human",
    )


def test_record_panel_decision_only_calls_journal(dcon, monkeypatch):
    calls = []
    real = j.record_decision

    def spy(*args, **kwargs):
        calls.append((args[1:], kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(q.journal, "record_decision", spy)
    q.record_panel_decision(dcon, "s-acme", "skip", planned_shares=3)
    assert calls == [
        (
            ("s-acme", "skip"),
            {
                "reason": None,
                "decided_by": "human",
                "planned_shares": None,
                "planned_entry": None,
                "planned_stop": None,
            },
        )
    ]


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
        q.record_panel_decision(con, "s-acme", "skip")
    with q.open_db(path) as con:
        assert q.signal_detail(con, "s-acme").last_decision["decision"] == "skip"


# ------------------------------------------------------------------------------ presentación


def test_feature_rows_robust():
    feats = {
        "n_insiders": "3",
        "total_value": None,
        "titles": ["CEO", None, "CFO"],
        "roles": {"director": 2},
        "pct_increase_max": 0.1234,  # fracción (como la guarda el screener)
        "filing_lag_max": 2,
        "price_last": "no-numero",
        "zeta": [1.5, 2],
        "alfa_raro": float("nan"),
        "insiders": "texto raro",
        "any_officer": "quizá",
    }
    rows = {r.key: r for r in p.feature_rows(feats)}
    assert rows["n_insiders"].value == "3"
    assert rows["total_value"].value == p.DASH
    assert rows["titles"].value == "CEO, CFO"
    assert rows["roles"].value == "director: 2"
    assert rows["pct_increase_max"].value == "12.34%"
    assert rows["filing_lag_max"].value == "2 días"
    assert rows["price_last"].value == "no-numero"
    assert rows["zeta"].label == "Zeta" and rows["zeta"].value == "1.50, 2"
    assert rows["alfa_raro"].value == p.DASH
    assert rows["insiders"].value == "texto raro"
    assert rows["any_officer"].value == "quizá"
    keys = [r.key for r in p.feature_rows(feats)]
    assert keys.index("n_insiders") < keys.index("alfa_raro")  # conocidas primero
    assert p.feature_rows(None) == [] and p.feature_rows("x") == []


def test_feature_rows_screener_keys():
    rows = {r.key: r for r in p.feature_rows(FEATURES)}
    # Todas las claves del screener tienen etiqueta en español (ninguna sale "cruda").
    screener_keys = set(FEATURES) - {"algo_nuevo"}
    assert screener_keys <= set(p.FEATURE_INFO)
    assert rows["n_insiders_window"].value == "3"
    assert rows["total_value"].value == "US$250,000.00"
    assert rows["max_insider_value"].value == "US$150,000.00"
    assert rows["any_officer"].value == "Sí" and rows["new_position"].value == "Sí"
    assert rows["pct_increase_max"].value.startswith("Posición nueva")
    assert rows["insider_price_max"].value == "US$10.40"
    assert rows["transaction_dates"].value == f"{DATES[1]}, {DATES[2]}"
    assert rows["insiders"].value.startswith("3 ")
    assert rows["adv20"].value == "US$2.50 millones"
    assert rows["atr14"].value == "US$0.40"
    # Precio "a una fecha": la etiqueta del último cierre lleva su fecha.
    assert rows["price_last"].label == f"Último cierre al {DATES[3].isoformat()}"
    assert rows["price_last"].value == "US$10.50"
    assert rows["price_last_date"].value == DATES[3].isoformat()
    assert rows["algo_nuevo"].label == "Algo nuevo"


def test_insiders_table():
    t = p.insiders_table(FEATURES)
    assert list(t.columns) == list(p.INSIDER_COLUMNS)
    # Primero los que cuentan, de mayor a menor valor.
    assert list(t["Nombre"]) == ["Jane Roe", "John Doe", "Rich Habit"]
    jane, john, rich = (t.iloc[i] for i in range(3))
    assert jane["Valor comprado"] == "US$150,000.00" and jane["Acciones"] == "15,000"
    assert jane["Aumento de participación"] == "25.00%"
    assert jane["Tipo de insider"].startswith("Oportunista")
    assert john["Aumento de participación"] == "Posición nueva"
    assert john["Tipo de insider"].startswith("Sin clasificar")
    assert rich["Tipo de insider"].startswith("Rutinario")
    assert rich["¿Cuenta para la señal?"] == "No" and "rutinario" in rich["Por qué no cuenta"]
    assert jane["¿Cuenta para la señal?"] == "Sí" and jane["Por qué no cuenta"] == ""
    # Robusto a datos raros.
    assert p.insiders_table({"insiders": "x"}).empty
    assert p.insiders_table(None).empty
    odd = p.insiders_table({"insiders": [1, None, {"nombre": None, "valor": "abc"}]})
    assert len(odd) == 1 and odd.loc[0, "Nombre"] == "(sin nombre)"
    assert odd.loc[0, "Valor comprado"] == p.DASH
    assert p.class_label("no_clasificable") == "Sin clasificar (no hay historia suficiente)"
    assert p.class_label(None) == "Sin dato" and p.class_label("otro") == "otro"
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
    # Acción de menos de medio centavo: no se prellena (la casilla exige >= 0.01).
    assert p.sizing_defaults({"price_last": 0.004, "atr14": 0.001}, 2.5).entry is None


def test_compute_sizing(app_config):
    res, msg = p.compute_sizing(app_config, 1000, 0.5, 10.0, 9.0, 2_000_000)
    assert msg is None and res.shares == 5 and res.risk_usd == pytest.approx(5.0)
    res, msg = p.compute_sizing(app_config, 1000, 0.5, None, 9.0)
    assert res is None and "entrada" in msg
    res, msg = p.compute_sizing(app_config, 1000, 0.5, 9.0, 10.0)
    assert res is None and "debajo" in msg
    res, msg = p.compute_sizing(app_config, 1000, 3.0, 10.0, 9.0)  # fuera de 0.01%-2%
    assert res is None and "2%" in msg


@pytest.mark.parametrize(
    ("capital", "risk_pct", "entry", "stop", "adv"),
    [
        (1000, 0.5, 10.0, 9.0, 2_000_000),
        (1000, 0.5, 50.0, 45.0, None),
        (25_000, 1.0, 123.45, 118.2, 80_000_000),
        (1000, 2.0, 3.0, 2.99, 500_000),  # topado por el % máximo por posición
        (500, 0.25, 400.0, 380.0, None),  # no alcanza para 1 acción
    ],
)
def test_calculator_equals_size_position(app_config, capital, risk_pct, entry, stop, adv):
    res, msg = p.compute_sizing(app_config, capital, risk_pct, entry, stop, adv)
    risk_cfg = app_config.risk.model_copy(update={"risk_per_trade_pct": risk_pct})
    expected = size_position(risk_cfg, app_config.costs, entry, stop, capital, adv)
    assert msg is None and res == expected


def test_decision_plan(app_config):
    res, _ = p.compute_sizing(app_config, 1000, 0.5, 10.5, 9.5, 2_500_000)
    plan = p.decision_plan(res, 10.5, 9.5)
    assert plan == p.DecisionPlan(shares=res.shares, entry=10.5, stop=9.5)
    assert "US$10.50" in plan.describe() and "US$9.50" in plan.describe()
    # Sin cálculo válido: nunca se inventan acciones; un stop inválido se descarta.
    assert p.decision_plan(None, 10.0, 11.0) == p.DecisionPlan(None, 10.0, None)
    assert p.decision_plan(None, None, None).describe().startswith("sin plan")
    zero, _ = p.compute_sizing(app_config, 500, 0.25, 400.0, 380.0)
    assert p.decision_plan(zero, 400.0, 380.0).shares is None


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
    assert p.days_ago_text(0) == "hoy" and p.days_ago_text(3) == "hace 3 días"
    assert p.fmt_date(date(2026, 1, 5)) == "2026-01-05"
    assert p.fmt_date(datetime(2026, 1, 5, 14, 3)) == "2026-01-05 14:03"
    assert p.fmt_date(None) == p.DASH


def test_formatting_usd_and_pct_two_decimals():
    assert p.fmt_money(-1234.5) == "-US$1,234.50"
    assert p.fmt_money(250000) == "US$250,000.00"
    assert p.fmt_money(float("inf")) == p.DASH and p.fmt_money("x") == p.DASH
    assert p.fmt_money_compact(2_500_000) == "US$2.50 millones"
    assert p.fmt_money_compact(950) == "US$950.00"
    assert p.fmt_money_compact(12_300) == "US$12.30 mil"
    assert p.fmt_pct(0.1234) == "12.34%" and p.fmt_pct(None) == p.DASH
    assert p.fmt_pct(-0.05, signed=True) == "-5.00%"
    assert p.fmt_pct_points(0.3) == "0.30%"
    assert p.fmt_shares(10) == "10" and p.fmt_shares(10.5) == "10.50"


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


def test_cli_panel_command_is_local_only(monkeypatch):
    """``tt panel`` (cli.py) es el único lanzador: escucha solo en 127.0.0.1."""
    from typer.testing import CliRunner

    from tradingtool import cli

    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    r = CliRunner().invoke(cli.app, ["panel", "--puerto", "8600"])
    assert r.exit_code == 0, r.output
    (cmd,) = calls
    assert cmd[cmd.index("--server.address") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--server.port") + 1] == "8600"
    assert Path(cmd[cmd.index("run") + 1]).resolve() == APP_PATH.resolve()


# ------------------------------------------------------------------------------ seguridad

ORDER_TOKENS = (
    "placeOrder",
    "cancelOrder",
    "reqGlobalCancel",
    "MarketOrder",
    "LimitOrder",
    "StopOrder",
    "bracketOrder",
    "whatIfOrder",
    "Order(",
    "ib_async",
    "ib_insync",
)


def test_ui_has_no_order_paths():
    """El panel no tiene ningún camino para enviar órdenes ni escribe SQL propio."""
    files = sorted(UI_DIR.glob("*.py"))
    assert APP_PATH in files
    for f in files:
        text = f.read_text(encoding="utf-8")
        for tok in ORDER_TOKENS:
            assert tok not in text, f"{f.name} menciona {tok!r}"
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b", text), f.name
        used = set(re.findall(r"\bjournal\.(\w+)", text))
        assert used <= {"list_signals", "outcome_summary", "record_decision", "VALID_DECISIONS"}
    # La única escritura es record_decision, y solo desde queries.record_panel_decision.
    for f in files:
        text = f.read_text(encoding="utf-8")
        if "journal.record_decision(" in text:
            assert f.name == "queries.py"
    assert "IbkrReadOnly" in (UI_DIR / "presenters.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------ app (AppTest)


def _run_app(monkeypatch, data_dir: Path, config_dir: Path) -> AppTest:
    monkeypatch.setenv("TT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TT_CONFIG_DIR", str(config_dir))
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    return at


def _decisions(db_path: Path) -> list[tuple]:
    with q.open_db(db_path) as con:
        return con.execute(
            "SELECT signal_id, decision, reason, planned_shares, planned_entry, planned_stop "
            "FROM decisions ORDER BY decided_at, decision_id"
        ).fetchall()


def _df_with(at: AppTest, column: str) -> pd.DataFrame:
    for df in at.dataframe:
        if column in df.value.columns:
            return df.value
    raise AssertionError(f"no hay tabla con la columna {column!r}")


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
    assert len(at.tabs) == 5
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
    at.button(key="btn_reject_s-acme_0").click().run()
    assert not at.exception
    assert any("motivo" in str(e.value) for e in at.error)
    assert len(_decisions(db_path)) == 1  # solo la del fixture

    # Aprobar con nota: se guarda con el plan que muestra la calculadora.
    at.text_input(key="reason_s-acme_0").input("cluster de directores")
    at.button(key="btn_approve_s-acme_0").click().run()
    assert not at.exception
    assert any("Decisión guardada" in str(e.value) for e in at.success)

    rows = _decisions(db_path)
    assert len(rows) == 2  # la del fixture + la del panel
    sid, decision, reason, shares, entry, stop = rows[-1]
    assert (sid, decision, reason) == ("s-acme", "approve", "cluster de directores")
    # Calculadora = risk.sizing.size_position con los valores por defecto.
    cfg = AppConfig()
    expected = size_position(cfg.risk, cfg.costs, 10.5, 9.5, adv_usd=2_500_000.0)
    assert shares == expected.shares > 0
    assert entry == pytest.approx(10.5) and stop == pytest.approx(9.5)
    shown = [m for m in at.metric if m.label == "Acciones (enteras)"]
    assert shown and shown[0].value == str(expected.shares)

    # Recargar o tocar otros controles NO vuelve a guardar la decisión.
    at.run()
    at.toggle(key="after_s-acme").set_value(True).run()
    assert not at.exception
    assert len(_decisions(db_path)) == 2
    # Los botones se renovaron (doble clic sobre el botón viejo no cuenta) y el motivo se limpió.
    keys = {b.key for b in at.button}
    assert "btn_approve_s-acme_1" in keys and "btn_approve_s-acme_0" not in keys
    assert at.text_input(key="reason_s-acme_1").value == ""

    # Rechazar con motivo: se guarda sin plan (el plan solo aplica al aprobar).
    at.text_input(key="reason_s-acme_1").input("cambié de opinión")
    at.button(key="btn_reject_s-acme_1").click().run()
    assert not at.exception
    rows = _decisions(db_path)
    assert len(rows) == 3
    assert rows[-1] == ("s-acme", "reject", "cambié de opinión", None, None, None)


def test_app_shows_blocked_when_toggle_off(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    _make_db(data_dir, with_data=True)
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert [o.split(" · ")[0] for o in at.selectbox(key="ideas_sel").options] == ["ACME"]
    at.toggle(key="ideas_solo_pasan").set_value(False).run()
    assert not at.exception
    sel = at.selectbox(key="ideas_sel")
    # Las opciones visibles son etiquetas legibles (no ids internos).
    assert sorted(o.split(" · ")[0] for o in sel.options) == ["ACME", "BETA"]
    assert any("Bloqueada" in o for o in sel.options)

    # Una bloqueada se puede ver, pero no se decide (no hay botones).
    sel.set_value("s-beta").run()
    assert not at.exception
    assert "BETA · BETA Corp" in _texts(at)
    assert "no se decide" in _texts(at)
    decision_keys = ("btn_approve_", "btn_reject_", "btn_skip_")
    assert not [b for b in at.button if str(b.key).startswith(decision_keys)]


def test_app_detail_insiders_table_and_point_in_time(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    _make_db(data_dir, with_data=True)
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert not at.exception
    text = _texts(at)
    # Tabla «Quién compró» con la clase en español sencillo.
    insiders = _df_with(at, "Tipo de insider")
    assert list(insiders["Nombre"]) == ["Jane Roe", "John Doe", "Rich Habit"]
    assert insiders["Tipo de insider"].str.startswith("Oportunista").any()
    assert insiders["Tipo de insider"].str.startswith("Rutinario").any()
    assert insiders["Tipo de insider"].str.startswith("Sin clasificar").any()
    assert "no_clasificable" not in insiders.to_string()
    # Precios etiquetados con su fecha (no son los de hoy).
    labels = [m.label for m in at.metric]
    assert f"Último cierre al {DATES[3].isoformat()}" in labels
    assert "no es el precio de hoy" in text.lower() or "no los de hoy" in text
    assert "US$250,000.00" in [m.value for m in at.metric]
    # El gráfico, por defecto, no muestra lo que pasó después de la fecha de la idea.
    chart_data = _df_with(at, "Cierre")
    assert max(chart_data["Fecha"]) == DATES[3]
    at.toggle(key="after_s-acme").set_value(True).run()
    assert max(_df_with(at, "Cierre")["Fecha"]) == DATES[-1]


def test_backtest_signals_hidden(dcon):
    j.record_signals(
        dcon,
        None,
        [_signal("s-bt", "BKTS", DATES[10], True, FEATURES, origin="backtest")],
    )
    assert "s-bt" not in set(q.latest_signals(dcon)["signal_id"])
    assert q.signal_date_bounds(dcon) == (DATES[3], DATES[4])
    assert q.signal_date_bounds(dcon, origin=None) == (DATES[3], DATES[10])
    assert q.pending_outcomes(dcon, 63) == 2  # la de backtest no cuenta
    status = q.data_status(dcon, today=DATES[-1]).set_index("tabla")
    assert status.loc["signals", "filas"] == 2
    with pytest.raises(q.DecisionInputError, match="histórica"):
        q.record_panel_decision(dcon, "s-bt", "approve")


def test_app_hides_backtest_signals(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    db_path = _make_db(data_dir, with_data=True)
    con = connect(db_path)
    j.record_signals(
        con, None, [_signal("s-bt", "BKTS", DATES[10], True, FEATURES, origin="backtest")]
    )
    con.close()
    at = _run_app(monkeypatch, data_dir, config_dir)
    at.toggle(key="ideas_solo_pasan").set_value(False).run()
    assert not at.exception
    options = at.selectbox(key="ideas_sel").options
    assert options and not any("BKTS" in o for o in options)


def test_app_db_locked_by_other_connection(monkeypatch, data_dirs):
    """Otra conexión de escritura abierta (como 'tt diario'): avisos amables, sin traceback."""
    data_dir, config_dir = data_dirs
    db_path = _make_db(data_dir, with_data=True)
    writer = duckdb.connect(str(db_path))  # lectura-escritura en este mismo proceso
    try:
        at = _run_app(monkeypatch, data_dir, config_dir)
        assert not at.exception
        busy = [str(w.value) for w in at.warning if "ocupada" in str(w.value)]
        assert len(busy) == 4  # Rotación ETF, Ideas, Diario y Estado
        assert not any("Traceback" in t or "IOException" in t for t in busy)
    finally:
        writer.close()
    at.run()  # liberada: vuelve a funcionar
    assert not at.exception
    assert at.selectbox(key="ideas_sel").value == "s-acme"


def test_app_decision_when_db_busy_is_not_lost_silently(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    db_path = _make_db(data_dir, with_data=True)
    at = _run_app(monkeypatch, data_dir, config_dir)
    reader = duckdb.connect(str(db_path), read_only=True)  # impide abrir en escritura
    try:
        at.text_input(key="reason_s-acme_0").input("cluster")
        at.button(key="btn_approve_s-acme_0").click().run()
        assert not at.exception
        assert any("ocupada" in str(w.value) for w in at.warning)
        assert any("NO se guardó" in str(c.value) for c in at.caption)
        assert not any("Decisión guardada" in str(e.value) for e in at.success)
    finally:
        reader.close()
    assert len(_decisions(db_path)) == 1
    # Ya libre: el mismo botón (mismo motivo) guarda una sola vez.
    at.button(key="btn_approve_s-acme_0").click().run()
    assert not at.exception
    rows = _decisions(db_path)
    assert len(rows) == 2 and rows[-1][:3] == ("s-acme", "approve", "cluster")


def test_app_survives_malformed_json(monkeypatch, data_dirs):
    """Base antigua con columnas JSON como texto y contenido roto: el panel no se cae."""
    data_dir, config_dir = data_dirs
    data_dir.mkdir()
    path = data_dir / "tradingtool.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute(
        "CREATE TABLE signals (signal_id VARCHAR PRIMARY KEY, run_id VARCHAR, "
        "strategy_version VARCHAR NOT NULL, config_hash VARCHAR NOT NULL, as_of_date DATE NOT "
        "NULL, ticker VARCHAR, issuer_cik VARCHAR NOT NULL, issuer_name VARCHAR, score DOUBLE, "
        "passed BOOLEAN NOT NULL, origin VARCHAR NOT NULL DEFAULT 'live', reasons VARCHAR, "
        "features VARCHAR, accessions VARCHAR, created_at TIMESTAMP DEFAULT current_timestamp)"
    )
    raw.execute(
        "INSERT INTO signals (signal_id, strategy_version, config_hash, as_of_date, ticker, "
        "issuer_cik, issuer_name, score, passed, reasons, features, accessions) VALUES "
        "('s-bad', 'v', 'h', ?, 'ROTO', '12', 'Roto Inc', 1.0, true, 'texto suelto', "
        "'{\"insiders\": [1, {\"nombre\": ', '[sin cerrar')",
        [DATES[3]],
    )
    raw.close()
    connect(path).close()  # crea el resto de tablas (como 'tt iniciar')
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert not at.exception
    text = _texts(at)
    assert "ROTO · Roto Inc" in text
    assert "texto suelto" in text
    assert "[sin cerrar" in text  # el accession inválido se muestra sin enlace


def test_app_ibkr_down_shows_spanish_message(monkeypatch, data_dirs):
    from tradingtool.broker.ibkr import IbkrReadOnly

    data_dir, config_dir = data_dirs
    monkeypatch.setattr(
        p,
        "IbkrReadOnly",
        lambda settings, ib_factory=None: IbkrReadOnly(
            settings, ib_factory=lambda: FakeIB(fail_connect=True)
        ),
    )
    at = _run_app(monkeypatch, data_dir, config_dir)
    at.button(key="btn_ibkr").click().run()
    assert not at.exception
    assert any("No se pudo conectar" in str(e.value) for e in at.error)


def test_app_calculator_equals_size_position(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    at = _run_app(monkeypatch, data_dir, config_dir)  # sin base: la calculadora funciona igual
    at.number_input(key="calc_entrada").set_value(50.0)
    at.number_input(key="calc_atr").set_value(2.0).run()  # stop vacío -> 50 - 2.5 x 2 = 45
    assert not at.exception
    cfg = AppConfig()
    expected = size_position(cfg.risk, cfg.costs, 50.0, 45.0)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Acciones (enteras)"] == str(expected.shares)
    assert metrics["Valor de la posición"] == p.fmt_money(expected.position_value)
    assert metrics["Riesgo si toca el stop"] == p.fmt_money(expected.risk_usd)
    assert metrics["Costo ida y vuelta (%)"] == p.fmt_pct_points(expected.roundtrip_cost_pct)
    assert re.fullmatch(r"US\$[\d,]+\.\d{2}", metrics["Costo ida y vuelta"])


def test_decided_after_open_uses_new_york_open():
    entry = date(2026, 1, 8)  # jueves, horario de invierno (UTC-5)
    assert q.decided_after_open(datetime(2026, 1, 8, 14, 29), entry) is False  # 9:29 NY
    assert q.decided_after_open(datetime(2026, 1, 8, 14, 30), entry) is True  # 9:30 NY
    assert q.decided_after_open(datetime(2026, 1, 8, 1, 0), entry) is False  # noche anterior NY
    summer = date(2026, 7, 9)  # horario de verano (UTC-4)
    assert q.decided_after_open(datetime(2026, 7, 9, 13, 29), summer) is False
    assert q.decided_after_open(datetime(2026, 7, 9, 13, 30), summer) is True
    assert q.decided_after_open(None, entry) is None
    assert q.decided_after_open(datetime(2026, 1, 8), None) is None
    assert q.next_weekday(date(2026, 1, 9)) == date(2026, 1, 12)  # viernes -> lunes
    assert q.next_weekday(date(2026, 1, 7)) == date(2026, 1, 8)


def test_decisions_history_late_flag_without_outcome(dcon):
    """Sin resultado calculado, 'tarde' se estima con el siguiente día hábil a la idea."""
    j.record_signals(dcon, None, [_signal("s-new", "NEWW", DATES[-1], True, FEATURES)])
    j.record_decision(dcon, "s-new", "approve")
    h = q.decisions_history(dcon, 5).set_index("signal_id")
    assert pd.isna(h.loc["s-new", "ret"])
    assert bool(h.loc["s-new", "decidida_tarde"])


def test_app_new_ideas_appear_without_restarting(monkeypatch, data_dirs):
    """Con el panel abierto, una corrida nueva de 'tt diario' trae ideas más recientes."""
    data_dir, config_dir = data_dirs
    db_path = _make_db(data_dir, with_data=True)
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert [o.split(" · ")[0] for o in at.selectbox(key="ideas_sel").options] == ["ACME"]
    con = connect(db_path)
    j.record_signals(con, None, [_signal("s-new", "NEWW", DATES[-1], True, FEATURES)])
    con.close()
    at.run()
    assert not at.exception
    options = [o.split(" · ")[0] for o in at.selectbox(key="ideas_sel").options]
    assert options[0] == "NEWW"


def test_app_with_tiny_risk_config(monkeypatch, data_dirs):
    """Una config válida con riesgo < 0.01% no debe romper las casillas de la calculadora."""
    data_dir, config_dir = data_dirs
    (config_dir / "risk.yaml").write_text("risk_per_trade_pct: 0.005\n", encoding="utf-8")
    _make_db(data_dir, with_data=True)
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert not at.exception
    assert at.number_input(key="calc_riesgo").value == pytest.approx(0.005)


# ------------------------------------------------------------------------------ rotación ETF


def _etf_curves(n_days: int = 600) -> pd.DataFrame:
    idx = pd.bdate_range("2014-12-31", periods=n_days)
    growth = np.linspace(1.0, 1.4, n_days)
    dip = np.where((np.arange(n_days) > 200) & (np.arange(n_days) < 260), 0.85, 1.0)
    return pd.DataFrame(
        {
            "★ Rotación rotacion-v1 (momentum 1-3-6-12)": growth,
            "Comprar y mantener SPY": growth * dip,
            "60/40 SPY/IEF": np.linspace(1.0, 1.25, n_days),
        },
        index=idx,
    ).rename_axis("fecha")


def test_etf_chart_helpers():
    curves = _etf_curves()
    long = charts.curves_long(curves, "valor")
    assert set(long["k"]) == {"s1", "s2", "s3"}
    assert long["valor"].iloc[0] == pytest.approx(1_000.0, rel=0.01)
    dd = charts.curves_long(curves, "caida")
    assert dd["valor"].min() == pytest.approx(-0.15, abs=0.01)  # el fondo semanal no se pierde
    for kind in ("valor", "caida"):
        spec = charts.etf_curves_chart(curves, kind, dark=kind == "caida").to_dict()
        assert spec["layer"]
    assert charts.etf_curves_chart(pd.DataFrame(), "valor") is None
    summary = q.curves_summary(curves)
    assert list(summary["Estrategia"]) == list(curves.columns)
    assert summary.loc[1, "Peor caída"] == pytest.approx(-0.15, abs=0.01)
    yearly = q.curves_yearly(curves)
    assert list(yearly.index) == [2015, 2016, 2017]  # la base (2014-12-31) no cuenta como año


def test_app_etf_tab_shows_verdict_recommendation_and_curves(monkeypatch, data_dirs):
    from tradingtool.config import EtfConfig

    data_dir, config_dir = data_dirs
    db_path = _make_db(data_dir, with_data=False)
    rules = EtfConfig().rules_hash()
    con = connect(db_path)
    con.execute(
        "INSERT INTO meta VALUES ('etf_veredicto', ?)",
        [json.dumps({"veredicto": "NO PASA", "reglas": rules, "fecha": "2026-10-08 10:00"})],
    )
    for as_of, w in (("2026-08-31", {"SPY": 1.0}), ("2026-09-30", {"SPY": 0.75, "IEF": 0.25})):
        con.execute(
            "INSERT INTO etf_recommendations (as_of_date, strategy_version, rules_hash, weights) "
            "VALUES (?, 'rotacion-v1', ?, ?)",
            [as_of, rules, json.dumps(w)],
        )
    con.close()
    (data_dir / "etf").mkdir(parents=True)
    _etf_curves().to_csv(data_dir / "etf" / "curvas_validacion.csv")
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert not at.exception
    text = _texts(at)
    assert "NO PASA" in text
    assert "Recomendación del mes" in text and "2026-09-30" in text
    frames = [e.value for e in at.dataframe]
    assert any("VUAA" in df.to_string() for df in frames)  # ticker UCITS sugerido
    assert len(at.get("vega_lite_chart")) >= 2  # crecimiento y caídas


def test_app_etf_tab_without_backtest_explains_next_step(monkeypatch, data_dirs):
    data_dir, config_dir = data_dirs
    _make_db(data_dir, with_data=False)
    at = _run_app(monkeypatch, data_dir, config_dir)
    assert not at.exception
    text = _texts(at)
    assert "uv run tt etf-backtest" in text
    assert "uv run tt etf-senal" in text
