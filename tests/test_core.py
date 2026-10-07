from datetime import date

from tradingtool.config import AppConfig, CostsConfig, load_config
from tradingtool.ids import stable_hash
from tradingtool.insiders.store import upsert_filings
from tradingtool.models import Form4Filing, InsiderTransaction, ReportingOwner


def _filing(acc="0000000001-26-000001", price=10.0):
    return Form4Filing(
        accession=acc,
        source="fixture",
        form_type="4",
        filing_date=date(2026, 10, 1),
        acceptance_ts=None,
        period_of_report=date(2026, 9, 29),
        issuer_cik="0000000123",
        issuer_name="Acme",
        issuer_ticker="ACME",
        aff10b5one=False,
        owners=(ReportingOwner("0000000999", "Jane Doe", is_director=True),),
        transactions=(
            InsiderTransaction(
                0,
                False,
                "Common Stock",
                date(2026, 9, 29),
                "P",
                1000.0,
                price,
                "A",
                5000.0,
                "D",
                footnote_ids=("F1",),
            ),
        ),
        footnotes={"F1": "Weighted average price."},
    )


def test_upsert_is_idempotent_and_replaces(con):
    assert upsert_filings(con, [_filing()]) == 1
    assert upsert_filings(con, [_filing(price=11.0)]) == 1
    assert con.sql("select count(*) from insider_filings").fetchone()[0] == 1
    assert con.sql("select count(*) from insider_transactions").fetchone()[0] == 1
    assert con.sql("select price_per_share, footnote_ids from insider_transactions").fetchone() == (
        11.0,
        "F1",
    )
    assert upsert_filings(con, []) == 0


def test_config_defaults_and_hash_stable(tmp_path):
    a = load_config(tmp_path)  # sin archivos -> defaults
    b = AppConfig()
    assert a.config_hash() == b.config_hash()
    assert stable_hash({"b": 1, "a": 2}) == stable_hash({"a": 2, "b": 1})


def test_repo_config_files_are_valid():
    from pathlib import Path

    cfg = load_config(Path(__file__).parents[1] / "config")
    assert cfg.screener.strategy_version


def test_costs_last_tier_must_be_unbounded():
    import pytest

    with pytest.raises(ValueError):
        CostsConfig(slippage_tiers=({"max_adv_usd": 1e6, "bps_per_side": 10},))
