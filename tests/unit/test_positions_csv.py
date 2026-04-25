# -*- coding: utf-8 -*-
"""Unit tests for src.parsers.positions_csv.

Covers:
  - blank / NaN ticker rows are dropped
  - MARGIN row: MARKET VALUE captured as cost_basis, row kept
  - $ / , / % / () formatting cleaned before numeric coerce
  - N/A values mapped to None
  - runtime columns (PRICE, COST, MARKET VALUE, totalReturn) excluded
  - column renames applied (Sh/Contr → Shares, COST BASIS → Cost_Basis)
  - missing file returns empty list
  - text columns whitespace-stripped
"""
import io
from pathlib import Path

import pandas as pd
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.parsers.positions_csv import parse


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_csv(tmp_path: Path, rows: list[dict], filename: str = "positions-test.csv") -> Path:
    df = pd.DataFrame(rows)
    p = tmp_path / filename
    df.to_csv(p, index=False)
    return p


def _minimal_rows() -> list[dict]:
    return [
        {"Ticker": "AAPL", "Name": "Apple Inc", "PRICE": "$150.00",
         "Sh/Contr": 10.0, "COST BASIS": "$145.00",
         "COST": "$1,450.00", "MARKET VALUE": "$1,500.00",
         "totalReturn": "$50.00",
         "sector": "Technology", "industry": "Consumer Electronics",
         "IV RANK": "40%", "PERF YTD": "10%", "ATR %": "2%", "TYPE": "Stock"},
    ]


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPositionsCsvParse:

    def test_missing_file_returns_empty(self, tmp_path):
        result = parse(str(tmp_path / "nonexistent.csv"), "SCHWAB")
        assert result == []

    def test_basic_row_parsed(self, tmp_path):
        p = _write_csv(tmp_path, _minimal_rows())
        recs = parse(str(p), "SCHWAB")
        assert len(recs) == 1
        r = recs[0]
        assert r["ticker"] == "AAPL"
        assert r["account_id"] == "SCHWAB"
        assert r["name"] == "Apple Inc"
        assert r["shares"] == pytest.approx(10.0)
        assert r["cost_basis"] == pytest.approx(145.0)

    def test_runtime_cols_excluded(self, tmp_path):
        p = _write_csv(tmp_path, _minimal_rows())
        recs = parse(str(p), "SCHWAB")
        assert len(recs) == 1
        r = recs[0]
        for col in ("PRICE", "COST", "MARKET VALUE", "totalReturn"):
            assert col not in r

    def test_blank_ticker_rows_dropped(self, tmp_path):
        rows = _minimal_rows() + [
            {"Ticker": "", "Name": "", "Sh/Contr": None, "COST BASIS": None,
             "COST": None, "MARKET VALUE": None, "totalReturn": None,
             "sector": None, "industry": None, "IV RANK": None,
             "PERF YTD": None, "ATR %": None, "TYPE": None},
        ]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "SCHWAB")
        assert len(recs) == 1
        assert all(r["ticker"] for r in recs)

    def test_nan_ticker_rows_dropped(self, tmp_path):
        """Trailing empty CSV rows (pandas reads as NaN ticker) must be dropped."""
        rows = _minimal_rows()
        p = _write_csv(tmp_path, rows)
        # Append extra blank lines
        with open(p, "a") as f:
            f.write("\n\n")
        recs = parse(str(p), "SCHWAB")
        assert len(recs) == 1

    def test_margin_row_kept_with_market_value_as_cost_basis(self, tmp_path):
        """MARGIN row must be kept; its MARKET VALUE must be stored as cost_basis."""
        rows = _minimal_rows() + [
            {"Ticker": "MARGIN", "Name": "Margin Balance",
             "MARKET VALUE": "$(25,000.00)",
             "Sh/Contr": None, "COST BASIS": None,
             "COST": None, "totalReturn": None,
             "sector": None, "industry": None,
             "IV RANK": None, "PERF YTD": None, "ATR %": None, "TYPE": None,
             "PRICE": None},
        ]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "SCHWAB")
        margin = [r for r in recs if r["ticker"] == "MARGIN"]
        assert len(margin) == 1
        assert margin[0]["cost_basis"] == pytest.approx(-25000.0)

    def test_parenthetical_negative_cleaned(self, tmp_path):
        """$(1,234.56) format must parse to -1234.56."""
        rows = [{"Ticker": "XYZ", "Name": "Test",
                 "Sh/Contr": 5.0, "COST BASIS": "$(100.00)",
                 "COST": "$(500.00)", "MARKET VALUE": "$(400.00)",
                 "totalReturn": "$(100.00)", "PRICE": "$80.00",
                 "sector": "Technology", "industry": "Software",
                 "IV RANK": "50%", "PERF YTD": "-20%", "ATR %": "3%",
                 "TYPE": "Stock"}]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "SCHWAB")
        assert recs[0]["cost_basis"] == pytest.approx(-100.0)

    def test_dollar_comma_formatting_cleaned(self, tmp_path):
        """$1,500.00 must parse to 1500.0."""
        rows = [{"Ticker": "AAPL", "Name": "Apple",
                 "Sh/Contr": 10.0, "COST BASIS": "$1,450.00",
                 "COST": "$14,500.00", "MARKET VALUE": "$15,000.00",
                 "totalReturn": "$500.00", "PRICE": "$1,500.00",
                 "sector": "Technology", "industry": "Hardware",
                 "IV RANK": "30%", "PERF YTD": "5%", "ATR %": "2%",
                 "TYPE": "Stock"}]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "SCHWAB")
        assert recs[0]["cost_basis"] == pytest.approx(1450.0)

    def test_percent_formatting_cleaned(self, tmp_path):
        """40% must parse to 40.0 for IV_Rank, PERF_YTD, ATR_pct."""
        p = _write_csv(tmp_path, _minimal_rows())
        recs = parse(str(p), "SCHWAB")
        r = recs[0]
        assert r["iv_rank"]  == pytest.approx(40.0)
        assert r["perf_ytd"] == pytest.approx(10.0)
        assert r["atr_pct"]  == pytest.approx(2.0)

    def test_na_value_maps_to_none(self, tmp_path):
        """N/A in a numeric field must produce None, not crash."""
        rows = [{"Ticker": "AMDW", "Name": "Roundhill AMD",
                 "Sh/Contr": 60.0, "COST BASIS": "$44.49",
                 "COST": "$2,669", "MARKET VALUE": "$4,000",
                 "totalReturn": "$1,331", "PRICE": "$65.00",
                 "sector": "Financial", "industry": "ETF",
                 "IV RANK": "N/A", "PERF YTD": "0%", "ATR %": "4%",
                 "TYPE": "ETF"}]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "RH-KD")
        assert recs[0]["iv_rank"] is None

    def test_column_renames_applied(self, tmp_path):
        """Sh/Contr → shares  and  COST BASIS → cost_basis."""
        p = _write_csv(tmp_path, _minimal_rows())
        recs = parse(str(p), "SCHWAB")
        assert "shares"     in recs[0]
        assert "cost_basis" in recs[0]

    def test_whitespace_stripped_from_column_names(self, tmp_path):
        """Columns exported with surrounding spaces must be handled."""
        rows = [{"  Ticker  ": "MSFT", "  Name  ": "Microsoft",
                 "  Sh/Contr  ": 5.0, "  COST BASIS  ": "$300.00",
                 "  COST  ": "$1,500", "  MARKET VALUE  ": "$1,600",
                 "  totalReturn  ": "$100", "  PRICE  ": "$320.00",
                 "  sector  ": " Technology ", "  IV RANK  ": "55%",
                 "  PERF YTD  ": "8%", "  ATR %  ": "1%",
                 "  TYPE  ": "Stock"}]
        df = pd.DataFrame(rows)
        p = tmp_path / "positions-ws.csv"
        df.to_csv(p, index=False)
        recs = parse(str(p), "SCHWAB")
        assert len(recs) == 1
        assert recs[0]["ticker"] == "MSFT"
        assert recs[0]["shares"] == pytest.approx(5.0)

    def test_unnamed_and_ms_form_cols_dropped(self, tmp_path):
        """Helper columns must not appear in records."""
        rows = _minimal_rows()
        df = pd.DataFrame(rows)
        df["Unnamed: 10"] = "junk"
        df["MS FORMAULA"] = "junk"
        p = tmp_path / "positions-extra.csv"
        df.to_csv(p, index=False)
        recs = parse(str(p), "SCHWAB")
        assert len(recs) == 1
        assert "Unnamed: 10" not in recs[0]
        assert "MS FORMAULA" not in recs[0]

    def test_multiple_accounts(self, tmp_path):
        """account_id is set correctly for each call."""
        p = _write_csv(tmp_path, _minimal_rows())
        for acct in ("SCHWAB", "TRADIER", "RH-BV"):
            recs = parse(str(p), acct)
            assert all(r["account_id"] == acct for r in recs)

    # ── stored_price tests ────────────────────────────────────────────────────

    def test_price_captured_as_stored_price(self, tmp_path):
        """PRICE column must be captured into stored_price, then dropped as a runtime col."""
        p = _write_csv(tmp_path, _minimal_rows())  # PRICE = $150.00
        recs = parse(str(p), "SCHWAB")
        assert len(recs) == 1
        assert recs[0]["stored_price"] == pytest.approx(150.0)
        assert "PRICE" not in recs[0]

    def test_stored_price_with_comma_formatting(self, tmp_path):
        """$4,695.32 in PRICE must parse correctly into stored_price."""
        rows = [{"Ticker": "PAXG", "Name": "PAXG",
                 "PRICE": "$4,695.32",
                 "Sh/Contr": 0.17556, "COST BASIS": "$4,661.18",
                 "COST": "$819.00", "MARKET VALUE": "$824.00",
                 "totalReturn": "$5.00",
                 "sector": "CRYPTO", "industry": None,
                 "IV RANK": None, "PERF YTD": None, "ATR %": None, "TYPE": "CRYPTO"}]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "COINBASE")
        assert recs[0]["stored_price"] == pytest.approx(4695.32)

    def test_margin_column_preferred_over_market_value(self, tmp_path):
        """When both MARGIN and MARKET VALUE columns exist, MARGIN must be used for balance."""
        rows = _minimal_rows() + [{
            "Ticker": "MARGIN", "Name": "Margin Balance",
            "PRICE": None,
            "Sh/Contr": None, "COST BASIS": None,
            "COST": None, "MARKET VALUE": "$(99,999.00)",  # decoy — should be ignored
            "MARGIN": "$(25,000.00)",                       # real balance
            "totalReturn": None,
            "sector": None, "industry": None,
            "IV RANK": None, "PERF YTD": None, "ATR %": None, "TYPE": None,
        }]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "SCHWAB")
        margin = [r for r in recs if r["ticker"] == "MARGIN"]
        assert len(margin) == 1
        assert margin[0]["cost_basis"] == pytest.approx(-25000.0)

    def test_margin_column_fallback_when_no_margin_col(self, tmp_path):
        """When no MARGIN column exists, MARKET VALUE must still be used as fallback."""
        rows = _minimal_rows() + [{
            "Ticker": "MARGIN", "Name": "Margin Balance",
            "PRICE": None,
            "Sh/Contr": None, "COST BASIS": None,
            "COST": None, "MARKET VALUE": "$(25,000.00)",
            "totalReturn": None,
            "sector": None, "industry": None,
            "IV RANK": None, "PERF YTD": None, "ATR %": None, "TYPE": None,
        }]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "SCHWAB")
        margin = [r for r in recs if r["ticker"] == "MARGIN"]
        assert len(margin) == 1
        assert margin[0]["cost_basis"] == pytest.approx(-25000.0)

    def test_margin_row_stored_price_cleared(self, tmp_path):
        """MARGIN row must have stored_price=None — its PRICE is meaningless."""
        rows = _minimal_rows() + [{
            "Ticker": "MARGIN", "Name": "Margin Balance",
            "PRICE": "$1.00",                      # should be ignored / cleared
            "Sh/Contr": None, "COST BASIS": None,
            "COST": None, "MARKET VALUE": "$(25,000.00)",
            "totalReturn": None,
            "sector": None, "industry": None,
            "IV RANK": None, "PERF YTD": None, "ATR %": None, "TYPE": None,
        }]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "SCHWAB")
        margin = [r for r in recs if r["ticker"] == "MARGIN"]
        assert len(margin) == 1
        assert margin[0]["stored_price"] is None

    def test_no_price_column_stored_price_is_none(self, tmp_path):
        """When CSV has no PRICE column, stored_price must be None (not crash)."""
        rows = [{"Ticker": "AAPL", "Name": "Apple",
                 "Sh/Contr": 10.0, "COST BASIS": "$150.00",
                 "sector": "Technology", "industry": "Hardware",
                 "IV RANK": None, "PERF YTD": None, "ATR %": None, "TYPE": "Stock"}]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "SCHWAB")
        assert len(recs) == 1
        assert recs[0]["stored_price"] is None

    def test_coinbase_csv_format(self, tmp_path):
        """Full Coinbase-style CSV: crypto tickers, MARGIN col, DERIVATIVES row."""
        rows = [
            {"Ticker": "BTC",  "Name": "BTC",  "PRICE": "$77,344.06",
             "sector": "CRYPTO", "TYPE": "CRYPTO",
             "Sh/Contr": 0.007022, "COST BASIS": "$71,717.25", "MARGIN": ""},
            {"Ticker": "ETH",  "Name": "ETH",  "PRICE": "$2,307.22",
             "sector": "CRYPTO", "TYPE": "CRYPTO",
             "Sh/Contr": 0.305212, "COST BASIS": "$2,249.72", "MARGIN": ""},
            {"Ticker": "USDC", "Name": "USDC", "PRICE": "$1.00",
             "sector": "STABLECOIN", "TYPE": "CRYPTO",
             "Sh/Contr": 18737.52, "COST BASIS": "$1.00", "MARGIN": ""},
            {"Ticker": "DERIVATIVES", "Name": "DERIVATIVES", "PRICE": "$1.00",
             "sector": "FUTURES", "TYPE": "FUTURES",
             "Sh/Contr": 4882.51, "COST BASIS": "$1.00", "MARGIN": ""},
            {"Ticker": "", "Name": "", "PRICE": "", "sector": "", "TYPE": "",
             "Sh/Contr": "", "COST BASIS": "", "MARGIN": ""},  # blank row
            {"Ticker": "MARGIN", "Name": "MARGIN", "PRICE": "",
             "sector": "", "TYPE": "",
             "Sh/Contr": "", "COST BASIS": "", "MARGIN": "($0.01)"},
        ]
        p = _write_csv(tmp_path, rows)
        recs = parse(str(p), "COINBASE")

        tickers = [r["ticker"] for r in recs]
        assert "BTC" in tickers
        assert "ETH" in tickers
        assert "USDC" in tickers
        assert "DERIVATIVES" in tickers
        assert "" not in tickers          # blank row dropped

        btc = next(r for r in recs if r["ticker"] == "BTC")
        assert btc["stored_price"] == pytest.approx(77344.06)
        assert btc["shares"]       == pytest.approx(0.007022)
        assert btc["cost_basis"]   == pytest.approx(71717.25)

        deriv = next(r for r in recs if r["ticker"] == "DERIVATIVES")
        assert deriv["shares"]     == pytest.approx(4882.51)
        assert deriv["cost_basis"] == pytest.approx(1.0)
        assert deriv["stored_price"] == pytest.approx(1.0)

        margin = next(r for r in recs if r["ticker"] == "MARGIN")
        assert margin["cost_basis"]   == pytest.approx(-0.01)
        assert margin["stored_price"] is None
