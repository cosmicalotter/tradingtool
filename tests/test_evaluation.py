from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tradingtool.config import AppConfig
from tradingtool.evaluation import add_costs, evaluate, group_stats, verdict


def _seed(con, n_months=30, per_month=10, excess=0.03, noise=0.02, passed=True, seed=1, prefix="s"):
    rng = np.random.default_rng(seed)
    sig_rows, out_rows = [], []
    for m in range(n_months):
        base = date(2019, 1, 15) + timedelta(days=30 * m)
        for k in range(per_month):
            sid = f"{prefix}{m}-{k}"
            feats = {
                "adv20": 50e6,
                "opportunistic_count": k % 2,
                "n_insiders_window": 1 + k % 3,
                "any_officer": k % 2 == 0,
            }
            sig_rows.append(
                {
                    "signal_id": sid,
                    "strategy_version": "v",
                    "config_hash": "h",
                    "as_of_date": base,
                    "ticker": "T",
                    "issuer_cik": "1",
                    "passed": passed,
                    "origin": "backtest",
                    "features": json.dumps(feats),
                }
            )
            bench = 0.01
            ret = bench + excess + rng.normal(0, noise)
            out_rows.append(
                {
                    "signal_id": sid,
                    "horizon_days": 63,
                    "entry_date": base + timedelta(days=1),
                    "entry_price": 20.0,
                    "ret": ret,
                    "bench_ret": bench,
                    "excess_ret": ret - bench,
                    "status": "complete",
                }
            )
    con.register("s_df", pd.DataFrame(sig_rows))
    con.execute(
        "INSERT INTO signals (signal_id, strategy_version, config_hash, as_of_date, ticker,"
        " issuer_cik, passed, origin, features) SELECT signal_id, strategy_version,"
        " config_hash, as_of_date, ticker, issuer_cik, passed, origin, features FROM s_df"
    )
    con.register("o_df", pd.DataFrame(out_rows))
    con.execute(
        "INSERT INTO outcomes (signal_id, horizon_days, entry_date, entry_price, ret,"
        " bench_ret, excess_ret, status) SELECT * FROM o_df"
    )


def test_strong_edge_passes_verdict(con):
    _seed(con, excess=0.03)
    rep = evaluate(con, AppConfig(), 63, "backtest")
    main = rep.groups[0]
    assert main.n_signals == 300 and main.n_months == 30
    assert main.mean_net_excess == pytest.approx(0.03 - main.mean_cost, abs=0.005)
    assert main.t_stat_monthly > 2 and main.ci90_low > 0
    assert rep.verdict.startswith("PASA")
    assert len(rep.by_year) >= 2


def test_no_edge_fails_verdict(con):
    _seed(con, excess=0.0, noise=0.05)
    rep = evaluate(con, AppConfig(), 63, "backtest")
    assert rep.verdict.startswith("NO PASA")


def test_small_sample_is_insufficient(con):
    _seed(con, n_months=5, per_month=5)
    rep = evaluate(con, AppConfig(), 63, "backtest")
    assert rep.verdict.startswith("INSUFICIENTE")


def test_blocked_group_and_origin_filter(con):
    _seed(con, excess=0.03)
    _seed(con, excess=-0.01, passed=False, seed=2, prefix="b")
    rep = evaluate(con, AppConfig(), 63, "backtest")
    names = {g.name: g for g in rep.groups}
    assert names["bloqueadas (contrafactual)"].n_signals == 300
    assert names["bloqueadas (contrafactual)"].mean_net_excess < 0
    assert evaluate(con, AppConfig(), 63, "live").verdict.startswith("Sin resultados")


def test_costs_reduce_returns():
    df = pd.DataFrame(
        {
            "entry_price": [10.0, 10.0],
            "adv20": [5e5, 1e9],
            "ret": [0.05, 0.05],
            "bench_ret": [0.0, 0.0],
        }
    )
    out = add_costs(df, AppConfig())
    assert (out["net_ret"] < out["ret"]).all()
    assert out.loc[0, "cost"] > out.loc[1, "cost"]  # ilíquida cuesta más


def test_group_stats_empty_and_verdict_years():
    g = group_stats("x", pd.DataFrame(columns=["net_excess", "entry_date", "cost", "status"]))
    assert g.n_signals == 0
    years = pd.DataFrame({"exceso_neto_medio": [0.01, -0.02, -0.01]})
    from tradingtool.evaluation import GroupStats

    strong = GroupStats("m", 500, 40, 0.02, 0.01, 0.6, 3.0, 0.01, 0.03, 0.005, 0)
    assert "años" in verdict(strong, years)
