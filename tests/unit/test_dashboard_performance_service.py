# -*- coding: utf-8 -*-
"""Tests for Performance tab dashboard service calculations."""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.services import dashboard_performance as svc


def _positions() -> pd.DataFrame:
    return pd.DataFrame([
        {"Account": "RH-BV", "Ticker": "AAPL", "MARKET VALUE": 1200.0},
        {"Account": "RH-BV", "Ticker": "MARGIN", "MARKET VALUE": -300.0},
        {"Account": "SCHWAB", "Ticker": "MSFT", "MARKET VALUE": 600.0},
    ])


def _snapshots() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "account_id": "RH-BV",
            "value_1w": 800.0,
            "value_1m": 750.0,
            "value_3m": 700.0,
            "value_ytd_start": 650.0,
            "value_1y": 600.0,
        },
        {
            "account_id": "SCHWAB",
            "value_1w": 500.0,
            "value_1m": 500.0,
            "value_3m": 500.0,
            "value_ytd_start": 500.0,
            "value_1y": 500.0,
        },
    ])


def test_performance_tables_use_margin_adjusted_net_value_and_cash_row():
    result = svc.performance_tables(
        all_positions=_positions(),
        snapshot_periods=_snapshots(),
        cash_balance=100.0,
    )

    summary = result["summary"]
    returns = result["returns"]

    rh = summary[summary["Account"] == "RH-BV"].iloc[0]
    cash = summary[summary["Account"] == "CASH"].iloc[0]
    total = summary[summary["Account"] == "TOTAL"].iloc[0]

    assert rh["Current Value"] == pytest.approx(900.0)
    assert rh["$ Change"] == pytest.approx(100.0)
    assert rh["% Change"] == pytest.approx(12.5)
    assert cash["1W Ago"] == pytest.approx(100.0)
    assert total["Current Value"] == pytest.approx(1600.0)
    assert total["1W Ago"] == pytest.approx(1400.0)

    total_returns = returns[returns["Account"] == "TOTAL"].iloc[0]
    assert total_returns["1-Week"] == pytest.approx(23.0769230769)


def test_performance_tables_without_snapshots_preserves_nan_caption_signal():
    result = svc.performance_tables(
        all_positions=_positions(),
        snapshot_periods=pd.DataFrame(),
        cash_balance=0.0,
    )

    summary = result["summary"]

    assert result["has_snapshots"] is False
    assert svc.is_nan(summary[summary["Account"] == "RH-BV"].iloc[0]["1W Ago"])
    assert svc.is_nan(summary[summary["Account"] == "TOTAL"].iloc[0]["% Change"])
