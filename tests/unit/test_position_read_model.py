# -*- coding: utf-8 -*-
"""Unit tests for canonical current-position read model."""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.services import position_read_model


def _mixed_positions() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Account": "RH-BV",
            "Ticker": "AAPL",
            "asset_class": "equity",
            "TYPE": "Stock",
            "Name": "Apple",
            "Shares": 10,
            "PRICE": 120.0,
            "Cost_Basis": 100.0,
            "COST": 1000.0,
            "MARKET VALUE": 1200.0,
            "totalReturn": 200.0,
            "sector": "Technology",
            "industry": "Hardware",
            "source_file": "positions.csv",
            "sync_run_id": 7,
        },
        {
            "Account": "RH-BV",
            "Ticker": "MARGIN",
            "asset_class": "equity",
            "Cost_Basis": -300.0,
            "MARKET VALUE": -300.0,
        },
        {
            "Account": "TRADIER",
            "Ticker": "AAPL260619C00200000",
            "asset_class": "options",
            "description": "AAPL call",
            "underlying": "AAPL",
            "expiry": "2026-06-19",
            "strike": 200.0,
            "call_put": "C",
            "qty": 1,
            "price": 3.0,
            "MARKET VALUE": 300.0,
        },
        {
            "Account": "COINBASE",
            "Ticker": "BTC",
            "asset_class": "crypto",
            "name": "Bitcoin",
            "qty": 0.1,
            "price": 100000.0,
            "cost_basis": 8000.0,
            "MARKET VALUE": 10000.0,
        },
    ])


def test_load_current_positions_normalizes_mixed_assets(monkeypatch):
    monkeypatch.setattr(position_read_model, "load_all_positions", _mixed_positions)

    df = position_read_model.load_current_positions()

    assert list(df.columns) == position_read_model.CANONICAL_COLUMNS
    aapl = df[df["symbol"] == "AAPL"].iloc[0]
    assert aapl["account_id"] == "RH-BV"
    assert aapl["quantity"] == pytest.approx(10)
    assert aapl["price"] == pytest.approx(120.0)
    assert aapl["cost_basis"] == pytest.approx(1000.0)
    assert aapl["market_value"] == pytest.approx(1200.0)
    assert aapl["sync_run_id"] == 7

    option = df[df["asset_class"] == "options"].iloc[0]
    assert option["underlying"] == "AAPL"
    assert option["expiration"] == "2026-06-19"
    assert option["quantity"] == pytest.approx(1)

    margin = df[df["symbol"] == "MARGIN"].iloc[0]
    assert margin["asset_class"] == "margin"
    assert bool(margin["is_margin"]) is True


def test_load_current_positions_filters_margin_account_and_asset(monkeypatch):
    monkeypatch.setattr(position_read_model, "load_all_positions", _mixed_positions)

    df = position_read_model.load_current_positions(
        include_margin=False,
        account_id="RH-BV",
        asset_class="equity",
    )

    assert list(df["symbol"]) == ["AAPL"]
    assert not df["is_margin"].any()


def test_summarize_positions_separates_margin(monkeypatch):
    monkeypatch.setattr(position_read_model, "load_all_positions", _mixed_positions)
    df = position_read_model.load_current_positions()

    summary = position_read_model.summarize_positions(df)

    assert summary["position_count"] == 3
    assert summary["market_value"] == pytest.approx(11500.0)
    assert summary["margin"] == pytest.approx(300.0)
    assert summary["net_value"] == pytest.approx(11200.0)
    assert {row["asset_class"] for row in summary["by_asset_class"]} == {
        "equity", "options", "crypto",
    }


def test_empty_current_positions_shape(monkeypatch):
    monkeypatch.setattr(position_read_model, "load_all_positions", lambda: pd.DataFrame())

    df = position_read_model.load_current_positions()
    summary = position_read_model.summarize_positions(df)

    assert list(df.columns) == position_read_model.CANONICAL_COLUMNS
    assert summary["position_count"] == 0
    assert summary["net_value"] == 0.0
