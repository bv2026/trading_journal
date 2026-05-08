# -*- coding: utf-8 -*-
"""Tests for Yearly Summary and By Account dashboard service calculations."""
from pathlib import Path
import datetime as dt
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.services import dashboard_transactions as svc


def _transactions() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "date": pd.Timestamp("2025-01-05"),
            "account_id": "RH-BV",
            "category": "cash_flow",
            "subcategory": "deposit",
            "amount": 1000.0,
        },
        {
            "date": pd.Timestamp("2026-01-05"),
            "account_id": "RH-BV",
            "category": "dividend",
            "subcategory": "cash_div",
            "amount": 25.0,
        },
        {
            "date": pd.Timestamp("2026-01-06"),
            "account_id": "SCHWAB",
            "category": "reward",
            "subcategory": "interest",
            "amount": 5.0,
        },
        {
            "date": pd.Timestamp("2026-01-07"),
            "account_id": "SCHWAB",
            "category": "fee",
            "subcategory": "wire",
            "amount": -2.0,
        },
        {
            "date": pd.Timestamp("2026-01-08"),
            "account_id": "COINBASE",
            "category": "crypto_flow",
            "subcategory": "usd_deposit",
            "amount": 300.0,
        },
        {
            "date": pd.Timestamp("2026-01-09"),
            "account_id": "COINBASE",
            "category": "crypto_flow",
            "subcategory": "crypto_sent",
            "amount": -50.0,
        },
    ])


def test_yearly_summary_table_uses_prior_current_and_all_columns():
    result = svc.yearly_summary_table(_transactions(), today=dt.date(2026, 5, 8))

    assert list(result.columns) == ["Metric", 2025, 2026, "ALL"]
    net_cash = result[result["Metric"] == "Net Cash"].iloc[0]
    assert net_cash[2025] == pytest.approx(1000.0)
    assert net_cash[2026] == pytest.approx(300.0)
    assert net_cash["ALL"] == pytest.approx(1300.0)


def test_income_breakdown_by_type_only_includes_positive_income():
    result = svc.income_breakdown_by_type(_transactions(), today=dt.date(2026, 5, 8))

    rows = result.set_index("Type")
    assert rows.loc["cash_div", 2026] == pytest.approx(25.0)
    assert rows.loc["interest", 2026] == pytest.approx(5.0)
    assert "wire" not in rows.index


def test_by_account_pivots_include_total_row_and_selected_accounts():
    result = svc.by_account_pivots(
        _transactions(),
        all_accounts=["RH-BV", "SCHWAB", "COINBASE"],
        selected_accounts=["RH-BV", "SCHWAB"],
        today=dt.date(2026, 5, 8),
    )

    net_cash = result["net_cash_flow"]
    assert list(net_cash["Account"]) == ["RH-BV", "SCHWAB", "TOTAL"]
    assert net_cash.loc[net_cash["Account"] == "RH-BV", 2025].iloc[0] == pytest.approx(1000.0)
    assert net_cash.loc[net_cash["Account"] == "TOTAL", 2026].iloc[0] == pytest.approx(300.0)

    div_rewards = result["div_rewards"]
    assert div_rewards.loc[div_rewards["Account"] == "TOTAL", "ALL"].iloc[0] == pytest.approx(30.0)

    margin_fees = result["margin_fees"]
    assert margin_fees.loc[margin_fees["Account"] == "SCHWAB", 2026].iloc[0] == pytest.approx(-2.0)


def test_crypto_flow_summary_builds_inflow_outflow_and_net_tables():
    result = svc.crypto_flow_summary(_transactions())

    assert result["has_crypto_flow"] is True
    assert result["total_in"] == pytest.approx(300.0)
    assert result["total_out"] == pytest.approx(-50.0)
    assert result["net"] == pytest.approx(250.0)
    assert result["inflows"].iloc[-1].to_dict() == {"Type": "Total In", "Amount": 300.0, "Txns": ""}
    assert result["outflows"].iloc[-1].to_dict() == {"Type": "Total Out", "Amount": -50.0, "Txns": ""}


def test_transaction_filter_options_and_filtered_table():
    txns = _transactions().assign(
        broker=["Robinhood", "Robinhood", "Schwab", "Schwab", "Coinbase", "Coinbase"],
        currency="USD",
        symbol=["", "AAPL", "", "", "BTC", "BTC"],
        description=["Deposit", "AAPL dividend", "Interest", "Wire fee", "USD deposit", "Wallet sent"],
    )

    options = svc.transaction_filter_options(txns)
    result = svc.filtered_transactions_table(
        txns,
        categories=["dividend", "fee"],
        brokers=["Robinhood", "Schwab"],
        years=[2026],
        search="",
    )

    assert options["brokers"] == ["Coinbase", "Robinhood", "Schwab"]
    assert options["years"] == [2026, 2025]
    assert list(result["category"]) == ["fee", "dividend"]
    assert list(result.columns) == svc.TRANSACTION_DISPLAY_COLUMNS
