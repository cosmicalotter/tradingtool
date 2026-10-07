"""Gráficos del panel con Altair (incluido con Streamlit). Sin Streamlit aquí.

Criterios (guía de visualización del proyecto):
- Pocos gráficos y simples: precio con la fecha de la idea marcada y barras de resultado
  promedio por grupo.
- Paleta categórica validada (azul / naranja), con pasos propios para modo claro y oscuro.
- Líneas de 2 px, barras delgadas (<= 24 px) con punta redondeada, ejes y rejilla discretos;
  el texto nunca usa el color de la serie.
- Una sola escala vertical; tooltip al pasar el mouse; la tabla de datos va al lado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import altair as alt
import pandas as pd

from tradingtool.ui.queries import GROUP_ORDER


@dataclass(frozen=True)
class Palette:
    series1: str  # azul
    series2: str  # naranja
    surface: str
    ink_secondary: str
    muted: str
    baseline: str


LIGHT = Palette(
    series1="#2a78d6",
    series2="#eb6834",
    surface="#fcfcfb",
    ink_secondary="#52514e",
    muted="#898781",
    baseline="#c3c2b7",
)
DARK = Palette(
    series1="#3987e5",
    series2="#d95926",
    surface="#1a1a19",
    ink_secondary="#c3c2b7",
    muted="#898781",
    baseline="#383835",
)


def palette(dark: bool = False) -> Palette:
    return DARK if dark else LIGHT


# ------------------------------------------------------------------------------ precio


def price_chart(
    bars: pd.DataFrame,
    signal_date: date | None = None,
    *,
    dark: bool = False,
    height: int = 280,
) -> alt.LayerChart | None:
    """Línea de cierre diario con la fecha de la idea marcada. None si no hay datos."""
    if bars is None or bars.empty or "close" not in bars.columns:
        return None
    pal = palette(dark)
    df = bars[["date", "close"]].dropna().copy()
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    x = alt.X("date:T", title=None, axis=alt.Axis(format="%d/%m/%y", labelOverlap=True))
    y = alt.Y("close:Q", title="Cierre (USD)", scale=alt.Scale(zero=False))

    line = (
        alt.Chart(df)
        .mark_line(color=pal.series1, strokeWidth=2, strokeCap="round", strokeJoin="round")
        .encode(x=x, y=y)
    )

    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False)
    hover_rule = (
        alt.Chart(df)
        .mark_rule(color=pal.muted, strokeWidth=1)
        .encode(
            x="date:T",
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[
                alt.Tooltip("date:T", title="Fecha", format="%d/%m/%Y"),
                alt.Tooltip("close:Q", title="Cierre (USD)", format=",.2f"),
            ],
        )
        .add_params(hover)
    )
    hover_point = (
        alt.Chart(df)
        .mark_point(filled=True, size=70, color=pal.series1, stroke=pal.surface, strokeWidth=2)
        .encode(x="date:T", y="close:Q")
        .transform_filter(hover)
    )
    layers: list[alt.Chart] = [line]

    if signal_date is not None:
        sig_ts = pd.Timestamp(signal_date)
        sig = pd.DataFrame({"date": [sig_ts], "label": ["Fecha de la idea"]})
        layers.append(
            alt.Chart(sig).mark_rule(color=pal.ink_secondary, strokeWidth=1).encode(x="date:T")
        )
        layers.append(
            alt.Chart(sig)
            .mark_text(
                align="left", baseline="top", dx=4, dy=2, fontSize=11, color=pal.ink_secondary
            )
            .encode(x="date:T", y=alt.value(0), text="label:N")
        )
        on_or_before = df[df["date"] <= sig_ts]
        if not on_or_before.empty:
            mark = on_or_before.iloc[[-1]]
            layers.append(
                alt.Chart(mark)
                .mark_point(
                    filled=True, size=80, color=pal.series1, stroke=pal.surface, strokeWidth=2
                )
                .encode(x="date:T", y="close:Q")
            )

    layers += [hover_rule, hover_point]
    return alt.layer(*layers).properties(height=height)


# ------------------------------------------------------------------------------ resultados


def outcome_long(summary: pd.DataFrame, benchmark: str = "SPY") -> pd.DataFrame:
    """Pasa el resumen a formato largo: grupo, medida, valor (fracción), n."""
    m_ret, m_exc = "Retorno medio", f"Exceso medio vs {benchmark}"
    rows = []
    if summary is not None and not summary.empty:
        for rec in summary.to_dict("records"):
            for col, label in (("ret_medio", m_ret), ("exceso_medio", m_exc)):
                val = rec.get(col)
                if val is None or pd.isna(val):
                    continue
                rows.append(
                    {
                        "grupo": str(rec.get("grupo")),
                        "medida": label,
                        "valor": float(val),
                        "n": int(rec.get("n") or 0),
                    }
                )
    return pd.DataFrame(rows, columns=["grupo", "medida", "valor", "n"])


def outcome_chart(
    summary: pd.DataFrame, benchmark: str = "SPY", *, dark: bool = False
) -> alt.LayerChart | None:
    """Barras horizontales: retorno medio y exceso medio por grupo. None si no hay datos."""
    long = outcome_long(summary, benchmark)
    if long.empty:
        return None
    pal = palette(dark)
    measures = ["Retorno medio", f"Exceso medio vs {benchmark}"]
    groups = [g for g in GROUP_ORDER if g in set(long["grupo"])]
    groups += sorted(set(long["grupo"]) - set(groups))

    y = alt.Y("grupo:N", sort=groups, title=None, axis=alt.Axis(labelLimit=160))
    y_off = alt.YOffset("medida:N", sort=measures)
    # padding: deja aire para las etiquetas en la punta de las barras.
    x = alt.X(
        "valor:Q",
        title="Promedio por idea",
        axis=alt.Axis(format="%"),
        scale=alt.Scale(padding=40),
    )
    base = alt.Chart(long).encode(
        y=y,
        yOffset=y_off,
        x=x,
        tooltip=[
            alt.Tooltip("grupo:N", title="Grupo"),
            alt.Tooltip("medida:N", title="Medida"),
            alt.Tooltip("valor:Q", title="Valor", format="+.2%"),
            alt.Tooltip("n:Q", title="Ideas (n)"),
        ],
    )
    bars = base.mark_bar(size=14, cornerRadiusEnd=4).encode(
        color=alt.Color(
            "medida:N",
            scale=alt.Scale(domain=measures, range=[pal.series1, pal.series2]),
            legend=alt.Legend(title=None, orient="top"),
        )
    )
    label_pos = (
        base.transform_filter("datum.valor >= 0")
        .mark_text(align="left", dx=4, fontSize=11, color=pal.ink_secondary)
        .encode(text=alt.Text("valor:Q", format="+.1%"))
    )
    label_neg = (
        base.transform_filter("datum.valor < 0")
        .mark_text(align="right", dx=-4, fontSize=11, color=pal.ink_secondary)
        .encode(text=alt.Text("valor:Q", format="+.1%"))
    )
    zero = (
        alt.Chart(pd.DataFrame({"valor": [0.0]}))
        .mark_rule(color=pal.baseline, strokeWidth=1)
        .encode(x="valor:Q")
    )
    height = 64 * len(groups) + 20
    return alt.layer(zero, bars, label_pos, label_neg).properties(height=height)
