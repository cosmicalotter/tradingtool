"""Línea de comandos ``tt`` (en español). Ejecuta ``uv run tt --help`` para ver todo.

Ningún comando envía órdenes a un broker: esta fase es solo investigación y diario.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from tradingtool import __version__
from tradingtool.config import AppConfig, load_config
from tradingtool.db import connect
from tradingtool.logging_setup import setup_logging
from tradingtool.settings import Settings

app = typer.Typer(
    help="Panel de apoyo a decisiones (compras de insiders). Solo investigación: NO envía órdenes.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
log = logging.getLogger("tradingtool")

# Periodos pre-registrados para la validación histórica (ver docs/EVALUACION.md).
PERIODS = {
    "diseno": (date(2009, 1, 1), date(2018, 12, 31)),
    "validacion": (date(2019, 1, 1), date(2025, 9, 30)),
    "reserva": (date(2025, 10, 1), date(2100, 1, 1)),
}
RESERVE_FLAG = "reserva_abierta"


def _ctx() -> tuple[Settings, AppConfig]:
    settings = Settings()
    settings.ensure_dirs()
    setup_logging(settings.log_dir)
    return settings, load_config(settings.config_dir)


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter("usa el formato AAAA-MM-DD, p. ej. 2026-10-06") from exc


def _edgar(settings: Settings):
    from tradingtool.edgar.client import EdgarClient, EdgarConfigError

    try:
        return EdgarClient(settings.sec_user_agent, settings.sec_max_requests_per_second)
    except EdgarConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _price_source(settings: Settings, broker=None, name: str | None = None):
    from tradingtool.prices.sources import (
        AlpacaSource,
        CsvSource,
        EodhdSource,
        IbkrSource,
        MassiveSource,
        TiingoSource,
    )

    name = name or settings.price_source
    if name == "massive":
        key = settings.massive_api_key.get_secret_value() if settings.massive_api_key else ""
        return MassiveSource(key)
    if name == "tiingo":
        key = settings.tiingo_api_key.get_secret_value() if settings.tiingo_api_key else ""
        return TiingoSource(key)
    if name == "eodhd":
        key = settings.eodhd_api_key.get_secret_value() if settings.eodhd_api_key else ""
        return EodhdSource(key)
    if name == "alpaca":
        kid = settings.alpaca_key_id.get_secret_value() if settings.alpaca_key_id else ""
        sec = settings.alpaca_secret_key.get_secret_value() if settings.alpaca_secret_key else ""
        return AlpacaSource(kid.strip(), sec.strip())
    if name == "csv":
        return CsvSource(settings.data_dir / "csv")
    if name == "ibkr":
        if broker is None:
            raise typer.BadParameter("la fuente 'ibkr' necesita IB Gateway/TWS abierto")
        return IbkrSource(broker._ib)
    raise typer.BadParameter(f"fuente de precios desconocida: {name}")


# ----------------------------------------------------------------------------- iniciar / revisar


@app.command()
def iniciar() -> None:
    """Crea la carpeta de datos, la base local y tu archivo .env (si no existe)."""
    settings, cfg = _ctx()
    env = Path(".env")
    if not env.exists() and Path(".env.example").exists():
        shutil.copy(".env.example", env)
        console.print("[green]Creado .env a partir de .env.example: ábrelo y complétalo.[/green]")
    con = connect(settings.db_path)
    con.close()
    console.print(f"Base de datos lista en [bold]{settings.db_path}[/bold]")
    console.print(f"Estrategia: {cfg.screener.strategy_version} (config {cfg.config_hash()})")
    console.print("Siguiente paso: [bold]uv run tt revisar[/bold]")


@app.command()
def revisar(
    ibkr: Annotated[bool, typer.Option("--ibkr", help="Probar también la conexión a IBKR")] = False,
) -> None:
    """Revisa la configuración y el estado de los datos (no descarga nada)."""
    settings, cfg = _ctx()
    t = Table(title=f"tradingtool {__version__} - revisión")
    t.add_column("Chequeo")
    t.add_column("Estado")
    t.add_column("Detalle")

    def row(name: str, ok: bool | None, detail: str) -> None:
        mark = (
            "[green]OK[/green]"
            if ok
            else ("[yellow]AVISO[/yellow]" if ok is None else "[red]FALTA[/red]")
        )
        t.add_row(name, mark, detail)

    ua = settings.sec_user_agent
    row(
        "Identificación SEC (TT_SEC_USER_AGENT)",
        "@" in ua and len(ua.split()) >= 2,
        ua or "vacío: la SEC lo exige (nombre y correo)",
    )
    if settings.price_source == "massive":
        row("Clave Massive (precios)", bool(settings.massive_api_key), "TT_MASSIVE_API_KEY")
    elif settings.price_source == "tiingo":
        row("Clave Tiingo (precios)", bool(settings.tiingo_api_key), "TT_TIINGO_API_KEY")
    else:
        row("Fuente de precios", True, settings.price_source)
    from tradingtool.registry import archived_note

    archived = archived_note(cfg.screener.strategy_version)
    row(
        "Estrategia de insiders",
        None if archived else True,
        f"{cfg.screener.strategy_version} · config {cfg.config_hash()}"
        + (" · ARCHIVADA (solo seguimiento)" if archived else ""),
    )

    if settings.db_path.exists():
        con = connect(settings.db_path)
        try:
            n_f, first_f, last_f = con.execute(
                "SELECT count(*), min(filing_date), max(filing_date) FROM insider_filings"
            ).fetchone()
            row("Filings de insiders", n_f > 0, f"{n_f:,} ({first_f} → {last_f})")
            if n_f and first_f is not None:
                ok_hist = (last_f - first_f).days >= 3 * 365
                row(
                    "Historia para clasificar (≥3 años)",
                    ok_hist or None,
                    "suficiente" if ok_hist else "carga más historia: tt sec-historico",
                )
            n_p, last_p = con.execute("SELECT count(*), max(date) FROM prices_daily").fetchone()
            row("Precios en caché", n_p > 0, f"{n_p:,} barras, última {last_p}")
            n_s, n_pass = con.execute(
                "SELECT count(*), count(*) FILTER (WHERE passed) FROM signals WHERE origin='live'"
            ).fetchone()
            row(
                "Señales (día a día)", None if n_s == 0 else True, f"{n_s} ({n_pass} pasan filtros)"
            )
            n_o = con.execute("SELECT count(*) FROM outcomes").fetchone()[0]
            row("Resultados medidos", None if n_o == 0 else True, f"{n_o}")
            reserve = con.execute("SELECT value FROM meta WHERE key = ?", [RESERVE_FLAG]).fetchone()
            row(
                "Periodo de reserva (desde 2025-10-01)",
                None if reserve else True,
                f"ABIERTO el {reserve[0]}" if reserve else "cerrado (bien)",
            )
        finally:
            con.close()
    else:
        row("Base de datos", False, "ejecuta: uv run tt iniciar")

    if ibkr:
        from tradingtool.broker.ibkr import IbkrReadOnly

        try:
            with IbkrReadOnly(settings) as b:
                snap = b.snapshot()
            row("IBKR (solo lectura)", True, f"cuenta {snap.account} paper={snap.is_paper}")
        except Exception as exc:
            row("IBKR (solo lectura)", False, str(exc)[:120])
    console.print(t)


# ----------------------------------------------------------------------------- SEC


@app.command("sec-historico")
def sec_historico(
    desde: Annotated[str, typer.Option(help="Trimestre inicial, p. ej. 2019Q1")] = "2019Q1",
    hasta: Annotated[str, typer.Option(help="Trimestre final, p. ej. 2026Q2")] = "2026Q2",
    borrar_zip: Annotated[bool, typer.Option(help="Borrar los .zip tras cargarlos")] = False,
) -> None:
    """Carga los datasets trimestrales de la SEC (Form 4 desde 2006). Se puede repetir."""
    from tradingtool.edgar.bulk import load_quarter, quarter_range

    settings, _ = _ctx()
    quarters = quarter_range(desde, hasta)
    con = connect(settings.db_path)
    client = _edgar(settings)
    try:
        for q in quarters:
            try:
                st = load_quarter(client, con, q, settings.raw_dir, keep_zip=not borrar_zip)
                console.print(
                    f"{q}: {st.filings_inserted:,} filings nuevos, "
                    f"{st.transactions_inserted:,} transacciones"
                )
            except FileNotFoundError as exc:
                console.print(f"[yellow]{exc}[/yellow]")
    finally:
        client.close()
        con.close()


def _sec_diario(settings: Settings, days: list[date]) -> None:
    from tradingtool.edgar.daily_index import sync_form4_day

    con = connect(settings.db_path)
    client = _edgar(settings)
    try:
        for d in days:
            try:
                st = sync_form4_day(client, con, d)
            except Exception as exc:
                console.print(f"[yellow]{d}: no se pudo descargar ({exc}). Se reintenta mañana.[/]")
                continue
            if not st.index_found:
                console.print(f"{d}: sin índice (fin de semana, feriado o aún no publicado)")
                continue
            console.print(
                f"{d}: {st.candidates} Form 4, {st.stored} nuevos, "
                f"{st.already_present} ya estaban, {len(st.errors)} errores"
            )
    finally:
        client.close()
        con.close()


@app.command("sec-diario")
def sec_diario(
    fecha: Annotated[str | None, typer.Option(help="Último día a descargar (AAAA-MM-DD)")] = None,
    dias: Annotated[int, typer.Option(help="Cuántos días hacia atrás revisar")] = 5,
) -> None:
    """Descarga los Form 4 recientes desde el índice diario de EDGAR."""
    settings, _ = _ctx()
    end = _parse_date(fecha, date.today() - timedelta(days=1))
    _sec_diario(settings, [end - timedelta(days=i) for i in range(dias - 1, -1, -1)])


# ----------------------------------------------------------------------------- precios


def _sync_prices(settings: Settings, cfg: AppConfig, start: date, end: date) -> None:
    from tradingtool.insiders.screener import candidate_tickers, pending_outcome_tickers
    from tradingtool.prices.base import MarketSnapshotSource, PriceSourceAuthError
    from tradingtool.prices.sync import (
        relevant_tickers,
        repair_split_jumps,
        sync_market,
        sync_tickers,
    )

    src = _price_source(settings)
    if settings.price_source == "massive":
        earliest = date.today() - timedelta(days=settings.massive_history_days)
        if start < earliest:
            console.print(
                f"[yellow]Tu plan de Massive cubre desde ~{earliest}; se omiten los días "
                f"anteriores (para historia más larga ver docs/COSTOS.md).[/yellow]"
            )
            start = earliest
    con = connect(settings.db_path)
    try:
        if isinstance(src, MarketSnapshotSource):
            st = sync_market(con, src, start, end)
            console.print(
                f"Precios ({src.name}): {st.days_fetched} días, {st.rows_written:,} barras"
            )
        else:
            tickers = set(candidate_tickers(con, start - timedelta(days=60), end))
            tickers |= set(pending_outcome_tickers(con, max(cfg.outcomes.horizons_days)))
            tickers.add(settings.benchmark_ticker)
            tickers.add(settings.benchmark_secondary_ticker)
            st = sync_tickers(con, src, sorted(tickers), start - timedelta(days=120), end)
            console.print(
                f"Precios ({src.name}): {st.tickers_updated} tickers, {st.rows_written:,} barras"
            )
        for e in st.errors[:10]:
            console.print(f"[yellow]  {e}[/yellow]")
        repaired = repair_split_jumps(
            con,
            src,
            relevant_tickers(con, [settings.benchmark_ticker, settings.benchmark_secondary_ticker]),
            boundary=st.first_day,
            # Massive solo cubre ~2 años; las demás fuentes re-descargan toda la historia
            # para que el ajuste por split quede consistente de punta a punta.
            history_start=end - timedelta(days=settings.massive_history_days)
            if settings.price_source == "massive"
            else date(2005, 1, 1),
            end=end,
        )
        if repaired:
            console.print(f"Historia re-descargada por posibles splits: {', '.join(repaired)}")
    except PriceSourceAuthError as exc:
        console.print(f"[red]{exc}.[/red]")
        console.print(_auth_hint(settings))
        raise typer.Exit(1) from None
    finally:
        con.close()


def _auth_hint(settings: Settings, source: str | None = None) -> str:
    """Pistas para claves rechazadas, sin mostrar nunca las claves."""
    source = source or settings.price_source
    if source == "tiingo":
        key = settings.tiingo_api_key.get_secret_value().strip() if settings.tiingo_api_key else ""
        return (
            "Tiingo rechazó la clave. Revisa TT_TIINGO_API_KEY en tu .env "
            f"({len(key)} caracteres; suele tener 40). La encuentras en tiingo.com → "
            "menú de tu cuenta → API → Token."
        )
    if source != "alpaca":
        return f"Revisa la clave de {source} en tu .env."
    kid = settings.alpaca_key_id.get_secret_value().strip() if settings.alpaca_key_id else ""
    sec = (
        settings.alpaca_secret_key.get_secret_value().strip() if settings.alpaca_secret_key else ""
    )
    lines = [
        "Alpaca rechazó las claves. Revisa en tu .env:",
        f"  TT_ALPACA_KEY_ID: {len(kid)} caracteres, empieza por '{kid[:2]}' "
        "(suele tener ~20 y empezar por PK o AK)",
        f"  TT_ALPACA_SECRET_KEY: {len(sec)} caracteres (suele tener ~40)",
        "  - ¿Están invertidas (ID en el lugar del secreto)?",
        "  - ¿Generaste claves nuevas después? Las anteriores dejan de servir.",
        "  - El secreto solo se muestra una vez: si no lo copiaste completo, genera claves nuevas.",
    ]
    return "\n".join(lines)


@app.command()
def precios(
    desde: Annotated[str | None, typer.Option(help="Fecha inicial (AAAA-MM-DD)")] = None,
    hasta: Annotated[str | None, typer.Option(help="Fecha final (AAAA-MM-DD)")] = None,
) -> None:
    """Actualiza la caché de precios diarios (fuente según TT_PRICE_SOURCE)."""
    settings, cfg = _ctx()
    end = _parse_date(hasta, date.today() - timedelta(days=1))
    start = _parse_date(desde, end - timedelta(days=10))
    _sync_prices(settings, cfg, start, end)


# ---------------------------------------------------------------- screener / resultados


def _run_screen(settings: Settings, cfg: AppConfig, start: date, end: date, origin: str) -> int:
    from tradingtool.insiders.screener import screen
    from tradingtool.journal.journal import finish_run, record_signals, start_run

    con = connect(settings.db_path)
    try:
        run_id = start_run(
            con,
            "screen" if origin == "live" else "backtest",
            cfg.config_hash(),
            {"start": start, "end": end, "origin": origin},
        )
        try:
            sigs = screen(con, start, end, cfg, origin=origin)
            n = record_signals(con, run_id, sigs)
            finish_run(con, run_id, "ok", f"{n} señales, {sum(s.passed for s in sigs)} pasan")
        except Exception as exc:
            finish_run(con, run_id, "error", repr(exc)[:500])
            raise
        return len(sigs)
    finally:
        con.close()


def _archived_banner(strategy_version: str) -> None:
    from tradingtool.registry import archived_note

    note = archived_note(strategy_version)
    if note:
        console.print(f"[yellow]{note}[/yellow]")


def _print_signals(settings: Settings, start: date, end: date) -> None:
    from tradingtool.journal.journal import list_signals

    con = connect(settings.db_path)
    try:
        df = list_signals(con, start=start, end=end, passed=True, limit=20)
    finally:
        con.close()
    _archived_banner(load_config(settings.config_dir).screener.strategy_version)
    if df.empty:
        console.print(
            "Hoy no hay ideas que pasen los filtros (es normal: la estrategia es selectiva)."
        )
        return
    t = Table(title="Ideas que pasan los filtros (revísalas en el panel)")
    for c in ("Fecha", "Ticker", "Empresa", "Puntaje", "Motivo principal"):
        t.add_column(c)
    for r in df.itertuples():
        reasons = json.loads(r.reasons) if isinstance(r.reasons, str) else []
        t.add_row(
            str(r.as_of_date),
            str(r.ticker),
            str(r.issuer_name)[:30],
            f"{r.score:.2f}",
            (reasons[0] if reasons else "")[:70],
        )
    console.print(t)


@app.command()
def screener(
    fecha: Annotated[str | None, typer.Option(help="Fecha final (AAAA-MM-DD)")] = None,
) -> None:
    """Evalúa las compras de insiders recientes y guarda las señales (pasen o no)."""
    settings, cfg = _ctx()
    end = _parse_date(fecha, date.today() - timedelta(days=1))
    start = end - timedelta(days=cfg.screener.lookback_days_for_events - 1)
    n = _run_screen(settings, cfg, start, end, "live")
    console.print(f"{n} eventos evaluados entre {start} y {end}.")
    _print_signals(settings, start, end)


@app.command()
def resultados() -> None:
    """Calcula los resultados pendientes de todas las señales (incluye contrafactuales)."""
    from tradingtool.journal.journal import update_outcomes

    settings, cfg = _ctx()
    con = connect(settings.db_path)
    try:
        n = update_outcomes(
            con,
            cfg.outcomes.horizons_days,
            settings.benchmark_ticker,
            settings.benchmark_secondary_ticker,
        )
    finally:
        con.close()
    console.print(f"{n} resultados nuevos calculados.")


@app.command()
def diario(
    fecha: Annotated[
        str | None, typer.Option(help="Día a procesar (AAAA-MM-DD); por defecto ayer")
    ] = None,
) -> None:
    """Rutina diaria completa: Form 4 → precios → screener → resultados. Idempotente."""
    settings, cfg = _ctx()
    end = _parse_date(fecha, date.today() - timedelta(days=1))
    console.rule("1/4 Form 4 de la SEC")
    _sec_diario(settings, [end - timedelta(days=i) for i in range(4, -1, -1)])
    console.rule("2/4 Precios")
    _sync_prices(settings, cfg, end - timedelta(days=10), end)
    console.rule("3/4 Screener")
    start = end - timedelta(days=cfg.screener.lookback_days_for_events - 1)
    _run_screen(settings, cfg, start, end, "live")
    _print_signals(settings, start, end)
    console.rule("4/4 Resultados")
    resultados()
    console.print("Listo. Revisa las ideas con: [bold]uv run tt panel[/bold]")


# ---------------------------------------------------------------- histórico (validación)


@app.command()
def historico(
    desde: Annotated[str, typer.Option(help="Fecha inicial (AAAA-MM-DD)")] = "2019-01-01",
    hasta: Annotated[str, typer.Option(help="Fecha final (AAAA-MM-DD)")] = "2025-09-30",
    abrir_reserva: Annotated[
        bool, typer.Option(help="Usa el periodo de reserva (desde 2025-10-01). Queda registrado.")
    ] = False,
) -> None:
    """Reconstruye las señales históricas (origen 'backtest') con las reglas congeladas."""
    settings, cfg = _ctx()
    start, end = _parse_date(desde, date(2019, 1, 1)), _parse_date(hasta, date(2025, 9, 30))
    reserve_start = PERIODS["reserva"][0]
    con = connect(settings.db_path)
    try:
        if end >= reserve_start:
            if not abrir_reserva:
                console.print(
                    "[red]El periodo de reserva (desde 2025-10-01) está bloqueado.[/red] "
                    "Úsalo UNA sola vez, al final, con --abrir-reserva (queda registrado)."
                )
                raise typer.Exit(2)
            con.execute(
                "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                [RESERVE_FLAG, f"{datetime.now():%Y-%m-%d %H:%M} config {cfg.config_hash()}"],
            )
    finally:
        con.close()
    total = 0
    cur = start
    while cur <= end:  # por años para mantener la memoria acotada
        chunk_end = min(date(cur.year, 12, 31), end)
        n = _run_screen(settings, cfg, cur, chunk_end, "backtest")
        console.print(f"{cur.year}: {n:,} eventos")
        total += n
        cur = chunk_end + timedelta(days=1)
    console.print(
        f"Total: {total:,} eventos. Ahora: uv run tt precios --desde {start} y tt resultados"
    )


@app.command()
def informe(
    origen: Annotated[str, typer.Option(help="live o backtest")] = "live",
    horizonte: Annotated[int, typer.Option(help="Días hábiles")] = 21,
) -> None:
    """Resume resultados por grupo (aprobadas, rechazadas, sin decisión, bloqueadas)."""
    from tradingtool.journal.journal import outcome_summary

    settings, _ = _ctx()
    con = connect(settings.db_path)
    try:
        df = outcome_summary(con, horizonte, origin=origen)
    finally:
        con.close()
    if df.empty:
        console.print("Aún no hay resultados para ese horizonte.")
        return
    t = Table(title=f"Resultados a {horizonte} días hábiles ({origen})")
    for c in ("Grupo", "N", "Ret. medio", "Ret. mediano", "Aciertos", "Exceso vs SPY", "Truncadas"):
        t.add_column(c)
    for r in df.itertuples():

        def pct(x):
            return "-" if x is None or x != x else f"{x:+.2%}"

        t.add_row(
            r.grupo,
            str(r.n),
            pct(r.ret_medio),
            pct(r.ret_mediano),
            "-" if r.tasa_acierto != r.tasa_acierto else f"{r.tasa_acierto:.0%}",
            pct(r.exceso_medio),
            str(r.truncadas),
        )
    console.print(t)
    if int(df["n"].sum()) < 200:
        console.print("[yellow]Muestra pequeña (<200): todavía no permite conclusiones.[/yellow]")


@app.command()
def evaluar(
    origen: Annotated[str, typer.Option(help="backtest o live")] = "backtest",
    horizonte: Annotated[int, typer.Option(help="Días hábiles (principal: 63)")] = 63,
    periodo: Annotated[
        str | None, typer.Option(help="diseno | validacion | reserva (ver EVALUACION.md)")
    ] = None,
) -> None:
    """Evaluación estadística pre-registrada: exceso NETO de costos, t mensual, IC y por año."""
    from tradingtool.evaluation import evaluate

    settings, cfg = _ctx()
    start = end = None
    if periodo:
        if periodo not in PERIODS:
            raise typer.BadParameter(f"periodo debe ser uno de {list(PERIODS)}")
        start, end = PERIODS[periodo]
    con = connect(settings.db_path)
    try:
        if (
            periodo == "reserva"
            and not con.execute("SELECT 1 FROM meta WHERE key = ?", [RESERVE_FLAG]).fetchone()
        ):
            console.print(
                "[red]El periodo de reserva está cerrado (ver tt historico --abrir-reserva).[/red]"
            )
            raise typer.Exit(2)
        rep = evaluate(con, cfg, horizonte, origen, start, end)
    finally:
        con.close()
    gf = rep.groups_frame()
    if gf.empty:
        console.print(rep.verdict)
        return

    def pct(x):
        return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.2%}"

    t = Table(
        title=f"Evaluación a {horizonte} días hábiles ({origen}{', ' + periodo if periodo else ''})"
    )
    for c in (
        "Grupo",
        "Señales",
        "Meses",
        "Exceso neto",
        "Mediana",
        "Aciertos",
        "t mensual",
        "IC 90%",
        "Costo medio",
    ):
        t.add_column(c)
    for g in rep.groups:
        t.add_row(
            g.name,
            str(g.n_signals),
            str(g.n_months),
            pct(g.mean_net_excess),
            pct(g.median_net_excess),
            "-" if g.hit_rate is None else f"{g.hit_rate:.0%}",
            "-" if g.t_stat_monthly is None else f"{g.t_stat_monthly:.2f}",
            "-" if g.ci90_low is None else f"[{g.ci90_low:+.2%}, {g.ci90_high:+.2%}]",
            pct(g.mean_cost),
        )
    console.print(t)
    if not rep.by_year.empty:
        y = Table(title="Señales que pasan, por año")
        for c in ("Año", "N", "Exceso neto medio", "Aciertos"):
            y.add_column(c)
        for r in rep.by_year.itertuples():
            y.add_row(str(r.year), str(r.n), pct(r.exceso_neto_medio), f"{r.aciertos:.0%}")
        console.print(y)
    console.print(f"[bold]Veredicto:[/bold] {rep.verdict}")


# ---------------------------------------------------------------- rotación de ETFs


def _etf_ctx():
    from tradingtool.config import load_etf_config

    settings, _ = _ctx()
    return settings, load_etf_config(settings.config_dir)


def _etf_source(settings: Settings):
    from tradingtool.prices.base import PriceSourceError

    try:
        return _price_source(settings, name=settings.etf_price_source)
    except PriceSourceError as exc:
        console.print(f"[red]{exc}[/red]")
        if settings.etf_price_source == "tiingo":
            console.print(
                "Crea una cuenta gratuita en tiingo.com, copia tu token (menú de la cuenta → API) "
                "y ponlo en .env como TT_TIINGO_API_KEY=... (sin comillas ni espacios)."
            )
        raise typer.Exit(2) from exc


def _refresh_etf_data(settings: Settings, tickers: list[str]) -> None:
    """Re-descarga la historia completa de ``tickers`` y del efectivo (FRED)."""
    from tradingtool.etf.data import refresh_cash, refresh_etfs
    from tradingtool.prices.base import PriceSourceAuthError, PriceSourceError

    src = _etf_source(settings)
    if settings.etf_price_source == "alpaca":
        console.print(
            "[yellow]Alpaca solo trae historia desde 2016: alcanza para la señal mensual, "
            "no para el backtest (usa Tiingo para eso).[/yellow]"
        )
    con = connect(settings.db_path)
    try:
        st = refresh_etfs(con, src, tickers, date(2000, 1, 1), date.today())
        for e in st.errors:
            console.print(f"[yellow]  {e}[/yellow]")
        console.print(f"ETFs ({src.name}): {len(st.rows)} de {len(tickers)} actualizados")
        try:
            n = refresh_cash(con)
            console.print(f"Efectivo (FRED, letras del Tesoro a 3 meses): {n:,} días")
        except PriceSourceError as exc:
            console.print(f"[red]Efectivo (FRED): {exc}[/red]")
    except PriceSourceAuthError as exc:
        console.print(f"[red]{exc}.[/red]")
        console.print(_auth_hint(settings, settings.etf_price_source))
        raise typer.Exit(1) from None
    finally:
        con.close()


def pd_date(x) -> str:
    """Fecha corta AAAA-MM-DD (DuckDB entrega fechas como Timestamp de pandas)."""
    return str(x)[:10]


@app.command("etf-precios")
def etf_precios() -> None:
    """Descarga la historia completa de los ETFs de la rotación y la tasa del Tesoro (FRED)."""
    from tradingtool.etf.data import coverage, data_warnings
    from tradingtool.etf.strategies import required_tickers

    settings, ecfg = _etf_ctx()
    tickers = required_tickers(ecfg)
    _refresh_etf_data(settings, tickers)
    con = connect(settings.db_path)
    try:
        cov = coverage(con)
        warnings = data_warnings(con, [*tickers, ecfg.cash_asset])
    finally:
        con.close()
    t = Table(title="Datos de la rotación de ETFs")
    for c in ("Ticker", "Fuente", "Desde", "Hasta", "Días"):
        t.add_column(c)
    for r in cov.itertuples():
        t.add_row(
            r.ticker,
            str(r.fuente),
            f"{pd_date(r.desde)}",
            f"{pd_date(r.hasta)}",
            f"{r.filas:,}",
        )
    console.print(t)
    for w in warnings:
        console.print(f"[yellow]Aviso: {w}[/yellow]")
    console.print("Siguiente paso: [bold]uv run tt etf-backtest[/bold]")


@app.command("etf-backtest")
def etf_backtest(
    periodo: Annotated[
        str, typer.Option(help="validacion (veredicto) | diseno | todo | reserva")
    ] = "validacion",
    abrir_reserva: Annotated[
        bool,
        typer.Option(help="Abre la reserva (desde 2025-01-01) UNA vez. Queda registrado."),
    ] = False,
) -> None:
    """Backtest pre-registrado de la rotación de ETFs (docs/ETF-ROTACION.md). Sin órdenes."""
    from tradingtool.etf.data import data_warnings, load_panel
    from tradingtool.etf.live import save_verdict
    from tradingtool.etf.report import (
        ETF_PERIODS,
        ETF_RESERVE_FLAG,
        VALIDATION_END,
        EtfDataError,
        run_report,
    )
    from tradingtool.etf.strategies import required_tickers
    from tradingtool.etf.views import print_report

    settings, ecfg = _etf_ctx()
    if periodo not in ETF_PERIODS:
        raise typer.BadParameter(f"periodo debe ser uno de {list(ETF_PERIODS)}")
    tickers = [*required_tickers(ecfg), ecfg.cash_asset]
    con = connect(settings.db_path)
    try:
        opened = con.execute("SELECT value FROM meta WHERE key = ?", [ETF_RESERVE_FLAG]).fetchone()
        if periodo == "reserva" and not opened:
            if not abrir_reserva:
                console.print(
                    "[red]La reserva (desde 2025-01-01) está bloqueada.[/red] Ábrela UNA sola "
                    "vez, al final, con --abrir-reserva (queda registrado)."
                )
                raise typer.Exit(2)
            con.execute(
                "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                [ETF_RESERVE_FLAG, f"{datetime.now():%Y-%m-%d %H:%M} reglas {ecfg.rules_hash()}"],
            )
            console.print("[yellow]Reserva abierta y registrada.[/yellow]")
        end = None if periodo == "reserva" else VALIDATION_END
        prices = load_panel(con, tickers, end=end)
        warnings = data_warnings(con, tickers)
        try:
            rep = run_report(prices, ecfg, periodo)
        except EtfDataError as exc:
            console.print(f"[red]{exc}[/red]")
            for w in warnings:
                console.print(f"[yellow]Aviso: {w}[/yellow]")
            raise typer.Exit(1) from None
        if periodo == "validacion" and rep.verdict:
            save_verdict(
                con,
                {
                    "veredicto": rep.verdict,
                    "reglas": rep.rules_hash,
                    "estrategia": ecfg.strategy_version,
                    "fecha": f"{datetime.now():%Y-%m-%d %H:%M}",
                },
            )
    finally:
        con.close()
    rep.warnings = [*warnings, *rep.warnings]
    out_dir = settings.data_dir / "etf"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"curvas_{periodo}.csv"
    if not rep.curves.empty:
        rep.curves.to_csv(csv_path, index_label="fecha")
    print_report(console, rep, ecfg, str(csv_path) if not rep.curves.empty else "")


@app.command("etf-senal")
def etf_senal(
    capital: Annotated[
        float | None, typer.Option(help="Valor de tu cartera en USD, para calcular montos")
    ] = None,
    sin_descargar: Annotated[
        bool, typer.Option(help="No actualizar precios (usa los ya guardados)")
    ] = False,
) -> None:
    """Recomendación del mes de la rotación de ETFs. Se guarda y NO envía órdenes."""
    from tradingtool.etf.data import load_panel
    from tradingtool.etf.live import (
        compute_signal,
        previous_recommendation,
        record_recommendation,
        stored_verdict,
    )
    from tradingtool.etf.report import EtfDataError
    from tradingtool.etf.views import print_signal

    settings, ecfg = _etf_ctx()
    assets = list(dict.fromkeys([*ecfg.risk_assets, *ecfg.defensive_assets]))
    tickers = [a for a in assets if a != ecfg.cash_asset]
    if not sin_descargar:
        _refresh_etf_data(settings, tickers)
    con = connect(settings.db_path)
    try:
        prices = load_panel(con, [*tickers, ecfg.cash_asset])
        try:
            sig = compute_signal(prices, ecfg, date.today())
        except EtfDataError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
        stored = None if sig.stale else record_recommendation(con, sig, ecfg)
        previous = previous_recommendation(con, ecfg, sig.as_of)
        verdict = stored_verdict(con)
    finally:
        con.close()
    first = not sig.stale and stored is None
    print_signal(console, sig, ecfg, previous, stored, verdict, capital, first_record=first)


# ---------------------------------------------------------------- IBKR / riesgo / panel


@app.command()
def cuenta() -> None:
    """Muestra tu cuenta IBKR PAPER en solo lectura (requiere IB Gateway o TWS abierto)."""
    from tradingtool.broker.ibkr import IbkrReadOnly

    settings, _ = _ctx()
    try:
        with IbkrReadOnly(settings) as b:
            snap = b.snapshot()
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    t = Table(
        title=f"Cuenta {snap.account} ({'paper' if snap.is_paper else 'REAL - solo lectura'})"
    )
    t.add_column("Concepto")
    t.add_column("Valor")
    for tag, (val, cur) in sorted(snap.values.items()):
        t.add_row(tag, "-" if val is None else f"{val:,.2f} {cur}")
    console.print(t)
    if snap.positions:
        p = Table(title="Posiciones")
        for c in ("Símbolo", "Cantidad", "Costo prom.", "Bolsa"):
            p.add_column(c)
        for pos in snap.positions:
            p.add_row(pos.symbol, f"{pos.quantity:g}", f"{pos.avg_cost:,.2f}", pos.exchange)
        console.print(p)


@app.command()
def riesgo(
    entrada: Annotated[float, typer.Option(help="Precio de entrada")],
    stop: Annotated[float, typer.Option(help="Precio del stop (debajo de la entrada)")],
    capital: Annotated[float | None, typer.Option(help="Capital (USD); por defecto config")] = None,
    adv: Annotated[float | None, typer.Option(help="Volumen promedio diario en USD")] = None,
) -> None:
    """Calculadora de tamaño de posición y costos (acciones enteras)."""
    from tradingtool.risk.sizing import size_position

    _, cfg = _ctx()
    r = size_position(cfg.risk, cfg.costs, entrada, stop, capital, adv)
    t = Table(title="Tamaño de posición")
    t.add_column("Concepto")
    t.add_column("Valor")
    t.add_row("Acciones", str(r.shares))
    t.add_row("Valor de la posición", f"US${r.position_value:,.2f}")
    t.add_row("Riesgo si toca el stop", f"US${r.risk_usd:,.2f} ({r.risk_pct_of_capital:.2f}%)")
    t.add_row(
        "Costo ida y vuelta (estimado)", f"US${r.roundtrip_cost:,.2f} ({r.roundtrip_cost_pct:.2f}%)"
    )
    t.add_row("Costo en R", f"{r.r_multiple_cost:.2f} R")
    t.add_row("¿Opera?", "[green]sí[/green]" if r.ok else "[red]no[/red]")
    console.print(t)
    for w in r.warnings:
        console.print(f"[yellow]- {w}[/yellow]")


@app.command()
def panel(
    puerto: Annotated[int, typer.Option(help="Puerto local")] = 8501,
) -> None:
    """Abre el panel web local (Streamlit). NO envía órdenes."""
    app_path = Path(__file__).parent / "ui" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(puerto),
        "--server.address",
        "127.0.0.1",
        "--browser.gatherUsageStats",
        "false",
        "--server.headless",  # evita la pregunta de correo de Streamlit al arrancar
        "true",
    ]
    console.print(f"Panel en [bold]http://127.0.0.1:{puerto}[/bold] (Ctrl+C para cerrar)")
    raise typer.Exit(subprocess.call(cmd))  # noqa: S603


if __name__ == "__main__":  # pragma: no cover
    app()
