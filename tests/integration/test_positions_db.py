# -*- coding: utf-8 -*-
"""Integration tests for the positions DB pipeline.

Covers the full round-trip:
  CSV parse → delete_positions_by_account → insert_positions
  → load_positions_db → load_positions_from_db (with mocked prices)

yfinance is monkey-patched so tests run offline and deterministically.
"""
import math
from pathlib import Path

import pandas as pd
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import src.db as db_module
from src.db import init_db, upsert_accounts, delete_positions_by_account, insert_positions, load_positions_db
from src.parsers.positions_csv import parse
import src.positions as positions_module


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_journal.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(positions_module, "DB_PATH", db_path, raising=False)
    return db_path


@pytest.fixture()
def seeded_db(tmp_db):
    init_db()
    upsert_accounts([
        {"account_id": "SCHWAB",  "broker": "schwab",  "account_type": "investment", "holder": None},
        {"account_id": "TRADIER", "broker": "tradier", "account_type": "investment", "holder": None},
    ])
    return tmp_db


@pytest.fixture()
def mock_prices(monkeypatch):
    """Patch _fetch_live_prices to return deterministic prices without network."""
    def _fake_prices(tickers):
        return {t: 100.0 for t in tickers}
    monkeypatch.setattr(positions_module, "_fetch_live_prices", _fake_prices)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_positions_csv(tmp_path: Path, rows: list[dict], name: str = "positions-test.csv") -> Path:
    p = tmp_path / name
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _pos_row(ticker, shares=10.0, cost_basis=50.0, sector="Technology") -> dict:
    return {
        "Ticker": ticker, "Name": f"{ticker} Inc",
        "Sh/Contr": shares, "COST BASIS": f"${cost_basis:.2f}",
        "PRICE": "$100.00",
        "COST": f"${shares * cost_basis:.2f}",
        "MARKET VALUE": f"${shares * 100:.2f}",
        "totalReturn": f"${shares * (100 - cost_basis):.2f}",
        "sector": sector, "industry": "Software",
        "IV RANK": "40%", "PERF YTD": "10%", "ATR %": "2%", "TYPE": "Stock",
    }


def _margin_row(balance: float) -> dict:
    sign = "-" if balance < 0 else ""
    return {
        "Ticker": "MARGIN", "Name": "Margin Balance",
        "MARKET VALUE": f"$({abs(balance):,.2f})" if balance < 0 else f"${balance:,.2f}",
        "Sh/Contr": None, "COST BASIS": None, "PRICE": None,
        "COST": None, "totalReturn": None,
        "sector": None, "industry": None,
        "IV RANK": None, "PERF YTD": None, "ATR %": None, "TYPE": None,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPositionsRoundTrip:

    def test_insert_and_load(self, seeded_db):
        recs = [
            {"account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
             "shares": 10.0, "cost_basis": 150.0, "sector": "Technology",
             "industry": "Hardware", "asset_type": "Stock",
             "iv_rank": 40.0, "perf_ytd": 5.0, "atr_pct": 2.0,
             "source_file": "test.csv"},
        ]
        insert_positions(recs)
        pos = load_positions_db()
        assert len(pos) == 1
        assert pos.iloc[0]["Ticker"] == "AAPL"
        assert pos.iloc[0]["Shares"] == pytest.approx(10.0)
        assert pos.iloc[0]["Cost_Basis"] == pytest.approx(150.0)

    def test_delete_then_reinsert_replaces_account(self, seeded_db):
        recs = [{"account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
                 "shares": 10.0, "cost_basis": 150.0, "sector": "Technology",
                 "industry": None, "asset_type": None,
                 "iv_rank": None, "perf_ytd": None, "atr_pct": None,
                 "source_file": "test.csv"}]
        insert_positions(recs)
        assert len(load_positions_db()) == 1

        delete_positions_by_account("SCHWAB")
        new_recs = [{"account_id": "SCHWAB", "ticker": "MSFT", "name": "Microsoft",
                     "shares": 5.0, "cost_basis": 300.0, "sector": "Technology",
                     "industry": None, "asset_type": None,
                     "iv_rank": None, "perf_ytd": None, "atr_pct": None,
                     "source_file": "test.csv"}]
        insert_positions(new_recs)
        pos = load_positions_db()
        assert len(pos) == 1
        assert pos.iloc[0]["Ticker"] == "MSFT"

    def test_csv_parse_and_insert(self, tmp_path, seeded_db):
        csv_path = _write_positions_csv(tmp_path, [_pos_row("AAPL"), _pos_row("MSFT")])
        recs = parse(str(csv_path), "SCHWAB")
        assert len(recs) == 2
        delete_positions_by_account("SCHWAB")
        insert_positions(recs)
        pos = load_positions_db()
        assert set(pos["Ticker"]) == {"AAPL", "MSFT"}

    def test_margin_stored_and_loaded(self, tmp_path, seeded_db):
        """MARGIN row must land in DB with the correct (negative) cost_basis."""
        csv_path = _write_positions_csv(tmp_path, [
            _pos_row("AAPL"),
            _margin_row(-25000.0),
        ])
        recs = parse(str(csv_path), "SCHWAB")
        margin_recs = [r for r in recs if r["ticker"] == "MARGIN"]
        assert len(margin_recs) == 1
        assert margin_recs[0]["cost_basis"] == pytest.approx(-25000.0)

        insert_positions(recs)
        pos = load_positions_db()
        margin_row = pos[pos["Ticker"] == "MARGIN"]
        assert len(margin_row) == 1
        assert margin_row.iloc[0]["Cost_Basis"] == pytest.approx(-25000.0)

    def test_load_positions_from_db_computes_derived_cols(self, tmp_path, seeded_db, mock_prices, monkeypatch):
        """load_positions_from_db must compute COST, MARKET VALUE, totalReturn."""
        # Point DB load at the test DB
        monkeypatch.setattr(
            positions_module,
            "load_positions_from_db",
            positions_module.load_positions_from_db,
        )
        recs = [{"account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
                 "shares": 10.0, "cost_basis": 80.0, "sector": "Technology",
                 "industry": None, "asset_type": None,
                 "iv_rank": None, "perf_ytd": None, "atr_pct": None,
                 "source_file": "test.csv"}]
        insert_positions(recs)

        # Re-point DB_PATH in db module so load_positions_db picks up the test DB
        monkeypatch.setattr(db_module, "DB_PATH", seeded_db)

        pos = positions_module.load_positions_from_db()
        aapl = pos[pos["Ticker"] == "AAPL"].iloc[0]

        # mock price = 100.0, shares = 10, cost_basis = 80
        assert aapl["PRICE"]         == pytest.approx(100.0)
        assert aapl["COST"]          == pytest.approx(800.0)   # 10 × 80
        assert aapl["MARKET VALUE"]  == pytest.approx(1000.0)  # 10 × 100
        assert aapl["totalReturn"]   == pytest.approx(200.0)   # 1000 - 800

    def test_margin_market_value_from_cost_basis(self, tmp_path, seeded_db, mock_prices):
        """MARGIN rows must get MARKET VALUE = cost_basis (no yfinance lookup)."""
        recs = [
            {"account_id": "SCHWAB", "ticker": "MARGIN", "name": "Margin",
             "shares": None, "cost_basis": -25000.0, "sector": None,
             "industry": None, "asset_type": None,
             "iv_rank": None, "perf_ytd": None, "atr_pct": None,
             "source_file": "test.csv"},
        ]
        insert_positions(recs)
        pos = positions_module.load_positions_from_db()
        margin = pos[pos["Ticker"] == "MARGIN"].iloc[0]
        assert margin["MARKET VALUE"] == pytest.approx(-25000.0)
        assert margin["COST"]         == pytest.approx(0.0)
        assert margin["totalReturn"]  == pytest.approx(0.0)

    def test_multiple_accounts_coexist(self, seeded_db):
        """Positions from two accounts must both appear in load_positions_db."""
        insert_positions([
            {"account_id": "SCHWAB",  "ticker": "AAPL", "name": "Apple",
             "shares": 10.0, "cost_basis": 150.0, "sector": "Technology",
             "industry": None, "asset_type": None,
             "iv_rank": None, "perf_ytd": None, "atr_pct": None,
             "source_file": "test.csv"},
            {"account_id": "TRADIER", "ticker": "MSFT", "name": "Microsoft",
             "shares": 5.0, "cost_basis": 300.0, "sector": "Technology",
             "industry": None, "asset_type": None,
             "iv_rank": None, "perf_ytd": None, "atr_pct": None,
             "source_file": "test.csv"},
        ])
        pos = load_positions_db()
        assert set(pos["Account"]) == {"SCHWAB", "TRADIER"}
        assert set(pos["Ticker"])  == {"AAPL", "MSFT"}
