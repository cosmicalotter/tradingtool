"""Screener de compras de insiders: estrategia ``insider-v1`` (reglas fijas, sin LLM).

Un "evento" = todas las compras en mercado abierto (código P) reportadas para una empresa en
una misma fecha de presentación (``filing_date``). Cada evento produce una señal:
- ``passed=True`` si al menos un insider cumple todos los filtros y el evento cumple los
  filtros de liquidez/cluster;
- ``passed=False`` (bloqueada) en caso contrario, con las razones. Las bloqueadas también se
  guardan: son los contrafactuales que permiten medir si los filtros agregan valor.

Reglas para no mirar al futuro:
- Todo se ancla en ``filing_date`` (cuándo la información fue pública), nunca en la fecha de
  la transacción. La entrada se mide en la apertura del día hábil siguiente (ver journal).
- La clasificación rutinario/oportunista usa solo filings previos al 1 de enero del año.
- Liquidez y ATR usan solo barras con fecha <= filing_date.
- El filtro de precio mínimo usa el precio pagado por el insider (no ajustado por splits
  posteriores, a diferencia de las barras históricas ajustadas).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import duckdb
import pandas as pd

from tradingtool.config import AppConfig, ScreenerConfig
from tradingtool.ids import stable_hash
from tradingtool.insiders.classify import (
    OPPORTUNISTIC,
    ROUTINE,
    UNCLASSIFIED,
    classify_insiders,
)
from tradingtool.models import Signal
from tradingtool.risk.sizing import atr, avg_dollar_volume

COMMON_TITLE_RE = re.compile(r"\b(common|ordinary|class\s+[a-z]|shares?|stock)\b", re.IGNORECASE)
EXCLUDED_TITLE_RE = re.compile(
    r"\b(preferred|pfd|warrants?|units?|notes?|debentures?|options?|rights?|depositary|adrs?|"
    r"ads|bonds?|convertible|phantom|restricted\s+stock\s+units?|rsus?)\b",
    re.IGNORECASE,
)
MAX_SANE_PRICE = 1_000_000.0
PRICE_HISTORY_DAYS = 60  # días calendario de barras a mirar hacia atrás para liquidez/ATR


def flag(value: Any) -> bool | None:
    """Convierte booleanos de pandas/numpy/DuckDB (incluido NA) a True/False/None.

    Importante: ``numpy.bool_(True) is True`` es False, por eso nunca se compara con ``is``.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return bool(value)


def is_common_stock(title: str | None) -> bool:
    if not title:
        return False
    return bool(COMMON_TITLE_RE.search(title)) and not EXCLUDED_TITLE_RE.search(title)


def _money(x: float) -> str:
    if x >= 1e6:
        return f"US${x / 1e6:,.2f} M"
    if x >= 1e3:
        return f"US${x / 1e3:,.1f} mil"
    return f"US${x:,.0f}"


# ----------------------------------------------------------------------------- carga


def load_purchase_rows(
    con: duckdb.DuckDBPyConnection, start: date, end: date, codes: tuple[str, ...] = ("P",)
) -> pd.DataFrame:
    """Transacciones no derivadas con los códigos indicados, presentadas entre start y end."""
    df = con.execute(
        """
        WITH own AS (
            SELECT accession,
                max(CASE WHEN owner_seq = 0 THEN owner_cik END) AS owner_cik,
                max(CASE WHEN owner_seq = 0 THEN owner_name END) AS owner_name,
                bool_or(coalesce(is_director, FALSE)) AS is_director,
                bool_or(coalesce(is_officer, FALSE)) AS is_officer,
                bool_or(coalesce(is_ten_pct_owner, FALSE)) AS is_ten_pct_owner,
                bool_or(coalesce(is_other, FALSE)) AS is_other,
                string_agg(DISTINCT officer_title, '; ') AS officer_title,
                count(*) AS n_owners
            FROM insider_owners GROUP BY accession
        )
        SELECT f.accession, f.form_type, f.filing_date, f.issuer_cik, f.issuer_name,
            f.issuer_ticker, f.aff10b5one, f.mentions_10b5_1,
            own.owner_cik, own.owner_name, own.is_director, own.is_officer,
            own.is_ten_pct_owner, own.is_other, own.officer_title, own.n_owners,
            t.seq, t.security_title, t.transaction_date, t.transaction_code, t.shares,
            t.price_per_share, t.acquired_disposed, t.shares_owned_after, t.direct_indirect,
            t.equity_swap
        FROM insider_filings f
        JOIN insider_transactions t USING (accession)
        LEFT JOIN own USING (accession)
        WHERE f.filing_date BETWEEN ? AND ?
          AND NOT t.is_derivative
          AND t.transaction_code IN (SELECT unnest(?::VARCHAR[]))
        ORDER BY f.filing_date, f.issuer_cik, f.accession, t.seq
        """,
        [start, end, list(codes)],
    ).df()
    for col in ("filing_date", "transaction_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col]).dt.date
    return df


# ----------------------------------------------------------------------------- reglas por fila


def row_failures(row: pd.Series, cfg: ScreenerConfig) -> list[str]:
    """Razones por las que una transacción NO cuenta como compra válida (lista vacía = válida)."""
    tc = cfg.transactions
    roles = cfg.roles
    out: list[str] = []
    form_type = str(row.get("form_type") or "")
    if form_type == "4/A" and not tc.include_amendments:
        out.append("Es una enmienda (4/A): se excluye para no contar doble")
    elif form_type not in ("4", "4/A"):
        out.append(f"Tipo de formulario {form_type!r} no válido")
    if tc.common_stock_only and not is_common_stock(row.get("security_title")):
        out.append("No es acción común (preferentes, warrants, unidades, ADR, etc.)")
    ad = row.get("acquired_disposed")
    if isinstance(ad, str) and ad and ad != "A":
        out.append("Código de compra pero marcada como 'dispuesta'")
    shares, price = row.get("shares"), row.get("price_per_share")
    if (
        shares is None
        or price is None
        or pd.isna(shares)
        or pd.isna(price)
        or shares <= 0
        or price <= 0
        or price >= MAX_SANE_PRICE
    ):
        out.append("Cantidad o precio faltante o inválido")
    if flag(row.get("equity_swap")) is True:
        out.append("Involucra un swap de acciones")
    tdate, fdate = row.get("transaction_date"), row.get("filing_date")
    if tdate is None or pd.isna(tdate):
        out.append("Sin fecha de transacción")
    else:
        lag = (fdate - tdate).days
        if lag < 0:
            out.append("Fecha de transacción posterior a la presentación (dato inválido)")
        elif lag > tc.max_filing_lag_days:
            out.append(
                f"Reportada {lag} días después de la compra (máximo {tc.max_filing_lag_days})"
            )
    aff = flag(row.get("aff10b5one"))
    if tc.exclude_10b5_1 and aff is True:
        out.append("Marcada como plan 10b5-1 (compra programada, poco informativa)")
    elif (
        tc.exclude_10b5_1_footnote_mentions
        and aff is None
        and flag(row.get("mentions_10b5_1")) is True
    ):
        out.append("Una nota al pie menciona un plan 10b5-1")
    is_off, is_dir = bool(flag(row.get("is_officer"))), bool(flag(row.get("is_director")))
    is_ten = bool(flag(row.get("is_ten_pct_owner")))
    is_oth = bool(flag(row.get("is_other")))
    role_ok = (
        (is_off and roles.include_officers)
        or (is_dir and roles.include_directors)
        or (is_ten and not is_off and not is_dir and roles.include_ten_pct_owners_only)
        or (is_oth and roles.include_other)
    )
    if not role_ok:
        out.append("Rol del insider no incluido (p. ej. solo dueño de más del 10%)")
    if not row.get("owner_cik"):
        out.append("Sin identificación del insider")
    return out


# ----------------------------------------------------------------------------- precios


@dataclass
class PriceContext:
    """Barras diarias en memoria para calcular liquidez/ATR sin mirar al futuro."""

    bars: dict[str, pd.DataFrame] = field(default_factory=dict)

    @classmethod
    def load(
        cls, con: duckdb.DuckDBPyConnection, tickers: list[str], start: date, end: date
    ) -> PriceContext:
        if not tickers:
            return cls()
        df = con.execute(
            "SELECT ticker, date, open, high, low, close, volume FROM prices_daily "
            "WHERE ticker IN (SELECT unnest(?::VARCHAR[])) AND date BETWEEN ? AND ? "
            "ORDER BY ticker, date",
            [tickers, start, end],
        ).df()
        if df.empty:
            return cls()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return cls({t: g.reset_index(drop=True) for t, g in df.groupby("ticker")})

    def upto(self, ticker: str | None, day: date) -> pd.DataFrame | None:
        if not ticker or ticker not in self.bars:
            return None
        b = self.bars[ticker]
        b = b[b["date"] <= day]
        return b if not b.empty else None


# ----------------------------------------------------------------------------- screener


def _pct_increase(bought: float, prior: float | None) -> float | None:
    """Acciones compradas / tenencia previa. inf = posición nueva; None = dato inconsistente."""
    if prior is None or pd.isna(prior):
        return None
    if abs(prior) < 1.0:
        return math.inf
    if prior < 0:
        return None
    return float(bought) / float(prior)


def _str_or_none(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _insider_label(row: dict[str, Any]) -> str:
    title = _str_or_none(row.get("officer_title"))
    if title:
        return title
    if flag(row.get("is_director")):
        return "Director"
    if flag(row.get("is_ten_pct_owner")):
        return "Dueño >10%"
    return "Insider"


def _score(cfg: ScreenerConfig, feats: dict[str, Any]) -> float:
    s = cfg.scoring
    value = max(float(feats.get("total_value") or 0.0), 1.0)
    pct = feats.get("pct_increase_max")
    if feats.get("new_position"):
        pct_term = 1.0  # posición nueva = aumento máximo
    elif pct is not None and math.isfinite(pct):
        pct_term = min(float(pct), 1.0)
    else:
        pct_term = 0.0
    return round(
        s.w_log_value * math.log10(value)
        + s.w_cluster * max(int(feats.get("n_insiders_window") or 1) - 1, 0)
        + s.w_officer * (1.0 if feats.get("any_officer") else 0.0)
        + s.w_ownership_increase * pct_term
        + s.w_opportunistic * (1.0 if (feats.get("opportunistic_count") or 0) > 0 else 0.0),
        4,
    )


def screen(
    con: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    app_cfg: AppConfig,
    origin: str = "live",
) -> list[Signal]:
    """Evalúa todos los eventos con filing_date en [start, end] y devuelve sus señales."""
    cfg = app_cfg.screener
    window = cfg.cluster.window_days
    rows = load_purchase_rows(con, start - timedelta(days=window), end, cfg.transactions.codes)
    if rows.empty:
        return []
    rows["failures"] = [row_failures(r, cfg) for _, r in rows.iterrows()]
    rows["row_ok"] = rows["failures"].map(lambda f: not f)
    rows["value"] = rows["shares"] * rows["price_per_share"]

    # Agregado por insider y evento (solo filas válidas).
    ok = rows[rows["row_ok"]].copy()
    ins = (
        ok.groupby(["issuer_cik", "filing_date", "owner_cik"], as_index=False).agg(
            owner_name=("owner_name", "first"),
            officer_title=("officer_title", "first"),
            is_officer=("is_officer", "max"),
            is_director=("is_director", "max"),
            is_ten_pct_owner=("is_ten_pct_owner", "max"),
            value=("value", "sum"),
            shares=("shares", "sum"),
            max_price=("price_per_share", "max"),
            owned_after=("shares_owned_after", "max"),
            first_tx=("transaction_date", "min"),
            last_tx=("transaction_date", "max"),
        )
        if not ok.empty
        else pd.DataFrame()
    )

    # Clasificación rutinario/oportunista (una consulta por año).
    classes: dict[tuple[str, str], str] = {}
    if not ins.empty:
        ins["year"] = ins["filing_date"].map(lambda d: d.year)
        for year, g in ins.groupby("year"):
            keys = list(zip(g["owner_cik"], g["issuer_cik"], strict=True))
            classes.update(
                {
                    (o, i, year): c
                    for (o, i), c in classify_insiders(
                        con, keys, date(int(year), 1, 1), cfg.classification.lookback_years
                    ).items()
                }
            )
        ins["clase"] = [
            classes.get((o, i, y), UNCLASSIFIED)
            for o, i, y in zip(ins["owner_cik"], ins["issuer_cik"], ins["year"], strict=True)
        ]
        mode = cfg.classification.mode
        min_value = cfg.transactions.min_value_usd
        ins["insider_failures"] = [
            [
                *(
                    [f"Monto comprado {_money(v)} menor al mínimo {_money(min_value)}"]
                    if v < min_value
                    else []
                ),
                *(
                    ["Insider rutinario (compra habitual en el mismo mes cada año)"]
                    if mode in ("opportunistic_only", "exclude_routine") and c == ROUTINE
                    else []
                ),
                *(
                    ["Insider sin historia suficiente para clasificarlo"]
                    if mode == "opportunistic_only" and c == UNCLASSIFIED
                    else []
                ),
            ]
            for v, c in zip(ins["value"], ins["clase"], strict=True)
        ]
        ins["insider_ok"] = ins["insider_failures"].map(lambda f: not f)
        prior = ins["owned_after"] - ins["shares"]
        ins["pct_increase"] = [
            _pct_increase(s, p) for s, p in zip(ins["shares"], prior, strict=True)
        ]

    # Precios para liquidez.
    events = rows[(rows["filing_date"] >= start) & (rows["filing_date"] <= end)]
    tickers = sorted({t for t in events["issuer_ticker"].dropna().unique()})
    prices = PriceContext.load(con, tickers, start - timedelta(days=PRICE_HISTORY_DAYS), end)

    signals: list[Signal] = []
    for (issuer_cik, fdate), ev in events.groupby(["issuer_cik", "filing_date"], sort=True):
        signals.append(_build_signal(issuer_cik, fdate, ev, ins, prices, app_cfg, origin, window))
    return signals


def _build_signal(
    issuer_cik: str,
    fdate: date,
    ev: pd.DataFrame,
    ins: pd.DataFrame,
    prices: PriceContext,
    app_cfg: AppConfig,
    origin: str,
    window: int,
) -> Signal:
    cfg = app_cfg.screener
    ticker = next((t for t in ev["issuer_ticker"] if isinstance(t, str) and t), None)
    name = next((n for n in ev["issuer_name"] if isinstance(n, str) and n), None)
    reasons: list[str] = []
    blockers: list[str] = []

    ev_ins = (
        ins[(ins["issuer_cik"] == issuer_cik) & (ins["filing_date"] == fdate)]
        if not ins.empty
        else pd.DataFrame()
    )
    good = ev_ins[ev_ins["insider_ok"]] if not ev_ins.empty else pd.DataFrame()

    # Insiders distintos con compras válidas en la ventana del cluster (hasta esta fecha).
    if not ins.empty:
        win = ins[
            (ins["issuer_cik"] == issuer_cik)
            & (ins["filing_date"] <= fdate)
            & (ins["filing_date"] > fdate - timedelta(days=window))
            & ins["insider_ok"]
        ]
        n_window = int(win["owner_cik"].nunique())
    else:
        n_window = 0

    if good.empty:
        row_reasons = Counter(r for fl in ev["failures"] for r in fl)
        ins_reasons = (
            Counter(r for fl in ev_ins["insider_failures"] for r in fl)
            if not ev_ins.empty
            else Counter()
        )
        for r, n in (row_reasons + ins_reasons).most_common():
            blockers.append(f"{r} ({n})" if n > 1 else r)
        if not blockers:
            blockers.append("Ninguna compra válida")

    total_value = float(good["value"].sum()) if not good.empty else 0.0
    insiders = []
    if not ev_ins.empty:
        for r in ev_ins.to_dict("records"):
            pct = r.get("pct_increase")
            insiders.append(
                {
                    "nombre": _str_or_none(r.get("owner_name")),
                    "cik": r.get("owner_cik"),
                    "cargo": _insider_label(r),
                    "valor": round(float(r["value"]), 2),
                    "acciones": float(r["shares"]),
                    "aumento_participacion": (
                        None if pct is None else (None if math.isinf(pct) else round(pct, 4))
                    ),
                    "posicion_nueva": bool(pct is not None and math.isinf(pct)),
                    "clase": r.get("clase"),
                    "valida": bool(r.get("insider_ok")),
                    "motivos": list(r.get("insider_failures") or []),
                }
            )
    pcts = [p for p in (good["pct_increase"] if not good.empty else []) if p is not None]
    feats: dict[str, Any] = {
        "n_insiders": int(good["owner_cik"].nunique()) if not good.empty else 0,
        "n_insiders_window": n_window,
        "total_value": round(total_value, 2),
        "max_insider_value": round(float(good["value"].max()), 2) if not good.empty else 0.0,
        "any_officer": bool(good["is_officer"].any()) if not good.empty else False,
        "any_director": bool(good["is_director"].any()) if not good.empty else False,
        "opportunistic_count": int((good["clase"] == OPPORTUNISTIC).sum()) if not good.empty else 0,
        "routine_count": int((ev_ins["clase"] == ROUTINE).sum()) if not ev_ins.empty else 0,
        "unclassified_count": int((good["clase"] == UNCLASSIFIED).sum()) if not good.empty else 0,
        "pct_increase_max": (
            None if not pcts else (None if math.isinf(max(pcts)) else round(max(pcts), 4))
        ),
        "new_position": any(math.isinf(p) for p in pcts),
        "filing_lag_max": int(max((fdate - d).days for d in ev["transaction_date"].dropna()))
        if ev["transaction_date"].notna().any()
        else None,
        "insider_price_max": round(float(good["max_price"].max()), 4) if not good.empty else None,
        "insiders": insiders,
        "n_purchase_rows": len(ev),
        "transaction_dates": sorted({str(d) for d in ev["transaction_date"].dropna()}),
    }

    # Filtros a nivel de evento.
    if good.shape[0] > 0:
        if ticker is None:
            blockers.append("La empresa no tiene ticker en el filing")
        if n_window < cfg.cluster.min_insiders:
            blockers.append(
                f"Solo {n_window} insider(s) comprando en {window} días "
                f"(mínimo {cfg.cluster.min_insiders})"
            )
        px = feats["insider_price_max"]
        if px is not None and px < cfg.universe.min_price:
            blockers.append(
                f"Precio pagado US${px:,.2f} menor al mínimo US${cfg.universe.min_price:,.2f}"
            )
    bars = prices.upto(ticker, fdate)
    if bars is not None:
        last = bars.iloc[-1]
        feats["price_last"] = round(float(last["close"]), 4)
        feats["price_last_date"] = str(last["date"])
        adv = avg_dollar_volume(bars, cfg.universe.adv_window_days)
        feats["adv20"] = None if adv is None else round(adv, 2)
        a = atr(bars, app_cfg.risk.atr_period)
        feats["atr14"] = None if a is None else round(a, 4)
    else:
        feats.update({"price_last": None, "price_last_date": None, "adv20": None, "atr14": None})
    if good.shape[0] > 0:
        if bars is None:
            blockers.append("Sin datos de precio para validar la liquidez")
        elif feats["adv20"] is None:
            blockers.append("Historia de precios insuficiente para medir la liquidez")
        elif feats["adv20"] < cfg.universe.min_avg_dollar_volume:
            blockers.append(
                f"Liquidez baja: volumen promedio {_money(feats['adv20'])}/día "
                f"(mínimo {_money(cfg.universe.min_avg_dollar_volume)})"
            )

    passed = not blockers
    if not good.empty:
        cargos = sorted({i["cargo"] for i in insiders if i["valida"]})
        reasons.append(
            f"{feats['n_insiders']} insider(s) compraron {_money(total_value)} "
            f"({', '.join(cargos)})"
        )
        if n_window > feats["n_insiders"]:
            reasons.append(f"{n_window} insiders distintos comprando en los últimos {window} días")
        if feats["opportunistic_count"]:
            reasons.append(f"{feats['opportunistic_count']} clasificado(s) como oportunista(s)")
        if feats["new_position"]:
            reasons.append("Al menos uno abre una posición nueva")
        elif feats["pct_increase_max"] is not None:
            reasons.append(f"Aumento de participación hasta {feats['pct_increase_max']:.0%}")
    all_reasons = tuple(reasons + [f"Bloqueada: {b}" for b in blockers])
    score = _score(cfg, feats) if not good.empty else 0.0
    accessions = tuple(sorted(set(ev["accession"])))
    signal_id = stable_hash(
        {"v": cfg.strategy_version, "cik": issuer_cik, "d": fdate, "o": origin}, length=20
    )
    return Signal(
        signal_id=signal_id,
        strategy_version=cfg.strategy_version,
        config_hash=app_cfg.screener_hash(),
        as_of_date=fdate,
        ticker=ticker,
        issuer_cik=issuer_cik,
        issuer_name=name,
        score=score,
        passed=passed,
        reasons=all_reasons,
        features=feats,
        accessions=accessions,
        origin=origin,
    )


def candidate_tickers(con: duckdb.DuckDBPyConnection, start: date, end: date) -> list[str]:
    """Tickers con compras de insiders en la ventana (para saber qué precios descargar)."""
    rows = con.execute(
        """
        SELECT DISTINCT f.issuer_ticker
        FROM insider_filings f JOIN insider_transactions t USING (accession)
        WHERE f.filing_date BETWEEN ? AND ? AND t.transaction_code = 'P'
          AND NOT t.is_derivative AND f.issuer_ticker IS NOT NULL
        ORDER BY 1
        """,
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def pending_outcome_tickers(con: duckdb.DuckDBPyConnection, max_horizon: int) -> list[str]:
    """Tickers de señales cuyos resultados aún no están completos (necesitan precios nuevos)."""
    rows = con.execute(
        """
        SELECT DISTINCT s.ticker FROM signals s
        LEFT JOIN outcomes o ON o.signal_id = s.signal_id AND o.horizon_days = ?
        WHERE s.ticker IS NOT NULL AND o.signal_id IS NULL
        ORDER BY 1
        """,
        [max_horizon],
    ).fetchall()
    return [r[0] for r in rows]
