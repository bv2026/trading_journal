# -*- coding: utf-8 -*-
"""Unit tests for src/fetchers/schwab.py — all public functions + _parse_expiry."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.fetchers.schwab import (
    normalize_equity,
    normalize_futures,
    normalize_transactions,
    normalize_balances,
    normalize_instruments,
    _parse_expiry,
)


# ── _parse_expiry ──────────────────────────────────────────────────────────────

class TestParseExpiry:
    def test_aug_26(self):
        assert _parse_expiry("AUG 26") == "2026-08-01"

    def test_dec_26(self):
        assert _parse_expiry("DEC 26") == "2026-12-01"

    def test_jan_27(self):
        assert _parse_expiry("JAN 27") == "2027-01-01"

    def test_case_insensitive(self):
        assert _parse_expiry("aug 26") == "2026-08-01"

    def test_invalid_returns_none(self):
        assert _parse_expiry("INVALID") is None

    def test_empty_returns_none(self):
        assert _parse_expiry("") is None

    def test_none_returns_none(self):
        assert _parse_expiry(None) is None


# ── normalize_equity ───────────────────────────────────────────────────────────

class TestNormalizeEquity:
    def _resp(self, positions):
        return {"positions": positions, "count": len(positions)}

    def test_basic_equity(self):
        pos = [{"symbol": "AAPL", "asset_type": "EQUITY",
                "quantity": 10, "avg_price": 150.0, "market_value": 1600.0,
                "description": "Apple Inc"}]
        eq, opts = normalize_equity(self._resp(pos), "SCHWAB")
        assert len(eq) == 1
        assert eq[0]["ticker"] == "AAPL"
        assert eq[0]["shares"] == pytest.approx(10.0)
        assert eq[0]["cost_basis"] == pytest.approx(150.0)
        assert eq[0]["asset_type"] == "Stock"

    def test_collective_investment_becomes_etf(self):
        pos = [{"symbol": "SPY", "asset_type": "COLLECTIVE_INVESTMENT",
                "quantity": 5, "avg_price": 500.0, "market_value": 2600.0,
                "description": "SPDR S&P 500 ETF"}]
        eq, opts = normalize_equity(self._resp(pos), "SCHWAB")
        assert eq[0]["asset_type"] == "ETF"

    def test_option_goes_to_option_list(self):
        pos = [{"symbol": "AAPL260619C00200000", "asset_type": "OPTION",
                "quantity": 2, "avg_price": 3.0, "market_value": 600.0,
                "description": "AAPL Jun 2026 200 Call"}]
        eq, opts = normalize_equity(self._resp(pos), "SCHWAB")
        assert len(eq) == 0
        assert len(opts) == 1
        assert opts[0]["underlying"] == "AAPL"
        assert opts[0]["call_put"] == "C"
        assert opts[0]["strike"] == pytest.approx(200.0)

    def test_currency_entry_skipped(self):
        # USD at $1.00/unit = cash balance, not equity
        pos = [{"symbol": "USD", "asset_type": "EQUITY",
                "quantity": 5000, "avg_price": 1.0, "market_value": 5000.0,
                "description": "US Dollar"}]
        eq, opts = normalize_equity(self._resp(pos), "SCHWAB")
        assert eq == []

    def test_usd_etf_not_skipped(self):
        # USD ETF at $70/share should pass through
        pos = [{"symbol": "USD", "asset_type": "ETF",
                "quantity": 100, "avg_price": 70.0, "market_value": 7200.0,
                "description": "ProShares Ultra Semi ETF"}]
        eq, opts = normalize_equity(self._resp(pos), "SCHWAB")
        assert len(eq) == 1

    def test_empty_symbol_skipped(self):
        pos = [{"symbol": "", "asset_type": "EQUITY",
                "quantity": 10, "avg_price": 100.0, "market_value": 1000.0}]
        eq, opts = normalize_equity(self._resp(pos), "SCHWAB")
        assert eq == []

    def test_live_price_computed_from_mv_and_qty(self):
        pos = [{"symbol": "MSFT", "asset_type": "EQUITY",
                "quantity": 10, "avg_price": 400.0, "market_value": 4200.0,
                "description": "Microsoft"}]
        eq, _ = normalize_equity(self._resp(pos), "SCHWAB")
        assert eq[0]["stored_price"] == pytest.approx(420.0)

    def test_empty_response(self):
        eq, opts = normalize_equity({"positions": []}, "SCHWAB")
        assert eq == []
        assert opts == []


# ── normalize_futures ─────────────────────────────────────────────────────────

class TestNormalizeFutures:
    def _resp(self, legs):
        return {"futures_legs": legs}

    def test_long_positive_qty(self):
        leg = {"symbol": "/GCZ26", "side": "LONG", "quantity": 1,
               "mark": 4729.5, "trade_price": 4800.0, "multiplier": 100,
               "description": "Gold Dec 2026", "expiration": "DEC 26",
               "spread_name": "GC Calendar"}
        records = normalize_futures(self._resp([leg]), "SCHWAB")
        assert records[0]["qty"] == pytest.approx(1.0)
        assert records[0]["market_value"] == pytest.approx(1.0 * 4729.5 * 100)

    def test_short_negative_qty(self):
        leg = {"symbol": "/GCQ26", "side": "SHORT", "quantity": 1,
               "mark": 4661.4, "trade_price": 4800.0, "multiplier": 100,
               "description": "Gold Aug 2026", "expiration": "AUG 26"}
        records = normalize_futures(self._resp([leg]), "SCHWAB")
        assert records[0]["qty"] == pytest.approx(-1.0)
        assert records[0]["market_value"] == pytest.approx(-1.0 * 4661.4 * 100)

    def test_underlying_stripped_of_month_year(self):
        leg = {"symbol": "/GCZ26", "side": "LONG", "quantity": 1,
               "mark": 100.0, "trade_price": 100.0, "multiplier": 100,
               "description": "Gold", "expiration": "DEC 26"}
        records = normalize_futures(self._resp([leg]), "SCHWAB")
        assert records[0]["underlying"] == "GC"

    def test_vxm_underlying(self):
        leg = {"symbol": "/VXMN26", "side": "LONG", "quantity": 2,
               "mark": 22.5, "trade_price": 21.0, "multiplier": 1000,
               "description": "VXM Jun 2026", "expiration": "JUN 26"}
        records = normalize_futures(self._resp([leg]), "SCHWAB")
        assert records[0]["underlying"] == "VXM"

    def test_expiry_parsed(self):
        leg = {"symbol": "/GCZ26", "side": "LONG", "quantity": 1,
               "mark": 100.0, "trade_price": 100.0, "multiplier": 100,
               "description": "", "expiration": "DEC 26"}
        records = normalize_futures(self._resp([leg]), "SCHWAB")
        assert records[0]["_expiry"] == "2026-12-01"

    def test_empty_symbol_skipped(self):
        leg = {"symbol": "", "side": "LONG", "quantity": 1,
               "mark": 100.0, "trade_price": 100.0, "multiplier": 100,
               "description": "", "expiration": ""}
        assert normalize_futures(self._resp([leg]), "SCHWAB") == []

    def test_empty_response(self):
        assert normalize_futures({"futures_legs": []}, "SCHWAB") == []


# ── normalize_balances ────────────────────────────────────────────────────────

class TestNormalizeBalances:
    def test_margin_negative_becomes_positive(self):
        r = normalize_balances({"equity": 50000, "margin_balance": -20000,
                                "long_market_value": 70000})
        assert r["margin"] == pytest.approx(20000.0)

    def test_positive_margin_balance_is_zero(self):
        # positive margin_balance means no debt
        r = normalize_balances({"equity": 50000, "margin_balance": 100,
                                "long_market_value": 50000})
        assert r["margin"] == pytest.approx(0.0)

    def test_all_fields_populated(self):
        r = normalize_balances({"equity": 40000, "margin_balance": -15000,
                                "long_market_value": 55000, "buying_power": 25000})
        assert r["equity"]       == pytest.approx(40000.0)
        assert r["market_value"] == pytest.approx(55000.0)
        assert r["margin"]       == pytest.approx(15000.0)
        assert r["buying_power"] == pytest.approx(25000.0)

    def test_missing_fields_default_to_zero(self):
        r = normalize_balances({})
        assert r["equity"] == 0.0
        assert r["margin"] == 0.0

    def test_none_values_handled(self):
        r = normalize_balances({"equity": None, "margin_balance": None,
                                "long_market_value": None})
        assert r["equity"] == 0.0
        assert r["margin"] == 0.0


# ── normalize_transactions ────────────────────────────────────────────────────

class TestNormalizeTransactions:
    def _resp(self, txns):
        return {"transactions": txns}

    def _txn(self, txn_type, amount, date="2026-04-01T10:00:00Z", desc="test"):
        return {"type": txn_type, "netAmount": amount,
                "tradeDate": date, "description": desc}

    def test_dividend_transaction(self):
        records = normalize_transactions(
            self._resp([self._txn("DIVIDEND_OR_INTEREST", 50.0)]), "SCHWAB"
        )
        assert records[0]["category"] == "dividend"
        assert records[0]["amount"] == pytest.approx(50.0)

    def test_ach_receipt(self):
        records = normalize_transactions(
            self._resp([self._txn("ACH_RECEIPT", 5000.0)]), "SCHWAB"
        )
        assert records[0]["subcategory"] == "deposit"

    def test_ach_disbursement(self):
        records = normalize_transactions(
            self._resp([self._txn("ACH_DISBURSEMENT", -2000.0)]), "SCHWAB"
        )
        assert records[0]["subcategory"] == "withdrawal"

    def test_margin_interest_forced_negative(self):
        records = normalize_transactions(
            self._resp([self._txn("MARGIN_INTEREST", 30.0)]), "SCHWAB"
        )
        assert records[0]["amount"] < 0

    def test_missing_date_skipped(self):
        txn = {"type": "DIVIDEND_OR_INTEREST", "netAmount": 50.0, "tradeDate": ""}
        records = normalize_transactions(self._resp([txn]), "SCHWAB")
        assert records == []

    def test_empty_response(self):
        assert normalize_transactions({}, "SCHWAB") == []

    def test_id_stability(self):
        txn = self._txn("DIVIDEND_OR_INTEREST", 50.0)
        r1 = normalize_transactions(self._resp([txn]), "SCHWAB")
        r2 = normalize_transactions(self._resp([txn]), "SCHWAB")
        assert r1[0]["id"] == r2[0]["id"]


# ── normalize_instruments ─────────────────────────────────────────────────────

class TestNormalizeInstruments:
    def test_equity_instrument(self):
        eq = [{"ticker": "AAPL", "name": "Apple Inc", "sector": None, "industry": None}]
        records = normalize_instruments(eq, [], [])
        assert any(r["symbol"] == "AAPL" and r["asset_class"] == "equity"
                   for r in records)

    def test_deduplication(self):
        eq = [
            {"ticker": "AAPL", "name": "Apple Inc"},
            {"ticker": "AAPL", "name": "Apple Inc"},  # duplicate
        ]
        records = normalize_instruments(eq, [], [])
        aapl = [r for r in records if r["symbol"] == "AAPL"]
        assert len(aapl) == 1

    def test_option_instrument(self):
        opts = [{"symbol": "AAPL260619C00200000", "underlying": "AAPL",
                 "expiry": "2026-06-19", "strike": 200.0, "call_put": "C"}]
        records = normalize_instruments([], opts, [])
        assert any(r["asset_class"] == "option" for r in records)

    def test_futures_instrument(self):
        futs = [{"symbol": "/GCZ26", "underlying": "GC",
                 "description": "Gold Dec 2026",
                 "_expiry": "2026-12-01", "_multiplier": 100}]
        records = normalize_instruments([], [], futs)
        assert any(r["asset_class"] == "future" and r["symbol"] == "/GCZ26"
                   for r in records)

    def test_empty_inputs(self):
        assert normalize_instruments([], [], []) == []
