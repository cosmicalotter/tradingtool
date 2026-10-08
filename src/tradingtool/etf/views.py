"""Salida en la terminal (rich) del informe y de la señal mensual de la rotación de ETFs."""

from __future__ import annotations

import math

from rich.console import Console
from rich.table import Table

from tradingtool.config import EtfConfig
from tradingtool.etf.live import LiveSignal
from tradingtool.etf.report import (
    BALANCED_KEY,
    BENCHMARK_KEY,
    FAIL,
    PASS,
    EtfReport,
)
from tradingtool.etf.strategies import LABELS, LITERATURE, PRINCIPAL, REFERENCE

PERIOD_NAMES = {
    "diseno": "diseño",
    "validacion": "validación",
    "reserva": "reserva",
    "todo": "todo (diseño + validación)",
}


def pct(x: float | None, signed: bool = True) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def usd(x: float) -> str:
    return f"US${x:,.0f}".replace(",", ".")


def num(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x:.{digits}f}"


def _mark(passed: bool | None) -> str:
    return {True: "[green]✓[/green]", False: "[red]✗[/red]"}.get(passed, "[yellow]?[/yellow]")


def print_report(console: Console, rep: EtfReport, cfg: EtfConfig, csv_path: str = "") -> None:
    p = rep.metrics(PRINCIPAL)
    title = f"Rotación de ETFs · {PERIOD_NAMES.get(rep.period, rep.period)}"
    if p is not None and len(p.monthly):
        title += f" ({p.monthly.index[0]} → {p.monthly.index[-1]})"
    console.print(
        f"[bold]{title}[/bold] · reglas {rep.rules_hash} · costos con una cuenta de "
        f"{usd(cfg.costs.capital_usd)}"
    )
    t = Table(title="Resumen (neto de costos)")
    for c in (
        "Estrategia",
        "Rinde/año",
        "Volatilidad",
        "Sharpe",
        "Peor caída",
        "Peor año",
        "Meses +",
        "Órdenes/año",
        "Costo/año",
        "En riesgo",
    ):
        t.add_column(c, justify="left" if c == "Estrategia" else "right")
    for row in rep.rows:
        if row.spec.role not in (PRINCIPAL, LITERATURE, REFERENCE) or row.metrics is None:
            continue
        m = row.metrics
        name = f"[bold]{row.spec.name}[/bold]" if row.spec.role == PRINCIPAL else row.spec.name
        t.add_row(
            name,
            pct(m.cagr),
            pct(m.vol, signed=False),
            num(m.sharpe),
            pct(m.max_dd),
            pct(m.worst_year),
            pct(m.pct_months_up, signed=False),
            num(m.trades_per_year, 1),
            pct(m.cost_per_year, signed=False),
            pct(m.risk_exposure, signed=False),
        )
    console.print(t)
    console.print(
        "[dim]Rinde/año = rendimiento anual compuesto. Sharpe = retorno sobre el efectivo por "
        "unidad de riesgo (más alto es mejor). Peor caída = mayor baja desde un máximo. "
        "En riesgo = % promedio en acciones, inmobiliario o materias primas.[/dim]"
    )

    if not rep.yearly.empty:
        y = Table(title="Por año (el primero y el último pueden ser parciales)")
        y.add_column("Año")
        for c in rep.yearly.columns:
            y.add_column(str(c), justify="right")
        for year, r in rep.yearly.iterrows():
            y.add_row(str(year), *[pct(v) for v in r.to_numpy()])
        console.print(y)

    if rep.neighbors:
        n = Table(title="Robustez: variantes vecinas (¿cumplen los criterios 1 y 2?)")
        for c in ("Variante", "Rinde/año", "Sharpe", "Peor caída", "Cumple"):
            n.add_column(c, justify="left" if c == "Variante" else "right")
        for spec, m, ok in rep.neighbors:
            n.add_row(
                spec.name,
                pct(m.cagr) if m else "-",
                num(m.sharpe) if m else "-",
                pct(m.max_dd) if m else "-",
                _mark(ok),
            )
        console.print(n)

    bench = rep.spec(BENCHMARK_KEY)
    bal = rep.spec(BALANCED_KEY)
    for ci, ref in ((rep.boot_benchmark, bench), (rep.boot_balanced, bal)):
        if ci is None or ref is None:
            continue
        console.print(
            f"Incertidumbre frente a {ref.name} (bootstrap, 90%): diferencia de rinde/año entre "
            f"{pct(ci.cagr_diff[0])} y {pct(ci.cagr_diff[1])} · diferencia de Sharpe entre "
            f"{ci.sharpe_diff[0]:+.2f} y {ci.sharpe_diff[1]:+.2f} · la rotación rinde más en el "
            f"{ci.p_cagr_better:.0%} de los remuestreos."
        )

    if rep.contributions and p is not None:
        parts = " · ".join(f"{name}: {usd(v)}" for name, v in rep.contributions)
        console.print(
            f"Si hubieras aportado {usd(cfg.monthly_contribution_usd)} al mes durante {p.months} "
            f"meses (total {usd(rep.contributed)}): {parts}. [dim]Sin impuestos.[/dim]"
        )
    if rep.cost_sensitivity:
        parts = " · ".join(
            f"cuenta de {usd(c)}: {pct(a)} vs {pct(b)}" for c, a, b in rep.cost_sensitivity
        )
        bname = bench.name if bench else "comparador"
        console.print(f"Costos según el tamaño de la cuenta (rotación vs {bname}): {parts}")

    for w in rep.warnings:
        console.print(f"[yellow]Aviso: {w}[/yellow]")
    if csv_path:
        console.print(f"[dim]Curvas diarias guardadas en {csv_path} (las usa el panel).[/dim]")

    if rep.verdict is None:
        console.print(
            "[bold]Veredicto:[/bold] este periodo es informativo; el veredicto pre-registrado se "
            "da sobre la validación: [bold]uv run tt etf-backtest --periodo validacion[/bold]"
        )
        return
    title = "Criterios pre-registrados (docs/ETF-ROTACION.md)"
    if rep.period == "reserva":
        title = "Confirmación en la reserva (criterios 1 a 3)"
    c = Table(title=title)
    for col in ("", "Criterio", "Detalle"):
        c.add_column(col)
    for cr in rep.criteria:
        c.add_row(_mark(cr.passed), cr.label, cr.detail)
    console.print(c)
    color = {PASS: "green", FAIL: "red"}.get(rep.verdict, "yellow")
    console.print(f"[bold]Veredicto:[/bold] [{color}]{rep.verdict}[/{color}]")
    if rep.verdict == PASS:
        console.print(
            "PASA no significa «probado»: significa que vale la pena seguirla en papel. "
            "Siguiente paso: abrir la reserva UNA vez (tt etf-backtest --periodo reserva "
            "--abrir-reserva) y luego seguimiento mensual con tt etf-senal."
        )
    elif rep.verdict == FAIL:
        console.print(
            "Según lo pre-registrado, rotacion-v1 se archiva. Cambiar reglas ahora para que "
            "«pase» sería ajustar a la medida del pasado."
        )


def print_signal(
    console: Console,
    sig: LiveSignal,
    cfg: EtfConfig,
    previous: tuple | None,
    stored: dict | None,
    verdict: dict | None,
    capital: float | None = None,
    first_record: bool = False,
) -> None:
    from tradingtool.etf.live import MONTHS_ES, changes

    if verdict and verdict.get("reglas") == cfg.rules_hash() and verdict.get("veredicto") == PASS:
        console.print(
            "[green]rotacion-v1 PASÓ el backtest pre-registrado.[/green] Aun así, opera solo en "
            "papel hasta que autorices otra cosa por escrito."
        )
    else:
        estado = (
            f"veredicto {verdict['veredicto']}"
            if verdict and verdict.get("reglas") == cfg.rules_hash()
            else "sin backtest con estas reglas (corre: uv run tt etf-backtest)"
        )
        console.print(
            f"[yellow]Ojo: rotacion-v1 no está aprobada ({estado}). Esta señal es solo para "
            "seguimiento en papel, no una recomendación de inversión.[/yellow]"
        )
    if sig.stale:
        console.print(
            f"[red]Datos incompletos: {sig.stale_reason}. Actualiza con "
            "uv run tt etf-senal (sin --sin-descargar).[/red]"
        )
    mes = MONTHS_ES[sig.execute_from.month - 1]
    console.print(
        f"[bold]Señal con el cierre del {sig.as_of:%Y-%m-%d}[/bold] → operar el primer día "
        f"hábil de {mes} (≈ {sig.execute_from:%Y-%m-%d}), idealmente cerca del cierre."
    )

    assets = list(dict.fromkeys([*cfg.risk_assets, *cfg.defensive_assets, cfg.cash_asset]))
    m = Table(title="Retorno total por horizonte (lo que mira la regla)")
    m.add_column("Activo")
    for p in sig.picks:
        m.add_column(f"{p.months} m", justify="right")
    for a in assets:
        cells = []
        for p in sig.picks:
            val = pct(p.returns.get(a))
            cells.append(f"[bold]{val}[/bold]" if a == p.pick else val)
        m.add_row(f"{a} · {LABELS.get(a, a)}", *cells)
    m.add_row("[bold]Elige[/bold]", *[f"[bold]{p.pick}[/bold]" for p in sig.picks])
    console.print(m)
    console.print(
        "[dim]Regla: en cada horizonte, el mejor activo de riesgo si rinde más que el efectivo; "
        "si no, el mejor refugio. Cada horizonte decide una parte igual de la cartera.[/dim]"
    )

    target = stored or sig.weights
    t = Table(title="Cartera objetivo")
    for c in ("Activo", "Backtest", "ETF UCITS (LSE, USD)", "Peso"):
        t.add_column(c, justify="right" if c == "Peso" else "left")
    if capital:
        t.add_column("Monto aprox.", justify="right")
    for a, w in sorted(target.items(), key=lambda kv: -kv[1]):
        row = [LABELS.get(a, a), a, cfg.ucits.get(a, "?"), f"{w:.0%}"]
        if capital:
            row.append(usd(w * capital))
        t.add_row(*row)
    console.print(t)
    if stored is not None and any(
        abs(stored.get(k, 0) - sig.weights.get(k, 0)) > 1e-9 for k in set(stored) | set(sig.weights)
    ):
        console.print(
            "[yellow]Los datos cambiaron desde que se guardó la recomendación de este mes; se "
            "mantiene la primera (registro honesto).[/yellow]"
        )
    if previous is None and first_record:
        console.print("Primera recomendación guardada: el seguimiento en papel empieza aquí.")
    if previous is not None:
        prev_date, prev_w = previous
        diff = changes(prev_w, target)
        if not diff:
            console.print(f"Sin cambios frente a {prev_date:%Y-%m}: este mes no hay que operar.")
        else:
            parts = ", ".join(f"{a} {x:.0%} → {y:.0%}" for a, x, y in diff)
            console.print(f"Cambios frente a {prev_date:%Y-%m}: {parts}.")
    console.print(
        "[dim]Consejo: usa tu aporte mensual para comprar lo que falta antes de vender. "
        "IBKR no permite fracciones de ETF UCITS. Verifica cada símbolo en IBKR. "
        "Esta herramienta NO envía órdenes.[/dim]"
    )
