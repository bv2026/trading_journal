# -*- coding: utf-8 -*-
"""Unit tests for src.metrics.

Covers every real bug that was hit in production:
  - colour_cell crashing on string values like "TOTAL"
  - colour_cell crashing on None / NaN
  - style_table referencing columns that don't exist in the DataFrame
  - compute_metrics with an empty DataFrame
  - compute_metrics correctly separating bank-funded crypto from wallet transfers
  - net_income sign convention (costs are negative)
"""
import math
import pandas as pd
import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.metrics import colour_cell, style_table, compute_metrics, net_income


# ── colour_cell ───────────────────────────────────────────────────────────────

class TestColourCell:
    def test_positive_green(self):
        assert colour_cell(100.0) == "color: #16a34a"

    def test_zero_green(self):
        assert colour_cell(0) == "color: #16a34a"

    def test_negative_red(self):
        assert colour_cell(-0.01) == "color: #dc2626"

    def test_string_number_positive(self):
        assert colour_cell("50") == "color: #16a34a"

    def test_string_number_negative(self):
        assert colour_cell("-10") == "color: #dc2626"

    # ── Real bug: any non-numeric string used to crash with TypeError ──────────
    def test_string_label_returns_empty(self):
        """'TOTAL', 'N/A', etc. must not raise — return ''."""
        assert colour_cell("TOTAL") == ""
        assert colour_cell("N/A") == ""
        assert colour_cell("") == ""
        assert colour_cell("unknown") == ""

    def test_none_returns_empty(self):
        assert colour_cell(None) == ""

    def test_nan_returns_empty(self):
        assert colour_cell(float("nan")) == ""
        assert colour_cell(math.nan) == ""

    def test_pandas_na_returns_empty(self):
        assert colour_cell(pd.NA) == ""

    def test_pandas_nat_returns_empty(self):
        assert colour_cell(pd.NaT) == ""


# ── style_table ───────────────────────────────────────────────────────────────

class TestStyleTable:
    def _df(self):
        return pd.DataFrame([
            {"Account": "RH-BV", "Dividends": 123.45, "Fees": -10.0},
            {"Account": "TOTAL", "Dividends": 123.45, "Fees": -10.0},
        ])

    # ── Real bug: missing columns caused KeyError inside pandas Styler ─────────
    def test_missing_columns_silently_ignored(self):
        """style_table must not raise when money_cols contains non-existent cols."""
        df = self._df()
        # "PnL" and "Market_Value" do not exist — should be silently skipped
        styled = style_table(df, ["Dividends", "Fees", "PnL", "Market_Value"])
        # Just check it returns a Styler without error
        assert hasattr(styled, "to_html")

    def test_all_present_columns_formatted(self):
        df = self._df()
        styled = style_table(df, ["Dividends", "Fees"])
        assert hasattr(styled, "to_html")

    def test_no_money_cols(self):
        df = self._df()
        styled = style_table(df, [])
        assert hasattr(styled, "to_html")

    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["Account", "Dividends"])
        styled = style_table(df, ["Dividends"])
        assert hasattr(styled, "to_html")


# ── compute_metrics ───────────────────────────────────────────────────────────

def _make_tx(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal transactions DataFrame from a list of dicts."""
    base = {"category": "cash_flow", "subcategory": "deposit",
            "amount": 0.0, "symbol": None, "description": ""}
    records = [{**base, **r} for r in rows]
    return pd.DataFrame(records)


class TestComputeMetrics:
    def test_empty_dataframe(self):
        """compute_metrics on an empty DataFrame must return all-zero floats."""
        empty = pd.DataFrame(columns=["category", "subcategory", "amount"])
        m = compute_metrics(empty)
        assert m["deposits"] == 0.0
        assert m["withdrawals"] == 0.0
        assert m["net_cash"] == 0.0
        assert m["dividends"] == 0.0
        assert m["rewards"] == 0.0
        assert m["margin_int"] == 0.0
        assert m["fees"] == 0.0

    def test_cash_flow_deposit(self):
        df = _make_tx([{"category": "cash_flow", "subcategory": "deposit", "amount": 1000.0}])
        m = compute_metrics(df)
        assert m["deposits"] == 1000.0
        assert m["withdrawals"] == 0.0
        assert m["net_cash"] == 1000.0

    def test_cash_flow_withdrawal(self):
        df = _make_tx([{"category": "cash_flow", "subcategory": "withdrawal", "amount": -500.0}])
        m = compute_metrics(df)
        assert m["withdrawals"] == -500.0
        assert m["net_cash"] == -500.0

    def test_internal_transfer_excluded_from_net_cash(self):
        df = _make_tx([
            {"category": "cash_flow", "subcategory": "deposit", "amount": 1000.0},
            {"category": "cash_flow", "subcategory": "internal_transfer", "amount": 500.0},
        ])
        m = compute_metrics(df)
        # Internal transfers must not count toward net_cash
        assert m["net_cash"] == 1000.0
        assert m["deposits"] == 1000.0

    def test_dividends(self):
        df = _make_tx([{"category": "dividend", "subcategory": "cash_div", "amount": 42.0}])
        m = compute_metrics(df)
        assert m["dividends"] == 42.0

    def test_rewards(self):
        df = _make_tx([{"category": "reward", "subcategory": "staking", "amount": 15.0}])
        m = compute_metrics(df)
        assert m["rewards"] == 15.0

    def test_margin_interest_negative(self):
        df = _make_tx([{"category": "margin_interest", "subcategory": "monthly", "amount": -25.0}])
        m = compute_metrics(df)
        assert m["margin_int"] == -25.0

    def test_fees_negative(self):
        df = _make_tx([{"category": "fee", "subcategory": "trading_fee", "amount": -3.0}])
        m = compute_metrics(df)
        assert m["fees"] == -3.0

    def test_bank_crypto_counts_toward_net_cash(self):
        """usd_deposit, usd_withdrawal, bank_purchase → net_cash, NOT crypto bucket."""
        df = _make_tx([
            {"category": "crypto_flow", "subcategory": "usd_deposit",   "amount": 200.0},
            {"category": "crypto_flow", "subcategory": "bank_purchase",  "amount": 300.0},
            {"category": "crypto_flow", "subcategory": "usd_withdrawal", "amount": -100.0},
        ])
        m = compute_metrics(df)
        assert m["net_cash"] == pytest.approx(400.0)
        assert m["deposits"] == pytest.approx(500.0)   # 200 + 300
        assert m["withdrawals"] == pytest.approx(-100.0)
        assert m["crypto_in"] == 0.0
        assert m["crypto_out"] == 0.0

    def test_wallet_crypto_in_separate_bucket(self):
        """crypto_received / crypto_sent → crypto bucket, not net_cash."""
        df = _make_tx([
            {"category": "crypto_flow", "subcategory": "crypto_received", "amount": 1000.0},
            {"category": "crypto_flow", "subcategory": "crypto_sent",     "amount": -400.0},
        ])
        m = compute_metrics(df)
        assert m["crypto_in"]  == pytest.approx(1000.0)
        assert m["crypto_out"] == pytest.approx(-400.0)
        assert m["net_crypto"] == pytest.approx(600.0)
        assert m["net_cash"]   == 0.0

    def test_combined(self):
        rows = [
            {"category": "cash_flow",      "subcategory": "deposit",       "amount":  5000.0},
            {"category": "cash_flow",      "subcategory": "withdrawal",     "amount": -1000.0},
            {"category": "dividend",       "subcategory": "cash_div",       "amount":   100.0},
            {"category": "reward",         "subcategory": "interest",       "amount":    20.0},
            {"category": "margin_interest","subcategory": "monthly",        "amount":   -30.0},
            {"category": "fee",            "subcategory": "trading_fee",    "amount":    -5.0},
        ]
        m = compute_metrics(_make_tx(rows))
        assert m["net_cash"]   == pytest.approx(4000.0)
        assert m["dividends"]  == pytest.approx(100.0)
        assert m["rewards"]    == pytest.approx(20.0)
        assert m["margin_int"] == pytest.approx(-30.0)
        assert m["fees"]       == pytest.approx(-5.0)


# ── net_income ────────────────────────────────────────────────────────────────

class TestNetIncome:
    def test_positive(self):
        m = {"dividends": 100.0, "rewards": 20.0, "margin_int": -30.0, "fees": -5.0}
        assert net_income(m) == pytest.approx(85.0)

    def test_all_zero(self):
        m = {"dividends": 0.0, "rewards": 0.0, "margin_int": 0.0, "fees": 0.0}
        assert net_income(m) == 0.0

    def test_negative_income(self):
        m = {"dividends": 10.0, "rewards": 5.0, "margin_int": -50.0, "fees": -20.0}
        assert net_income(m) == pytest.approx(-55.0)
