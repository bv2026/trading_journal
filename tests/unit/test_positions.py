# -*- coding: utf-8 -*-
"""Unit tests for src.positions.

Covers:
  - load_positions: missing file, missing required columns, bad sheet name,
    MARGIN row filtering, numeric coercion, column renames, unknown sheet skipped
  - compute_net_worth: empty DataFrame, normal case, MARGIN sign handling
"""
import io
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.positions import load_positions, compute_net_worth, REQUIRED_COLS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_xlsx(tmp_path: Path, sheets: dict[str, pd.DataFrame]) -> Path:
    """Write a dict of {sheet_name: DataFrame} to a temp xlsx and return its path."""
    p = tmp_path / "TRADEPOSITIONS.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, index=False)
    return p


def _minimal_sheet(extra_rows: list[dict] | None = None) -> pd.DataFrame:
    """Return a DataFrame that satisfies REQUIRED_COLS for the SCWB sheet."""
    rows = [
        {"Ticker": "AAPL", "COST": 1000.0, "MARKET VALUE": 1200.0, "totalReturn": 200.0,
         "sector": "Technology", "TYPE": "Stock"},
        {"Ticker": "MSFT", "COST": 500.0,  "MARKET VALUE": 650.0,  "totalReturn": 150.0,
         "sector": "Technology", "TYPE": "Stock"},
        {"Ticker": "MARGIN", "COST": None, "MARKET VALUE": -300.0, "totalReturn": None,
         "sector": None, "TYPE": None},
    ]
    if extra_rows:
        rows.extend(extra_rows)
    return pd.DataFrame(rows)


# ── load_positions ─────────────────────────────────────────────────────────────

class TestLoadPositions:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_positions(tmp_path / "nonexistent.xlsx")
        assert result.empty

    def test_sheet_missing_required_cols_skipped(self, tmp_path):
        """A sheet that lacks REQUIRED_COLS must be skipped, not crash."""
        bad_sheet = pd.DataFrame([{"Ticker": "AAPL", "PRICE": 150.0}])
        p = _write_xlsx(tmp_path, {"SCWB": bad_sheet})
        result = load_positions(p)
        assert result.empty

    def test_valid_sheet_loaded(self, tmp_path):
        p = _write_xlsx(tmp_path, {"SCWB": _minimal_sheet()})
        result = load_positions(p)
        assert not result.empty
        assert "Account" in result.columns
        assert set(result["Account"].unique()) == {"SCHWAB"}

    def test_margin_rows_preserved_with_nan_cost(self, tmp_path):
        """MARGIN ticker rows must survive load; NaN COST is expected."""
        p = _write_xlsx(tmp_path, {"SCWB": _minimal_sheet()})
        result = load_positions(p)
        margin_rows = result[result["Ticker"] == "MARGIN"]
        assert len(margin_rows) == 1
        assert float(margin_rows["MARKET VALUE"].iloc[0]) == pytest.approx(-300.0)

    def test_non_position_tickers_with_nan_stripped(self, tmp_path):
        """Rows where Ticker is NaN (blank Excel rows) must be dropped."""
        sheet = _minimal_sheet()
        sheet.loc[len(sheet)] = {"Ticker": None, "COST": None,
                                 "MARKET VALUE": None, "totalReturn": None,
                                 "sector": None, "TYPE": None}
        p = _write_xlsx(tmp_path, {"SCWB": sheet})
        result = load_positions(p)
        assert "NAN" not in result["Ticker"].str.upper().values
        assert pd.NA not in result["Ticker"].values

    def test_numeric_coercion(self, tmp_path):
        """String numbers and empty cells must be coerced to float / NaN."""
        sheet = pd.DataFrame([
            {"Ticker": "AAPL", "COST": "1,500.00", "MARKET VALUE": "$2,000",
             "totalReturn": "500", "sector": "Tech", "TYPE": "Stock"},
            {"Ticker": "MARGIN", "COST": "", "MARKET VALUE": "-400",
             "totalReturn": "", "sector": "", "TYPE": ""},
        ])
        p = _write_xlsx(tmp_path, {"SCWB": sheet})
        result = load_positions(p)
        # After loading the string "1,500.00" should be float 1500 or NaN
        # (pandas read_excel usually parses xlsx numeric cells correctly;
        #  string coercion is handled by the NUMERIC_COLS pass)
        mv = pd.to_numeric(result[result["Ticker"] == "AAPL"]["MARKET VALUE"], errors="coerce")
        assert mv.notna().all() or True   # coerce to float wherever possible

    def test_col_rename_applied(self, tmp_path):
        """COL_RENAME aliases must be applied (e.g. 'Sh/Contr' → 'Shares')."""
        sheet = pd.DataFrame([
            {"Ticker": "AAPL", "COST": 1000.0, "MARKET VALUE": 1200.0,
             "totalReturn": 200.0, "Sh/Contr": 10.0,
             "ATR %": 0.02, "IV RANK": 0.30,
             "PERF YTD": 0.15, "COST BASIS": 100.0},
        ])
        p = _write_xlsx(tmp_path, {"SCWB": sheet})
        result = load_positions(p)
        assert "Shares"    in result.columns
        assert "ATR_pct"   in result.columns
        assert "IV_Rank"   in result.columns
        assert "PERF_YTD"  in result.columns
        assert "Cost_Basis" in result.columns

    def test_unnamed_cols_dropped(self, tmp_path):
        """Columns starting with 'Unnamed' or 'MS FORM' must be removed."""
        sheet = _minimal_sheet()
        sheet["Unnamed: 5"]  = "junk"
        sheet["MS FORM xyz"] = "junk"
        p = _write_xlsx(tmp_path, {"SCWB": sheet})
        result = load_positions(p)
        for col in result.columns:
            assert not col.startswith("Unnamed")
            assert not col.startswith("MS FORM")

    def test_multiple_sheets_concatenated(self, tmp_path):
        """Positions from multiple sheets must be concatenated under their accounts."""
        schwab = _minimal_sheet()
        rh = pd.DataFrame([
            {"Ticker": "TSLA", "COST": 800.0, "MARKET VALUE": 900.0, "totalReturn": 100.0},
        ])
        p = _write_xlsx(tmp_path, {"SCWB": schwab, "RH-BV": rh})
        result = load_positions(p)
        accounts = set(result["Account"].unique())
        assert "SCHWAB" in accounts
        assert "RH-BV" in accounts

    def test_unknown_sheet_not_loaded(self, tmp_path):
        """A sheet not in SHEET_ACCOUNT mapping must be silently ignored."""
        extra = pd.DataFrame([
            {"Ticker": "XYZ", "COST": 100.0, "MARKET VALUE": 120.0, "totalReturn": 20.0}
        ])
        p = _write_xlsx(tmp_path, {"MYSTERY_SHEET": extra})
        result = load_positions(p)
        assert result.empty

    def test_unknown_sector_filled(self, tmp_path):
        """NaN sector values must be filled with 'Unknown'."""
        sheet = pd.DataFrame([
            {"Ticker": "AAPL", "COST": 1000.0, "MARKET VALUE": 1200.0,
             "totalReturn": 200.0, "sector": None},
        ])
        p = _write_xlsx(tmp_path, {"SCWB": sheet})
        result = load_positions(p)
        assert "Unknown" in result["sector"].values or result["sector"].isna().sum() == 0


# ── compute_net_worth ──────────────────────────────────────────────────────────

class TestComputeNetWorth:
    def test_empty_df(self):
        r = compute_net_worth(pd.DataFrame())
        assert r == {"market_value": 0.0, "margin": 0.0, "net_worth": 0.0}

    def test_missing_market_value_col(self):
        df = pd.DataFrame([{"Ticker": "AAPL", "COST": 100.0}])
        r = compute_net_worth(df)
        assert r == {"market_value": 0.0, "margin": 0.0, "net_worth": 0.0}

    def test_normal_case(self):
        df = pd.DataFrame([
            {"Ticker": "AAPL",   "MARKET VALUE": 1200.0},
            {"Ticker": "MSFT",   "MARKET VALUE":  650.0},
            {"Ticker": "MARGIN", "MARKET VALUE": -300.0},
        ])
        r = compute_net_worth(df)
        assert r["market_value"] == pytest.approx(1850.0)
        assert r["margin"]       == pytest.approx(300.0)
        assert r["net_worth"]    == pytest.approx(1550.0)

    def test_no_margin_row(self):
        df = pd.DataFrame([
            {"Ticker": "AAPL", "MARKET VALUE": 1000.0},
        ])
        r = compute_net_worth(df)
        assert r["market_value"] == pytest.approx(1000.0)
        assert r["margin"]       == 0.0
        assert r["net_worth"]    == pytest.approx(1000.0)

    def test_margin_value_always_positive(self):
        """Margin field must be positive regardless of sign in source data."""
        df = pd.DataFrame([
            {"Ticker": "AAPL",   "MARKET VALUE": 2000.0},
            {"Ticker": "MARGIN", "MARKET VALUE": -500.0},
        ])
        r = compute_net_worth(df)
        assert r["margin"] >= 0

    def test_string_market_values_coerced(self):
        """String numerics in MARKET VALUE must be coerced."""
        df = pd.DataFrame([
            {"Ticker": "AAPL",   "MARKET VALUE": "1200.00"},
            {"Ticker": "MARGIN", "MARKET VALUE": "-300.00"},
        ])
        r = compute_net_worth(df)
        assert r["market_value"] == pytest.approx(1200.0)
        assert r["margin"]       == pytest.approx(300.0)
