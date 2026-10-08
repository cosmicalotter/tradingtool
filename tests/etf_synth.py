"""Precios sintéticos para probar la rotación de ETFs (sin red)."""

from __future__ import annotations

import numpy as np
import pandas as pd

INCEPTION = {
    "EEM": "2003-04-14",
    "AGG": "2003-09-29",
    "VNQ": "2004-09-29",
    "DBC": "2006-02-06",
}
RANDOM_SPEC = {
    "SPY": (0.08, 0.18),
    "EFA": (0.05, 0.20),
    "EEM": (0.06, 0.25),
    "IEF": (0.03, 0.07),
    "AGG": (0.03, 0.05),
    "VNQ": (0.07, 0.25),
    "DBC": (0.01, 0.20),
}


def random_prices(
    start: str = "2003-01-02", end: str = "2024-12-31", seed: int = 1, cash_rate: float = 0.02
) -> pd.DataFrame:
    """Caminatas aleatorias (sin momentum por construcción) + efectivo al ``cash_rate``."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    data = {}
    for t, (mu, sig) in RANDOM_SPEC.items():
        r = rng.normal(mu / 252, sig / np.sqrt(252), len(dates))
        s = pd.Series(100 * np.exp(np.cumsum(r)), index=dates)
        if t in INCEPTION:
            s[s.index < INCEPTION[t]] = np.nan
        data[t] = s
    data["CASH"] = pd.Series(np.cumprod(np.full(len(dates), 1 + cash_rate / 252)), index=dates)
    return pd.DataFrame(data)


def regime_prices(seed: int = 2) -> pd.DataFrame:
    """Mercado con tendencias claras: alza, una caída LENTA de 14 meses (2008) y recuperación.

    Acciones: +12%/año → -45%/año (2008-01 a 2009-02) → +15%/año. Bonos del Tesoro suben más en
    la crisis. Ruido bajo. Sirve para verificar que la regla hace lo que dice (no es evidencia
    sobre mercados reales).
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2003-01-02", "2016-12-30")
    crash = (dates >= "2008-01-01") & (dates < "2009-03-01")
    after = dates >= "2009-03-01"

    def path(up: float, down: float, rec: float, noise: float) -> np.ndarray:
        mu = np.where(crash, down, np.where(after, rec, up)) / 252
        r = mu + rng.normal(0, noise / np.sqrt(252), len(dates))
        return 100 * np.exp(np.cumsum(r))

    data = {
        "SPY": path(0.12, -0.45, 0.15, 0.05),
        "EFA": path(0.10, -0.50, 0.10, 0.05),
        "EEM": path(0.14, -0.60, 0.12, 0.06),
        "IEF": path(0.03, 0.10, 0.02, 0.02),
        "AGG": path(0.03, 0.05, 0.02, 0.02),
        "VNQ": path(0.10, -0.60, 0.15, 0.06),
        "DBC": path(0.05, -0.40, 0.05, 0.06),
    }
    df = pd.DataFrame(data, index=dates)
    for t, d in INCEPTION.items():
        df.loc[df.index < d, t] = np.nan
    df["CASH"] = np.cumprod(np.full(len(dates), 1 + 0.02 / 252))
    return df


def monthly_frame(rows: dict[str, list[float]], end: str = "2020-12-31") -> pd.DataFrame:
    """Cierres mensuales a mano (la última fila es el mes de la señal)."""
    n = len(next(iter(rows.values())))
    idx = pd.date_range(end=end, periods=n, freq="ME")
    return pd.DataFrame(rows, index=idx, dtype=float)
