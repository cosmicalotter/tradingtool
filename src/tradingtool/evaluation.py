"""Evaluación estadística pre-registrada de la estrategia (ver docs/EVALUACION.md).

Pregunta principal: ¿las señales que pasan los filtros generan retorno en exceso sobre el
benchmark, NETO de costos estimados, a 63 días hábiles, de forma estadísticamente creíble?

Métodos (elegidos antes de ver resultados):
- Retorno neto = retorno - costo ida y vuelta estimado (modelo de costos IBKR + slippage por
  liquidez, para una posición de referencia).
- Exceso neto = retorno neto - retorno del benchmark en la misma ventana.
- Estadístico t "por meses": se promedia el exceso de las señales que entran en cada mes y se
  calcula el t de esos promedios mensuales (las señales del mismo mes no son independientes).
- Intervalo de confianza 90% por bootstrap de meses.
- Desglose por año y subgrupos pre-registrados (oportunista vs. no clasificable, cluster vs.
  individual, con cargo ejecutivo vs. solo director).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date

import duckdb
import numpy as np
import pandas as pd

from tradingtool.config import AppConfig
from tradingtool.risk.costs import estimate_roundtrip_cost

REFERENCE_POSITION_USD = 1_000.0  # tamaño de referencia para estimar costos por señal


def load_outcomes(
    con: duckdb.DuckDBPyConnection,
    horizon: int,
    origin: str = "backtest",
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    sql = """
        SELECT s.signal_id, s.as_of_date, s.ticker, s.passed, s.features, s.score,
               o.entry_date, o.entry_price, o.ret, o.bench_ret, o.excess_ret, o.bench2_ret,
               o.mae, o.mfe, o.status
        FROM outcomes o JOIN signals s USING (signal_id)
        WHERE o.horizon_days = ? AND s.origin = ?
    """
    params: list[object] = [horizon, origin]
    if start is not None:
        sql += " AND s.as_of_date >= ?"
        params.append(start)
    if end is not None:
        sql += " AND s.as_of_date <= ?"
        params.append(end)
    df = con.execute(sql, params).df()
    if df.empty:
        return df
    feats = df["features"].map(lambda x: json.loads(x) if isinstance(x, str) else (x or {}))
    df["adv20"] = feats.map(lambda f: f.get("adv20"))
    df["opportunistic"] = feats.map(lambda f: (f.get("opportunistic_count") or 0) > 0)
    df["cluster"] = feats.map(lambda f: (f.get("n_insiders_window") or 0) >= 2)
    df["officer"] = feats.map(lambda f: bool(f.get("any_officer")))
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.date
    return df.drop(columns=["features"])


def add_costs(
    df: pd.DataFrame, cfg: AppConfig, position_usd: float = REFERENCE_POSITION_USD
) -> pd.DataFrame:
    """Agrega costo estimado ida y vuelta (fracción) y retornos netos."""
    if df.empty:
        return df.assign(cost=[], net_ret=[], net_excess=[])
    costs = []
    for price, adv in zip(df["entry_price"], df["adv20"], strict=True):
        shares = max(int(position_usd // price), 1) if price and price > 0 else 1
        _, pct = estimate_roundtrip_cost(
            cfg.costs, shares, float(price), adv_usd=None if pd.isna(adv) else float(adv)
        )
        costs.append(pct)
    out = df.copy()
    out["cost"] = costs
    out["net_ret"] = out["ret"] - out["cost"]
    out["net_excess"] = out["net_ret"] - out["bench_ret"]
    if "bench2_ret" in out.columns:
        out["net_excess2"] = out["net_ret"] - out["bench2_ret"]
    return out


@dataclass
class GroupStats:
    name: str
    n_signals: int
    n_months: int
    mean_net_excess: float | None
    median_net_excess: float | None
    hit_rate: float | None
    t_stat_monthly: float | None
    ci90_low: float | None
    ci90_high: float | None
    mean_cost: float | None
    truncated: int

    def as_row(self) -> dict[str, object]:
        return self.__dict__.copy()


def _monthly_means(df: pd.DataFrame, col: str) -> pd.Series:
    return df.groupby(df["entry_date"].dt.to_period("M"))[col].mean()


def group_stats(name: str, df: pd.DataFrame, n_boot: int = 2000, seed: int = 7) -> GroupStats:
    d = df.dropna(subset=["net_excess"])
    if d.empty:
        return GroupStats(name, 0, 0, None, None, None, None, None, None, None, 0)
    monthly = _monthly_means(d, "net_excess").to_numpy()
    n_m = len(monthly)
    t = None
    if n_m >= 3 and np.std(monthly, ddof=1) > 0:
        t = float(np.mean(monthly) / (np.std(monthly, ddof=1) / math.sqrt(n_m)))
    lo = hi = None
    if n_m >= 3:
        rng = np.random.default_rng(seed)
        boots = rng.choice(monthly, size=(n_boot, n_m), replace=True).mean(axis=1)
        lo, hi = float(np.quantile(boots, 0.05)), float(np.quantile(boots, 0.95))
    return GroupStats(
        name=name,
        n_signals=len(d),
        n_months=n_m,
        mean_net_excess=float(d["net_excess"].mean()),
        median_net_excess=float(d["net_excess"].median()),
        hit_rate=float((d["net_excess"] > 0).mean()),
        t_stat_monthly=t,
        ci90_low=lo,
        ci90_high=hi,
        mean_cost=float(d["cost"].mean()),
        truncated=int((d["status"] == "truncated").sum()),
    )


@dataclass
class EvaluationReport:
    horizon: int
    origin: str
    groups: list[GroupStats] = field(default_factory=list)
    by_year: pd.DataFrame = field(default_factory=pd.DataFrame)
    verdict: str = ""

    def groups_frame(self) -> pd.DataFrame:
        return pd.DataFrame([g.as_row() for g in self.groups])


def evaluate(
    con: duckdb.DuckDBPyConnection,
    cfg: AppConfig,
    horizon: int = 63,
    origin: str = "backtest",
    start: date | None = None,
    end: date | None = None,
) -> EvaluationReport:
    rep = EvaluationReport(horizon=horizon, origin=origin)
    df = load_outcomes(con, horizon, origin, start, end)
    if df.empty:
        rep.verdict = "Sin resultados para evaluar todavía."
        return rep
    df = add_costs(df, cfg)
    passed, blocked = df[df["passed"]], df[~df["passed"]]
    rep.groups = [
        group_stats("pasan filtros (principal)", passed),
        group_stats("bloqueadas (contrafactual)", blocked),
        group_stats("pasan · con oportunista", passed[passed["opportunistic"]]),
        group_stats("pasan · sin oportunista", passed[~passed["opportunistic"]]),
        group_stats("pasan · cluster (≥2 insiders)", passed[passed["cluster"]]),
        group_stats("pasan · individual", passed[~passed["cluster"]]),
        group_stats("pasan · con ejecutivo", passed[passed["officer"]]),
        group_stats("pasan · solo directores", passed[~passed["officer"]]),
    ]
    if "net_excess2" in passed.columns and passed["net_excess2"].notna().any():
        # Secundario: exceso contra empresas pequeñas (IWM), para separar "prima de tamaño".
        rep.groups.append(
            group_stats(
                "pasan · exceso vs IWM (tamaño)",
                passed.drop(columns=["net_excess"]).rename(columns={"net_excess2": "net_excess"}),
            )
        )
    if not passed.empty:
        py = passed.assign(year=passed["entry_date"].dt.year)
        rep.by_year = (
            py.groupby("year")
            .agg(
                n=("net_excess", "size"),
                exceso_neto_medio=("net_excess", "mean"),
                aciertos=("net_excess", lambda s: float((s > 0).mean())),
            )
            .reset_index()
        )
    rep.verdict = verdict(rep.groups[0], rep.by_year)
    return rep


def verdict(main: GroupStats, by_year: pd.DataFrame) -> str:
    """Criterio pre-registrado (resumen). No reemplaza el juicio, lo disciplina."""
    if main.n_signals < 200 or main.n_months < 24:
        return (
            f"INSUFICIENTE: {main.n_signals} señales en {main.n_months} meses "
            "(se exigen ≥200 señales y ≥24 meses)."
        )
    checks = {
        "exceso neto medio > 0": (main.mean_net_excess or 0) > 0,
        "t mensual ≥ 2": (main.t_stat_monthly or 0) >= 2,
        "IC 90% por encima de 0": (main.ci90_low or -1) > 0,
        "positivo en ≥ 60% de los años": (
            not by_year.empty and float((by_year["exceso_neto_medio"] > 0).mean()) >= 0.6
        ),
    }
    failed = [k for k, ok in checks.items() if not ok]
    if not failed:
        return "PASA el criterio principal pre-registrado (siguiente paso: paper trading)."
    return "NO PASA: falla " + "; ".join(failed) + "."
