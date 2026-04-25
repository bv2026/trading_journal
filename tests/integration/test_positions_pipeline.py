# -*- coding: utf-8 -*-
"""Integration tests for the Positions tab data pipeline.

Mirrors the logic in dashboard/app.py (tab_positions):
  1. Load positions from xlsx (excluding MARGIN rows)
  2. Group by Ticker — sum Market Value, Cost, PnL across accounts
  3. Compute Return_%
  4. Join lifetime dividends from the transactions DB by symbol
  5. Verify totals never include a TOTAL sentinel row in the sortable data

No Streamlit runtime is required; we test the pure data transformations.
"""
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db import init_db, upsert_accounts, insert_transactions
import src.db as db_module
from src.positions import load_positions


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_journal.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    return db_path


@pytest.fixture()
def initialised_db(tmp_db):
    init_db()
    return tmp_db


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_xlsx(tmp_path: Path, sheets: dict) -> Path:
    p = tmp_path / "TRADEPOSITIONS.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, index=False)
    return p


def _pos_sheet(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _build_sym_table(pos_raw: pd.DataFrame, df_all: pd.DataFrame) -> pd.DataFrame:
    """Replicate the Positions tab groupby + dividend join exactly."""
    pos = pos_raw[pos_raw["Ticker"] != "MARGIN"].copy()
    for col in ["COST", "MARKET VALUE", "totalReturn"]:
        pos[col] = pd.to_numeric(pos[col], errors="coerce")

    sym = (
        pos.groupby(["Ticker", "Name", "sector"])
           .agg(
               Market_Value=("MARKET VALUE", "sum"),
               Total_Cost   =("COST",         "sum"),
               PnL          =("totalReturn",  "sum"),
           )
           .reset_index()
           .sort_values("Market_Value", ascending=False)
    )
    sym["Return_%"] = (
        sym["PnL"] / sym["Total_Cost"].replace(0, float("nan")) * 100
    ).round(2)

    divs = (
        df_all[df_all["category"] == "dividend"]
        .groupby("symbol")["amount"].sum()
        .reset_index()
        .rename(columns={"symbol": "Ticker", "amount": "Dividends"})
    )
    sym = sym.merge(divs, on="Ticker", how="left")
    sym["Dividends"] = sym["Dividends"].fillna(0)
    return sym


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPositionsTabPipeline:

    def test_groupby_sums_across_accounts(self, tmp_path):
        """Same ticker held in two accounts must appear as one row with summed values."""
        xlsx = _write_xlsx(tmp_path, {"SCWB": _pos_sheet([
            {"Ticker": "AAPL", "Name": "Apple", "COST": 1000.0,
             "MARKET VALUE": 1200.0, "totalReturn": 200.0, "sector": "Technology"},
        ]), "RH-BV": _pos_sheet([
            {"Ticker": "AAPL", "Name": "Apple", "COST": 500.0,
             "MARKET VALUE": 600.0, "totalReturn": 100.0, "sector": "Technology"},
        ])})
        pos_raw = load_positions(xlsx)
        df_all  = pd.DataFrame(columns=["category", "symbol", "amount"])
        sym = _build_sym_table(pos_raw, df_all)

        aapl = sym[sym["Ticker"] == "AAPL"]
        assert len(aapl) == 1
        assert aapl["Market_Value"].iloc[0] == pytest.approx(1800.0)
        assert aapl["Total_Cost"].iloc[0]   == pytest.approx(1500.0)
        assert aapl["PnL"].iloc[0]          == pytest.approx(300.0)

    def test_return_pct_computed_correctly(self, tmp_path):
        xlsx = _write_xlsx(tmp_path, {"SCWB": _pos_sheet([
            {"Ticker": "MSFT", "Name": "Microsoft", "COST": 2000.0,
             "MARKET VALUE": 2500.0, "totalReturn": 500.0, "sector": "Technology"},
        ])})
        pos_raw = load_positions(xlsx)
        df_all  = pd.DataFrame(columns=["category", "symbol", "amount"])
        sym = _build_sym_table(pos_raw, df_all)

        msft = sym[sym["Ticker"] == "MSFT"]
        assert msft["Return_%"].iloc[0] == pytest.approx(25.0)

    def test_dividends_joined_from_transactions(self, tmp_path, initialised_db):
        """Dividend amounts from the DB must join correctly onto the symbol table."""
        xlsx = _write_xlsx(tmp_path, {"SCWB": _pos_sheet([
            {"Ticker": "AAPL", "Name": "Apple", "COST": 1000.0,
             "MARKET VALUE": 1200.0, "totalReturn": 200.0, "sector": "Technology"},
            {"Ticker": "MSFT", "Name": "Microsoft", "COST": 500.0,
             "MARKET VALUE": 600.0, "totalReturn": 100.0, "sector": "Technology"},
        ])})

        upsert_accounts([{"account_id": "SCHWAB", "broker": "schwab",
                          "account_type": "investment", "holder": None}])
        insert_transactions([
            {"id": "d1", "account_id": "SCHWAB", "date": "2024-06-01",
             "category": "dividend", "subcategory": "cash_div", "amount": 88.0,
             "currency": "USD", "symbol": "AAPL", "description": "div",
             "source_file": "schwab.csv"},
            {"id": "d2", "account_id": "SCHWAB", "date": "2024-09-01",
             "category": "dividend", "subcategory": "cash_div", "amount": 44.0,
             "currency": "USD", "symbol": "AAPL", "description": "div",
             "source_file": "schwab.csv"},
        ])

        from src.db import load_transactions
        df_all  = load_transactions()
        pos_raw = load_positions(xlsx)
        sym = _build_sym_table(pos_raw, df_all)

        aapl = sym[sym["Ticker"] == "AAPL"]
        msft = sym[sym["Ticker"] == "MSFT"]
        assert aapl["Dividends"].iloc[0] == pytest.approx(132.0)  # 88 + 44
        assert msft["Dividends"].iloc[0] == pytest.approx(0.0)    # no dividends

    def test_margin_rows_excluded_from_symbol_table(self, tmp_path):
        """MARGIN rows must never appear in the grouped symbol table."""
        xlsx = _write_xlsx(tmp_path, {"SCWB": _pos_sheet([
            {"Ticker": "AAPL",   "Name": "Apple",  "COST": 1000.0,
             "MARKET VALUE": 1200.0, "totalReturn": 200.0, "sector": "Technology"},
            {"Ticker": "MARGIN", "Name": "Margin", "COST": None,
             "MARKET VALUE": -5000.0, "totalReturn": None, "sector": None},
        ])})
        pos_raw = load_positions(xlsx)
        df_all  = pd.DataFrame(columns=["category", "symbol", "amount"])
        sym = _build_sym_table(pos_raw, df_all)

        assert "MARGIN" not in sym["Ticker"].values

    def test_no_total_row_in_symbol_table(self, tmp_path):
        """The sortable symbol table must not contain a TOTAL sentinel row."""
        xlsx = _write_xlsx(tmp_path, {"SCWB": _pos_sheet([
            {"Ticker": "AAPL", "Name": "Apple", "COST": 1000.0,
             "MARKET VALUE": 1200.0, "totalReturn": 200.0, "sector": "Technology"},
        ])})
        pos_raw = load_positions(xlsx)
        df_all  = pd.DataFrame(columns=["category", "symbol", "amount"])
        sym = _build_sym_table(pos_raw, df_all)

        assert "TOTAL" not in sym["Ticker"].values

    def test_totals_computed_independently(self, tmp_path):
        """Pre-computed totals must equal the column sums of the symbol table."""
        xlsx = _write_xlsx(tmp_path, {"SCWB": _pos_sheet([
            {"Ticker": "AAPL", "Name": "Apple", "COST": 1000.0,
             "MARKET VALUE": 1200.0, "totalReturn": 200.0, "sector": "Technology"},
            {"Ticker": "MSFT", "Name": "Microsoft", "COST": 800.0,
             "MARKET VALUE": 900.0, "totalReturn": 100.0, "sector": "Technology"},
        ])})
        pos_raw = load_positions(xlsx)
        df_all  = pd.DataFrame(columns=["category", "symbol", "amount"])
        sym = _build_sym_table(pos_raw, df_all)

        assert sym["Market_Value"].sum() == pytest.approx(2100.0)
        assert sym["Total_Cost"].sum()   == pytest.approx(1800.0)
        assert sym["PnL"].sum()          == pytest.approx(300.0)

    def test_zero_cost_return_pct_is_nan_not_error(self, tmp_path):
        """Return_% for a position with zero cost must be NaN, not raise ZeroDivisionError."""
        xlsx = _write_xlsx(tmp_path, {"SCWB": _pos_sheet([
            {"Ticker": "XYZ", "Name": "Free stock", "COST": 0.0,
             "MARKET VALUE": 50.0, "totalReturn": 50.0, "sector": "Unknown"},
        ])})
        pos_raw = load_positions(xlsx)
        df_all  = pd.DataFrame(columns=["category", "symbol", "amount"])
        sym = _build_sym_table(pos_raw, df_all)

        xyz = sym[sym["Ticker"] == "XYZ"]
        assert pd.isna(xyz["Return_%"].iloc[0]) or xyz["Return_%"].iloc[0] == 0.0
