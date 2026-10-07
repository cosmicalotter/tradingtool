"""Consultas del panel (sin Streamlit): todo lo que la interfaz muestra sale de aquí.

Reglas:
- Las funciones de lectura reciben una conexión DuckDB ya abierta y no escriben nada.
- La única escritura es :func:`record_panel_decision`, que llama a
  ``journal.record_decision`` (solo escribe en el diario; NUNCA envía órdenes).
- DuckDB admite un solo proceso escritor: el panel abre conexiones cortas, de solo lectura
  para mostrar datos (:func:`open_db`) y de lectura-escritura solo al guardar una decisión.
  Los errores de bloqueo o de base inexistente se convierten en mensajes en español.
- Por defecto solo se muestran señales del día a día (``origin='live'``): las del histórico
  (backtest) no son ideas para decidir.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import duckdb
import numpy as np
import pandas as pd

from tradingtool.db import connect
from tradingtool.journal import journal

# ------------------------------------------------------------------------------ textos

DECISION_LABELS = {"approve": "Aprobada", "reject": "Rechazada", "skip": "Omitida"}
NO_DECISION_LABEL = "Sin decisión"
PASSED_LABEL = "Pasa"
BLOCKED_LABEL = "Bloqueada"

# Orden fijo de los grupos de resultados (igual en tablas y gráficos).
GROUP_ORDER = ("aprobada", "rechazada", "sin decisión", "bloqueada")

LIVE = "live"

MSG_DB_MISSING = "Todavía no existe la base de datos ({path}). Ejecuta primero: uv run tt iniciar"
MSG_DB_BUSY = (
    "La base está ocupada por otro proceso (¿se está ejecutando 'tt diario'?). "
    "Intenta de nuevo en un momento."
)
MSG_DB_SCHEMA = (
    "La base de datos existe pero le faltan tablas o columnas (quizá es de una versión "
    "anterior). Ejecuta: uv run tt iniciar"
)
MSG_DB_OTHER = "No se pudo leer la base de datos ({path}). Detalle técnico: {detail}"

MIN_REJECT_REASON_CHARS = 3
MAX_REASON_CHARS = 300

# Apertura del mercado de EE. UU. (convención de resultados: entrada en la apertura).
MARKET_TZ_NAME = "America/New_York"
MARKET_OPEN = time(9, 30)


# ------------------------------------------------------------------------------ errores


class PanelDbError(RuntimeError):
    """Problema con la base de datos; ``str(exc)`` es un mensaje en español para mostrar."""


class DbMissingError(PanelDbError):
    """La base de datos todavía no existe (hay que ejecutar ``tt iniciar``)."""


class DbBusyError(PanelDbError):
    """Otro proceso (o conexión) tiene la base bloqueada."""


class DbSchemaError(PanelDbError):
    """Faltan tablas o columnas."""


class DecisionInputError(ValueError):
    """Datos de la decisión inválidos; ``str(exc)`` es un mensaje en español."""


_LOCK_MARKERS = (
    "could not set lock",
    "conflicting lock",
    "different configuration",  # mismo proceso: otra conexión con otro modo (lectura/escritura)
    "being used by another process",
    "resource temporarily unavailable",
)


def _is_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in _LOCK_MARKERS)


@contextmanager
def open_db(db_path: Path | str, read_only: bool = True) -> Iterator[duckdb.DuckDBPyConnection]:
    """Abre una conexión corta y la cierra siempre al salir.

    Lanza :class:`DbMissingError`, :class:`DbBusyError`, :class:`DbSchemaError` o
    :class:`PanelDbError` con mensajes listos para mostrar.
    """
    path = Path(db_path)
    if not path.exists():
        raise DbMissingError(MSG_DB_MISSING.format(path=path))
    try:
        con = connect(path, read_only=read_only)
    except (duckdb.IOException, duckdb.ConnectionException) as exc:
        if _is_lock_error(exc):
            raise DbBusyError(MSG_DB_BUSY) from exc
        raise PanelDbError(MSG_DB_OTHER.format(path=path, detail=exc)) from exc
    except duckdb.Error as exc:
        raise PanelDbError(MSG_DB_OTHER.format(path=path, detail=exc)) from exc
    try:
        yield con
    except (duckdb.CatalogException, duckdb.BinderException) as exc:
        raise DbSchemaError(f"{MSG_DB_SCHEMA} (detalle: {exc})") from exc
    except (duckdb.IOException, duckdb.TransactionException, duckdb.ConnectionException) as exc:
        if _is_lock_error(exc):
            raise DbBusyError(MSG_DB_BUSY) from exc
        raise PanelDbError(MSG_DB_OTHER.format(path=path, detail=exc)) from exc
    except duckdb.Error as exc:  # cualquier otro error de DuckDB: mensaje, no traceback
        raise PanelDbError(MSG_DB_OTHER.format(path=path, detail=exc)) from exc
    finally:
        con.close()


# ------------------------------------------------------------------------------ utilidades


def _clean(value: Any) -> Any:
    """Convierte NaN/NaT/NA de pandas en None y Timestamps en date/datetime nativos."""
    if value is None:
        return None
    if isinstance(value, str | bytes | list | tuple | dict | set):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    if isinstance(value, pd.Timestamp | np.datetime64):
        return pd.Timestamp(value).to_pydatetime()
    if isinstance(value, np.generic):
        return value.item()  # numpy escalar -> Python
    return value


def _to_date(value: Any) -> date | None:
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError):
        return None


_FAILED = object()


def _loads(value: Any) -> Any:
    """JSON tolerante: acepta texto JSON, listas/dicts ya decodificados o None."""
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, list | dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return _FAILED
    return _FAILED


def parse_reasons(value: Any) -> list[str]:
    """Razones como lista de textos (nunca falla)."""
    data = _loads(value)
    if data is _FAILED:
        text = str(_clean(value) or "").strip()
        return [text] if text else []
    if data is None:
        return []
    if isinstance(data, str):
        return [data] if data.strip() else []
    if isinstance(data, dict):
        return [f"{k}: {v}" for k, v in data.items()]
    if isinstance(data, list):
        return [str(x) for x in data if x is not None and str(x).strip()]
    return [str(data)]


def parse_features(value: Any) -> dict[str, Any]:
    """Features como dict (si el JSON no es un objeto, devuelve {})."""
    data = _loads(value)
    return dict(data) if isinstance(data, dict) else {}


def parse_accessions(value: Any) -> list[str]:
    data = _loads(value)
    if data is _FAILED:
        text = str(_clean(value) or "").strip()
        return [text] if text else []
    if isinstance(data, str):
        return [data] if data.strip() else []
    if isinstance(data, list):
        return [str(x).strip() for x in data if x is not None and str(x).strip()]
    return []


# ------------------------------------------------------------------------------ SEC

_ACCESSION_DASHED = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_ACCESSION_PLAIN = re.compile(r"^\d{18}$")


def normalize_accession(accession: str | None) -> str | None:
    """Devuelve el accession con guiones (0000000000-00-000000) o None si no es válido."""
    if not accession:
        return None
    text = str(accession).strip()
    if _ACCESSION_DASHED.match(text):
        return text
    if _ACCESSION_PLAIN.match(text):
        return f"{text[:10]}-{text[10:12]}-{text[12:]}"
    return None


def sec_filing_index_url(accession: str | None, issuer_cik: str | int | None) -> str | None:
    """URL del índice del filing en EDGAR, o None si los datos no son válidos.

    Formato: https://www.sec.gov/Archives/edgar/data/<cik sin ceros>/<accession sin guiones>/
    <accession>-index.htm
    """
    acc = normalize_accession(accession)
    if acc is None or issuer_cik is None:
        return None
    cik_text = str(issuer_cik).strip()
    if not cik_text.isdigit():
        return None
    cik = str(int(cik_text))
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm"


# ------------------------------------------------------------------------------ señales


def signal_date_bounds(
    con: duckdb.DuckDBPyConnection, origin: str | None = LIVE
) -> tuple[date | None, date | None]:
    """Primera y última fecha (as_of_date) con señales (por defecto solo las del día a día)."""
    row = con.execute(
        "SELECT min(as_of_date), max(as_of_date) FROM signals WHERE (? IS NULL OR origin = ?)",
        [origin, origin],
    ).fetchone()
    if not row:
        return None, None
    return _to_date(row[0]), _to_date(row[1])


def latest_signals(
    con: duckdb.DuckDBPyConnection,
    start: date | None = None,
    end: date | None = None,
    only_passed: bool = False,
    limit: int | None = 500,
    origin: str | None = LIVE,
) -> pd.DataFrame:
    """Señales con su última decisión (reutiliza ``journal.list_signals``).

    Por defecto solo las del día a día (las del histórico no se deciden).
    Agrega columnas: ``estado`` (Pasa / Bloqueada) y ``decision_label`` (en español).
    """
    df = journal.list_signals(
        con,
        start=start,
        end=end,
        passed=True if only_passed else None,
        limit=limit,
        origin=origin,
    )
    df = df.copy()
    if df.empty:
        df["estado"] = pd.Series(dtype=object)
        df["decision_label"] = pd.Series(dtype=object)
        return df
    df["as_of_date"] = [_to_date(v) for v in df["as_of_date"]]
    df["estado"] = [PASSED_LABEL if bool(p) else BLOCKED_LABEL for p in df["passed"]]
    df["decision_label"] = [
        DECISION_LABELS.get(str(_clean(d)), NO_DECISION_LABEL) if _clean(d) else NO_DECISION_LABEL
        for d in df["decision"]
    ]
    return df.reset_index(drop=True)


@dataclass(frozen=True)
class SignalDetail:
    signal_id: str
    as_of_date: date | None
    ticker: str | None
    issuer_cik: str
    issuer_name: str | None
    score: float | None
    passed: bool
    strategy_version: str | None
    config_hash: str | None
    run_id: str | None
    created_at: datetime | None
    origin: str | None
    reasons: list[str]
    features: dict[str, Any]
    accessions: list[str]
    decisions: list[dict[str, Any]] = field(default_factory=list)  # más reciente primero

    @property
    def last_decision(self) -> dict[str, Any] | None:
        return self.decisions[0] if self.decisions else None

    @property
    def filing_links(self) -> list[tuple[str, str | None]]:
        """[(accession, url o None)] para enlazar a la SEC."""
        return [(a, sec_filing_index_url(a, self.issuer_cik)) for a in self.accessions]


def signal_decisions(con: duckdb.DuckDBPyConnection, signal_id: str) -> list[dict[str, Any]]:
    df = con.execute(
        "SELECT decision_id, decision, reason, decided_by, decided_at, planned_shares, "
        "planned_entry, planned_stop FROM decisions WHERE signal_id = ? "
        "ORDER BY decided_at DESC, decision_id DESC",
        [signal_id],
    ).df()
    out = []
    for rec in df.to_dict("records"):
        rec = {k: _clean(v) for k, v in rec.items()}
        rec["decision_label"] = DECISION_LABELS.get(rec.get("decision") or "", NO_DECISION_LABEL)
        out.append(rec)
    return out


def signal_detail(con: duckdb.DuckDBPyConnection, signal_id: str) -> SignalDetail | None:
    """Detalle de una señal con los campos JSON ya decodificados (o None si no existe)."""
    df = con.execute("SELECT * FROM signals WHERE signal_id = ?", [signal_id]).df()
    if df.empty:
        return None
    r = {k: _clean(v) for k, v in df.iloc[0].to_dict().items()}
    score = r.get("score")
    try:
        score = float(score) if score is not None else None
        if score is not None and not math.isfinite(score):
            score = None
    except (TypeError, ValueError):
        score = None
    created = r.get("created_at")
    return SignalDetail(
        signal_id=str(r["signal_id"]),
        as_of_date=_to_date(r.get("as_of_date")),
        ticker=(str(r["ticker"]).strip() or None) if r.get("ticker") else None,
        issuer_cik=str(r.get("issuer_cik") or ""),
        issuer_name=r.get("issuer_name"),
        score=score,
        passed=bool(r.get("passed")),
        strategy_version=r.get("strategy_version"),
        config_hash=r.get("config_hash"),
        run_id=r.get("run_id"),
        created_at=created if isinstance(created, datetime) else None,
        origin=r.get("origin"),
        reasons=parse_reasons(r.get("reasons")),
        features=parse_features(r.get("features")),
        accessions=parse_accessions(r.get("accessions")),
        decisions=signal_decisions(con, signal_id),
    )


# ------------------------------------------------------------------------------ precios


def price_bars(
    con: duckdb.DuckDBPyConnection,
    ticker: str | None,
    end: date | None = None,
    start: date | None = None,
) -> pd.DataFrame:
    """Barras diarias de un ticker (``start`` <= fecha <= ``end``), ordenadas por fecha.

    La columna ``date`` sale como datetime64 (lista para graficar).
    """
    cols = ["date", "open", "high", "low", "close", "volume"]
    if not ticker:
        return pd.DataFrame(columns=cols)
    where, params = ["ticker = ?"], [ticker]
    if start is not None:
        where.append("date >= ?")
        params.append(start)
    if end is not None:
        where.append("date <= ?")
        params.append(end)
    df = con.execute(
        f"SELECT {', '.join(cols)} FROM prices_daily WHERE {' AND '.join(where)} ORDER BY date",  # noqa: S608
        params,
    ).df()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# ------------------------------------------------------------------------------ resultados


def outcome_summary(con: duckdb.DuckDBPyConnection, horizon: int) -> pd.DataFrame:
    """Resumen por grupo (reutiliza ``journal.outcome_summary``) en orden fijo."""
    df = journal.outcome_summary(con, int(horizon))
    if df.empty:
        return df
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    df = df.copy()
    df["_o"] = [order.get(str(g), len(order)) for g in df["grupo"]]
    return df.sort_values(["_o", "grupo"]).drop(columns="_o").reset_index(drop=True)


def pending_outcomes(
    con: duckdb.DuckDBPyConnection, horizon: int, origin: str | None = LIVE
) -> int:
    """Señales con ticker que todavía no tienen resultado a este horizonte."""
    row = con.execute(
        """
        SELECT count(*) FROM signals s
        LEFT JOIN outcomes o ON o.signal_id = s.signal_id AND o.horizon_days = ?
        WHERE s.ticker IS NOT NULL AND o.signal_id IS NULL AND (? IS NULL OR s.origin = ?)
        """,
        [int(horizon), origin, origin],
    ).fetchone()
    return int(row[0]) if row else 0


def _market_tz() -> Any:
    try:
        return ZoneInfo(MARKET_TZ_NAME)
    except ZoneInfoNotFoundError:  # pragma: no cover - sin base de zonas horarias
        return timezone(timedelta(hours=-5))


def next_weekday(day: date) -> date:
    """Primer día hábil (lunes a viernes) estrictamente posterior a ``day``: el día de entrada
    de la convención de resultados (aproximado: no descuenta feriados)."""
    out = day + timedelta(days=1)
    while out.weekday() >= 5:
        out += timedelta(days=1)
    return out


def decided_after_open(decided_at: Any, entry_date: Any) -> bool | None:
    """True si la decisión (``decided_at`` en UTC sin zona) fue en o después de la apertura
    (9:30 hora de Nueva York) del día de entrada: ya se conocía parte del resultado."""
    decided, entry = _clean(decided_at), _to_date(entry_date)
    if entry is None or not isinstance(decided, datetime):
        return None
    if decided.tzinfo is None:
        decided = decided.replace(tzinfo=UTC)
    tz = _market_tz()
    return decided.astimezone(tz) >= datetime.combine(entry, MARKET_OPEN, tzinfo=tz)


def decisions_history(
    con: duckdb.DuckDBPyConnection,
    horizon: int,
    limit: int | None = 1000,
    origin: str | None = LIVE,
) -> pd.DataFrame:
    """Todas tus decisiones (más recientes primero) con el resultado de la señal al horizonte.

    Columnas extra:
    - ``vigente``: True si es la última decisión sobre esa señal (la que cuenta).
    - ``decidida_tarde``: True si decidiste en o después de la apertura del día de entrada
      de la convención de resultados (ya podías conocer parte del resultado: sesgo de
      retrospectiva). Si el resultado aún no existe, se usa el siguiente día hábil a la fecha
      de la idea (sin descontar feriados).
    """
    sql = """
        WITH ranked AS (
            SELECT d.*, row_number() OVER (
                PARTITION BY signal_id ORDER BY decided_at DESC, decision_id DESC) AS rn
            FROM decisions d
        )
        SELECT r.decision_id, r.signal_id, r.decision, r.reason, r.decided_at,
               r.planned_shares, r.planned_entry, r.planned_stop, (r.rn = 1) AS vigente,
               s.ticker, s.issuer_name, s.as_of_date, s.passed,
               o.entry_date, o.entry_price, o.exit_date, o.exit_price, o.ret, o.bench_ret,
               o.excess_ret, o.mae, o.mfe, o.status AS outcome_status
        FROM ranked r
        JOIN signals s USING (signal_id)
        LEFT JOIN outcomes o ON o.signal_id = r.signal_id AND o.horizon_days = ?
        WHERE (? IS NULL OR s.origin = ?)
        ORDER BY r.decided_at DESC, r.decision_id DESC
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    df = con.execute(sql, [int(horizon), origin, origin]).df()
    if df.empty:
        df["decision_label"] = pd.Series(dtype=object)
        df["decidida_tarde"] = pd.Series(dtype=object)
        return df
    df["decision_label"] = [DECISION_LABELS.get(str(d), str(d)) for d in df["decision"]]
    for col in ("as_of_date", "entry_date", "exit_date"):
        df[col] = [_to_date(v) for v in df[col]]
    late = []
    for decided, entry, as_of in zip(
        df["decided_at"], df["entry_date"], df["as_of_date"], strict=True
    ):
        if entry is None and as_of is not None:
            entry = next_weekday(as_of)
        late.append(decided_after_open(decided, entry))
    df["decidida_tarde"] = late
    return df


# ------------------------------------------------------------------------------ estado

# (tabla, descripción en español, columna de "último dato" o None, filtro SQL fijo o None)
STATUS_TABLES: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("insider_filings", "Formularios 4 de la SEC", "filing_date", None),
    ("insider_transactions", "Transacciones de insiders", "transaction_date", None),
    ("insider_owners", "Insiders que reportan", None, None),
    ("prices_daily", "Precios diarios", "date", None),
    ("signals", "Ideas del día a día", "as_of_date", "origin = 'live'"),
    ("decisions", "Tus decisiones", "decided_at", None),
    ("outcomes", "Resultados calculados", "computed_at", None),
    ("runs", "Ejecuciones (corridas)", "started_at", None),
)


def data_status(con: duckdb.DuckDBPyConnection, today: date | None = None) -> pd.DataFrame:
    """Filas y último dato por tabla. Una tabla inexistente aparece con filas = None."""
    today = today or date.today()
    rows = []
    for table, desc, col, where in STATUS_TABLES:
        n, last = None, None
        # Solo constantes del módulo en el SQL (nada que venga del usuario).
        cond = f" WHERE {where}" if where else ""
        try:
            if col:
                sql = f"SELECT count(*), max({col}) FROM {table}{cond}"  # noqa: S608
                res = con.execute(sql).fetchone()
                n, last = (int(res[0]), _clean(res[1])) if res else (0, None)
            else:
                res = con.execute(f"SELECT count(*) FROM {table}{cond}").fetchone()  # noqa: S608
                n = int(res[0]) if res else 0
        except (duckdb.CatalogException, duckdb.BinderException):
            pass
        last_date = _to_date(last)
        rows.append(
            {
                "tabla": table,
                "descripcion": desc,
                "filas": n,
                "ultimo_dato": last,
                "dias_desde": (today - last_date).days if last_date else None,
            }
        )
    return pd.DataFrame(rows)


def price_coverage(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Cuántos tickers tienen precios y rango de fechas."""
    try:
        row = con.execute(
            "SELECT count(DISTINCT ticker), min(date), max(date) FROM prices_daily"
        ).fetchone()
    except duckdb.CatalogException:
        return {"tickers": None, "first": None, "last": None}
    return {"tickers": int(row[0]), "first": _to_date(row[1]), "last": _to_date(row[2])}


def last_runs(con: duckdb.DuckDBPyConnection, n: int = 10) -> pd.DataFrame:
    """Últimas ejecuciones registradas (más recientes primero), con duración en segundos."""
    df = con.execute(
        "SELECT run_id, kind, started_at, finished_at, status, notes, git_commit, config_hash "
        "FROM runs ORDER BY started_at DESC LIMIT ?",
        [int(n)],
    ).df()
    if df.empty:
        df["duracion_s"] = pd.Series(dtype=float)
        return df
    dur = []
    for s, f in zip(df["started_at"], df["finished_at"], strict=True):
        s, f = _clean(s), _clean(f)
        dur.append((f - s).total_seconds() if s is not None and f is not None else None)
    df["duracion_s"] = dur
    return df


def strategy_versions(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Versiones de estrategia y hashes de configuración presentes en las señales."""
    df = con.execute(
        """
        SELECT strategy_version, config_hash, origin, count(*) AS n,
               min(as_of_date) AS primera, max(as_of_date) AS ultima
        FROM signals GROUP BY strategy_version, config_hash, origin
        ORDER BY ultima DESC, origin
        """
    ).df()
    for col in ("primera", "ultima"):
        if not df.empty:
            df[col] = [_to_date(v) for v in df[col]]
    return df


# ------------------------------------------------------------------------------ decisiones


def _opt_float(value: Any) -> float | None:
    value = _clean(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None


def _opt_shares(value: Any) -> int | None:
    """Acciones enteras y positivas, o None (nunca redondea en silencio 5.7 -> 5)."""
    v = _opt_float(value)
    return int(v) if v is not None and v.is_integer() else None


def validate_decision(decision: str, reason: str | None) -> str | None:
    """Valida y normaliza el motivo. Lanza :class:`DecisionInputError` (mensaje en español)."""
    if decision not in journal.VALID_DECISIONS:
        raise DecisionInputError(f"Decisión desconocida: {decision!r}.")
    text = " ".join((reason or "").split())
    if decision == "reject" and len(text) < MIN_REJECT_REASON_CHARS:
        raise DecisionInputError(
            "Para rechazar escribe un motivo corto (por ejemplo: 'empresa sin ventas' o "
            "'muy poco líquida'). Sirve para revisar después si tu criterio ayuda."
        )
    if len(text) > MAX_REASON_CHARS:
        raise DecisionInputError(
            f"El motivo es muy largo ({len(text)} caracteres). Máximo {MAX_REASON_CHARS}."
        )
    return text or None


def record_panel_decision(
    con: duckdb.DuckDBPyConnection,
    signal_id: str,
    decision: str,
    reason: str | None = None,
    planned_shares: int | None = None,
    planned_entry: float | None = None,
    planned_stop: float | None = None,
) -> str:
    """Guarda una decisión del panel en el diario. NO envía órdenes a ningún broker.

    Reglas:
    - El motivo es obligatorio para rechazar (ver :func:`validate_decision`).
    - Solo se deciden ideas del día a día que pasaron los filtros: las bloqueadas son
      contrafactuales y las del histórico (backtest) no son ideas para decidir.
    - El plan (acciones, entrada, stop) solo se guarda al aprobar; se descartan valores
      vacíos, no positivos o acciones no enteras, y el stop debe quedar bajo la entrada.

    Devuelve el ``decision_id``. Lanza :class:`DecisionInputError` con mensajes en español.
    """
    clean_reason = validate_decision(decision, reason)
    row = con.execute(
        "SELECT passed, origin FROM signals WHERE signal_id = ?", [signal_id]
    ).fetchone()
    if row is None:
        raise DecisionInputError(
            "Esa idea ya no existe en la base (¿se volvió a generar?). Recarga la página."
        )
    passed, origin = row
    if origin != LIVE:
        raise DecisionInputError(
            "Esta idea viene de la reconstrucción histórica (backtest): no se decide en el panel."
        )
    if not passed:
        raise DecisionInputError(
            "Esta idea no pasó los filtros: no se decide. Se sigue igual como contrafactual "
            "para comparar."
        )
    shares = entry = stop = None
    if decision == "approve":
        shares = _opt_shares(planned_shares)
        entry, stop = _opt_float(planned_entry), _opt_float(planned_stop)
        if entry is not None and stop is not None and stop >= entry:
            raise DecisionInputError(
                "El stop del plan debe estar por debajo de la entrada. Corrígelo en la "
                "calculadora (o bórralo) y vuelve a aprobar."
            )
    try:
        return journal.record_decision(
            con,
            signal_id,
            decision,
            reason=clean_reason,
            decided_by="human",
            planned_shares=shares,
            planned_entry=entry,
            planned_stop=stop,
        )
    except KeyError as exc:  # borrada entre la verificación y la escritura
        raise DecisionInputError(
            "Esa idea ya no existe en la base (¿se volvió a generar?). Recarga la página."
        ) from exc
