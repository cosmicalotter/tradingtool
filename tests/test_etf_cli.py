"""Comandos etf-* de punta a punta, sin red (ETFs desde CSV y FRED simulado)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from etf_synth import random_prices
from tradingtool.cli import app
from tradingtool.db import connect

REPO = Path(__file__).parents[1]
runner = CliRunner()
FRED = "observation_date,DTB3\n" + "".join(
    f"{d:%Y-%m-%d},{2.0 + (i % 7) / 10:.2f}\n"
    for i, d in enumerate(pd.bdate_range("2000-01-03", "2026-10-06"))
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # evita leer un .env real
    data = tmp_path / "data"
    monkeypatch.setenv("TT_DATA_DIR", str(data))
    monkeypatch.setenv("TT_CONFIG_DIR", str(REPO / "config"))
    monkeypatch.setenv("TT_ETF_PRICE_SOURCE", "csv")
    monkeypatch.setenv("TT_SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setattr("tradingtool.etf.data.fetch_fred_csv", lambda **kw: FRED)
    csv_dir = data / "csv"
    csv_dir.mkdir(parents=True)
    prices = random_prices(end="2026-10-06").drop(columns=["CASH"])
    for t in prices.columns:
        s = prices[t].dropna()
        df = s.to_frame("close").assign(open=s, high=s, low=s, volume=1e6)
        df.index.name = "date"
        df.reset_index().to_csv(csv_dir / f"{t}.csv", index=False)
    return data


def test_etf_commands_end_to_end(env):
    r = runner.invoke(app, ["etf-precios"])
    assert r.exit_code == 0, r.output
    assert "7 de 7 actualizados" in r.output and "CASH" in r.output

    r = runner.invoke(app, ["etf-backtest"])
    assert r.exit_code == 0, r.output
    assert "Veredicto:" in r.output and "Robustez" in r.output
    assert (env / "etf" / "curvas_validacion.csv").exists()
    con = connect(env / "tradingtool.duckdb")
    try:
        saved = json.loads(
            con.execute("SELECT value FROM meta WHERE key = 'etf_veredicto'").fetchone()[0]
        )
    finally:
        con.close()
    assert saved["veredicto"] in ("PASA", "NO PASA", "INSUFICIENTE")

    r = runner.invoke(app, ["etf-backtest", "--periodo", "diseno"])
    assert r.exit_code == 0 and "informativo" in r.output

    # la reserva está bloqueada hasta abrirla explícitamente (y queda registrado)
    r = runner.invoke(app, ["etf-backtest", "--periodo", "reserva"])
    assert r.exit_code == 2 and "bloqueada" in r.output
    r = runner.invoke(app, ["etf-backtest", "--periodo", "reserva", "--abrir-reserva"])
    assert r.exit_code == 0, r.output
    assert "Reserva abierta" in r.output

    r = runner.invoke(app, ["etf-senal", "--sin-descargar", "--capital", "2000"])
    assert r.exit_code == 0, r.output
    assert "Cartera objetivo" in r.output and "NO envía órdenes" in r.output
    assert "seguimiento en papel" in r.output
    con = connect(env / "tradingtool.duckdb")
    try:
        n = con.execute("SELECT count(*) FROM etf_recommendations").fetchone()[0]
        flag = con.execute("SELECT 1 FROM meta WHERE key = 'etf_reserva_abierta'").fetchone()
    finally:
        con.close()
    assert n == 1 and flag is not None


def test_etf_backtest_without_data_explains_next_step(env):
    r = runner.invoke(app, ["etf-backtest"])
    assert r.exit_code == 1
    assert "etf-precios" in r.output


def test_missing_tiingo_key_is_explained(env, monkeypatch):
    monkeypatch.setenv("TT_ETF_PRICE_SOURCE", "tiingo")
    monkeypatch.delenv("TT_TIINGO_API_KEY", raising=False)
    r = runner.invoke(app, ["etf-precios"])
    assert r.exit_code == 2
    assert "tiingo.com" in r.output
