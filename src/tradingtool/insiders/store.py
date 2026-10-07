"""Persistencia de filings de Form 4 en DuckDB (idempotente por número de accesión)."""

from __future__ import annotations

from collections.abc import Iterable

import duckdb
import pandas as pd

from tradingtool.models import Form4Filing


def _filings_frames(
    filings: Iterable[Form4Filing],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    f_rows, o_rows, t_rows = [], [], []
    for f in filings:
        f_rows.append(
            {
                "accession": f.accession,
                "source": f.source,
                "form_type": f.form_type,
                "filing_date": f.filing_date,
                "acceptance_ts": f.acceptance_ts,
                "period_of_report": f.period_of_report,
                "issuer_cik": f.issuer_cik,
                "issuer_name": f.issuer_name,
                "issuer_ticker": f.issuer_ticker,
                "aff10b5one": f.aff10b5one,
                "mentions_10b5_1": f.mentions_10b5_1,
            }
        )
        for i, o in enumerate(f.owners):
            o_rows.append(
                {
                    "accession": f.accession,
                    "owner_seq": i,
                    "owner_cik": o.owner_cik,
                    "owner_name": o.owner_name,
                    "is_director": o.is_director,
                    "is_officer": o.is_officer,
                    "is_ten_pct_owner": o.is_ten_pct_owner,
                    "is_other": o.is_other,
                    "officer_title": o.officer_title,
                }
            )
        for t in f.transactions:
            t_rows.append(
                {
                    "accession": f.accession,
                    "seq": t.seq,
                    "is_derivative": t.is_derivative,
                    "security_title": t.security_title,
                    "transaction_date": t.transaction_date,
                    "transaction_code": t.transaction_code,
                    "shares": t.shares,
                    "price_per_share": t.price_per_share,
                    "acquired_disposed": t.acquired_disposed,
                    "shares_owned_after": t.shares_owned_after,
                    "direct_indirect": t.direct_indirect,
                    "equity_swap": t.equity_swap,
                    "footnote_ids": ",".join(t.footnote_ids) if t.footnote_ids else None,
                }
            )
    return pd.DataFrame(f_rows), pd.DataFrame(o_rows), pd.DataFrame(t_rows)


_FILING_COLS = (
    "accession, source, form_type, filing_date, acceptance_ts, period_of_report, "
    "issuer_cik, issuer_name, issuer_ticker, aff10b5one, mentions_10b5_1"
)
_OWNER_COLS = (
    "accession, owner_seq, owner_cik, owner_name, is_director, is_officer, "
    "is_ten_pct_owner, is_other, officer_title"
)
_TX_COLS = (
    "accession, seq, is_derivative, security_title, transaction_date, transaction_code, "
    "shares, price_per_share, acquired_disposed, shares_owned_after, direct_indirect, "
    "equity_swap, footnote_ids"
)


def upsert_filings(con: duckdb.DuckDBPyConnection, filings: Iterable[Form4Filing]) -> int:
    """Inserta o reemplaza filings completos (cabecera, dueños y transacciones).

    Devuelve el número de filings escritos. Re-ejecutar con los mismos datos no duplica nada.
    """
    fdf, odf, tdf = _filings_frames(filings)
    if fdf.empty:
        return 0
    con.execute("BEGIN TRANSACTION")
    try:
        con.register("tt_new_filings", fdf)
        con.execute(
            "DELETE FROM insider_owners WHERE accession IN (SELECT accession FROM tt_new_filings)"
        )
        con.execute(
            "DELETE FROM insider_transactions "
            "WHERE accession IN (SELECT accession FROM tt_new_filings)"
        )
        con.execute(
            "DELETE FROM insider_filings WHERE accession IN (SELECT accession FROM tt_new_filings)"
        )
        con.execute(
            f"INSERT INTO insider_filings ({_FILING_COLS}) "  # noqa: S608
            f"SELECT {_FILING_COLS} FROM tt_new_filings"
        )
        if not odf.empty:
            con.register("tt_new_owners", odf)
            con.execute(
                f"INSERT INTO insider_owners ({_OWNER_COLS}) "  # noqa: S608
                f"SELECT {_OWNER_COLS} FROM tt_new_owners"
            )
            con.unregister("tt_new_owners")
        if not tdf.empty:
            con.register("tt_new_tx", tdf)
            con.execute(
                f"INSERT INTO insider_transactions ({_TX_COLS}) SELECT {_TX_COLS} FROM tt_new_tx"  # noqa: S608
            )
            con.unregister("tt_new_tx")
        con.unregister("tt_new_filings")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(fdf)
