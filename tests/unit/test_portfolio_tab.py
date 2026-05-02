# -*- coding: utf-8 -*-
"""Unit tests for the Portfolio tab helper logic.

Tests cover the pure-Python data transformations that the Portfolio tab
performs before rendering — account summary builder, asset class breakdown,
futures-by-commodity grouping, and sector collapse logic.

These run without Streamlit, a real DB, or network access.
"""
from pathlib import Path
import re
import sys

import pandas as pd
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.positions import compute_net_worth


# ── Helpers mirroring dashboard logic ─────────────────────────────────────────

_ETF_SECTORS = {"Fixed Income", "Broad Market", "International"}


def _collapse_sectors(pos: pd.DataFrame) -> pd.Series:
    """Mirror the sector-collapse logic from tab_portfolio."""
    sec = pos["sector"].copy()
    sec = sec.where(~sec.isin(_ETF_SECTORS), "ETF")
    if "TYPE" in pos.columns:
        is_etf_type = pos["TYPE"].str.upper().eq("ETF") & (sec != "Income ETF")
        sec = sec.where(~is_etf_type, "ETF")
    sec = sec.replace("Unknown", "Other")
    return sec


def _fut_root(ticker: str) -> str:
    """Mirror the futures root-symbol extractor."""
    m = re.match(r'(/[A-Z]+)(?=[A-Z]\d{2})', str(ticker))
    return m.group(1) if m else str(ticker)


def _build_account_summary(pos: pd.DataFrame, margin_df: pd.DataFrame,
                            non_eq_mv: pd.DataFrame, cash_balance: float) -> pd.DataFrame:
    """Mirror the account summary builder from tab_portfolio."""
    pos_by_acct = (
        pos.groupby("Account")
           .agg(Market_Value=("MARKET VALUE", "sum"),
                Total_Cost=("COST", "sum"))
           .reset_index()
    )
    margin_by_acct = (
        margin_df.groupby("Account")["MARKET VALUE"].sum()
                 .reset_index()
                 .rename(columns={"MARKET VALUE": "Margin"})
    )
    rows = []
    for acct in pos["Account"].unique():
        eq_mv  = float(pos_by_acct.loc[pos_by_acct["Account"] == acct, "Market_Value"].sum())
        cost   = float(pos_by_acct.loc[pos_by_acct["Account"] == acct, "Total_Cost"].sum())
        margin = abs(float(margin_by_acct.loc[margin_by_acct["Account"] == acct, "Margin"].sum()))
        other  = float(non_eq_mv.loc[non_eq_mv["Account"] == acct, "Other_MV"].sum()) if not non_eq_mv.empty else 0.0
        mv     = eq_mv + other
        rows.append({
            "Account":      acct,
            "Market Value": mv,
            "Cost Basis":   cost,
            "Margin":       margin,
            "Net Equity":   mv - margin,
        })
    if cash_balance > 0:
        rows.append({
            "Account":      "CASH",
            "Market Value": cash_balance,
            "Cost Basis":   cash_balance,
            "Margin":       0.0,
            "Net Equity":   cash_balance,
        })
    df = pd.DataFrame(rows)
    total = {
        "Account":      "TOTAL",
        "Market Value": df["Market Value"].sum(),
        "Cost Basis":   df["Cost Basis"].sum(),
        "Margin":       df["Margin"].sum(),
        "Net Equity":   df["Net Equity"].sum(),
    }
    return pd.concat([df, pd.DataFrame([total])], ignore_index=True)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def equity_pos():
    return pd.DataFrame([
        {"Account": "RH-BV",  "Ticker": "AAPL", "MARKET VALUE": 10_000.0, "COST": 8_000.0},
        {"Account": "RH-BV",  "Ticker": "MSFT", "MARKET VALUE":  5_000.0, "COST": 4_500.0},
        {"Account": "WEBULL", "Ticker": "AMD",  "MARKET VALUE":  3_000.0, "COST": 2_800.0},
    ])


@pytest.fixture()
def margin_pos():
    return pd.DataFrame([
        {"Account": "RH-BV",  "Ticker": "MARGIN", "MARKET VALUE": -5_000.0},
        {"Account": "WEBULL", "Ticker": "MARGIN", "MARKET VALUE": -1_000.0},
    ])


@pytest.fixture()
def futures_pos():
    return pd.DataFrame([
        {"Account": "SCHWAB", "Ticker": "/GCQ26", "MARKET VALUE":  466_140.0, "qty": -1},
        {"Account": "SCHWAB", "Ticker": "/GCZ26", "MARKET VALUE": -472_950.0, "qty":  1},
        {"Account": "SCHWAB", "Ticker": "/KEU26", "MARKET VALUE":  -35_325.0, "qty": -1},
        {"Account": "SCHWAB", "Ticker": "/KEZ26", "MARKET VALUE":   36_050.0, "qty":  1},
    ])


# ── Sector collapse tests ─────────────────────────────────────────────────────

class TestSectorCollapse:
    def test_fixed_income_becomes_etf(self):
        pos = pd.DataFrame([{"sector": "Fixed Income", "MARKET VALUE": 1000}])
        result = _collapse_sectors(pos)
        assert result.iloc[0] == "ETF"

    def test_broad_market_becomes_etf(self):
        pos = pd.DataFrame([{"sector": "Broad Market", "MARKET VALUE": 1000}])
        result = _collapse_sectors(pos)
        assert result.iloc[0] == "ETF"

    def test_international_becomes_etf(self):
        pos = pd.DataFrame([{"sector": "International", "MARKET VALUE": 1000}])
        result = _collapse_sectors(pos)
        assert result.iloc[0] == "ETF"

    def test_income_etf_preserved(self):
        pos = pd.DataFrame([{"sector": "Income ETF", "TYPE": "ETF", "MARKET VALUE": 1000}])
        result = _collapse_sectors(pos)
        assert result.iloc[0] == "Income ETF"

    def test_type_etf_becomes_etf(self):
        pos = pd.DataFrame([{"sector": "Technology", "TYPE": "ETF", "MARKET VALUE": 1000}])
        result = _collapse_sectors(pos)
        assert result.iloc[0] == "ETF"

    def test_unknown_becomes_other(self):
        pos = pd.DataFrame([{"sector": "Unknown", "MARKET VALUE": 1000}])
        result = _collapse_sectors(pos)
        assert result.iloc[0] == "Other"

    def test_known_sector_unchanged(self):
        pos = pd.DataFrame([{"sector": "Technology", "TYPE": "Stock", "MARKET VALUE": 5000}])
        result = _collapse_sectors(pos)
        assert result.iloc[0] == "Technology"

    def test_mixed_sectors(self):
        pos = pd.DataFrame([
            {"sector": "Technology",  "TYPE": "Stock", "MARKET VALUE": 5000},
            {"sector": "Fixed Income","TYPE": "ETF",   "MARKET VALUE": 2000},
            {"sector": "Income ETF",  "TYPE": "ETF",   "MARKET VALUE": 1000},
            {"sector": "Unknown",     "TYPE": "Stock", "MARKET VALUE":  500},
            {"sector": "International","TYPE": "ETF",  "MARKET VALUE":  800},
        ])
        result = _collapse_sectors(pos)
        assert list(result) == ["Technology", "ETF", "Income ETF", "Other", "ETF"]

    def test_no_unknown_in_collapsed_output(self):
        pos = pd.DataFrame([
            {"sector": "Unknown", "MARKET VALUE": 100},
            {"sector": "Technology", "MARKET VALUE": 200},
        ])
        result = _collapse_sectors(pos)
        assert "Unknown" not in result.values


# ── Futures root extraction tests ─────────────────────────────────────────────

class TestFuturesRoot:
    def test_gold_dec(self):
        assert _fut_root("/GCZ26") == "/GC"

    def test_gold_aug(self):
        assert _fut_root("/GCQ26") == "/GC"

    def test_wheat(self):
        assert _fut_root("/KEU26") == "/KE"

    def test_vxm(self):
        assert _fut_root("/VXMH27") == "/VXM"

    def test_non_futures_passthrough(self):
        assert _fut_root("AAPL") == "AAPL"

    def test_futures_grouping_by_root(self, futures_pos):
        futures_pos["Root"] = futures_pos["Ticker"].apply(_fut_root)
        futures_pos["MARKET VALUE"] = pd.to_numeric(futures_pos["MARKET VALUE"], errors="coerce")
        grp = (
            futures_pos.groupby("Root")
                .agg(Contracts=("qty", "count"), Net_MV=("MARKET VALUE", "sum"))
                .reset_index()
        )
        assert set(grp["Root"]) == {"/GC", "/KE"}
        gc = grp.loc[grp["Root"] == "/GC", "Net_MV"].iloc[0]
        ke = grp.loc[grp["Root"] == "/KE", "Net_MV"].iloc[0]
        assert abs(gc) < abs(466_140)  # GC longs + shorts partially net
        assert ke == pytest.approx(36_050 - 35_325)

    def test_futures_net_mv_sums_correctly(self, futures_pos):
        futures_pos["Root"] = futures_pos["Ticker"].apply(_fut_root)
        futures_pos["MARKET VALUE"] = pd.to_numeric(futures_pos["MARKET VALUE"], errors="coerce")
        grp = futures_pos.groupby("Root")["MARKET VALUE"].sum()
        assert grp["/GC"] == pytest.approx(466_140 - 472_950)
        assert grp["/KE"] == pytest.approx(36_050 - 35_325)


# ── Account summary tests ─────────────────────────────────────────────────────

class TestAccountSummary:
    def test_net_equity_equals_mv_minus_margin(self, equity_pos, margin_pos):
        summary = _build_account_summary(
            equity_pos, margin_pos, pd.DataFrame(columns=["Account", "Other_MV"]), 0.0
        )
        rhbv = summary[summary["Account"] == "RH-BV"].iloc[0]
        assert rhbv["Net Equity"] == pytest.approx(rhbv["Market Value"] - rhbv["Margin"])

    def test_margin_is_positive(self, equity_pos, margin_pos):
        summary = _build_account_summary(
            equity_pos, margin_pos, pd.DataFrame(columns=["Account", "Other_MV"]), 0.0
        )
        assert all(summary["Margin"] >= 0)

    def test_total_row_is_last(self, equity_pos, margin_pos):
        summary = _build_account_summary(
            equity_pos, margin_pos, pd.DataFrame(columns=["Account", "Other_MV"]), 0.0
        )
        assert summary.iloc[-1]["Account"] == "TOTAL"

    def test_total_row_sums_correctly(self, equity_pos, margin_pos):
        summary = _build_account_summary(
            equity_pos, margin_pos, pd.DataFrame(columns=["Account", "Other_MV"]), 0.0
        )
        data = summary[summary["Account"] != "TOTAL"]
        total = summary[summary["Account"] == "TOTAL"].iloc[0]
        assert total["Market Value"] == pytest.approx(data["Market Value"].sum())
        assert total["Margin"]       == pytest.approx(data["Margin"].sum())
        assert total["Net Equity"]   == pytest.approx(data["Net Equity"].sum())

    def test_cash_row_has_zero_pnl(self, equity_pos, margin_pos):
        summary = _build_account_summary(
            equity_pos, margin_pos, pd.DataFrame(columns=["Account", "Other_MV"]), 18_500.0
        )
        cash = summary[summary["Account"] == "CASH"].iloc[0]
        assert cash["Market Value"] == pytest.approx(cash["Cost Basis"])
        assert cash["Margin"] == 0.0
        assert cash["Net Equity"] == pytest.approx(18_500.0)

    def test_no_cash_row_when_balance_zero(self, equity_pos, margin_pos):
        summary = _build_account_summary(
            equity_pos, margin_pos, pd.DataFrame(columns=["Account", "Other_MV"]), 0.0
        )
        assert "CASH" not in summary["Account"].values

    def test_other_mv_included_in_market_value(self, equity_pos, margin_pos):
        other_mv = pd.DataFrame([
            {"Account": "RH-BV",  "Other_MV": 2_000.0},
            {"Account": "WEBULL", "Other_MV":   500.0},
        ])
        summary = _build_account_summary(equity_pos, margin_pos, other_mv, 0.0)
        rhbv = summary[summary["Account"] == "RH-BV"].iloc[0]
        # equity MV = 15_000, other = 2_000 → MV = 17_000
        assert rhbv["Market Value"] == pytest.approx(17_000.0)

    def test_account_without_margin_has_zero_margin(self, equity_pos):
        no_margin = pd.DataFrame(columns=["Account", "Ticker", "MARKET VALUE"])
        summary = _build_account_summary(
            equity_pos, no_margin, pd.DataFrame(columns=["Account", "Other_MV"]), 0.0
        )
        assert all(summary[summary["Account"] != "TOTAL"]["Margin"] == 0.0)

    def test_net_equity_never_negative_for_cash(self, equity_pos, margin_pos):
        summary = _build_account_summary(
            equity_pos, margin_pos, pd.DataFrame(columns=["Account", "Other_MV"]), 5_000.0
        )
        cash = summary[summary["Account"] == "CASH"].iloc[0]
        assert cash["Net Equity"] >= 0


# ── compute_net_worth tests (existing helper, regression guard) ────────────────

class TestComputeNetWorth:
    def test_empty_returns_zeros(self):
        result = compute_net_worth(pd.DataFrame())
        assert result == {"market_value": 0.0, "margin": 0.0, "net_worth": 0.0}

    def test_no_margin(self):
        pos = pd.DataFrame([
            {"Ticker": "AAPL", "MARKET VALUE": 10_000.0},
            {"Ticker": "MSFT", "MARKET VALUE":  5_000.0},
        ])
        result = compute_net_worth(pos)
        assert result["market_value"] == pytest.approx(15_000.0)
        assert result["margin"]       == pytest.approx(0.0)
        assert result["net_worth"]    == pytest.approx(15_000.0)

    def test_margin_subtracted_from_net_worth(self):
        pos = pd.DataFrame([
            {"Ticker": "AAPL",   "MARKET VALUE": 50_000.0},
            {"Ticker": "MARGIN", "MARKET VALUE": -20_000.0},
        ])
        result = compute_net_worth(pos)
        assert result["market_value"] == pytest.approx(50_000.0)
        assert result["margin"]       == pytest.approx(20_000.0)
        assert result["net_worth"]    == pytest.approx(30_000.0)

    def test_margin_always_positive(self):
        pos = pd.DataFrame([
            {"Ticker": "AAPL",   "MARKET VALUE":  10_000.0},
            {"Ticker": "MARGIN", "MARKET VALUE": -99_999.0},
        ])
        result = compute_net_worth(pos)
        assert result["margin"] > 0
