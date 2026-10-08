"""Informe completo de la rotación de ETFs y veredicto pre-registrado (docs/ETF-ROTACION.md)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from tradingtool.config import EtfConfig, EtfVerdictCfg
from tradingtool.etf.engine import (
    CostModel,
    Rebalance,
    SimResult,
    build_schedule,
    month_ends,
    simulate,
)
from tradingtool.etf.metrics import (
    BootstrapCI,
    PeriodMetrics,
    bootstrap_vs,
    contributions_value,
    period_metrics,
)
from tradingtool.etf.strategies import (
    NEIGHBOR,
    PRINCIPAL,
    RISKY,
    StrategySpec,
    build_specs,
)

ETF_PERIODS: dict[str, tuple[date, date]] = {
    "diseno": (date(2000, 1, 1), date(2014, 12, 31)),
    "validacion": (date(2015, 1, 1), date(2024, 12, 31)),
    "reserva": (date(2025, 1, 1), date(2100, 1, 1)),
    "todo": (date(2000, 1, 1), date(2024, 12, 31)),
}
RESERVE_START = ETF_PERIODS["reserva"][0]
VALIDATION_END = ETF_PERIODS["validacion"][1]
ETF_RESERVE_FLAG = "etf_reserva_abierta"
ETF_VERDICT_KEY = "etf_veredicto"

PASS, FAIL, INSUFFICIENT = "PASA", "NO PASA", "INSUFICIENTE"
BENCHMARK_KEY, BALANCED_KEY = "benchmark", "balanced"


class EtfDataError(RuntimeError):
    """Faltan datos para correr el informe (mensaje en español, listo para mostrar)."""


@dataclass(frozen=True)
class Criterion:
    label: str
    passed: bool | None  # None = no se pudo evaluar (faltan datos)
    detail: str


@dataclass
class Row:
    spec: StrategySpec
    metrics: PeriodMetrics | None


@dataclass
class EtfReport:
    period: str
    rules_hash: str
    rows: list[Row]
    first_signal: pd.Timestamp
    verdict: str | None  # None si el periodo no da veredicto (solo informativo)
    criteria: list[Criterion] = field(default_factory=list)
    neighbors: list[tuple[StrategySpec, PeriodMetrics | None, bool | None]] = field(
        default_factory=list
    )
    boot_benchmark: BootstrapCI | None = None
    boot_balanced: BootstrapCI | None = None
    contributions: list[tuple[str, float]] = field(default_factory=list)
    contributed: float = 0.0
    cost_sensitivity: list[tuple[float, float | None, float | None]] = field(default_factory=list)
    yearly: pd.DataFrame = field(default_factory=pd.DataFrame)
    curves: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)

    def metrics(self, key: str) -> PeriodMetrics | None:
        for r in self.rows:
            if r.spec.key == key:
                return r.metrics
        return None

    def spec(self, key: str) -> StrategySpec | None:
        for r in self.rows:
            if r.spec.key == key:
                return r.spec
        return None


# ----------------------------------------------------------------------------- veredicto


def _fmt_pct(x: float) -> str:
    return "-" if x is None or math.isnan(x) else f"{x:+.1%}"


def _ok(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and not math.isnan(a) and not math.isnan(b)


def meets_core(p: PeriodMetrics | None, b: PeriodMetrics | None, ratio: float) -> bool | None:
    """Criterios 1 y 2: Sharpe ≥ el del comparador y peor caída ≤ ratio x la del comparador."""
    if p is None or b is None or not _ok(p.sharpe, b.sharpe) or not _ok(p.max_dd, b.max_dd):
        return None
    return p.sharpe >= b.sharpe and abs(p.max_dd) <= ratio * abs(b.max_dd)


def evaluate_verdict(
    principal: PeriodMetrics | None,
    benchmark: PeriodMetrics | None,
    balanced: PeriodMetrics | None,
    neighbors: list[PeriodMetrics | None],
    design_principal: PeriodMetrics | None,
    design_benchmark: PeriodMetrics | None,
    vcfg: EtfVerdictCfg,
    benchmark_name: str = "SPY",
    balanced_name: str = "60/40",
) -> tuple[str, list[Criterion]]:
    crit: list[Criterion] = []
    months = principal.months if principal else 0
    enough = months >= vcfg.min_validation_months
    crit.append(
        Criterion(
            f"Datos suficientes (≥ {vcfg.min_validation_months} meses)",
            enough,
            f"{months} meses de validación con datos",
        )
    )
    p, b, bal = principal, benchmark, balanced
    if p is None or b is None:
        crit.append(Criterion("Comparación con el comparador", None, "faltan datos"))
        return INSUFFICIENT, crit

    c1 = p.sharpe >= b.sharpe if _ok(p.sharpe, b.sharpe) else None
    crit.append(
        Criterion(
            f"1. Mejor riesgo/retorno que {benchmark_name} (Sharpe ≥)",
            c1,
            f"Sharpe {p.sharpe:.2f} vs {b.sharpe:.2f}",
        )
    )
    limit = vcfg.max_drawdown_ratio * abs(b.max_dd)
    c2 = abs(p.max_dd) <= limit if _ok(p.max_dd, b.max_dd) else None
    crit.append(
        Criterion(
            f"2. Caídas mucho menores (peor caída ≤ {vcfg.max_drawdown_ratio:.0%} de la de "
            f"{benchmark_name})",
            c2,
            f"peor caída {_fmt_pct(p.max_dd)} vs {_fmt_pct(b.max_dd)} (límite {-limit:+.1%})",
        )
    )
    c3 = p.cagr >= bal.cagr if bal is not None and _ok(p.cagr, bal.cagr) else None
    crit.append(
        Criterion(
            f"3. Rinde al menos como el {balanced_name}",
            c3,
            f"{_fmt_pct(p.cagr)}/año vs {_fmt_pct(bal.cagr) if bal else '-'}/año",
        )
    )
    flags = [meets_core(n, b, vcfg.max_drawdown_ratio) for n in neighbors]
    n_ok = sum(1 for f in flags if f)
    need = min(vcfg.min_neighbors_pass, len(neighbors))
    c4 = (n_ok >= need) if neighbors and all(f is not None for f in flags) else None
    crit.append(
        Criterion(
            "4. Robusta (variantes vecinas que cumplen 1 y 2)",
            c4,
            f"{n_ok} de {len(neighbors)} (mínimo {need})",
        )
    )
    c5 = meets_core(design_principal, design_benchmark, vcfg.max_drawdown_ratio)
    if design_principal is not None and design_benchmark is not None:
        d = design_principal
        db = design_benchmark
        detail = (
            f"diseño {d.monthly.index[0]} → {d.monthly.index[-1]}: Sharpe {d.sharpe:.2f} vs "
            f"{db.sharpe:.2f}, "
            f"peor caída {_fmt_pct(d.max_dd)} vs {_fmt_pct(db.max_dd)}"
        )
    else:
        detail = "sin datos del periodo de diseño"
    crit.append(Criterion("5. Consistente en el periodo de diseño (1 y 2)", c5, detail))

    results = [c.passed for c in crit]
    if not enough or any(r is None for r in results):
        return INSUFFICIENT, crit
    return (PASS if all(results) else FAIL), crit


# ----------------------------------------------------------------------------- informe


def _schedules(
    prices: pd.DataFrame, specs: list[StrategySpec]
) -> tuple[dict[str, list[Rebalance]], list[str]]:
    ends = month_ends(pd.DatetimeIndex(prices.index))
    out: dict[str, list[Rebalance]] = {}
    warnings: list[str] = []
    for s in specs:
        missing = [a for a in s.assets if a not in prices.columns]
        if missing:
            warnings.append(f"{s.name}: faltan datos de {', '.join(missing)}; no se incluye")
            continue
        sched = build_schedule(prices, s, ends)
        if not sched:
            warnings.append(f"{s.name}: no hay historia suficiente; no se incluye")
            continue
        out[s.key] = sched
    return out, warnings


def _period_bounds(period: str, prices: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    start, end = ETF_PERIODS[period]
    last = pd.Timestamp(prices.index[-1])
    return pd.Timestamp(start), min(pd.Timestamp(end), last)


def run_report(prices: pd.DataFrame, cfg: EtfConfig, period: str = "validacion") -> EtfReport:
    """Simula todas las estrategias desde un inicio común y arma el informe del periodo.

    ``prices``: cierres ajustados diarios (columnas = tickers, incluido el efectivo). Quien
    llama es responsable de recortar los datos al final de la validación si la reserva
    está cerrada.
    """
    if period not in ETF_PERIODS:
        raise ValueError(f"periodo desconocido: {period}")
    if prices.empty or cfg.cash_asset not in prices.columns:
        raise EtfDataError(
            "No hay precios de ETFs o falta el efectivo (FRED). Ejecuta: uv run tt etf-precios"
        )
    specs = build_specs(cfg)
    schedules, warnings = _schedules(prices, specs)
    for key in (PRINCIPAL, BENCHMARK_KEY, BALANCED_KEY):
        if key not in schedules:
            name = next(s.name for s in specs if s.key == key)
            raise EtfDataError(
                f"No hay datos suficientes para «{name}». Revisa: uv run tt etf-precios"
            )
    # Inicio común: el primer mes en que TODAS las estrategias incluidas tienen señal.
    first_signal = max(s[0].signal_date for s in schedules.values())
    schedules = {k: [r for r in v if r.signal_date >= first_signal] for k, v in schedules.items()}
    costs = CostModel.from_config(cfg.costs)
    band = cfg.rebalance_band
    sims: dict[str, SimResult] = {
        k: simulate(prices, v, costs, band, key=k) for k, v in schedules.items() if v
    }
    for k, sim in sims.items():
        if sim.nan_days:
            warnings.append(f"{k}: {sim.nan_days} días con precio faltante (retorno 0)")
    cash = prices[cfg.cash_asset]
    p_start, p_end = _period_bounds(period, prices)
    if p_end < p_start:
        raise EtfDataError(f"No hay datos del periodo «{period}».")

    def metrics_for(key: str, start=p_start, end=p_end) -> PeriodMetrics | None:
        sim = sims.get(key)
        return period_metrics(sim, cash, start, end, RISKY) if sim else None

    rows = [Row(s, metrics_for(s.key)) for s in specs if s.key in sims]
    rep = EtfReport(
        period=period,
        rules_hash=cfg.rules_hash(),
        rows=rows,
        first_signal=first_signal,
        verdict=None,
        warnings=warnings,
    )
    principal = rep.metrics(PRINCIPAL)
    bench = rep.metrics(BENCHMARK_KEY)
    balanced = rep.metrics(BALANCED_KEY)
    neighbor_specs = [s for s in specs if s.role == NEIGHBOR and s.key in sims]
    ratio = cfg.verdict.max_drawdown_ratio
    rep.neighbors = [
        (s, rep.metrics(s.key), meets_core(rep.metrics(s.key), bench, ratio))
        for s in neighbor_specs
    ]
    if period in ("validacion", "reserva"):
        d_start, d_end = ETF_PERIODS["diseno"]
        d_start, d_end = pd.Timestamp(d_start), pd.Timestamp(d_end)
        design_p = metrics_for(PRINCIPAL, d_start, d_end) if period == "validacion" else None
        design_b = metrics_for(BENCHMARK_KEY, d_start, d_end) if period == "validacion" else None
        vcfg = cfg.verdict
        if period == "reserva":  # confirmación: mismos criterios 1-3, sin mínimo de meses
            vcfg = vcfg.model_copy(update={"min_validation_months": 1})
        verdict, criteria = evaluate_verdict(
            principal,
            bench,
            balanced,
            [m for _, m, _ in rep.neighbors],
            design_p,
            design_b,
            vcfg,
            benchmark_name=cfg.benchmark,
            balanced_name=rep.spec(BALANCED_KEY).name if rep.spec(BALANCED_KEY) else "60/40",
        )
        if period == "reserva":
            criteria = [c for c in criteria if not c.label.startswith(("4.", "5."))]
            core = [c.passed for c in criteria[1:]]
            verdict = (
                INSUFFICIENT if any(x is None for x in core) else (PASS if all(core) else FAIL)
            )
        rep.verdict, rep.criteria = verdict, criteria

    if principal is not None:
        vc = cfg.verdict
        if bench is not None:
            rep.boot_benchmark = bootstrap_vs(
                principal.monthly,
                bench.monthly,
                principal.cash_monthly,
                vc.bootstrap_samples,
                vc.bootstrap_block_months,
                vc.seed,
            )
        if balanced is not None:
            rep.boot_balanced = bootstrap_vs(
                principal.monthly,
                balanced.monthly,
                principal.cash_monthly,
                vc.bootstrap_samples,
                vc.bootstrap_block_months,
                vc.seed,
            )
        amount = cfg.monthly_contribution_usd
        rep.contributed = amount * principal.months
        for key in (PRINCIPAL, BENCHMARK_KEY, BALANCED_KEY):
            m = rep.metrics(key)
            spec = rep.spec(key)
            if m is not None and spec is not None:
                rep.contributions.append((spec.name, contributions_value(m.monthly, amount)))
        for capital in cfg.costs.sensitivity_capitals_usd:
            cm = CostModel.from_config(cfg.costs, capital_usd=capital)
            out: list[float | None] = []
            for key in (PRINCIPAL, BENCHMARK_KEY):
                sim = simulate(prices, schedules[key], cm, band, key=key)
                m = period_metrics(sim, cash, p_start, p_end, RISKY)
                out.append(m.cagr if m else None)
            rep.cost_sensitivity.append((capital, out[0], out[1]))

    yearly = {}
    curves = {}
    for key in (PRINCIPAL, BENCHMARK_KEY, BALANCED_KEY):
        m, spec = rep.metrics(key), rep.spec(key)
        if m is not None and spec is not None:
            yearly[spec.name] = m.yearly
            curves[spec.name] = m.curve
    if yearly:
        y = pd.DataFrame(yearly)
        y.index = [p.year for p in y.index]
        rep.yearly = y
    if curves:
        rep.curves = pd.DataFrame(curves).ffill()
    return rep
