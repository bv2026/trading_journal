"""Unit tests for src/parsers/static_positions_csv.py"""
import textwrap
from pathlib import Path

import pytest

from src.parsers.static_positions_csv import parse

# ── CSV fixture helpers ───────────────────────────────────────────────────────

HEADER = "Symbol,Expiry,Strike,Call/Put,Description,Qty,Price,Market Value,Underlying Symbol"


def _csv(tmp_path: Path, name: str, rows: list[str]) -> str:
    content = HEADER + "\n" + "\n".join(rows) + "\n"
    p = tmp_path / name
    p.write_text(content)
    return str(p)


# ── Options ───────────────────────────────────────────────────────────────────

class TestOptions:
    def test_basic_options_row(self, tmp_path):
        path = _csv(tmp_path, "options-trader.csv", [
            "GOOGL 20270115C360.000,2027-01-15,360.00,Call,GOOGL Jan 2027 360 Call,-2,5.50,$-1100.00,GOOGL",
        ])
        rows = parse(path, "TRADIER-OPT", "options")
        assert len(rows) == 1
        r = rows[0]
        assert r["account_id"] == "TRADIER-OPT"
        assert r["symbol"] == "GOOGL 20270115C360.000"
        assert r["underlying"] == "GOOGL"
        assert r["expiry"] == "2027-01-15"
        assert r["strike"] == pytest.approx(360.0)
        assert r["call_put"] == "Call"
        assert r["qty"] == pytest.approx(-2.0)
        assert r["price"] == pytest.approx(5.5)
        assert r["market_value"] == pytest.approx(-1100.0)
        assert r["source_file"] == "options-trader.csv"

    def test_put_row(self, tmp_path):
        path = _csv(tmp_path, "options-trader.csv", [
            "SPX 20251220P5000,2025-12-20,5000.00,Put,SPX Dec 2025 5000 Put,1,30.00,$3000.00,SPX",
        ])
        rows = parse(path, "SCHWAB-OPT", "options")
        assert len(rows) == 1
        assert rows[0]["call_put"] == "Put"
        assert rows[0]["qty"] == pytest.approx(1.0)

    def test_multiple_options_rows(self, tmp_path):
        path = _csv(tmp_path, "options-schwab.csv", [
            "QQQ 20260120C500,2026-01-20,500.00,Call,QQQ Call,1,2.00,$200.00,QQQ",
            "QQQ 20260120P450,2026-01-20,450.00,Put,QQQ Put,-1,1.50,$-150.00,QQQ",
        ])
        rows = parse(path, "SCHWAB-OPT", "options")
        assert len(rows) == 2

    def test_equity_rows_skipped_for_options(self, tmp_path):
        path = _csv(tmp_path, "options-trader.csv", [
            "AAPL,,,, Apple Inc,10,150.00,$1500.00,",        # equity — no expiry/call_put
            "QQQ 20260120C500,2026-01-20,500.00,Call,QQQ Call,1,2.00,$200.00,QQQ",
        ])
        rows = parse(path, "TRADIER-OPT", "options")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "QQQ 20260120C500"

    def test_parenthetical_negative_market_value(self, tmp_path):
        path = _csv(tmp_path, "options-trader.csv", [
            "XYZ 20260601C100,2026-06-01,100.00,Call,XYZ Call,-5,2.00,$(1000.00),XYZ",
        ])
        rows = parse(path, "TRADIER-OPT", "options")
        assert rows[0]["market_value"] == pytest.approx(-1000.0)

    def test_comma_formatted_market_value(self, tmp_path):
        path = _csv(tmp_path, "options-trader.csv", [
            'SPY 20260101C600,2026-01-01,600.00,Call,SPY Call,10,15.00,"$15,000.00",SPY',
        ])
        rows = parse(path, "TRADIER-OPT", "options")
        assert rows[0]["market_value"] == pytest.approx(15000.0)


# ── Futures ───────────────────────────────────────────────────────────────────

class TestFutures:
    def test_basic_futures_row(self, tmp_path):
        path = _csv(tmp_path, "futures-ts.csv", [
            "/ESM25,,,, E-mini S&P 500 Jun 2025,2,5200.00,$520000.00,/ES",
        ])
        rows = parse(path, "TS-FUT", "futures")
        assert len(rows) == 1
        r = rows[0]
        assert r["account_id"] == "TS-FUT"
        assert r["symbol"] == "/ESM25"
        assert r["underlying"] == "/ES"
        assert r["qty"] == pytest.approx(2.0)
        assert r["price"] == pytest.approx(5200.0)
        assert r["market_value"] == pytest.approx(520000.0)
        assert r["source_file"] == "futures-ts.csv"

    def test_short_futures(self, tmp_path):
        path = _csv(tmp_path, "futures-ts.csv", [
            "/CLM25,,,, Crude Oil Jun 2025,-3,75.00,$-225000.00,/CL",
        ])
        rows = parse(path, "TS-FUT", "futures")
        assert rows[0]["qty"] == pytest.approx(-3.0)

    def test_options_rows_skipped_for_futures(self, tmp_path):
        path = _csv(tmp_path, "futures-ts.csv", [
            "/ESM25,,,, E-mini S&P,2,5200.00,$520000.00,/ES",
            "AAPL 20260120C200,2026-01-20,200.00,Call,AAPL Call,1,3.00,$300.00,AAPL",  # options row
        ])
        rows = parse(path, "TS-FUT", "futures")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "/ESM25"


# ── Crypto ────────────────────────────────────────────────────────────────────

class TestCrypto:
    def test_basic_crypto_row(self, tmp_path):
        path = _csv(tmp_path, "crypto-coinbase.csv", [
            "BTC,,,,Bitcoin,0.5,60000.00,$30000.00,",
        ])
        rows = parse(path, "COINBASE", "crypto")
        assert len(rows) == 1
        r = rows[0]
        assert r["account_id"] == "COINBASE"
        assert r["symbol"] == "BTC"
        assert r["name"] == "Bitcoin"
        assert r["qty"] == pytest.approx(0.5)
        assert r["price"] == pytest.approx(60000.0)
        assert r["market_value"] == pytest.approx(30000.0)
        assert r["cost_basis"] is None  # not in this CSV format
        assert r["source_file"] == "crypto-coinbase.csv"

    def test_multiple_crypto_rows(self, tmp_path):
        path = _csv(tmp_path, "crypto-coinbase.csv", [
            "BTC,,,,Bitcoin,0.5,60000.00,$30000.00,",
            "ETH,,,,Ethereum,2.0,3000.00,$6000.00,",
        ])
        rows = parse(path, "COINBASE", "crypto")
        assert len(rows) == 2
        symbols = {r["symbol"] for r in rows}
        assert symbols == {"BTC", "ETH"}

    def test_options_rows_skipped_for_crypto(self, tmp_path):
        path = _csv(tmp_path, "crypto-coinbase.csv", [
            "BTC,,,,Bitcoin,0.5,60000.00,$30000.00,",
            "AAPL 20260120C200,2026-01-20,200.00,Call,AAPL Call,1,3.00,$300.00,AAPL",
        ])
        rows = parse(path, "COINBASE", "crypto")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTC"

    def test_blank_symbol_skipped(self, tmp_path):
        path = _csv(tmp_path, "crypto-coinbase.csv", [
            ",,,,Total,,,$36000.00,",  # summary/total row with no symbol
            "BTC,,,,Bitcoin,0.5,60000.00,$30000.00,",
        ])
        rows = parse(path, "COINBASE", "crypto")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTC"


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_missing_file_returns_empty(self, tmp_path):
        rows = parse(str(tmp_path / "nonexistent.csv"), "TRADIER-OPT", "options")
        assert rows == []

    def test_unknown_account_type_returns_empty(self, tmp_path):
        path = _csv(tmp_path, "mystery.csv", [
            "AAPL,,,,Apple,10,150.00,$1500.00,",
        ])
        rows = parse(path, "MYSTERY", "equity")
        assert rows == []

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text(HEADER + "\n")
        rows = parse(str(p), "TRADIER-OPT", "options")
        assert rows == []

    def test_extra_columns_ignored(self, tmp_path):
        p = tmp_path / "options-extra.csv"
        p.write_text(
            "Symbol,Expiry,Strike,Call/Put,Description,Qty,Price,Market Value,"
            "Underlying Symbol,Account Type,Day Change\n"
            "QQQ 20260120C500,2026-01-20,500.00,Call,QQQ Call,1,2.00,$200.00,QQQ,Individual,+$5.00\n"
        )
        rows = parse(str(p), "TRADIER-OPT", "options")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "QQQ 20260120C500"

    def test_mixed_row_types_filtered_by_account_type(self, tmp_path):
        path = _csv(tmp_path, "mixed.csv", [
            "AAPL 20260120C200,2026-01-20,200.00,Call,AAPL Call,1,3.00,$300.00,AAPL",
            "/ESM25,,,, E-mini S&P,2,5200.00,$520000.00,/ES",
            "BTC,,,,Bitcoin,0.5,60000.00,$30000.00,",
        ])
        opt_rows = parse(path, "X-OPT", "options")
        fut_rows = parse(path, "X-FUT", "futures")
        cry_rows = parse(path, "X-CRYPTO", "crypto")

        assert len(opt_rows) == 1 and opt_rows[0]["symbol"] == "AAPL 20260120C200"
        assert len(fut_rows) == 1 and fut_rows[0]["symbol"] == "/ESM25"
        assert len(cry_rows) == 1 and cry_rows[0]["symbol"] == "BTC"
