"""Textos, formatos y pequeñas reglas de presentación del panel (sin Streamlit).

Todo lo que convierte datos en texto en español vive aquí para poder probarlo sin
levantar la interfaz. Convenciones:
- Dinero en USD con 2 decimales (``US$1,234.50``); porcentajes con 2 decimales.
- Punto decimal (igual que las casillas de la calculadora) para no confundir al escribir.
- Fechas en formato AAAA-MM-DD (el mismo que usan los comandos ``tt``).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd
from pydantic import ValidationError

from tradingtool.broker.ibkr import (
    AccountSnapshot,
    BrokerConnectionError,
    IbkrReadOnly,
    LiveAccountRefusedError,
)
from tradingtool.config import AppConfig, RiskConfig
from tradingtool.models import SizingResult
from tradingtool.risk.sizing import size_position
from tradingtool.settings import Settings

DASH = "—"
DATE_FORMAT = "%Y-%m-%d"

NO_ORDERS_BANNER = (
    "Este panel NO envía órdenes. Aprobar solo lo registra en tu diario para medir resultados."
)
HONESTY_NOTE = (
    "Herramienta de estudio, no asesoría financiera. Cada estrategia se prueba con reglas "
    "congeladas antes de ver resultados; aun así, nada garantiza ganancias: mide antes de "
    "arriesgar dinero real."
)
SMALL_SAMPLE_N = 30
SMALL_SAMPLE_MSG = "Muestra pequeña: estos números todavía no dicen nada confiable."

# Pasos para empezar (se muestran cuando todavía no hay base de datos).
ONBOARDING_STEPS = (
    "Copia el archivo `.env.example` como `.env` y escribe tu nombre y correo en "
    "`TT_SEC_USER_AGENT` (la SEC lo exige).",
    "Ejecuta primero: `uv run tt iniciar` (crea la base de datos).",
    "Carga el histórico de compras de insiders (una sola vez, tarda): "
    "`uv run tt sec-historico --desde 2021Q1 --hasta 2026Q2`",
    "Cada día: `uv run tt diario` (descarga Form 4 nuevos, precios, genera ideas y "
    "calcula resultados).",
    "Abre este panel con `uv run tt panel`.",
)

COMMANDS_HELP = (
    ("uv run tt etf-senal", "Rotación de ETFs: recomendación del mes (una vez al mes)."),
    ("uv run tt etf-backtest", "Rotación de ETFs: backtest pre-registrado y veredicto."),
    ("uv run tt diario", "Insiders (archivada): pasos diarios de seguimiento."),
    ("uv run tt sec-diario", "Descarga los Form 4 nuevos de la SEC."),
    ("uv run tt precios", "Actualiza los precios diarios."),
    ("uv run tt screener", "Genera las ideas del día."),
    ("uv run tt resultados", "Calcula qué pasó con las ideas anteriores."),
    ("uv run tt revisar", "Revisa la configuración y la conexión."),
    ("uv run tt cuenta", "Muestra tu cuenta de IBKR (solo lectura)."),
)

# ------------------------------------------------------------------------------ números


def _num(value: Any) -> float | None:
    """float finito o None (acepta int, float, numpy, texto numérico)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def fmt_num(value: Any, decimals: int = 2) -> str:
    v = _num(value)
    return DASH if v is None else f"{v:,.{decimals}f}"


def fmt_int(value: Any) -> str:
    v = _num(value)
    return DASH if v is None else f"{round(v):,}"


def fmt_shares(value: Any) -> str:
    """Acciones: enteras sin decimales; fraccionarias con 2 decimales."""
    v = _num(value)
    if v is None:
        return DASH
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def fmt_money(value: Any) -> str:
    """USD con 2 decimales: ``US$1,234.50`` (``-US$5.00`` si es negativo)."""
    v = _num(value)
    if v is None:
        return DASH
    sign = "-" if v < 0 else ""
    return f"{sign}US${abs(v):,.2f}"


def fmt_money_compact(value: Any) -> str:
    """US$950.00 · US$12.30 mil · US$4.50 millones · US$1.20 mil millones."""
    v = _num(value)
    if v is None:
        return DASH
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        text = f"{a / 1e9:,.2f} mil millones"
    elif a >= 1e6:
        text = f"{a / 1e6:,.2f} millones"
    elif a >= 1e3:
        text = f"{a / 1e3:,.2f} mil"
    else:
        text = f"{a:,.2f}"
    return f"{sign}US${text}"


def fmt_pct(fraction: Any, decimals: int = 2, signed: bool = False) -> str:
    """Fracción -> porcentaje (0.1234 -> '12.34%')."""
    v = _num(fraction)
    if v is None:
        return DASH
    return f"{v * 100:+,.{decimals}f}%" if signed else f"{v * 100:,.{decimals}f}%"


def fmt_pct_points(points: Any, decimals: int = 2) -> str:
    """Valor ya expresado en puntos porcentuales (12.3 -> '12.30%')."""
    v = _num(points)
    return DASH if v is None else f"{v:,.{decimals}f}%"


def fmt_date(value: Any) -> str:
    """AAAA-MM-DD (y la hora HH:MM si es un datetime)."""
    if value is None:
        return DASH
    if isinstance(value, datetime):
        return value.strftime(f"{DATE_FORMAT} %H:%M")
    if isinstance(value, date):
        return value.strftime(DATE_FORMAT)
    try:
        if pd.isna(value):
            return DASH
        return pd.Timestamp(value).strftime(DATE_FORMAT)
    except (TypeError, ValueError):
        return str(value)


def days_ago_text(days: Any) -> str:
    v = _num(days)
    if v is None:
        return DASH
    d = int(v)
    if d <= 0:
        return "hoy"
    if d == 1:
        return "ayer"
    return f"hace {d} días"


_MD_SPECIAL = set("\\`*_{}[]<>#|$~")


def md_escape(text: Any) -> str:
    """Escapa texto externo para st.markdown (evita negritas, enlaces o LaTeX con '$')."""
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in str(text))


# ------------------------------------------------------------------------------ horizontes

_HORIZON_HINTS = {
    5: "~1 semana",
    10: "~2 semanas",
    21: "~1 mes",
    42: "~2 meses",
    63: "~3 meses",
    126: "~6 meses",
    252: "~1 año",
}


def horizon_label(days: int) -> str:
    hint = _HORIZON_HINTS.get(int(days))
    base = f"{int(days)} días hábiles"
    return f"{base} ({hint})" if hint else base


# ------------------------------------------------------------------------------ features

# Claves exactas que produce el screener ``insider-v1`` (insiders/screener.py).
# clave -> (etiqueta, tipo, ayuda). Tipos: int, money, money_compact, price, pct, days,
# bool, date, list, insiders.
FEATURE_INFO: dict[str, tuple[str, str, str]] = {
    "n_insiders": (
        "Insiders que compraron (este reporte)",
        "int",
        "Personas de la empresa con compras válidas reportadas en esta fecha.",
    ),
    "n_insiders_window": (
        "Insiders comprando en la ventana",
        "int",
        "Insiders distintos con compras válidas en los últimos días (ventana del cluster, "
        "hasta la fecha de la idea). Varias personas a la vez suele ser mejor señal.",
    ),
    "total_value": (
        "Valor total comprado",
        "money",
        "Suma en dólares de las compras válidas en mercado abierto de este reporte.",
    ),
    "max_insider_value": (
        "Mayor compra de un solo insider",
        "money",
        "Lo que compró el insider que más invirtió en este reporte.",
    ),
    "any_officer": (
        "¿Compró algún directivo?",
        "bool",
        "Directivo (officer) = CEO, CFO u otro cargo ejecutivo.",
    ),
    "any_director": (
        "¿Compró algún director?",
        "bool",
        "Miembro de la junta directiva.",
    ),
    "opportunistic_count": (
        "Insiders oportunistas",
        "int",
        "Compran fuera de su patrón habitual. En los estudios, sus compras han sido las más "
        "informativas.",
    ),
    "routine_count": (
        "Insiders rutinarios",
        "int",
        "Compran casi siempre en el mismo mes cada año; sus compras informan poco.",
    ),
    "unclassified_count": (
        "Insiders sin clasificar",
        "int",
        "No tienen historia suficiente (3 años) para saber si son rutinarios u oportunistas.",
    ),
    "pct_increase_max": (
        "Mayor aumento de participación",
        "pct",
        "Cuánto creció la cantidad de acciones del insider que más aumentó (100% = la duplicó).",
    ),
    "new_position": (
        "¿Alguien abrió una posición nueva?",
        "bool",
        "Un insider que no tenía acciones y compró por primera vez.",
    ),
    "filing_lag_max": (
        "Máx. días entre compra y reporte",
        "days",
        "Días calendario entre la compra y su publicación en la SEC.",
    ),
    "insider_price_max": (
        "Precio más alto pagado por un insider",
        "price",
        "Precio de la transacción tal como aparece en el Form 4 (sin ajustar por splits "
        "posteriores).",
    ),
    "n_purchase_rows": (
        "Líneas de compra en los Form 4",
        "int",
        "Transacciones de compra leídas en los formularios de este evento (válidas o no).",
    ),
    "transaction_dates": (
        "Fechas de las compras",
        "list",
        "Días en que los insiders compraron (antes de reportarlo).",
    ),
    "price_last": (
        "Último cierre conocido",
        "price",
        "Último cierre disponible cuando se generó la idea (no es el precio de hoy).",
    ),
    "price_last_date": (
        "Fecha del último cierre",
        "date",
        "Día del cierre usado para el último precio, la liquidez y el ATR.",
    ),
    "adv20": (
        "Volumen diario promedio (20 días)",
        "money_compact",
        "Dólares negociados por día en promedio hasta la fecha del último cierre. Más alto = "
        "más fácil comprar y vender sin mover el precio.",
    ),
    "atr14": (
        "Movimiento diario típico (ATR)",
        "price",
        "Rango promedio de un día de esta acción hasta la fecha del último cierre. Se usa "
        "para ubicar el stop.",
    ),
    "insiders": (
        "Insiders del reporte",
        "insiders",
        "Detalle por persona en la tabla «Quién compró».",
    ),
}

KEY_METRICS = ("n_insiders", "total_value", "price_last", "adv20")


@dataclass(frozen=True)
class FeatureRow:
    key: str
    label: str
    value: str
    help: str


def _generic_value(value: Any) -> str:
    if value is None:
        return DASH
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, int | float):
        v = _num(value)
        if v is None:
            return DASH
        if v.is_integer() or abs(v) >= 1000:
            return f"{v:,.0f}"
        return f"{v:,.2f}" if abs(v) >= 1 else f"{v:.4g}"
    if isinstance(value, list | tuple | set):
        items = [_generic_value(x) for x in value if x is not None]
        return ", ".join(items) if items else DASH
    if isinstance(value, Mapping):
        items = [f"{k}: {_generic_value(v)}" for k, v in value.items()]
        return "; ".join(items) if items else DASH
    text = str(value).strip()
    return text or DASH


def _bool_value(value: Any) -> str | None:
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, int | float) and value in (0, 1):
        return "Sí" if value else "No"
    return None


def format_feature(key: str, value: Any, features: Mapping[str, Any] | None = None) -> str:
    """Formatea un valor de feature según su tipo conocido; si no, genérico (nunca falla).

    ``features`` (opcional) da contexto: p. ej. ``pct_increase_max`` vacío con
    ``new_position`` verdadero se muestra como "Posición nueva".
    """
    info = FEATURE_INFO.get(key)
    if info is None:
        return _generic_value(value)
    kind = info[1]
    feats = features if isinstance(features, Mapping) else {}
    if kind == "list":
        return _generic_value(value)
    if kind == "insiders":
        if isinstance(value, list | tuple):
            n = sum(1 for x in value if isinstance(x, Mapping))
            return f"{n} (ver tabla «Quién compró»)" if n else DASH
        return _generic_value(value)
    if kind == "bool":
        return _bool_value(value) or _generic_value(value)
    if kind == "date":
        return fmt_date(value) if value else DASH
    if kind == "pct" and value is None and feats.get("new_position") is True:
        return "Posición nueva (no tenía acciones)"
    v = _num(value)
    if v is None:
        return _generic_value(value)
    if kind == "int":
        return fmt_int(v)
    if kind in ("money", "price"):
        return fmt_money(v)
    if kind == "money_compact":
        return fmt_money_compact(v)
    if kind == "pct":
        return fmt_pct(v)
    if kind == "days":
        return f"{fmt_int(v)} días"
    return _generic_value(value)  # pragma: no cover - tipos cerrados


def feature_label(key: str, features: Mapping[str, Any] | None = None) -> str:
    """Etiqueta en español; el último cierre lleva su fecha (precio "a esa fecha")."""
    info = FEATURE_INFO.get(key)
    if info is None:
        return _pretty_key(key)
    label = info[0]
    feats = features if isinstance(features, Mapping) else {}
    if key == "price_last" and feats.get("price_last_date"):
        label = f"Último cierre al {fmt_date(feats['price_last_date'])}"
    return label


def _pretty_key(key: str) -> str:
    text = str(key).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "(sin nombre)"


def feature_rows(features: Mapping[str, Any] | None) -> list[FeatureRow]:
    """Filas legibles: primero las claves conocidas (en orden fijo), luego el resto."""
    if not isinstance(features, Mapping):
        return []
    rows = []
    for key, (_label, _kind, help_text) in FEATURE_INFO.items():
        if key in features:
            rows.append(
                FeatureRow(
                    key,
                    feature_label(key, features),
                    format_feature(key, features[key], features),
                    help_text,
                )
            )
    for key in sorted((k for k in features if k not in FEATURE_INFO), key=str):
        rows.append(FeatureRow(str(key), _pretty_key(key), _generic_value(features[key]), ""))
    return rows


def key_metric_rows(features: Mapping[str, Any] | None) -> list[FeatureRow]:
    rows = {r.key: r for r in feature_rows(features)}
    return [rows[k] for k in KEY_METRICS if k in rows]


# ------------------------------------------------------------------------------ insiders

CLASS_LABELS = {
    "oportunista": "Oportunista (compra fuera de su patrón habitual)",
    "rutinario": "Rutinario (compra casi siempre en el mismo mes)",
    "no_clasificable": "Sin clasificar (no hay historia suficiente)",
}

INSIDER_COLUMNS = (
    "Nombre",
    "Cargo",
    "Valor comprado",
    "Acciones",
    "Aumento de participación",
    "Tipo de insider",
    "¿Cuenta para la señal?",
    "Por qué no cuenta",
)


def class_label(value: Any) -> str:
    """oportunista / rutinario / no_clasificable en español sencillo."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "Sin dato"
    return CLASS_LABELS.get(str(value).strip(), str(value))


def insiders_table(features: Mapping[str, Any] | None) -> pd.DataFrame:
    """Tabla «Quién compró» a partir de ``features['insiders']`` (nunca falla).

    Primero los que cuentan para la señal, de mayor a menor valor comprado.
    """
    feats = features if isinstance(features, Mapping) else {}
    raw = feats.get("insiders")
    items = [x for x in raw if isinstance(x, Mapping)] if isinstance(raw, list | tuple) else []
    rows = []
    for ins in items:
        valida = ins.get("valida")
        motivos = ins.get("motivos")
        if isinstance(motivos, list | tuple):
            motivos_text = "; ".join(str(m) for m in motivos if m)
        else:
            motivos_text = str(motivos) if motivos else ""
        if ins.get("posicion_nueva") is True:
            aumento = "Posición nueva"
        else:
            aumento = fmt_pct(ins.get("aumento_participacion"))
        rows.append(
            {
                "Nombre": str(ins.get("nombre") or "").strip() or "(sin nombre)",
                "Cargo": str(ins.get("cargo") or "").strip() or DASH,
                "Valor comprado": fmt_money(ins.get("valor")),
                "Acciones": fmt_shares(ins.get("acciones")),
                "Aumento de participación": aumento,
                "Tipo de insider": class_label(ins.get("clase")),
                "¿Cuenta para la señal?": "Sí" if valida is True else "No",
                "Por qué no cuenta": "" if valida is True else motivos_text,
                "_valida": valida is True,
                "_valor": _num(ins.get("valor")) or 0.0,
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(INSIDER_COLUMNS))
    df = pd.DataFrame(rows).sort_values(["_valida", "_valor"], ascending=[False, False])
    return df[list(INSIDER_COLUMNS)].reset_index(drop=True)


# ------------------------------------------------------------------------------ tamaño


@dataclass(frozen=True)
class SizingDefaults:
    entry: float | None
    stop: float | None
    adv: float | None
    atr: float | None


def _positive(value: Any) -> float | None:
    v = _num(value)
    return v if v is not None and v > 0 else None


def sizing_defaults(features: Mapping[str, Any] | None, stop_atr_multiple: float) -> SizingDefaults:
    """Valores iniciales de la calculadora a partir de las features de la señal.

    entrada = price_last (redondeado a centavos); stop = entrada - stop_atr_multiple x atr14
    (si ambos existen y el stop queda por encima de 0 y por debajo de la entrada).
    Ojo: ``price_last`` es el cierre de ``price_last_date``, no el precio de hoy.
    """
    feats = features if isinstance(features, Mapping) else {}
    entry = _positive(feats.get("price_last"))
    atr_v = _positive(feats.get("atr14"))
    adv = _positive(feats.get("adv20"))
    stop = None
    if entry is not None:
        entry = round(entry, 2)
        if entry <= 0:  # acción de menos de medio centavo: no se puede prellenar
            entry = None
        elif atr_v is not None:
            candidate = round(entry - stop_atr_multiple * atr_v, 2)
            if 0 < candidate < entry:
                stop = candidate
    return SizingDefaults(entry=entry, stop=stop, adv=adv, atr=atr_v)


def compute_sizing(
    cfg: AppConfig,
    capital: Any,
    risk_pct: Any,
    entry: Any,
    stop: Any,
    adv: Any = None,
) -> tuple[SizingResult | None, str | None]:
    """Calcula el tamaño con ``risk.sizing.size_position`` (mismas reglas que ``tt riesgo``).

    Devuelve (resultado, None) o (None, mensaje en español si faltan datos o son inválidos).
    """
    entry_v, stop_v = _positive(entry), _positive(stop)
    capital_v, risk_v = _positive(capital), _positive(risk_pct)
    if entry_v is None or stop_v is None:
        return None, "Escribe el precio de entrada y el stop para calcular el tamaño."
    if capital_v is None or risk_v is None:
        return None, "El capital y el riesgo por operación deben ser mayores que cero."
    if stop_v >= entry_v:
        return None, "El stop debe estar por debajo de la entrada (esto es una compra)."
    try:
        risk_cfg = RiskConfig.model_validate(
            {**cfg.risk.model_dump(), "risk_per_trade_pct": risk_v}
        )
    except ValidationError:
        return None, "El riesgo por operación debe estar entre 0.01% y 2%."
    try:
        result = size_position(
            risk_cfg, cfg.costs, entry_v, stop_v, capital_usd=capital_v, adv_usd=_positive(adv)
        )
    except ValueError as exc:
        return None, str(exc)
    return result, None


@dataclass(frozen=True)
class DecisionPlan:
    """Lo que se guarda en el diario junto con una aprobación."""

    shares: int | None
    entry: float | None
    stop: float | None

    def describe(self) -> str:
        if self.shares is None and self.entry is None and self.stop is None:
            return "sin plan de tamaño (la calculadora no tiene datos válidos)"
        return (
            f"{fmt_int(self.shares) if self.shares else DASH} acciones, entrada "
            f"{fmt_money(self.entry)}, stop {fmt_money(self.stop)}"
        )


def decision_plan(result: SizingResult | None, entry: Any, stop: Any) -> DecisionPlan:
    """Plan a registrar: el mismo resultado que muestra la calculadora.

    Con un cálculo válido se usan sus acciones, entrada y stop. Sin cálculo válido solo se
    guarda la entrada (y el stop si está por debajo); nunca se inventan acciones.
    """
    if result is not None:
        return DecisionPlan(
            shares=int(result.shares) if result.shares > 0 else None,
            entry=float(result.entry),
            stop=float(result.stop),
        )
    entry_v, stop_v = _positive(entry), _positive(stop)
    if entry_v is not None and stop_v is not None and stop_v >= entry_v:
        stop_v = None
    return DecisionPlan(shares=None, entry=entry_v, stop=stop_v)


# ------------------------------------------------------------------------------ resultados

GROUP_LABELS = {
    "aprobada": "Aprobadas",
    "rechazada": "Rechazadas",
    "sin decisión": "Sin decisión",
    "bloqueada": "Bloqueadas (contrafactuales)",
}

GROUP_EXPLANATIONS = {
    "aprobada": "Pasaron los filtros y tú las aprobaste.",
    "rechazada": (
        "Pasaron los filtros y tú las rechazaste. Si les va mejor que a las aprobadas, tu "
        "criterio personal está restando en vez de sumar."
    ),
    "sin decisión": (
        "Pasaron los filtros pero no decidiste o marcaste 'Omitir'. Muestran qué habría "
        "pasado siguiendo la regla sin tu intervención."
    ),
    "bloqueada": (
        "NO pasaron los filtros (contrafactuales). Si a estas les va igual o mejor que a las "
        "que pasan, los filtros no están ayudando."
    ),
}


def summary_columns(benchmark: str) -> dict[str, tuple[str, str]]:
    """columna original -> (nombre en español, explicación)."""
    return {
        "grupo": ("Grupo", "Tipo de idea según los filtros y tu decisión."),
        "n": ("Ideas (n)", "Cuántas ideas tienen resultado a este horizonte."),
        "ret_medio": (
            "Retorno medio",
            "Promedio de ganancia o pérdida comprando en la apertura del día siguiente.",
        ),
        "ret_mediano": (
            "Retorno mediano",
            "El del medio: la mitad de las ideas ganó más y la otra mitad menos.",
        ),
        "tasa_acierto": ("% que subieron", "Porcentaje de ideas con retorno positivo."),
        "exceso_medio": (
            f"Exceso medio vs {benchmark}",
            f"Retorno menos el de {benchmark} en las mismas fechas. Positivo = le ganó al mercado.",
        ),
        "mae_medio": (
            "Peor caída media",
            "En promedio, cuánto llegó a caer por debajo de la entrada (MAE).",
        ),
        "mfe_medio": (
            "Mejor subida media",
            "En promedio, cuánto llegó a subir por encima de la entrada (MFE).",
        ),
        "truncadas": (
            "Truncadas",
            "Ideas cuya acción dejó de cotizar antes del horizonte (se cerró con el último "
            "precio).",
        ),
    }


PCT_SUMMARY_COLS = (
    "ret_medio",
    "ret_mediano",
    "tasa_acierto",
    "exceso_medio",
    "mae_medio",
    "mfe_medio",
)


def summary_for_display(summary: pd.DataFrame, benchmark: str = "SPY") -> pd.DataFrame:
    """Tabla de resultados en español; porcentajes en puntos (12.34 = 12.34%)."""
    cols = summary_columns(benchmark)
    if summary is None or summary.empty:
        return pd.DataFrame(columns=[name for name, _ in cols.values()])
    df = summary.copy()
    for c in PCT_SUMMARY_COLS:
        if c in df.columns:
            df[c] = [None if _num(v) is None else round(float(v) * 100, 2) for v in df[c]]
    if "grupo" in df.columns:
        df["grupo"] = [GROUP_LABELS.get(str(g), str(g)) for g in df["grupo"]]
    keep = [c for c in cols if c in df.columns]
    return df[keep].rename(columns={c: cols[c][0] for c in keep})


def small_sample_groups(summary: pd.DataFrame, threshold: int = SMALL_SAMPLE_N) -> list[str]:
    """Grupos con menos de ``threshold`` ideas (etiquetas en español)."""
    if summary is None or summary.empty or "n" not in summary.columns:
        return []
    out = []
    for g, n in zip(summary["grupo"], summary["n"], strict=True):
        v = _num(n)
        if v is None or v < threshold:
            out.append(GROUP_LABELS.get(str(g), str(g)))
    return out


def freshness_warnings(status: pd.DataFrame, max_age_days: int = 4) -> list[str]:
    """Avisos en español sobre datos vacíos o viejos, con el comando para arreglarlos."""
    if status is None or status.empty:
        return []
    by_table = {r["tabla"]: r for r in status.to_dict("records")}
    checks = (
        (
            "insider_filings",
            "No hay Form 4 cargados. Para el histórico ejecuta: "
            "uv run tt sec-historico --desde 2021Q1 --hasta 2026Q2",
            "Los Form 4 no se actualizan desde {age}. Ejecuta: uv run tt sec-diario "
            "(o uv run tt diario).",
        ),
        (
            "prices_daily",
            "No hay precios cargados. Ejecuta: uv run tt precios",
            "Los precios no se actualizan desde {age}. Ejecuta: uv run tt precios "
            "(o uv run tt diario).",
        ),
        (
            "signals",
            "Todavía no hay ideas. Ejecuta: uv run tt screener (o uv run tt diario).",
            "No hay ideas nuevas desde {age}. Ejecuta: uv run tt screener (o uv run tt diario).",
        ),
    )
    out = []
    for table, empty_msg, old_msg in checks:
        row = by_table.get(table)
        if row is None:
            continue
        n = _num(row.get("filas"))
        if n is None or n == 0:
            out.append(empty_msg)
            continue
        age = _num(row.get("dias_desde"))
        if age is not None and age > max_age_days:
            out.append(old_msg.format(age=days_ago_text(age)))
    return out


ORIGIN_LABELS = {"live": "Día a día", "backtest": "Histórico (backtest)"}


def origin_label(value: Any) -> str:
    return ORIGIN_LABELS.get(str(value), str(value) if value else DASH)


# ------------------------------------------------------------------------------ IBKR

SUMMARY_TAG_LABELS = {
    "NetLiquidation": "Valor total de la cuenta",
    "TotalCashValue": "Efectivo",
    "BuyingPower": "Poder de compra",
    "AvailableFunds": "Fondos disponibles",
    "GrossPositionValue": "Valor de las posiciones",
    "UnrealizedPnL": "Ganancia/pérdida no realizada",
    "RealizedPnL": "Ganancia/pérdida realizada",
}


@dataclass(frozen=True)
class IbkrProbe:
    ok: bool
    message: str
    snapshot: AccountSnapshot | None = None


def probe_ibkr(
    settings: Settings,
    ib_factory: Callable[[], Any] | None = None,
    timeout: float = 8.0,
) -> IbkrProbe:
    """Prueba la conexión de SOLO LECTURA con IBKR y trae un resumen de la cuenta.

    Usa ``IbkrReadOnly`` (``readonly=True``; no tiene funciones para enviar órdenes).
    Nunca lanza excepciones: cualquier problema vuelve como mensaje en español.
    """
    try:
        broker = IbkrReadOnly(settings, ib_factory=ib_factory)
    except Exception as exc:
        return IbkrProbe(False, f"No se pudo preparar la conexión con IBKR. Detalle: {exc!r}")
    try:
        broker.connect(timeout=timeout)
        snap = broker.snapshot()
    except LiveAccountRefusedError as exc:
        return IbkrProbe(False, f"Conexión rechazada por seguridad. {exc}")
    except BrokerConnectionError as exc:
        return IbkrProbe(False, str(exc))
    except Exception as exc:
        return IbkrProbe(False, f"Error inesperado al leer la cuenta de IBKR. Detalle: {exc!r}")
    finally:
        broker.disconnect()
    kind = "paper (simulada)" if snap.is_paper else "REAL, en solo lectura"
    return IbkrProbe(True, f"Conectado en solo lectura a la cuenta {snap.account} ({kind}).", snap)


def snapshot_values_table(snap: AccountSnapshot) -> pd.DataFrame:
    rows = [
        {
            "Concepto": SUMMARY_TAG_LABELS.get(tag, tag),
            "Valor": fmt_num(val, 2),
            "Moneda": cur or DASH,
        }
        for tag, (val, cur) in snap.values.items()
    ]
    return pd.DataFrame(rows, columns=["Concepto", "Valor", "Moneda"])


def snapshot_positions_table(snap: AccountSnapshot) -> pd.DataFrame:
    rows = [
        {
            "Símbolo": p.symbol,
            "Tipo": p.sec_type,
            "Moneda": p.currency,
            "Bolsa": p.exchange or DASH,
            "Cantidad": fmt_shares(p.quantity),
            "Costo promedio": fmt_num(p.avg_cost, 2),
        }
        for p in snap.positions
    ]
    return pd.DataFrame(
        rows, columns=["Símbolo", "Tipo", "Moneda", "Bolsa", "Cantidad", "Costo promedio"]
    )
