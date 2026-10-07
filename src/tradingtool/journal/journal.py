"""Diario de trading automático: ejecuciones, señales, decisiones y resultados.

Ideas clave:
- TODA señal se guarda, pase o no los filtros (las bloqueadas son contrafactuales).
- Las decisiones humanas (aprobar / rechazar) se guardan aparte y nunca envían órdenes.
- Los resultados se calculan para todas las señales con la misma convención, de modo que se
  puede comparar: aprobadas vs. rechazadas vs. bloqueadas vs. sin decisión.

Convención de resultados (sin mirar al futuro):
- Entrada: precio de APERTURA del primer día hábil estrictamente posterior a ``as_of_date``.
- Salida a horizonte h: CIERRE del h-ésimo día hábil contando el día de entrada como el 1.
- MAE / MFE: peor mínimo y mejor máximo intradía en la ventana, relativos a la entrada.
- Benchmark: misma ventana (apertura de entrada -> cierre de salida) del ticker de referencia.
- Si el ticker deja de cotizar antes del horizonte (p. ej. deslistado), se cierra con el último
  cierre disponible y se marca ``truncated``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pandas as pd

from tradingtool.ids import git_commit, new_run_id
from tradingtool.models import Signal

VALID_DECISIONS = ("approve", "reject", "skip")
# Si el último dato del ticker es N días hábiles más viejo que el último dato del mercado,
# se asume que dejó de cotizar.
STALE_BARS_FOR_TRUNCATION = 10
# La entrada debe ocurrir poco después de la señal. Si la primera barra disponible está más
# lejos (cambio de ticker, hueco de datos), no se mide: sería una entrada falsa.
MAX_ENTRY_DELAY_DAYS = 10


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ------------------------------------------------------------------------------ runs


def start_run(
    con: duckdb.DuckDBPyConnection,
    kind: str,
    config_hash: str | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    run_id = new_run_id(kind)
    con.execute(
        "INSERT INTO runs (run_id, kind, started_at, git_commit, config_hash, params, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'running')",
        [run_id, kind, _now(), git_commit(), config_hash, json.dumps(params or {}, default=str)],
    )
    return run_id


def finish_run(
    con: duckdb.DuckDBPyConnection, run_id: str, status: str = "ok", notes: str | None = None
) -> None:
    con.execute(
        "UPDATE runs SET finished_at = ?, status = ?, notes = ? WHERE run_id = ?",
        [_now(), status, notes, run_id],
    )


# ------------------------------------------------------------------------------ señales


def record_signals(
    con: duckdb.DuckDBPyConnection, run_id: str | None, signals: Iterable[Signal]
) -> int:
    """Guarda señales. Si una señal (mismo signal_id) ya existe, actualiza su contenido pero
    conserva la fecha de creación original y sus decisiones."""
    rows = [
        {
            "signal_id": s.signal_id,
            "run_id": run_id,
            "strategy_version": s.strategy_version,
            "config_hash": s.config_hash,
            "as_of_date": s.as_of_date,
            "ticker": s.ticker,
            "issuer_cik": s.issuer_cik,
            "issuer_name": s.issuer_name,
            "score": float(s.score),
            "passed": bool(s.passed),
            "origin": s.origin,
            "reasons": json.dumps(list(s.reasons), ensure_ascii=False),
            "features": json.dumps(s.features, default=str, ensure_ascii=False),
            "accessions": json.dumps(list(s.accessions)),
        }
        for s in signals
    ]
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    con.register("tt_new_signals", df)
    try:
        con.execute(
            """
            INSERT INTO signals (signal_id, run_id, strategy_version, config_hash, as_of_date,
                ticker, issuer_cik, issuer_name, score, passed, origin, reasons, features,
                accessions)
            SELECT signal_id, run_id, strategy_version, config_hash, as_of_date, ticker,
                issuer_cik, issuer_name, score, passed, origin, reasons, features, accessions
            FROM tt_new_signals
            ON CONFLICT (signal_id) DO UPDATE SET
                run_id = excluded.run_id, score = excluded.score, passed = excluded.passed,
                reasons = excluded.reasons, features = excluded.features,
                accessions = excluded.accessions, ticker = excluded.ticker,
                issuer_name = excluded.issuer_name
            """
        )
    finally:
        con.unregister("tt_new_signals")
    return len(rows)


def list_signals(
    con: duckdb.DuckDBPyConnection,
    start: date | None = None,
    end: date | None = None,
    passed: bool | None = None,
    limit: int | None = None,
    origin: str | None = "live",
) -> pd.DataFrame:
    """Señales con su última decisión (si existe), más recientes primero.

    Por defecto solo las del día a día (``origin='live'``); ``origin=None`` trae todas.
    """
    where, params = [], []
    if origin is not None:
        where.append("s.origin = ?")
        params.append(origin)
    if start is not None:
        where.append("s.as_of_date >= ?")
        params.append(start)
    if end is not None:
        where.append("s.as_of_date <= ?")
        params.append(end)
    if passed is not None:
        where.append("s.passed = ?")
        params.append(passed)
    sql = """
        WITH last_dec AS (
            SELECT * EXCLUDE (rn) FROM (
                SELECT d.*, row_number() OVER (
                    PARTITION BY signal_id ORDER BY decided_at DESC, decision_id DESC) AS rn
                FROM decisions d) WHERE rn = 1
        )
        SELECT s.*, ld.decision, ld.reason AS decision_reason, ld.decided_at,
               ld.planned_shares, ld.planned_entry, ld.planned_stop
        FROM signals s LEFT JOIN last_dec ld USING (signal_id)
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.as_of_date DESC, s.passed DESC, s.score DESC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return con.execute(sql, params).df()


# ------------------------------------------------------------------------------ decisiones


def record_decision(
    con: duckdb.DuckDBPyConnection,
    signal_id: str,
    decision: str,
    reason: str | None = None,
    decided_by: str = "human",
    planned_shares: int | None = None,
    planned_entry: float | None = None,
    planned_stop: float | None = None,
) -> str:
    """Registra una decisión. NO envía órdenes: solo escribe en el diario."""
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decisión inválida {decision!r}; usa una de {VALID_DECISIONS}")
    exists = con.execute("SELECT 1 FROM signals WHERE signal_id = ?", [signal_id]).fetchone()
    if not exists:
        raise KeyError(f"No existe la señal {signal_id}")
    decision_id = uuid.uuid4().hex
    con.execute(
        "INSERT INTO decisions (decision_id, signal_id, decision, reason, decided_by, decided_at, "
        "planned_shares, planned_entry, planned_stop) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            decision_id,
            signal_id,
            decision,
            reason,
            decided_by,
            _now(),
            planned_shares,
            planned_entry,
            planned_stop,
        ],
    )
    return decision_id


# ------------------------------------------------------------------------------ resultados


def _bench_ret(bench: pd.DataFrame | None, entry_day: date, exit_day: date) -> float | None:
    if bench is None or bench.empty:
        return None
    b_entry = bench[bench["date"] == entry_day]
    b_exit = bench[bench["date"] == exit_day]
    if b_entry.empty or b_exit.empty or float(b_entry["open"].iloc[0]) <= 0:
        return None
    return float(b_exit["close"].iloc[0]) / float(b_entry["open"].iloc[0]) - 1.0


def _compute_one(
    bars: pd.DataFrame,
    bench: pd.DataFrame | None,
    as_of: date,
    horizon: int,
    market_last_date: date | None,
    market_dates: Sequence[date],
    bench2: pd.DataFrame | None = None,
) -> dict[str, Any] | None:
    """Calcula el resultado de una señal a un horizonte. None si aún no se puede calcular."""
    future = bars[bars["date"] > as_of]
    if future.empty:
        return None
    entry_bar = future.iloc[0]
    if (entry_bar["date"] - as_of).days > MAX_ENTRY_DELAY_DAYS:
        return None
    entry_price = float(entry_bar["open"])
    if not entry_price or entry_price <= 0:
        return None
    window = future.iloc[:horizon]
    status = "complete"
    if len(window) < horizon:
        # ¿Dejó de cotizar? Compara su último dato con el último dato del mercado.
        last_bar_date = window["date"].iloc[-1]
        if market_last_date is None:
            return None
        newer = [d for d in market_dates if d > last_bar_date]
        if len(newer) < STALE_BARS_FOR_TRUNCATION:
            return None  # todavía pendiente
        status = "truncated"
    exit_bar = window.iloc[-1]
    exit_price = float(exit_bar["close"])
    ret = exit_price / entry_price - 1.0
    mae = float(window["low"].min()) / entry_price - 1.0
    mfe = float(window["high"].max()) / entry_price - 1.0

    bench_ret = _bench_ret(bench, entry_bar["date"], exit_bar["date"])
    bench2_ret = _bench_ret(bench2, entry_bar["date"], exit_bar["date"])
    return {
        "entry_date": entry_bar["date"],
        "entry_price": entry_price,
        "exit_date": exit_bar["date"],
        "exit_price": exit_price,
        "ret": ret,
        "mae": mae,
        "mfe": mfe,
        "bench_ret": bench_ret,
        "excess_ret": (ret - bench_ret) if bench_ret is not None else None,
        "bench2_ret": bench2_ret,
        "excess2_ret": (ret - bench2_ret) if bench2_ret is not None else None,
        "bars_held": len(window),
        "status": status,
    }


def _load_bars(con: duckdb.DuckDBPyConnection, ticker: str) -> pd.DataFrame:
    df = con.execute(
        "SELECT date, open, high, low, close FROM prices_daily WHERE ticker = ? ORDER BY date",
        [ticker],
    ).df()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def update_outcomes(
    con: duckdb.DuckDBPyConnection,
    horizons: Sequence[int],
    benchmark_ticker: str | None = "SPY",
    secondary_ticker: str | None = None,
) -> int:
    """Calcula resultados pendientes para todas las señales con ticker. Idempotente.

    Devuelve el número de filas (señal, horizonte) escritas.
    """
    pending = con.execute(
        """
        SELECT s.signal_id, s.ticker, s.as_of_date, h.horizon
        FROM signals s
        CROSS JOIN (SELECT unnest(?::INTEGER[]) AS horizon) h
        LEFT JOIN outcomes o ON o.signal_id = s.signal_id AND o.horizon_days = h.horizon
        WHERE s.ticker IS NOT NULL AND o.signal_id IS NULL
        ORDER BY s.ticker
        """,
        [list(horizons)],
    ).df()
    if pending.empty:
        return 0

    market_dates = [
        r[0] for r in con.execute("SELECT DISTINCT date FROM prices_daily ORDER BY date").fetchall()
    ]
    market_last = market_dates[-1] if market_dates else None
    bench = _load_bars(con, benchmark_ticker) if benchmark_ticker else None
    bench2 = _load_bars(con, secondary_ticker) if secondary_ticker else None

    rows = []
    for ticker, group in pending.groupby("ticker"):
        bars = _load_bars(con, str(ticker))
        if bars.empty:
            continue
        for rec in group.itertuples(index=False):
            as_of = pd.Timestamp(rec.as_of_date).date()
            res = _compute_one(
                bars, bench, as_of, int(rec.horizon), market_last, market_dates, bench2
            )
            if res is None:
                continue
            rows.append({"signal_id": rec.signal_id, "horizon_days": int(rec.horizon), **res})
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    con.register("tt_new_outcomes", df)
    try:
        con.execute(
            """
            INSERT INTO outcomes (signal_id, horizon_days, entry_date, entry_price, exit_date,
                exit_price, ret, mae, mfe, bench_ret, excess_ret, bench2_ret, excess2_ret,
                bars_held, status)
            SELECT signal_id, horizon_days, entry_date, entry_price, exit_date, exit_price, ret,
                mae, mfe, bench_ret, excess_ret, bench2_ret, excess2_ret, bars_held, status
            FROM tt_new_outcomes
            ON CONFLICT (signal_id, horizon_days) DO NOTHING
            """
        )
    finally:
        con.unregister("tt_new_outcomes")
    return len(rows)


def outcome_summary(
    con: duckdb.DuckDBPyConnection, horizon: int, origin: str | None = "live"
) -> pd.DataFrame:
    """Compara grupos: aprobadas, rechazadas, sin decisión (pasaron filtros) y bloqueadas.

    ``origin='backtest'`` resume la reconstrucción histórica; ``None`` mezcla todo.
    """
    return con.execute(
        """
        WITH last_dec AS (
            SELECT signal_id, decision FROM (
                SELECT signal_id, decision, row_number() OVER (
                    PARTITION BY signal_id ORDER BY decided_at DESC, decision_id DESC) rn
                FROM decisions) WHERE rn = 1
        ),
        labeled AS (
            SELECT o.*, CASE
                WHEN NOT s.passed THEN 'bloqueada'
                WHEN ld.decision = 'approve' THEN 'aprobada'
                WHEN ld.decision = 'reject' THEN 'rechazada'
                ELSE 'sin decisión' END AS grupo
            FROM outcomes o JOIN signals s USING (signal_id)
            LEFT JOIN last_dec ld USING (signal_id)
            WHERE o.horizon_days = ? AND (? IS NULL OR s.origin = ?)
        )
        SELECT grupo, count(*) AS n,
            avg(ret) AS ret_medio, median(ret) AS ret_mediano,
            avg(CASE WHEN ret > 0 THEN 1.0 ELSE 0.0 END) AS tasa_acierto,
            avg(excess_ret) AS exceso_medio, avg(mae) AS mae_medio, avg(mfe) AS mfe_medio,
            sum(CASE WHEN status = 'truncated' THEN 1 ELSE 0 END) AS truncadas
        FROM labeled GROUP BY grupo ORDER BY grupo
        """,
        [horizon, origin, origin],
    ).df()
