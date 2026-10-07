"""Prueba de punta a punta de la CLI, sin red (precios desde CSV, filings insertados)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from tradingtool.cli import app
from tradingtool.db import connect
from tradingtool.insiders.store import upsert_filings
from tradingtool.models import Form4Filing, InsiderTransaction, ReportingOwner
from tradingtool.prices.sync import business_days

REPO = Path(__file__).parents[1]
runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # evita leer un .env real
    data = tmp_path / "data"
    monkeypatch.setenv("TT_DATA_DIR", str(data))
    monkeypatch.setenv("TT_CONFIG_DIR", str(REPO / "config"))
    monkeypatch.setenv("TT_PRICE_SOURCE", "csv")
    monkeypatch.setenv("TT_SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setenv("TT_BENCHMARK_TICKER", "SPY")
    return data


def _write_csv(dirpath: Path, ticker: str, start: date, end: date, price: float, step: float):
    dirpath.mkdir(parents=True, exist_ok=True)
    rows, p = [], price
    for d in business_days(start, end):
        rows.append(
            {"date": d, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 500_000}
        )
        p += step
    pd.DataFrame(rows).to_csv(dirpath / f"{ticker}.csv", index=False)


def _filing(fdate: date) -> Form4Filing:
    return Form4Filing(
        accession="0000000001-26-000777",
        source="fixture",
        form_type="4",
        filing_date=fdate,
        acceptance_ts=None,
        period_of_report=fdate - timedelta(days=1),
        issuer_cik="0000000100",
        issuer_name="Acme Corp",
        issuer_ticker="ACME",
        aff10b5one=False,
        owners=(ReportingOwner("0000000900", "Jane Doe", is_officer=True, officer_title="CEO"),),
        transactions=(
            InsiderTransaction(
                0,
                False,
                "Common Stock",
                fdate - timedelta(days=1),
                "P",
                5000.0,
                30.0,
                "A",
                50000.0,
                "D",
            ),
        ),
    )


def test_full_offline_pipeline(env):
    r = runner.invoke(app, ["iniciar"])
    assert r.exit_code == 0, r.output
    fdate = date(2026, 3, 10)
    con = connect(env / "tradingtool.duckdb")
    upsert_filings(con, [_filing(fdate)])
    con.close()
    _write_csv(env / "csv", "ACME", date(2025, 12, 1), date(2026, 6, 30), 30.0, 0.05)
    _write_csv(env / "csv", "SPY", date(2025, 12, 1), date(2026, 6, 30), 500.0, 0.1)

    r = runner.invoke(app, ["precios", "--desde", "2026-03-01", "--hasta", "2026-06-30"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["screener", "--fecha", "2026-03-10"])
    assert r.exit_code == 0, r.output
    assert "ACME" in r.output
    r = runner.invoke(app, ["resultados"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["informe", "--horizonte", "21"])
    assert r.exit_code == 0, r.output
    assert "Resultados a 21" in r.output and "sin" in r.output

    con = connect(env / "tradingtool.duckdb")
    sig = con.execute("select passed, origin, ticker from signals").fetchall()
    assert sig == [(True, "live", "ACME")]
    o = con.execute("select entry_date, status from outcomes where horizon_days = 21").fetchone()
    assert o[0] > fdate and o[1] == "complete"
    runs = con.execute("select kind, status from runs").fetchall()
    assert ("screen", "ok") in runs
    con.close()

    r = runner.invoke(app, ["revisar"])
    assert r.exit_code == 0, r.output
    assert "Filings de insiders" in r.output


def test_reserve_period_is_locked(env):
    runner.invoke(app, ["iniciar"])
    r = runner.invoke(app, ["historico", "--desde", "2023-01-01", "--hasta", "2023-12-31"])
    assert r.exit_code == 2
    assert "reserva" in r.output
    con = connect(env / "tradingtool.duckdb")
    assert con.execute("select count(*) from meta where key='reserva_abierta'").fetchone()[0] == 0
    con.close()
    r = runner.invoke(
        app, ["historico", "--desde", "2023-01-01", "--hasta", "2023-01-31", "--abrir-reserva"]
    )
    assert r.exit_code == 0, r.output
    con = connect(env / "tradingtool.duckdb")
    assert con.execute("select count(*) from meta where key='reserva_abierta'").fetchone()[0] == 1
    con.close()


def test_riesgo_command(env):
    r = runner.invoke(app, ["riesgo", "--entrada", "50", "--stop", "45", "--capital", "10000"])
    assert r.exit_code == 0, r.output
    assert "Acciones" in r.output


def test_sec_commands_require_user_agent(env, monkeypatch):
    monkeypatch.setenv("TT_SEC_USER_AGENT", "")
    runner.invoke(app, ["iniciar"])
    r = runner.invoke(app, ["sec-diario", "--dias", "1"])
    assert r.exit_code == 2
    assert "TT_SEC_USER_AGENT" in r.output
