# -*- coding: utf-8 -*-
"""Unit tests for src/parsers/*.

Uses synthetic CSV content written to tmp files so no real broker files
are needed.  Focus is on:
  - Correct category/subcategory assignment
  - sign conventions (deposits +, withdrawals −, fees −, margin_int −)
  - Coinbase bank-funded vs internal-balance detection (the key tricky logic)
  - parse_amount handles currency strings, parentheses, commas
  - parse_date handles MM/DD/YYYY, ISO, and "as of" variants
"""
import io
import textwrap
from pathlib import Path

import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.parsers.utils import parse_amount, parse_date, make_id
from src.parsers import robinhood, coinbase


# ══════════════════════════════════════════════════════════════════════════════
# parse_amount
# ══════════════════════════════════════════════════════════════════════════════

class TestParseAmount:
    def test_plain_positive(self):
        assert parse_amount("1234.56") == pytest.approx(1234.56)

    def test_dollar_sign(self):
        assert parse_amount("$1,234.56") == pytest.approx(1234.56)

    def test_parentheses_negative(self):
        """(1,234.56) — standard accounting negative notation."""
        assert parse_amount("(1,234.56)") == pytest.approx(-1234.56)

    def test_negative_prefix(self):
        assert parse_amount("-500.00") == pytest.approx(-500.00)

    def test_dollar_parentheses(self):
        assert parse_amount("($500.00)") == pytest.approx(-500.00)

    def test_none_returns_zero(self):
        assert parse_amount(None) == 0.0

    def test_empty_returns_zero(self):
        assert parse_amount("") == 0.0

    def test_dash_returns_zero(self):
        assert parse_amount("-") == 0.0

    def test_nan_str_returns_zero(self):
        assert parse_amount("nan") == 0.0

    def test_na_returns_zero(self):
        assert parse_amount("N/A") == 0.0

    def test_non_breaking_space(self):
        assert parse_amount("\xa0$100.00") == pytest.approx(100.0)


# ══════════════════════════════════════════════════════════════════════════════
# parse_date
# ══════════════════════════════════════════════════════════════════════════════

class TestParseDate:
    def test_iso_format(self):
        assert parse_date("2024-03-15") == "2024-03-15"

    def test_us_format(self):
        assert parse_date("03/15/2024") == "2024-03-15"

    def test_as_of_stripped(self):
        """Schwab format: '04/09/2026 as of 04/08/2026' → use first date."""
        assert parse_date("04/09/2026 as of 04/08/2026") == "2026-04-09"

    def test_datetime_with_timezone(self):
        """Coinbase format: '2024-01-15 10:30:00 UTC' → date only."""
        assert parse_date("01/15/2024 10:30:00 EDT") == "2024-01-15"

    def test_none_returns_none(self):
        assert parse_date(None) is None

    def test_empty_returns_none(self):
        assert parse_date("") is None

    def test_nan_str_returns_none(self):
        assert parse_date("nan") is None


# ══════════════════════════════════════════════════════════════════════════════
# make_id
# ══════════════════════════════════════════════════════════════════════════════

class TestMakeId:
    def test_deterministic(self):
        a = make_id("RH-BV", "file.csv", 10)
        b = make_id("RH-BV", "file.csv", 10)
        assert a == b

    def test_different_inputs_different_ids(self):
        a = make_id("RH-BV", "file.csv", 10)
        b = make_id("RH-BV", "file.csv", 11)
        assert a != b

    def test_returns_hex_string(self):
        result = make_id("ACCT", "source.csv", 0)
        assert isinstance(result, str)
        assert len(result) == 32  # MD5 hex digest


# ══════════════════════════════════════════════════════════════════════════════
# Robinhood parser
# ══════════════════════════════════════════════════════════════════════════════

def _rh_csv(rows: list[dict], tmp_path: Path) -> Path:
    """Write a minimal Robinhood CSV to tmp_path and return its path."""
    header = "Activity Date,Trans Code,Instrument,Description,Amount\n"
    lines = [header]
    for r in rows:
        lines.append(
            f"{r.get('Activity Date','2024-01-15')},"
            f"{r.get('Trans Code','ACH')},"
            f"{r.get('Instrument','')},"
            f"{r.get('Description','Test transaction')},"
            f"{r.get('Amount','1000.00')}\n"
        )
    p = tmp_path / "rh.csv"
    p.write_text("".join(lines), encoding="utf-8")
    return p


class TestRobinhoodParser:
    def test_deposit_ach(self, tmp_path):
        p = _rh_csv([{"Trans Code": "ACH", "Amount": "1000.00"}], tmp_path)
        records = robinhood.parse(str(p), "RH-BV")
        assert len(records) == 1
        r = records[0]
        assert r["category"]    == "cash_flow"
        assert r["subcategory"] == "deposit"
        assert r["amount"]      == pytest.approx(1000.0)
        assert r["account_id"]  == "RH-BV"

    def test_withdrawal_ach(self, tmp_path):
        p = _rh_csv([{"Trans Code": "ACH", "Amount": "-500.00"}], tmp_path)
        records = robinhood.parse(str(p), "RH-BV")
        assert len(records) == 1
        assert records[0]["subcategory"] == "withdrawal"
        assert records[0]["amount"]      == pytest.approx(-500.0)

    def test_dividend(self, tmp_path):
        p = _rh_csv([{"Trans Code": "CDIV", "Instrument": "AAPL", "Amount": "42.50"}], tmp_path)
        records = robinhood.parse(str(p), "RH-BV")
        assert len(records) == 1
        r = records[0]
        assert r["category"]    == "dividend"
        assert r["subcategory"] == "cash_div"
        assert r["amount"]      == pytest.approx(42.5)

    def test_margin_interest(self, tmp_path):
        p = _rh_csv([{"Trans Code": "MINT", "Amount": "-25.00"}], tmp_path)
        records = robinhood.parse(str(p), "RH-BV")
        assert len(records) == 1
        r = records[0]
        assert r["category"]    == "margin_interest"
        assert r["amount"]      < 0

    def test_subscription_fee(self, tmp_path):
        p = _rh_csv([{"Trans Code": "GOLD", "Amount": "-5.00"}], tmp_path)
        records = robinhood.parse(str(p), "RH-BV")
        assert len(records) == 1
        assert records[0]["category"]    == "fee"
        assert records[0]["subcategory"] == "subscription_fee"

    def test_skip_codes_produce_no_records(self, tmp_path):
        skip_rows = [
            {"Trans Code": "Buy",  "Amount": "-1500.00"},
            {"Trans Code": "Sell", "Amount":  "1600.00"},
            {"Trans Code": "BTO",  "Amount":  "-200.00"},
        ]
        p = _rh_csv(skip_rows, tmp_path)
        records = robinhood.parse(str(p), "RH-BV")
        assert records == []

    def test_bad_date_row_skipped(self, tmp_path):
        p = _rh_csv([{"Activity Date": "", "Trans Code": "ACH", "Amount": "100.00"}], tmp_path)
        records = robinhood.parse(str(p), "RH-BV")
        assert records == []

    def test_unknown_code_skipped(self, tmp_path):
        p = _rh_csv([{"Trans Code": "ZZZZ", "Amount": "100.00"}], tmp_path)
        records = robinhood.parse(str(p), "RH-BV")
        assert records == []


# ══════════════════════════════════════════════════════════════════════════════
# Coinbase parser — bank-funded detection is the most critical logic
# ══════════════════════════════════════════════════════════════════════════════

def _cb_csv(rows: list[dict], tmp_path: Path) -> Path:
    """Write a minimal Coinbase CSV (with the 2-line preamble) to tmp_path."""
    preamble = "You can use this transaction report,,,,,,,\nYour transactions,,,,,,,\n"
    header   = (
        "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Spot Price Currency,Spot Price at Transaction,Subtotal,"
        "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n"
    )
    lines = [preamble, header]
    for i, r in enumerate(rows):
        lines.append(
            f"TXN{i},"
            f"{r.get('Timestamp', '2024-01-15T10:00:00Z')},"
            f"{r.get('Transaction Type', 'Deposit')},"
            f"{r.get('Asset', 'USD')},"
            f"{r.get('Quantity Transacted', '100')},"
            f"USD,"
            f"{r.get('Spot Price at Transaction', '1.00')},"
            f"{r.get('Subtotal', '100.00')},"
            f"{r.get('Total (inclusive of fees and/or spread)', '100.00')},"
            f"{r.get('Fees and/or Spread', '0.00')},"
            f"{r.get('Notes', '')}\n"
        )
    p = tmp_path / "coinbase.csv"
    p.write_text("".join(lines), encoding="utf-8")
    return p


class TestCoinbaseParser:
    def test_usd_deposit(self, tmp_path):
        p = _cb_csv([{"Transaction Type": "Deposit", "Total (inclusive of fees and/or spread)": "500.00"}], tmp_path)
        records = coinbase.parse(str(p))
        assert len(records) == 1
        r = records[0]
        assert r["category"]    == "crypto_flow"
        assert r["subcategory"] == "usd_deposit"
        assert r["amount"]      == pytest.approx(500.0)

    def test_usd_withdrawal(self, tmp_path):
        p = _cb_csv([{"Transaction Type": "Withdrawal",
                      "Total (inclusive of fees and/or spread)": "200.00"}], tmp_path)
        records = coinbase.parse(str(p))
        assert len(records) == 1
        r = records[0]
        assert r["subcategory"] == "usd_withdrawal"
        assert r["amount"]      == pytest.approx(-200.0)

    def test_bank_funded_buy(self, tmp_path):
        """Buy with Notes containing a bank name → bank_purchase (+)."""
        p = _cb_csv([{
            "Transaction Type": "Buy",
            "Asset": "BTC",
            "Total (inclusive of fees and/or spread)": "1000.00",
            "Fees and/or Spread": "2.99",
            "Notes": "Bought 0.01 BTC using JPMorgan Chase Bank",
        }], tmp_path)
        records = coinbase.parse(str(p))
        # Expect: one bank_purchase + one trading_fee
        by_sub = {r["subcategory"]: r for r in records}
        assert "bank_purchase" in by_sub
        assert by_sub["bank_purchase"]["amount"] == pytest.approx(1000.0)
        assert "trading_fee"   in by_sub
        assert by_sub["trading_fee"]["amount"]   < 0

    def test_internal_buy_not_bank_purchase(self, tmp_path):
        """Buy using 'Cash (USD)' balance → NOT a bank_purchase."""
        p = _cb_csv([{
            "Transaction Type": "Buy",
            "Asset": "ETH",
            "Total (inclusive of fees and/or spread)": "500.00",
            "Fees and/or Spread": "1.50",
            "Notes": "Bought 0.1 ETH using Cash (USD)",
        }], tmp_path)
        records = coinbase.parse(str(p))
        subs = [r["subcategory"] for r in records]
        assert "bank_purchase" not in subs
        # fee still captured
        assert "trading_fee" in subs

    def test_crypto_received(self, tmp_path):
        p = _cb_csv([{"Transaction Type": "Receive", "Asset": "BTC",
                      "Total (inclusive of fees and/or spread)": "500.00"}], tmp_path)
        records = coinbase.parse(str(p))
        assert records[0]["subcategory"] == "crypto_received"
        assert records[0]["amount"]      == pytest.approx(500.0)

    def test_crypto_sent(self, tmp_path):
        p = _cb_csv([{"Transaction Type": "Send", "Asset": "ETH",
                      "Total (inclusive of fees and/or spread)": "300.00"}], tmp_path)
        records = coinbase.parse(str(p))
        assert records[0]["subcategory"] == "crypto_sent"
        assert records[0]["amount"]      == pytest.approx(-300.0)

    def test_staking_income(self, tmp_path):
        p = _cb_csv([{"Transaction Type": "Staking Income", "Asset": "ETH",
                      "Total (inclusive of fees and/or spread)": "12.50"}], tmp_path)
        records = coinbase.parse(str(p))
        assert records[0]["category"]    == "reward"
        assert records[0]["subcategory"] == "staking"
        assert records[0]["amount"]      == pytest.approx(12.5)

    def test_skip_types_produce_no_records(self, tmp_path):
        p = _cb_csv([
            {"Transaction Type": "Portfolio Transfer"},
            {"Transaction Type": "Retail Staking Transfer"},
        ], tmp_path)
        records = coinbase.parse(str(p))
        assert records == []

    def test_sell_only_captures_fee(self, tmp_path):
        """Sell stays internal; only the fee record is emitted."""
        p = _cb_csv([{
            "Transaction Type": "Sell",
            "Asset": "BTC",
            "Total (inclusive of fees and/or spread)": "2000.00",
            "Fees and/or Spread": "5.00",
        }], tmp_path)
        records = coinbase.parse(str(p))
        subs = [r["subcategory"] for r in records]
        assert "trading_fee" in subs
        # No cash_flow or crypto_flow records
        cats = [r["category"] for r in records]
        assert "cash_flow"   not in cats
        assert "crypto_flow" not in cats
