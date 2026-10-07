"""Limpieza de símbolos bursátiles escritos a mano en los Form 4.

Los insiders (o sus abogados) escriben el símbolo como quieren: ``"OMEX"``, ``(CALX)``,
``NASDAQ: ABC``, ``ABC, ABCW``... Aquí se reduce a un símbolo válido o a ``None``.
La versión en Python y la versión SQL (para cargas masivas y migraciones) aplican las mismas
reglas.
"""

from __future__ import annotations

import re

TICKER_NULLS = ("", "NONE", "N/A", "NA", "NULL", "-", "--")
_VALID = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")


def normalize_ticker(raw: str | None) -> str | None:
    if raw is None:
        return None
    t = raw.strip().upper()
    if t in TICKER_NULLS:
        return None
    t = re.sub(r"[^A-Z0-9.,;/: \-]", "", t)  # comillas, paréntesis, $, etc.
    t = re.sub(r"^.*:\s*", "", t)  # "NASDAQ: ABC" -> "ABC"
    # Algunos filings traen varias clases separadas por coma o espacio: usamos la primera.
    t = re.split(r"[,;/\s]+", t.strip())[0].strip(".-")
    if t in TICKER_NULLS or not _VALID.fullmatch(t):
        return None
    return t


def ticker_sql(col: str) -> str:
    """Expresión DuckDB equivalente a :func:`normalize_ticker` para la columna ``col``."""
    nulls = ", ".join(f"'{n}'" for n in TICKER_NULLS)
    t = (
        "trim(regexp_extract(trim(regexp_replace(regexp_replace(upper(trim("
        + col
        + r")), '[^A-Z0-9.,;/: \-]', '', 'g'), '^.*:\s*', '')), '^[^,;/ ]*'), '.-')"
    )
    return (
        f"CASE WHEN upper(trim({col})) IN ({nulls}) THEN NULL "
        f"WHEN {t} NOT IN ({nulls}) "
        f"AND regexp_full_match({t}, '[A-Z][A-Z0-9.\\-]{{0,9}}') THEN {t} ELSE NULL END"
    )
