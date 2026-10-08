"""Panel de ideas de compras de insiders (Streamlit). Fase 1: SOLO investigación.

Este panel NUNCA envía órdenes a ningún broker. Aprobar / Rechazar / Omitir solo escribe en
el diario local (``journal.record_decision``) para medir después si tu criterio ayuda.

Abrir con:  uv run tt panel
(equivale a: uv run streamlit run src/tradingtool/ui/app.py --server.address 127.0.0.1)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from tradingtool.config import AppConfig, load_config
from tradingtool.models import SizingResult
from tradingtool.registry import archived_note
from tradingtool.settings import Settings
from tradingtool.ui import charts
from tradingtool.ui import presenters as p
from tradingtool.ui import queries as q

DEFAULT_RANGE_DAYS = 30
SIGNALS_LIMIT = 500
PRICE_LOOKBACK_DAYS = 180
DATE_WIDGET_FORMAT = "YYYY-MM-DD"  # igual que p.fmt_date y los comandos tt
FLASH_KEY = "_tt_flash"
NONCE_KEY = "_tt_decision_nonce"
LAST_ROWS_KEY = "_tt_ideas_last_rows"
SELECT_KEY = "ideas_sel"


# ------------------------------------------------------------------------------ utilidades


def load_settings() -> Settings | None:
    """Lee la configuración en cada ejecución (sin caché) para tomar cambios de .env/entorno."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(x) for x in err.get('loc', ()))}: {err.get('msg')}"
            for err in exc.errors()
        )
        st.error(
            "Hay un valor inválido en tu archivo .env o en las variables TT_... "
            f"Revísalo y recarga la página. Detalle: {problems}"
        )
        return None


def load_app_config(settings: Settings) -> tuple[AppConfig, str | None]:
    try:
        return load_config(settings.config_dir), None
    except Exception as exc:  # YAML mal escrito o valores fuera de rango
        return AppConfig(), (
            f"Hay un error en los archivos de configuración ({settings.config_dir}/*.yaml). "
            "Se usan los valores por defecto hasta que lo corrijas. "
            f"Detalle: {exc}"
        )


def is_dark_theme() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def show_db_error(exc: q.PanelDbError) -> None:
    if isinstance(exc, q.DbMissingError):
        st.info(str(exc))
    elif isinstance(exc, q.DbBusyError):
        st.warning(str(exc))
    else:
        st.error(str(exc))


def flash(kind: str, message: str) -> None:
    st.session_state[FLASH_KEY] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop(FLASH_KEY, None)
    if item:
        kind, message = item
        {"success": st.success, "warning": st.warning}.get(kind, st.info)(message)


def _date_or_default(value: Any, default: date) -> date:
    return value if isinstance(value, date) else default


# ------------------------------------------------------------------------------ secciones


def render_sidebar(settings: Settings) -> None:
    with st.sidebar:
        st.header("Ayuda rápida")
        st.markdown("**Para actualizar los datos** (en la terminal, dentro de la carpeta):")
        for cmd, desc in p.COMMANDS_HELP:
            st.markdown(f"`{cmd}`  \n{desc}")
        st.divider()
        st.caption(f"Datos: {settings.data_dir}")
        st.caption(f"Configuración: {settings.config_dir}")


def render_onboarding() -> None:
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(p.ONBOARDING_STEPS, start=1))
    st.info(
        "**Todavía no hay base de datos.** Ejecuta primero: `uv run tt iniciar`\n\n"
        f"Pasos para empezar:\n\n{steps}"
    )


def sizing_inputs(
    prefix: str, cfg: AppConfig, entry: float | None, stop: float | None, adv: float | None
) -> tuple[float, float, float | None, float | None, float | None]:
    c1, c2 = st.columns(2)
    capital = c1.number_input(
        "Capital (USD)",
        min_value=0.0,
        value=float(cfg.risk.capital_usd),
        step=100.0,
        key=f"{prefix}_capital",
        help="Dinero dedicado a esta estrategia (el satélite), no todo tu patrimonio.",
    )
    risk_pct = c2.number_input(
        "Riesgo por operación (%)",
        # La config admite valores > 0; si es menor que 0.01 la casilla no debe fallar.
        min_value=min(0.01, float(cfg.risk.risk_per_trade_pct)),
        max_value=2.0,
        value=float(cfg.risk.risk_per_trade_pct),
        step=0.05,
        format="%.2f",
        key=f"{prefix}_riesgo",
        help="Qué porcentaje del capital aceptas perder si el precio toca el stop.",
    )
    c3, c4, c5 = st.columns(3)
    entry_v = c3.number_input(
        "Entrada (USD)",
        min_value=0.01,
        value=entry,
        step=0.01,
        format="%.2f",
        key=f"{prefix}_entrada",
        placeholder="ej. 12.50",
        help="Precio al que comprarías. Usa punto para decimales.",
    )
    stop_v = c4.number_input(
        "Stop (USD)",
        min_value=0.01,
        value=stop,
        step=0.01,
        format="%.2f",
        key=f"{prefix}_stop",
        placeholder="ej. 11.00",
        help="Precio al que venderías para cortar la pérdida. Debe estar debajo de la entrada.",
    )
    adv_v = c5.number_input(
        "Volumen diario promedio (USD, opcional)",
        min_value=0.0,
        value=adv,
        step=10_000.0,
        format="%.0f",
        key=f"{prefix}_adv",
        help="Si lo dejas vacío se asume la acción menos líquida (costos más altos).",
    )
    return capital, risk_pct, entry_v, stop_v, adv_v


def render_sizing(result: SizingResult | None, message: str | None) -> None:
    if result is None:
        st.info(message or "Completa los datos para calcular.")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("Acciones (enteras)", p.fmt_int(result.shares))
    m2.metric("Valor de la posición", p.fmt_money(result.position_value))
    m3.metric(
        "Riesgo si toca el stop",
        p.fmt_money(result.risk_usd),
        help=f"{p.fmt_pct_points(result.risk_pct_of_capital, 2)} del capital.",
    )
    m4, m5, m6 = st.columns(3)
    m4.metric("Costo ida y vuelta", p.fmt_money(result.roundtrip_cost))
    m5.metric("Costo ida y vuelta (%)", p.fmt_pct_points(result.roundtrip_cost_pct, 2))
    m6.metric(
        "Costo en R",
        f"{p.fmt_num(result.r_multiple_cost, 2)} R",
        help="R = lo que pierdes si el precio toca el stop. Esto dice qué parte de ese riesgo "
        "se va en comisiones y spread.",
    )
    st.caption(
        f"Riesgo por acción: {p.fmt_money(result.risk_per_share)} "
        f"({p.fmt_pct(result.risk_per_share / result.entry)} debajo de la entrada)."
    )
    if result.ok:
        st.success("Cumple las reglas de riesgo y costos.")
    else:
        st.error("No cumple las reglas: con este capital y este stop no conviene operarla.")
    for w in result.warnings:
        st.warning(w)


# ------------------------------------------------------------------------------ Ideas


def _signal_label(row: dict[str, Any]) -> str:
    ticker = row.get("ticker") or "(sin ticker)"
    name = str(row.get("issuer_name") or "")[:40]
    return (
        f"{ticker} · {name} · {p.fmt_date(row.get('as_of_date'))} · {row.get('estado')} · "
        f"{row.get('decision_label')}"
    )


def _filter_search(df: pd.DataFrame, text: str) -> pd.DataFrame:
    text = (text or "").strip().lower()
    if not text or df.empty:
        return df
    hay = df["ticker"].fillna("").astype(str) + " " + df["issuer_name"].fillna("").astype(str)
    return df[hay.str.lower().str.contains(text, regex=False)].reset_index(drop=True)


def ideas_filters(last: date) -> tuple[date, date, bool, str]:
    c1, c2, c3, c4 = st.columns([1, 1, 1.2, 1.6])
    default_start = last - timedelta(days=DEFAULT_RANGE_DAYS)
    # Las claves llevan la última fecha con ideas: si 'tt diario' trae ideas nuevas mientras
    # el panel está abierto, el rango vuelve a incluirlas (en vez de quedarse en el viejo).
    suffix = last.isoformat()
    start = c1.date_input(
        "Desde", value=default_start, key=f"ideas_desde_{suffix}", format=DATE_WIDGET_FORMAT
    )
    end = c2.date_input("Hasta", value=last, key=f"ideas_hasta_{suffix}", format=DATE_WIDGET_FORMAT)
    start = _date_or_default(start, default_start)
    end = _date_or_default(end, last)
    if start > end:
        start, end = end, start
    with c3:
        st.write("")  # alinea el interruptor con las casillas de fecha
        only_passed = st.toggle(
            "Solo las que pasan filtros",
            value=True,
            key="ideas_solo_pasan",
            help="Las bloqueadas no se deciden, pero se guardan para comparar (contrafactuales).",
        )
    search = c4.text_input("Buscar ticker o empresa", key="ideas_buscar")
    return start, end, only_passed, search or ""


def signals_table_and_picker(df: pd.DataFrame) -> str:
    table = pd.DataFrame(
        {
            "Ticker": df["ticker"].fillna(p.DASH),
            "Empresa": df["issuer_name"].fillna(p.DASH),
            "Fecha": df["as_of_date"],
            "Puntaje": df["score"],
            "Filtros": df["estado"],
            "Decisión": df["decision_label"],
        }
    )
    event = st.dataframe(
        table,
        hide_index=True,
        key="ideas_tabla",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Fecha": st.column_config.DateColumn(
                "Fecha",
                format=DATE_WIDGET_FORMAT,
                help="Día en que la compra se hizo pública (SEC).",
            ),
            "Puntaje": st.column_config.NumberColumn(
                "Puntaje", format="%.2f", help="Más alto = señal más fuerte según las reglas."
            ),
        },
    )
    ids = [str(x) for x in df["signal_id"]]
    rows: list[int] = []
    try:
        rows = list(event.selection.rows)  # type: ignore[union-attr]
    except AttributeError:
        rows = []
    if rows and rows != st.session_state.get(LAST_ROWS_KEY) and rows[0] < len(ids):
        st.session_state[SELECT_KEY] = ids[rows[0]]
    st.session_state[LAST_ROWS_KEY] = rows
    if st.session_state.get(SELECT_KEY) not in ids:
        st.session_state[SELECT_KEY] = ids[0]
    labels = {str(rec["signal_id"]): _signal_label(rec) for rec in df.to_dict("records")}
    if len(df) >= SIGNALS_LIMIT:
        st.caption(f"Se muestran las {SIGNALS_LIMIT} más recientes. Acorta el rango de fechas.")
    return st.selectbox(
        "Idea seleccionada (o haz clic en una fila de la tabla)",
        options=ids,
        format_func=lambda sid: labels.get(sid, sid),
        key=SELECT_KEY,
    )


def render_ideas(settings: Settings, cfg: AppConfig) -> None:
    show_flash()
    try:
        with q.open_db(settings.db_path) as con:
            _first, last = q.signal_date_bounds(con)
            if last is None:
                st.info(
                    "Todavía no hay ideas. Ejecuta: uv run tt screener "
                    "(o uv run tt diario, que hace todos los pasos)."
                )
                return
            start, end, only_passed, search = ideas_filters(last)
            df = q.latest_signals(con, start, end, only_passed=only_passed, limit=SIGNALS_LIMIT)
            df = _filter_search(df, search)
            if df.empty:
                st.info("No hay ideas con estos filtros. Prueba ampliar el rango de fechas.")
                return
            st.caption(f"{len(df)} ideas en el rango.")
            sid = signals_table_and_picker(df)
            detail = q.signal_detail(con, sid)
            bars = pd.DataFrame()
            if detail is not None and detail.ticker and detail.as_of_date:
                bars = q.price_bars(
                    con,
                    detail.ticker,
                    start=detail.as_of_date - timedelta(days=PRICE_LOOKBACK_DAYS),
                )
    except q.PanelDbError as exc:
        show_db_error(exc)
        return
    # Conexión de lectura ya cerrada: desde aquí se puede escribir una decisión.
    if detail is None:
        st.warning("No se encontró esa idea (¿se regeneró?). Recarga la página.")
        return
    render_signal_detail(settings, cfg, detail, bars)


def render_signal_detail(
    settings: Settings, cfg: AppConfig, detail: q.SignalDetail, bars: pd.DataFrame
) -> None:
    st.divider()
    title = detail.ticker or "(sin ticker)"
    st.subheader(f"{title} · {detail.issuer_name or 'Empresa sin nombre'}")
    last = detail.last_decision
    feats = detail.features
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Fecha pública",
        p.fmt_date(detail.as_of_date),
        help="Día en que la SEC publicó la compra. Todo lo de esta idea usa datos hasta ese día.",
    )
    c2.metric("Puntaje", p.fmt_num(detail.score, 2))
    c3.metric("Filtros", q.PASSED_LABEL if detail.passed else q.BLOCKED_LABEL)
    c4.metric("Tu decisión", last["decision_label"] if last else q.NO_DECISION_LABEL)
    st.caption(
        f"Estrategia: {detail.strategy_version or p.DASH} · "
        f"configuración {detail.config_hash or p.DASH} · id {detail.signal_id}"
    )

    if detail.as_of_date is not None and detail.passed:
        entry_day = q.next_weekday(detail.as_of_date)
        today = date.today()
        if today > entry_day:
            st.warning(
                f"El día de entrada de esta idea ya pasó (apertura del "
                f"{p.fmt_date(entry_day)}). Si decides ahora ya no es 'a ciegas': podrías "
                "conocer lo que pasó después y eso ensucia la comparación de tu criterio. "
                "Para medirte bien, decide sobre ideas recientes."
            )
        elif today == entry_day:
            st.caption(
                "Hoy es el día de entrada: para que la decisión cuente 'a ciegas', decide antes "
                "de la apertura (9:30 hora de Nueva York)."
            )
    if last:
        when = p.fmt_date(last.get("decided_at"))
        why = f" Motivo: {p.md_escape(last['reason'])}" if last.get("reason") else ""
        st.info(
            f"Ya decidiste: **{last['decision_label']}** ({when} UTC).{why} "
            "Si vuelves a decidir, cuenta la última."
        )

    st.markdown("**Por qué pasó los filtros**" if detail.passed else "**Por qué se bloqueó**")
    if detail.reasons:
        st.markdown("\n".join(f"- {p.md_escape(r)}" for r in detail.reasons))
    else:
        st.caption("El screener no guardó razones para esta idea.")

    metrics = p.key_metric_rows(feats)
    if metrics:
        cols = st.columns(len(metrics))
        for col, row in zip(cols, metrics, strict=True):
            col.metric(row.label, row.value, help=row.help or None)
    if feats.get("price_last") is not None:
        st.caption(
            "Precios y liquidez son los conocidos al "
            f"{p.fmt_date(feats.get('price_last_date'))} (cuando se generó la idea), no los "
            "de hoy."
        )

    render_insiders(feats)

    rows = p.feature_rows(feats)
    if rows:
        with st.expander("Todos los datos de la idea"):
            st.dataframe(
                pd.DataFrame(
                    [{"Dato": r.label, "Valor": r.value, "Qué significa": r.help} for r in rows]
                ),
                hide_index=True,
            )

    st.markdown("**Formularios en la SEC**")
    if detail.filing_links:
        lines = []
        for acc, url in detail.filing_links:
            lines.append(f"- [{acc}]({url})" if url else f"- {p.md_escape(acc)} (sin enlace)")
        st.markdown("\n".join(lines))
    else:
        st.caption("Sin formularios asociados.")

    render_price_section(detail, bars)

    st.markdown("#### Tamaño y riesgo")
    defaults = p.sizing_defaults(feats, cfg.risk.stop_atr_multiple)
    if defaults.entry is not None:
        when = p.fmt_date(feats.get("price_last_date"))
        stop_text = (
            f"; stop = entrada - {p.fmt_num(cfg.risk.stop_atr_multiple, 1)} x ATR "
            f"({p.fmt_money(defaults.atr)})"
            if defaults.stop is not None
            else ""
        )
        st.caption(
            f"Prellenado: entrada = último cierre conocido ({p.fmt_money(defaults.entry)}, del "
            f"{when}){stop_text}. No es el precio de hoy: revisa la cotización actual en tu "
            "broker y corrige la entrada si cambió."
        )
    capital, risk_pct, entry, stop, adv = sizing_inputs(
        f"ideas_{detail.signal_id}", cfg, defaults.entry, defaults.stop, defaults.adv
    )
    result, message = p.compute_sizing(cfg, capital, risk_pct, entry, stop, adv)
    render_sizing(result, message)

    render_decision(settings, detail, result, entry, stop)


def render_insiders(features: dict[str, Any]) -> None:
    st.markdown("**Quién compró**")
    table = p.insiders_table(features)
    if table.empty:
        st.caption("El screener no guardó el detalle por insider para esta idea.")
        return
    st.dataframe(table, hide_index=True)
    st.caption(
        "Tipo de insider: 'oportunista' = compra fuera de su patrón habitual (lo más "
        "informativo según los estudios); 'rutinario' = compra casi siempre en el mismo mes "
        "(se excluye); 'sin clasificar' = no hay 3 años de historia para saberlo."
    )


def render_price_section(detail: q.SignalDetail, bars: pd.DataFrame) -> None:
    st.markdown("#### Precio")
    if not detail.ticker:
        st.caption("Sin ticker: no hay precios para graficar.")
        return
    show_after = st.toggle(
        "Mostrar también lo que pasó después (para aprender; no decidas viéndolo)",
        value=False,
        key=f"after_{detail.signal_id}",
    )
    view = bars
    if not show_after and detail.as_of_date is not None and not bars.empty:
        view = bars[bars["date"] <= pd.Timestamp(detail.as_of_date)]
    chart = charts.price_chart(view, detail.as_of_date, dark=is_dark_theme())
    if chart is None:
        st.caption(
            f"No hay precios de {detail.ticker} cerca de esta fecha. Ejecuta: uv run tt precios"
        )
        return
    st.altair_chart(chart, width="stretch")
    st.caption(
        "Cierres diarios según tu caché de precios actual (pueden estar ajustados por splits, "
        "a diferencia del precio que pagó el insider). La línea vertical marca la fecha "
        "pública de la idea"
        + (
            "; a la derecha está lo que pasó después."
            if show_after
            else "; no se muestra lo que pasó después."
        )
    )
    with st.expander("Ver datos del gráfico"):
        table = view.copy()
        table["date"] = table["date"].dt.date
        money = st.column_config.NumberColumn
        st.dataframe(
            table.rename(
                columns={
                    "date": "Fecha",
                    "open": "Apertura",
                    "high": "Máximo",
                    "low": "Mínimo",
                    "close": "Cierre",
                    "volume": "Volumen",
                }
            ).iloc[::-1],
            hide_index=True,
            column_config={
                "Fecha": st.column_config.DateColumn("Fecha", format=DATE_WIDGET_FORMAT),
                "Apertura": money("Apertura", format="US$%.2f"),
                "Máximo": money("Máximo", format="US$%.2f"),
                "Mínimo": money("Mínimo", format="US$%.2f"),
                "Cierre": money("Cierre", format="US$%.2f"),
                "Volumen": money("Volumen (acciones)", format="%.0f"),
            },
        )


def render_decision(
    settings: Settings,
    detail: q.SignalDetail,
    result: SizingResult | None,
    entry: float | None,
    stop: float | None,
) -> None:
    st.markdown("#### Tu decisión")
    sid = detail.signal_id
    if not detail.passed:
        st.info(
            "Esta idea no pasó los filtros, así que no se decide: queda en el diario como "
            "contrafactual (sirve para medir si los filtros ayudan)."
        )
        return
    st.caption(
        "Aprobar o rechazar solo se guarda en tu diario (no se envía ninguna orden). "
        "Omitir = no la evalúas; en los resultados cuenta como 'sin decisión'."
    )
    plan = p.decision_plan(result, entry, stop)
    st.caption(f"Si apruebas se guardará este plan: {plan.describe()}.")
    if result is not None and not result.ok:
        st.caption(
            "Ojo: la calculadora dice que con este capital y este stop no conviene operarla. "
            "Si la apruebas igual, queda registrado así."
        )
    # El nonce cambia después de cada decisión guardada: limpia el motivo y renueva las
    # claves de los botones, así un doble clic o una recarga no guardan dos veces.
    nonce = st.session_state.get(NONCE_KEY, 0)
    reason = st.text_input(
        "Motivo o nota (obligatorio para rechazar)",
        key=f"reason_{sid}_{nonce}",
        max_chars=q.MAX_REASON_CHARS,
        placeholder="Ej.: compra grande del CEO y la empresa tiene ventas",
    )
    b1, b2, b3 = st.columns(3)
    choice = None
    if b1.button("Aprobar (solo diario)", key=f"btn_approve_{sid}_{nonce}", type="primary"):
        choice = "approve"
    if b2.button("Rechazar", key=f"btn_reject_{sid}_{nonce}"):
        choice = "reject"
    if b3.button("Omitir", key=f"btn_skip_{sid}_{nonce}"):
        choice = "skip"
    if choice is None:
        return
    try:
        q.validate_decision(choice, reason)
        with q.open_db(settings.db_path, read_only=False) as con:
            q.record_panel_decision(
                con,
                sid,
                choice,
                reason=reason,
                planned_shares=plan.shares,
                planned_entry=plan.entry,
                planned_stop=plan.stop,
            )
            st.session_state[NONCE_KEY] = nonce + 1  # antes de cualquier otro paso
    except q.DecisionInputError as exc:
        st.error(str(exc))
        return
    except q.PanelDbError as exc:
        show_db_error(exc)
        st.caption("Tu decisión NO se guardó. Vuelve a intentarlo en un momento.")
        return
    label = q.DECISION_LABELS[choice]
    flash(
        "success",
        f"Decisión guardada en tu diario: {label} ({detail.ticker or sid}). "
        "No se envió ninguna orden.",
    )
    st.rerun()


# ------------------------------------------------------------------------------ Diario


def render_journal(settings: Settings, cfg: AppConfig) -> None:
    horizons = [int(h) for h in cfg.outcomes.horizons_days] or [21]
    default_idx = horizons.index(21) if 21 in horizons else 0
    horizon = st.selectbox(
        "Horizonte",
        options=horizons,
        index=default_idx,
        format_func=p.horizon_label,
        key="diario_horizonte",
        help="Cuántos días hábiles después de comprar (en la apertura siguiente) se mide.",
    )
    try:
        with q.open_db(settings.db_path) as con:
            summary = q.outcome_summary(con, horizon)
            pending = q.pending_outcomes(con, horizon)
            history = q.decisions_history(con, horizon)
    except q.PanelDbError as exc:
        show_db_error(exc)
        return

    bench = settings.benchmark_ticker
    with st.expander("¿Qué significa cada grupo?", expanded=summary.empty):
        st.markdown(
            "\n".join(
                f"- **{p.GROUP_LABELS[g]}**: {p.GROUP_EXPLANATIONS[g]}" for g in q.GROUP_ORDER
            )
        )
        st.caption(
            "Todas se miden igual: compra en la apertura del día hábil siguiente a la fecha "
            f"pública y venta al cierre del día {horizon}. El exceso compara contra {bench} "
            "en las mismas fechas. No incluye comisiones."
        )

    if summary.empty:
        st.info(
            "Aún no hay resultados a este horizonte. Se calculan cuando pasan los días: "
            "uv run tt resultados (o uv run tt diario)."
        )
    else:
        small = p.small_sample_groups(summary)
        if small:
            st.warning(
                f"{p.SMALL_SAMPLE_MSG} Grupos con menos de {p.SMALL_SAMPLE_N} ideas: "
                f"{', '.join(small)}."
            )
        cols = p.summary_columns(bench)
        display = p.summary_for_display(summary, bench)
        pct_names = {cols[c][0] for c in p.PCT_SUMMARY_COLS}
        config = {}
        for name, help_text in cols.values():
            if name in pct_names:
                config[name] = st.column_config.NumberColumn(name, help=help_text, format="%.2f%%")
            else:
                config[name] = st.column_config.Column(name, help=help_text)
        st.dataframe(display, hide_index=True, column_config=config)
        chart = charts.outcome_chart(summary, bench, dark=is_dark_theme())
        if chart is not None:
            st.markdown(f"**Retorno medio y exceso vs {bench} por grupo**")
            st.altair_chart(chart, width="stretch")
    if pending:
        st.caption(
            f"{pending} ideas todavía no tienen resultado a {horizon} días hábiles "
            "(faltan días o faltan precios)."
        )

    st.markdown("#### Tus decisiones")
    if history.empty:
        st.caption("Todavía no has registrado decisiones.")
        return
    hist = pd.DataFrame(
        {
            "Decidida (UTC)": [p.fmt_date(v) for v in history["decided_at"]],
            "Ticker": history["ticker"].fillna(p.DASH),
            "Empresa": history["issuer_name"].fillna(p.DASH),
            "Fecha idea": history["as_of_date"],
            "Decisión": history["decision_label"],
            "Vigente": history["vigente"],
            "Motivo": history["reason"].fillna(""),
            "Acciones plan": history["planned_shares"],
            "Entrada plan": history["planned_entry"],
            "Stop plan": history["planned_stop"],
            "Retorno": [None if pd.isna(v) else v * 100 for v in history["ret"]],
            f"Exceso vs {bench}": [None if pd.isna(v) else v * 100 for v in history["excess_ret"]],
            "Tarde": history["decidida_tarde"],
        }
    )
    st.dataframe(
        hist,
        hide_index=True,
        column_config={
            "Vigente": st.column_config.CheckboxColumn(
                "Vigente", help="La última decisión sobre esa idea (la que cuenta)."
            ),
            "Retorno": st.column_config.NumberColumn("Retorno", format="%.2f%%"),
            f"Exceso vs {bench}": st.column_config.NumberColumn(
                f"Exceso vs {bench}", format="%.2f%%"
            ),
            "Fecha idea": st.column_config.DateColumn("Fecha idea", format=DATE_WIDGET_FORMAT),
            "Acciones plan": st.column_config.NumberColumn("Acciones plan", format="%d"),
            "Entrada plan": st.column_config.NumberColumn("Entrada plan", format="US$%.2f"),
            "Stop plan": st.column_config.NumberColumn("Stop plan", format="US$%.2f"),
            "Tarde": st.column_config.CheckboxColumn(
                "Tarde",
                help="Decidiste en o después de la apertura del día de entrada: ya podías "
                "saber parte del resultado (sesgo de retrospectiva).",
            ),
        },
    )
    if any(v is True for v in history["decidida_tarde"]):
        st.caption(
            "Las decisiones marcadas como 'Tarde' se tomaron cuando ya se conocía parte del "
            "resultado; no sirven para medir tu criterio."
        )


# ------------------------------------------------------------------------------ Calculadora


def render_calculator(cfg: AppConfig) -> None:
    st.markdown(
        "Calcula cuántas acciones comprar para que, si el precio toca el stop, pierdas solo "
        "el porcentaje de riesgo elegido. Incluye comisiones de IBKR y un spread conservador."
    )
    capital, risk_pct, entry, stop, adv = sizing_inputs("calc", cfg, None, None, None)
    atr_v = st.number_input(
        "ATR / movimiento diario típico (USD, opcional)",
        min_value=0.0,
        value=None,
        step=0.01,
        format="%.2f",
        key="calc_atr",
        help=f"Si lo escribes y dejas el stop vacío, se sugiere stop = entrada - "
        f"{p.fmt_num(cfg.risk.stop_atr_multiple, 1)} x ATR.",
    )
    if stop is None and entry is not None and atr_v:
        suggested = p.sizing_defaults(
            {"price_last": entry, "atr14": atr_v}, cfg.risk.stop_atr_multiple
        ).stop
        if suggested is not None:
            st.caption(f"Stop sugerido con ATR: {p.fmt_money(suggested)}")
            stop = suggested
    result, message = p.compute_sizing(cfg, capital, risk_pct, entry, stop, adv)
    render_sizing(result, message)


# ------------------------------------------------------------------------------ Estado


def render_status(settings: Settings, cfg: AppConfig) -> None:
    st.markdown("#### Datos")
    try:
        with q.open_db(settings.db_path) as con:
            status = q.data_status(con)
            runs = q.last_runs(con, 10)
            versions = q.strategy_versions(con)
            coverage = q.price_coverage(con)
    except q.PanelDbError as exc:
        show_db_error(exc)
    else:
        for w in p.freshness_warnings(status):
            st.warning(w)
        st.dataframe(
            pd.DataFrame(
                {
                    "Datos": status["descripcion"],
                    "Filas": status["filas"],
                    "Último dato": [p.fmt_date(v) for v in status["ultimo_dato"]],
                    "Antigüedad": [p.days_ago_text(v) for v in status["dias_desde"]],
                }
            ),
            hide_index=True,
        )
        if coverage.get("tickers"):
            st.caption(
                f"Precios de {coverage['tickers']} tickers, del "
                f"{p.fmt_date(coverage['first'])} al {p.fmt_date(coverage['last'])}."
            )
        st.markdown("#### Últimas ejecuciones")
        if runs.empty:
            st.caption("Todavía no hay ejecuciones registradas.")
        else:
            st.dataframe(
                pd.DataFrame(
                    {
                        "Tipo": runs["kind"],
                        "Inicio (UTC)": [p.fmt_date(v) for v in runs["started_at"]],
                        "Estado": runs["status"].fillna(p.DASH),
                        "Duración (s)": runs["duracion_s"],
                        "Notas": runs["notes"].fillna(""),
                        "Commit": runs["git_commit"].fillna(p.DASH),
                        "Config": runs["config_hash"].fillna(p.DASH),
                    }
                ),
                hide_index=True,
                column_config={
                    "Duración (s)": st.column_config.NumberColumn("Duración (s)", format="%.0f")
                },
            )
        if not versions.empty:
            st.markdown("**Versiones de estrategia en tus ideas**")
            st.dataframe(
                pd.DataFrame(
                    {
                        "Versión": versions["strategy_version"],
                        "Hash de configuración": versions["config_hash"],
                        "Origen": [p.origin_label(v) for v in versions["origin"]],
                        "Ideas": versions["n"],
                        "Primera": [p.fmt_date(v) for v in versions["primera"]],
                        "Última": [p.fmt_date(v) for v in versions["ultima"]],
                    }
                ),
                hide_index=True,
            )

    st.markdown("#### Estrategia y configuración actual")
    st.markdown(
        f"- Versión de estrategia: `{cfg.screener.strategy_version}`\n"
        f"- Hash de toda la configuración: `{cfg.config_hash()}`\n"
        f"- Hash del screener: `{cfg.screener_hash()}`\n"
        f"- Capital del satélite: {p.fmt_money(cfg.risk.capital_usd)} · riesgo por "
        f"operación {p.fmt_pct_points(cfg.risk.risk_per_trade_pct, 2)} · stop a "
        f"{p.fmt_num(cfg.risk.stop_atr_multiple, 1)} x ATR"
    )
    st.caption(
        "Si cambias los archivos de config/, el hash cambia; las ideas nuevas quedan marcadas "
        "con el hash nuevo para poder comparar versiones."
    )

    st.markdown("#### Conexión con IBKR (solo lectura)")
    st.caption(
        f"Se conecta a IB Gateway / TWS en {settings.ibkr_host}:{settings.ibkr_port} en modo "
        "solo lectura. No puede enviar órdenes. Por seguridad solo acepta cuentas paper "
        "salvo que lo cambies en .env."
    )
    if st.button("Probar conexión IBKR (solo lectura)", key="btn_ibkr"):
        with st.spinner("Conectando con IBKR..."):
            probe = p.probe_ibkr(settings)
        if probe.ok and probe.snapshot is not None:
            st.success(probe.message)
            st.dataframe(p.snapshot_values_table(probe.snapshot), hide_index=True)
            positions = p.snapshot_positions_table(probe.snapshot)
            if positions.empty:
                st.caption("Sin posiciones abiertas.")
            else:
                st.dataframe(positions, hide_index=True)
        else:
            st.error(probe.message)


# ------------------------------------------------------------------------------ rotación ETF


def render_etf(settings: Settings) -> None:
    """Rotación de ETFs: veredicto, recomendación del mes y curvas del backtest (sin órdenes)."""
    from tradingtool.config import load_etf_config
    from tradingtool.etf.strategies import LABELS

    st.subheader("Rotación de ETFs (rotacion-v1)")
    st.caption(
        "Una vez al mes elige entre acciones de EE. UU., de otros países desarrollados y "
        "emergentes, solo si le ganan al efectivo; si no, se refugia en bonos del Tesoro o "
        "efectivo. Reglas congeladas en docs/ETF-ROTACION.md."
    )
    try:
        ecfg = load_etf_config(settings.config_dir)
    except Exception as exc:  # YAML mal escrito o valores fuera de rango
        st.error(f"Hay un error en config/etf.yaml: {exc}")
        return
    try:
        with q.open_db(settings.db_path) as con:
            state = q.etf_state(con)
    except q.PanelDbError as exc:
        show_db_error(exc)
        return

    verdict = state["verdict"]
    rules = ecfg.rules_hash()
    if not verdict or verdict.get("reglas") != rules:
        st.info(
            "Aún no hay backtest con estas reglas. En la terminal: "
            "`uv run tt etf-precios` y luego `uv run tt etf-backtest`.",
            icon=":material/info:",
        )
    elif verdict.get("veredicto") == "PASA":
        st.success(
            f"Backtest pre-registrado: PASA ({verdict.get('fecha', '')}). Siguiente paso: "
            "seguimiento en papel. Nunca dinero real sin tu autorización escrita.",
            icon=":material/check_circle:",
        )
    elif verdict.get("veredicto") == "NO PASA":
        st.error(
            f"Backtest pre-registrado: NO PASA ({verdict.get('fecha', '')}). Las señales "
            "quedan solo como seguimiento en papel; no son recomendaciones.",
            icon=":material/cancel:",
        )
    else:
        st.warning(
            f"Backtest pre-registrado: {verdict.get('veredicto', '?')}. Faltan datos para "
            "decidir (revisa `uv run tt etf-precios`).",
            icon=":material/warning:",
        )

    st.markdown("#### Recomendación del mes")
    recs = state["recommendations"]
    if recs is None or recs.empty:
        st.caption(
            "Todavía no hay recomendaciones guardadas. En la terminal: `uv run tt etf-senal`."
        )
    else:
        last = recs.iloc[0]
        weights = q.etf_weights(last["weights"])
        as_of = p.fmt_date(q._to_date(last["as_of_date"]))
        st.caption(
            f"Con el cierre del {as_of} (se opera el primer día hábil siguiente). "
            "Queda guardada y no se reescribe."
        )
        st.dataframe(
            pd.DataFrame(
                {
                    "Activo": [LABELS.get(a, a) for a in weights],
                    "Sustituto en el backtest": list(weights),
                    "ETF UCITS (LSE, USD)": [ecfg.ucits.get(a, "?") for a in weights],
                    "Peso": [w * 100 for w in weights.values()],
                }
            ).sort_values("Peso", ascending=False),
            hide_index=True,
            column_config={"Peso": st.column_config.NumberColumn("Peso", format="%.0f%%")},
        )
        if len(recs) > 1:
            with st.expander("Historial de recomendaciones"):
                st.dataframe(
                    pd.DataFrame(
                        {
                            "Cierre de mes": [
                                p.fmt_date(q._to_date(v)) for v in recs["as_of_date"]
                            ],
                            "Cartera": [
                                ", ".join(
                                    f"{a} {w:.0%}"
                                    for a, w in sorted(
                                        q.etf_weights(x).items(), key=lambda kv: -kv[1]
                                    )
                                )
                                for x in recs["weights"]
                            ],
                            "Reglas": recs["rules_hash"],
                        }
                    ),
                    hide_index=True,
                )

    st.markdown("#### Backtest: validación 2015–2024")
    curves = q.etf_curves(settings.data_dir / "etf" / "curvas_validacion.csv")
    if curves.empty:
        st.caption("Sin curvas todavía: aparecen al correr `uv run tt etf-backtest`.")
        return
    dark = is_dark_theme()
    growth = charts.etf_curves_chart(curves, "valor", dark=dark)
    drawdown = charts.etf_curves_chart(curves, "caida", dark=dark, height=220)
    if growth is not None:
        st.altair_chart(growth, width="stretch")
    if drawdown is not None:
        st.altair_chart(drawdown, width="stretch")
    st.caption(
        "Neto de costos (cuenta supuesta de US$5.000), sin impuestos. Gráficos con datos "
        "semanales; la tabla trae los valores exactos. Rendimientos pasados no garantizan "
        "rendimientos futuros."
    )
    summary = q.curves_summary(curves)
    st.dataframe(
        summary,
        hide_index=True,
        column_config={
            "Rinde/año": st.column_config.NumberColumn("Rinde/año", format="percent"),
            "Peor caída": st.column_config.NumberColumn("Peor caída", format="percent"),
            "US$1.000 →": st.column_config.NumberColumn("US$1.000 →", format="dollar"),
        },
    )
    with st.expander("Ver por año (tabla)"):
        yearly = q.curves_yearly(curves)
        st.dataframe(
            yearly,
            column_config={
                c: st.column_config.NumberColumn(c, format="percent") for c in yearly.columns
            },
        )


# ------------------------------------------------------------------------------ main


def main() -> None:
    st.set_page_config(page_title="Panel de inversión", layout="wide")
    st.title("Panel de apoyo a decisiones")
    st.warning(p.NO_ORDERS_BANNER, icon=":material/block:")
    st.caption(p.HONESTY_NOTE)

    settings = load_settings()
    if settings is None:
        st.stop()
        return
    cfg, cfg_error = load_app_config(settings)
    if cfg_error:
        st.error(cfg_error)
    archived = archived_note(cfg.screener.strategy_version)
    if archived:
        st.warning(archived, icon=":material/inventory_2:")
    render_sidebar(settings)
    if not settings.db_path.exists():
        render_onboarding()

    tab_etf, tab_ideas, tab_journal, tab_calc, tab_status = st.tabs(
        ["Rotación ETF", "Ideas (insiders)", "Diario y resultados", "Calculadora", "Estado"]
    )
    with tab_etf:
        render_etf(settings)
    with tab_ideas:
        render_ideas(settings, cfg)
    with tab_journal:
        render_journal(settings, cfg)
    with tab_calc:
        render_calculator(cfg)
    with tab_status:
        render_status(settings, cfg)


if __name__ == "__main__":
    main()
