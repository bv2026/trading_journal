# -*- coding: utf-8 -*-
"""Unit tests for src.services.portfolio."""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.services import portfolio


@pytest.fixture()
def db_exists(tmp_path, monkeypatch):
    db_path = tmp_path / "journal.db"
    db_path.touch()
    monkeypatch.setattr(portfolio.db, "DB_PATH", db_path)
    return db_path


def _transactions() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "date": pd.Timestamp("2026-01-05"),
            "account_id": "RH-BV",
            "broker": "robinhood",
            "category": "dividend",
            "subcategory": "cash_div",
            "amount": 25.0,
            "currency": "USD",
            "symbol": "AAPL",
            "description": "AAPL dividend",
        },
        {
            "date": pd.Timestamp("2026-01-06"),
            "account_id": "RH-BV",
            "broker": "robinhood",
            "category": "cash_flow",
            "subcategory": "internal_transfer",
            "amount": 100.0,
            "currency": "USD",
            "symbol": None,
            "description": "Internal move",
        },
        {
            "date": pd.Timestamp("2026-01-07"),
            "account_id": "SCHWAB",
            "broker": "schwab",
            "category": "fee",
            "subcategory": "wire",
            "amount": -5.0,
            "currency": "USD",
            "symbol": None,
            "description": "Wire fee",
        },
    ])


def test_portfolio_summary_excludes_internal_transfers(db_exists, monkeypatch):
    monkeypatch.setattr(portfolio.db, "load_transactions", _transactions)
    monkeypatch.setattr(portfolio, "load_all_positions", lambda: pd.DataFrame([
        {"Account": "RH-BV", "Ticker": "AAPL", "MARKET VALUE": 1200.0},
        {"Account": "RH-BV", "Ticker": "MARGIN", "MARKET VALUE": -300.0},
    ]))

    result = portfolio.get_portfolio_summary(account_id="RH-BV")

    assert result["transaction_count"] == 1
    assert result["dividends"] == 25.0
    assert result["net_cash_flow"] == 0.0
    assert result["live_net_worth"] == pytest.approx(900.0)


def test_query_transactions_formats_rows(db_exists, monkeypatch):
    monkeypatch.setattr(portfolio.db, "load_transactions", _transactions)

    result = portfolio.query_transactions(category="fee")

    assert result["count"] == 1
    row = result["transactions"][0]
    assert row["date"] == "2026-01-07"
    assert row["amount"] == -5.0
    assert row["account_id"] == "SCHWAB"


def test_positions_report_filters_and_summarizes(monkeypatch):
    legacy_rows = pd.DataFrame([
        {
            "Account": "RH-BV", "asset_class": "equity", "Ticker": "AAPL",
            "sector": "Technology", "TYPE": "Stock", "MARKET VALUE": 1200.0,
            "COST": 1000.0, "totalReturn": 200.0,
        },
        {
            "Account": "SCHWAB", "asset_class": "options", "Ticker": "AAPL260619C00200000",
            "MARKET VALUE": 300.0,
        },
        {
            "Account": "RH-BV", "asset_class": "equity", "Ticker": "MARGIN",
            "MARKET VALUE": -300.0,
        },
    ])
    canonical_rows = pd.DataFrame([
        {
            "account_id": "RH-BV",
            "symbol": "AAPL",
            "asset_class": "equity",
            "position_kind": "Stock",
            "market_value": 1200.0,
            "cost_basis": 1000.0,
            "unrealized_pnl": 200.0,
            "sector": "Technology",
            "is_margin": False,
        },
    ])
    monkeypatch.setattr(portfolio, "load_all_positions", lambda: legacy_rows)
    monkeypatch.setattr(portfolio, "load_current_positions", lambda **kwargs: canonical_rows)

    result = portfolio.get_positions_report(asset_class="equity")

    assert result["summary"]["position_count"] == 1
    assert result["summary"]["total_market_value"] == 1200.0
    assert result["summary"]["equity_return_pct"] == 20.0
    assert result["positions"][0]["Ticker"] == "AAPL"
    assert result["canonical_positions"][0]["symbol"] == "AAPL"
    assert result["canonical_positions"][0]["account_id"] == "RH-BV"


def test_performance_report_uses_live_net_values(monkeypatch):
    monkeypatch.setattr(portfolio.db, "load_snapshot_periods", lambda: pd.DataFrame([
        {
            "account_id": "RH-BV",
            "current_value": 700.0,
            "value_1w": 600.0,
            "value_1m": None,
            "value_3m": None,
            "value_ytd_start": None,
            "value_1y": None,
        },
    ]))
    monkeypatch.setattr(portfolio, "load_all_positions", lambda: pd.DataFrame([
        {"Account": "RH-BV", "Ticker": "AAPL", "MARKET VALUE": 1200.0},
        {"Account": "RH-BV", "Ticker": "MARGIN", "MARKET VALUE": -300.0},
    ]))

    rows = portfolio.get_performance_report()

    assert rows[0]["account_id"] == "RH-BV"
    assert rows[0]["current_value"] == pytest.approx(900.0)
    assert rows[0]["returns"]["1w"] == 50.0
    assert rows[-1]["account_id"] == "TOTAL"
