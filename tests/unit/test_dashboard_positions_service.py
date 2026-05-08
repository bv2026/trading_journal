# -*- coding: utf-8 -*-
"""Tests for Positions tab dashboard service calculations."""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.services import dashboard_positions as svc


def _transactions() -> pd.DataFrame:
    return pd.DataFrame([
        {"account_id": "RH-BV", "broker": "Robinhood", "category": "dividend", "symbol": "AAPL", "amount": 25.0},
        {"account_id": "TRADIER", "broker": "Tradier", "category": "fee", "symbol": None, "amount": -1.0},
    ])


def _equity() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Account": "RH-BV",
            "Ticker": "AAPL",
            "Name": "Apple",
            "sector": "Technology",
            "COST": 1000.0,
            "MARKET VALUE": 1200.0,
            "totalReturn": 200.0,
        },
        {
            "Account": "TRADIER",
            "Ticker": "MSFT",
            "Name": "Microsoft",
            "sector": "Technology",
            "COST": 500.0,
            "MARKET VALUE": 450.0,
            "totalReturn": -50.0,
        },
        {
            "Account": "RH-BV",
            "Ticker": "MARGIN",
            "Name": "Margin",
            "sector": "Other",
            "COST": 0.0,
            "MARKET VALUE": -100.0,
            "totalReturn": 0.0,
        },
    ])


def test_broker_helpers_filter_positions_by_selected_broker():
    brokers = svc.broker_filter_options(_transactions())
    account_map = svc.account_broker_map(_transactions())
    filtered = svc.filter_positions_by_broker(_equity(), account_map, ["Robinhood"])

    assert brokers == ["Robinhood", "Tradier"]
    assert set(filtered["Account"]) == {"RH-BV"}


def test_equity_positions_summary_excludes_margin_and_adds_dividends():
    result = svc.equity_positions_summary(_equity(), _transactions())

    holdings = result["holdings"]
    totals = result["totals"]
    assert list(holdings["Ticker"]) == ["AAPL", "MSFT"]
    assert holdings.loc[holdings["Ticker"] == "AAPL", "Dividends"].iloc[0] == pytest.approx(25.0)
    assert totals["market_value"] == pytest.approx(1650.0)
    assert totals["total_cost"] == pytest.approx(1500.0)
    assert totals["pnl"] == pytest.approx(150.0)
    assert totals["return_pct"] == pytest.approx(10.0)


def test_option_account_groups_include_display_table_and_totals():
    options = pd.DataFrame([
        {
            "Account": "TRADIER",
            "symbol": "AAPL260619C00200000",
            "underlying": "AAPL",
            "expiry": "2026-06-19",
            "strike": "200",
            "call_put": "C",
            "qty": "1",
            "price": "3",
            "MARKET VALUE": "300",
            "description": "AAPL call",
        },
    ])

    result = svc.option_account_groups(options)

    assert result["total_contracts"] == 1
    assert result["total_market_value"] == pytest.approx(300.0)
    assert result["groups"][0]["label"] == "**TRADIER** — 1 contracts · MV $300"
    assert result["groups"][0]["table"].iloc[0]["strike"] == pytest.approx(200.0)


def test_futures_and_crypto_summaries_return_current_dashboard_totals():
    futures = pd.DataFrame([
        {"Account": "SCHWAB", "Ticker": "/GCZ26", "qty": "1", "price": "10", "MARKET VALUE": "-25"},
    ])
    crypto = pd.DataFrame([
        {"Account": "COINBASE", "Ticker": "BTC", "name": "Bitcoin", "qty": "0.1", "price": "100000", "cost_basis": "8000", "MARKET VALUE": "10000"},
    ])

    fut_result = svc.futures_account_groups(futures)
    cry_result = svc.crypto_positions_summary(crypto)

    assert fut_result["net_market_value"] == pytest.approx(-25.0)
    assert fut_result["groups"][0]["label"] == "**SCHWAB** — 1 contracts · Net MV $-25"
    assert cry_result["holding_count"] == 1
    assert cry_result["market_value"] == pytest.approx(10000.0)
    assert cry_result["cost_basis"] == pytest.approx(8000.0)
    assert cry_result["pnl"] == pytest.approx(2000.0)
