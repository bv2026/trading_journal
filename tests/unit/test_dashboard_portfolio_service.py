# -*- coding: utf-8 -*-
"""Tests for Portfolio tab dashboard service calculations."""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.services import dashboard_portfolio as svc


def _positions() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Account": "RH-BV",
            "Ticker": "AAPL",
            "Name": "Apple",
            "TYPE": "Stock",
            "sector": "Technology",
            "Shares": 10,
            "PRICE": 120,
            "Cost_Basis": 100,
            "COST": 1000,
            "MARKET VALUE": 1200,
            "totalReturn": 200,
        },
        {
            "Account": "COINBASE",
            "Ticker": "BTC",
            "Name": "Bitcoin",
            "TYPE": "Crypto",
            "sector": "Unknown",
            "Shares": 1,
            "PRICE": 50000,
            "Cost_Basis": 40000,
            "COST": 40000,
            "MARKET VALUE": 50000,
            "totalReturn": 10000,
        },
        {
            "Account": "RH-BV",
            "Ticker": "MARGIN",
            "Name": "Margin",
            "TYPE": "Margin",
            "sector": "Unknown",
            "Shares": 0,
            "PRICE": None,
            "Cost_Basis": -300,
            "COST": 0,
            "MARKET VALUE": -300,
            "totalReturn": 0,
        },
    ])


def _transactions() -> pd.DataFrame:
    return pd.DataFrame([
        {"account_id": "RH-BV", "broker": "Robinhood", "category": "dividend", "symbol": "AAPL", "amount": 25.0},
        {"account_id": "COINBASE", "broker": "Coinbase", "category": "crypto_flow", "symbol": "BTC", "amount": 100.0},
    ])


def test_portfolio_kpi_row_matches_current_dashboard_formula():
    metrics = {
        "net_cash": 1000.0,
        "dividends": 25.0,
        "rewards": 5.0,
        "margin_int": -3.0,
        "fees": -2.0,
    }

    assert svc.portfolio_kpi_row(metrics) == {
        "Cash In/Out": 1000.0,
        "Div+Rewards": 30.0,
        "Costs": -5.0,
        "Net Income": 25.0,
    }


def test_split_equity_margin_separates_margin_rows():
    pos, margin = svc.split_equity_margin(_positions())

    assert set(pos["Ticker"]) == {"AAPL", "BTC"}
    assert margin.iloc[0]["Ticker"] == "MARGIN"
    assert margin.iloc[0]["MARKET VALUE"] == -300


def test_account_summary_includes_non_equity_cash_and_total():
    pos, margin = svc.split_equity_margin(_positions())
    opts = pd.DataFrame([{"Account": "RH-BV", "MARKET VALUE": 50.0}])
    futs = pd.DataFrame([{"Account": "SCHWAB", "MARKET VALUE": 75.0}])
    crypto = pd.DataFrame()

    result = svc.account_summary(
        pos=pos,
        margin_df=margin,
        opts_all=opts,
        futs_all=futs,
        cry_all=crypto,
        account_balances=pd.DataFrame(),
        transactions=_transactions(),
        all_accounts=["RH-BV", "COINBASE", "SCHWAB"],
        selected_accounts=["RH-BV", "COINBASE", "SCHWAB"],
        cash_balance=500.0,
    )

    rh = result[result["Account"] == "RH-BV"].iloc[0]
    total = result[result["Account"] == "TOTAL"].iloc[0]
    assert rh["Market Value"] == pytest.approx(1250.0)
    assert rh["Margin"] == pytest.approx(300.0)
    assert total["Market Value"] == pytest.approx(51825.0)


def test_asset_class_breakdown_separates_static_crypto_accounts():
    pos, _ = svc.split_equity_margin(_positions())

    result = svc.asset_class_breakdown(
        pos=pos,
        opts_all=pd.DataFrame([{"MARKET VALUE": 50.0}]),
        futs_all=pd.DataFrame([{"MARKET VALUE": 75.0}]),
        cry_all=pd.DataFrame([{"MARKET VALUE": 25.0}]),
        cash_balance=500.0,
        crypto_accounts={"COINBASE"},
    )

    values = dict(zip(result["Asset Class"], result["Market Value"]))
    assert values["Stocks"] == pytest.approx(1200.0)
    assert values["Crypto"] == pytest.approx(50025.0)
    assert values["TOTAL"] == pytest.approx(51850.0)


def test_futures_by_commodity_strips_contract_month():
    futs = pd.DataFrame([
        {"Ticker": "/GCZ26", "qty": 1, "MARKET VALUE": 100.0},
        {"Ticker": "/GCM27", "qty": 1, "MARKET VALUE": -25.0},
        {"Ticker": "_FUTURES_ADJ_", "qty": 0, "MARKET VALUE": 999.0},
    ])

    result = svc.futures_by_commodity(futs)

    assert result.to_dict(orient="records") == [
        {"Commodity": "/GC", "Contracts": 2, "Net_MV": 75.0}
    ]


def test_futures_by_commodity_keeps_vxm_by_contract():
    futs = pd.DataFrame([
        {"Ticker": "/VXMH27", "qty": 1, "MARKET VALUE": 10.0},
        {"Ticker": "/VXMU27", "qty": 2, "MARKET VALUE": -5.0},
    ])

    result = svc.futures_by_commodity(futs)

    assert result.to_dict(orient="records") == [
        {"Commodity": "/VXMH27", "Contracts": 1, "Net_MV": 10.0},
        {"Commodity": "/VXMU27", "Contracts": 1, "Net_MV": -5.0},
    ]


def test_sector_summary_uses_collapsed_labels_and_lifetime_dividends():
    pos, _ = svc.split_equity_margin(_positions())
    result = svc.sector_summary(pos=pos, transactions=_transactions())

    tech = result[result["sector"] == "Technology"].iloc[0]
    other = result[result["sector"] == "Other"].iloc[0]
    assert tech["Dividends"] == pytest.approx(25.0)
    assert other["Market_Value"] == pytest.approx(50000.0)
