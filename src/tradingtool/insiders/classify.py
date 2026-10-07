"""Clasificación de insiders en "rutinarios" u "oportunistas" (Cohen, Malloy y Pomorski, 2012).

Definición implementada (sin mirar al futuro), como en el paper la clasificación se fija al
inicio de cada año calendario:
- Para un evento en el año Y se miran las operaciones en mercado abierto (códigos P o S, no
  derivadas) del insider EN ESA MISMA EMPRESA durante los años Y-3, Y-2 y Y-1, usando solo
  filings presentados antes del 1 de enero de Y.
- Si el insider operó al menos una vez en cada uno de esos 3 años:
    * rutinario  -> existe un mes calendario en el que operó en los 3 años;
    * oportunista -> en caso contrario.
- Si no hay historia suficiente: "no clasificable".

Notas:
- Los datos de la SEC empiezan en 2006, así que antes de 2009 casi todo es "no clasificable".
- En filings conjuntos (varios reporting owners) la operación cuenta para todos ellos.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import duckdb
import pandas as pd

ROUTINE = "rutinario"
OPPORTUNISTIC = "oportunista"
UNCLASSIFIED = "no_clasificable"


def classify_from_trades(
    trade_months: Iterable[tuple[int, int]], year: int, lookback: int = 3
) -> str:
    """Clasifica a partir de pares (año, mes) de operaciones previas del insider."""
    years = set(range(year - lookback, year))
    by_year: dict[int, set[int]] = {}
    for y, m in trade_months:
        if y in years:
            by_year.setdefault(y, set()).add(m)
    if set(by_year) != years:
        return UNCLASSIFIED
    common = set.intersection(*by_year.values())
    return ROUTINE if common else OPPORTUNISTIC


def classify_insiders(
    con: duckdb.DuckDBPyConnection,
    keys: Iterable[tuple[str, str]],
    as_of: date,
    lookback_years: int = 3,
) -> dict[tuple[str, str], str]:
    """Clasifica pares (owner_cik, issuer_cik) para eventos del año de ``as_of``.

    Solo usa filings presentados antes del 1 de enero de ese año (información pública).
    """
    keys = list(dict.fromkeys(keys))
    if not keys:
        return {}
    year = as_of.year
    cutoff = date(year, 1, 1)
    kdf = pd.DataFrame(keys, columns=["owner_cik", "issuer_cik"])
    con.register("tt_cls_keys", kdf)
    try:
        trades = con.execute(
            """
            SELECT DISTINCT o.owner_cik, f.issuer_cik,
                year(t.transaction_date) AS y, month(t.transaction_date) AS m
            FROM insider_transactions t
            JOIN insider_filings f USING (accession)
            JOIN insider_owners o USING (accession)
            JOIN tt_cls_keys k ON k.owner_cik = o.owner_cik AND k.issuer_cik = f.issuer_cik
            WHERE NOT t.is_derivative
              AND t.transaction_code IN ('P', 'S')
              AND t.transaction_date IS NOT NULL
              AND f.filing_date < ?
              AND year(t.transaction_date) BETWEEN ? AND ?
            """,
            [cutoff, year - lookback_years, year - 1],
        ).df()
    finally:
        con.unregister("tt_cls_keys")
    out = {k: UNCLASSIFIED for k in keys}
    if trades.empty:
        return out
    for (owner, issuer), g in trades.groupby(["owner_cik", "issuer_cik"]):
        out[(owner, issuer)] = classify_from_trades(
            zip(g["y"].astype(int), g["m"].astype(int), strict=True), year, lookback_years
        )
    return out
