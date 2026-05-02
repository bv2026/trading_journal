# -*- coding: utf-8 -*-
"""Unit tests for src/fetchers/base.py — all public and critical private helpers."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.fetchers.base import (
    is_ts_option_symbol, parse_ts_option,
    is_occ_symbol, parse_occ,
    is_currency_entry,
    make_txn_id,
    parse_iso_date,
)


# ── is_ts_option_symbol ───────────────────────────────────────────────────────

class TestIsTsOptionSymbol:
    def test_valid_call(self):
        assert is_ts_option_symbol("MSFT 260717C425")

    def test_valid_put(self):
        assert is_ts_option_symbol("SPY 260618P665")

    def test_spxw_large_strike(self):
        assert is_ts_option_symbol("SPXW 260428C7370")

    def test_decimal_strike(self):
        assert is_ts_option_symbol("AAPL 261219C192.5")

    def test_plain_equity_is_not_option(self):
        assert not is_ts_option_symbol("AAPL")

    def test_occ_format_is_not_ts(self):
        assert not is_ts_option_symbol("GOOGL270115C00360000")

    def test_empty_string(self):
        assert not is_ts_option_symbol("")

    def test_missing_space(self):
        assert not is_ts_option_symbol("MSFT260717C425")


# ── parse_ts_option ───────────────────────────────────────────────────────────

class TestParseTsOption:
    def test_msft_call(self):
        r = parse_ts_option("MSFT 260717C425")
        assert r["underlying"] == "MSFT"
        assert r["expiry"] == "2026-07-17"
        assert r["call_put"] == "C"
        assert r["strike"] == pytest.approx(425.0)

    def test_occ_symbol_format(self):
        r = parse_ts_option("MSFT 260717C425")
        assert r["occ_symbol"] == "MSFT260717C00425000"

    def test_spy_put(self):
        r = parse_ts_option("SPY 260618P665")
        assert r["underlying"] == "SPY"
        assert r["call_put"] == "P"
        assert r["strike"] == pytest.approx(665.0)
        assert r["occ_symbol"] == "SPY260618P00665000"

    def test_decimal_strike_occ(self):
        r = parse_ts_option("AAPL 261219C192.5")
        assert r["strike"] == pytest.approx(192.5)
        assert r["occ_symbol"] == "AAPL261219C00192500"

    def test_spxw_large_strike_occ(self):
        r = parse_ts_option("SPXW 260428C7370")
        assert r["strike"] == pytest.approx(7370.0)
        assert r["occ_symbol"] == "SPXW260428C07370000"

    def test_invalid_returns_none(self):
        assert parse_ts_option("AAPL") is None

    def test_empty_returns_none(self):
        assert parse_ts_option("") is None

    def test_expiry_year_2000_plus(self):
        r = parse_ts_option("XYZ 300101C100")
        assert r["expiry"] == "2030-01-01"


# ── is_occ_symbol ─────────────────────────────────────────────────────────────

class TestIsOccSymbol:
    def test_googl_call(self):
        assert is_occ_symbol("GOOGL270115C00360000")

    def test_spxw_put(self):
        assert is_occ_symbol("SPXW260429P06870000")

    def test_qqq_call(self):
        assert is_occ_symbol("QQQ260618C00670000")

    def test_plain_equity(self):
        assert not is_occ_symbol("AAPL")

    def test_ts_format(self):
        assert not is_occ_symbol("MSFT 260717C425")

    def test_futures(self):
        assert not is_occ_symbol("/GCZ26")

    def test_empty(self):
        assert not is_occ_symbol("")


# ── parse_occ ─────────────────────────────────────────────────────────────────

class TestParseOcc:
    def test_googl_call(self):
        r = parse_occ("GOOGL270115C00360000")
        assert r["underlying"] == "GOOGL"
        assert r["expiry"] == "2027-01-15"
        assert r["call_put"] == "C"
        assert r["strike"] == pytest.approx(360.0)

    def test_spxw_put_large_strike(self):
        r = parse_occ("SPXW260429P06870000")
        assert r["underlying"] == "SPXW"
        assert r["call_put"] == "P"
        assert r["strike"] == pytest.approx(6870.0)

    def test_strike_division_precision(self):
        r = parse_occ("QQQ260618C00670000")
        assert r["strike"] == pytest.approx(670.0)

    def test_fractional_strike(self):
        # strike field = 00192500 → 192.5
        r = parse_occ("AAPL261219C00192500")
        assert r["strike"] == pytest.approx(192.5)

    def test_invalid_returns_none(self):
        assert parse_occ("AAPL") is None

    def test_empty_returns_none(self):
        assert parse_occ("") is None


# ── is_currency_entry ─────────────────────────────────────────────────────────

class TestIsCurrencyEntry:
    def test_usd_cash_entry(self):
        # $1.00 per unit → cash
        assert is_currency_entry("USD", 5000.0, 5000.0)

    def test_usd_etf_not_cash(self):
        # ProShares USD ETF: $70/share
        assert not is_currency_entry("USD", 7000.0, 100.0)

    def test_eur_cash_entry(self):
        assert is_currency_entry("EUR", 2000.0, 2000.0)

    def test_non_currency_code(self):
        assert not is_currency_entry("AAPL", 100.0, 1.0)

    def test_zero_quantity(self):
        assert not is_currency_entry("USD", 0.0, 0.0)

    def test_just_over_threshold(self):
        # $1.03 per unit — above 1.02 cutoff
        assert not is_currency_entry("USD", 103.0, 100.0)

    def test_just_under_threshold(self):
        # $1.01 per unit — within cutoff
        assert is_currency_entry("USD", 101.0, 100.0)

    def test_gbp_cash(self):
        assert is_currency_entry("GBP", 1000.0, 1000.0)


# ── make_txn_id ───────────────────────────────────────────────────────────────

class TestMakeTxnId:
    def test_returns_32_char_hex(self):
        result = make_txn_id("RH-BV", "2026-01-15", 100.0, "dividend")
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_inputs_same_id(self):
        a = make_txn_id("TS", "2026-03-01", -50.0, "margin interest")
        b = make_txn_id("TS", "2026-03-01", -50.0, "margin interest")
        assert a == b

    def test_different_account_different_id(self):
        a = make_txn_id("RH-BV",  "2026-01-15", 100.0, "dividend")
        b = make_txn_id("WEBULL", "2026-01-15", 100.0, "dividend")
        assert a != b

    def test_different_amount_different_id(self):
        a = make_txn_id("RH-BV", "2026-01-15", 100.0, "dividend")
        b = make_txn_id("RH-BV", "2026-01-15", 101.0, "dividend")
        assert a != b

    def test_description_truncated_at_80_chars(self):
        long_desc = "x" * 200
        short_desc = "x" * 80
        a = make_txn_id("RH-BV", "2026-01-15", 100.0, long_desc)
        b = make_txn_id("RH-BV", "2026-01-15", 100.0, short_desc)
        assert a == b  # only first 80 chars used


# ── parse_iso_date ────────────────────────────────────────────────────────────

class TestParseIsoDate:
    def test_standard_iso(self):
        assert parse_iso_date("2026-01-15T10:30:00Z") == "2026-01-15"

    def test_with_offset(self):
        assert parse_iso_date("2026-01-15T10:30:00-05:00") == "2026-01-15"

    def test_date_only(self):
        assert parse_iso_date("2026-01-15") == "2026-01-15"

    def test_empty_string_returns_none(self):
        assert parse_iso_date("") is None

    def test_none_returns_none(self):
        assert parse_iso_date(None) is None

    def test_invalid_string_returns_none(self):
        assert parse_iso_date("not-a-date") is None

    def test_utc_z_suffix(self):
        assert parse_iso_date("2026-03-15T00:00:00Z") == "2026-03-15"

    def test_end_of_day(self):
        assert parse_iso_date("2026-12-31T23:59:59Z") == "2026-12-31"
