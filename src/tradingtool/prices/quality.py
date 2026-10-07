"""Chequeos de calidad de barras diarias. "La calidad de datos pesa más que el modelo."

Cada problema se reporta (no se corrige en silencio). Severidades:
- error: el dato es imposible (precio <= 0, high < low, cierre fuera del rango del día).
- warning: sospechoso (salto > 50% en un día: posible split mal ajustado; hueco largo).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class QualityIssue:
    ticker: str
    date: str | None
    severity: str  # "error" | "warning"
    kind: str
    detail: str


def check_bars(
    bars: pd.DataFrame, max_daily_move: float = 0.5, max_gap_business_days: int = 7
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if bars is None or bars.empty:
        return issues
    df = bars.sort_values(["ticker", "date"]).reset_index(drop=True)
    for ticker, g in df.groupby("ticker"):
        g = g.reset_index(drop=True)
        t = str(ticker)
        dup = g[g.duplicated("date", keep=False)]
        for d in sorted(set(dup["date"])):
            issues.append(QualityIssue(t, str(d), "error", "fecha_duplicada", "fecha repetida"))
        price_cols = ["open", "high", "low", "close"]
        nonpos = g[(g[price_cols] <= 0).any(axis=1)]
        for d in nonpos["date"]:
            issues.append(QualityIssue(t, str(d), "error", "precio_no_positivo", "precio <= 0"))
        bad_range = g[g["high"] < g["low"]]
        for d in bad_range["date"]:
            issues.append(QualityIssue(t, str(d), "error", "rango_invertido", "high < low"))
        tol = 1e-6
        outside = g[(g["close"] > g["high"] * (1 + tol)) | (g["close"] < g["low"] * (1 - tol))]
        for d in outside["date"]:
            issues.append(
                QualityIssue(
                    t, str(d), "error", "cierre_fuera_de_rango", "cierre fuera de high/low"
                )
            )
        neg_vol = g[g["volume"] < 0]
        for d in neg_vol["date"]:
            issues.append(QualityIssue(t, str(d), "error", "volumen_negativo", "volumen < 0"))
        rets = g["close"].pct_change()
        jumps = g[rets.abs() > max_daily_move]
        for d, r in zip(jumps["date"], rets[jumps.index], strict=True):
            issues.append(
                QualityIssue(
                    t,
                    str(d),
                    "warning",
                    "salto_extremo",
                    f"movimiento de {r:+.0%} en un día (¿split sin ajustar o noticia?)",
                )
            )
        dates = pd.to_datetime(g["date"]).to_numpy(dtype="datetime64[D]")
        if len(dates) > 1:
            gaps = np.busday_count(dates[:-1], dates[1:])
            for i in np.where(gaps > max_gap_business_days)[0]:
                issues.append(
                    QualityIssue(
                        t,
                        str(g["date"].iloc[i + 1]),
                        "warning",
                        "hueco",
                        f"{int(gaps[i])} días hábiles sin datos",
                    )
                )
    return issues


def summarize(issues: list[QualityIssue]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in issues:
        out[f"{i.severity}:{i.kind}"] = out.get(f"{i.severity}:{i.kind}", 0) + 1
    return out
