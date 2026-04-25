"""Integration tests for the static positions DB pipeline.

Covers:
  - Options / futures / crypto parse → delete → insert → load round-trips
  - portfolio_snapshots write and load_snapshot_periods
  - Ingest pipeline with OPTIONS_FILES wired up (monkeypatched)
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import src.db as db_module
from src.db import (
    init_db, upsert_accounts,
    delete_options_by_account, insert_options, load_options_db,
    delete_futures_by_account, insert_futures, load_futures_db,
    delete_crypto_by_account, insert_crypto, load_crypto_db,
    write_portfolio_snapshot, load_snapshot_periods,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_journal.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    return db_path


@pytest.fixture()
def seeded_db(tmp_db):
    init_db()
    upsert_accounts([
        {"account_id": "TRADIER",     "broker": "tradier",  "account_type": "equity",
         "account_group": "investment", "holder": None, "price_source": "live",   "active": 1},
        {"account_id": "TRADIER-OPT", "broker": "tradier",  "account_type": "options",
         "account_group": "investment", "holder": None, "price_source": "static", "active": 1},
        {"account_id": "SCHWAB-OPT",  "broker": "schwab",   "account_type": "options",
         "account_group": "investment", "holder": None, "price_source": "static", "active": 1},
        {"account_id": "TS-FUT",      "broker": "tradestation", "account_type": "futures",
         "account_group": "investment", "holder": None, "price_source": "static", "active": 1},
        {"account_id": "COINBASE",    "broker": "coinbase", "account_type": "crypto",
         "account_group": "investment", "holder": None, "price_source": "static", "active": 1},
    ])
    return tmp_db


# ── Options round-trip ────────────────────────────────────────────────────────

class TestOptionsRoundTrip:
    def _opt_rec(self, acct="TRADIER-OPT", symbol="GOOGL 20270115C360", **kwargs):
        base = {
            "account_id": acct, "symbol": symbol,
            "underlying": "GOOGL", "expiry": "2027-01-15",
            "strike": 360.0, "call_put": "Call",
            "description": "GOOGL Call", "qty": -2.0,
            "price": 5.5, "market_value": -1100.0, "source_file": "options-trader.csv",
        }
        base.update(kwargs)
        return base

    def test_insert_and_load(self, seeded_db):
        insert_options([self._opt_rec()])
        df = load_options_db()
        assert len(df) == 1
        row = df.iloc[0]
        assert row["account_id"] == "TRADIER-OPT"
        assert row["symbol"] == "GOOGL 20270115C360"
        assert row["underlying"] == "GOOGL"
        assert row["call_put"] == "Call"
        assert row["qty"] == pytest.approx(-2.0)
        assert row["market_value"] == pytest.approx(-1100.0)

    def test_delete_then_reinsert(self, seeded_db):
        insert_options([self._opt_rec(symbol="A"), self._opt_rec(symbol="B")])
        assert len(load_options_db()) == 2

        delete_options_by_account("TRADIER-OPT")
        assert len(load_options_db()) == 0

        insert_options([self._opt_rec(symbol="C")])
        assert len(load_options_db()) == 1

    def test_multiple_accounts_coexist(self, seeded_db):
        insert_options([
            self._opt_rec(acct="TRADIER-OPT", symbol="SYM1"),
            self._opt_rec(acct="SCHWAB-OPT",  symbol="SYM2"),
        ])
        df = load_options_db()
        assert set(df["account_id"]) == {"TRADIER-OPT", "SCHWAB-OPT"}

    def test_delete_one_account_leaves_other(self, seeded_db):
        insert_options([
            self._opt_rec(acct="TRADIER-OPT", symbol="SYM1"),
            self._opt_rec(acct="SCHWAB-OPT",  symbol="SYM2"),
        ])
        delete_options_by_account("TRADIER-OPT")
        df = load_options_db()
        assert len(df) == 1
        assert df.iloc[0]["account_id"] == "SCHWAB-OPT"

    def test_insert_or_replace(self, seeded_db):
        insert_options([self._opt_rec(price=5.5)])
        insert_options([self._opt_rec(price=6.0)])   # same PK → replace
        df = load_options_db()
        assert len(df) == 1
        assert df.iloc[0]["price"] == pytest.approx(6.0)

    def test_empty_records_returns_zero(self, seeded_db):
        assert insert_options([]) == 0

    def test_csv_parse_then_db_insert(self, tmp_path, seeded_db):
        from src.parsers.static_positions_csv import parse
        p = tmp_path / "options-trader.csv"
        p.write_text(
            "Symbol,Expiry,Strike,Call/Put,Description,Qty,Price,Market Value,Underlying Symbol\n"
            "QQQ 20260120C500,2026-01-20,500.00,Call,QQQ Call,1,2.00,$200.00,QQQ\n"
            "SPY 20261231P400,2026-12-31,400.00,Put,-3,1.50,$-450.00,SPY\n"
        )
        recs = parse(str(p), "TRADIER-OPT", "options")
        delete_options_by_account("TRADIER-OPT")
        written = insert_options(recs)
        assert written == 2
        df = load_options_db()
        assert set(df["symbol"]) == {"QQQ 20260120C500", "SPY 20261231P400"}


# ── Futures round-trip ────────────────────────────────────────────────────────

class TestFuturesRoundTrip:
    def _fut_rec(self, acct="TS-FUT", symbol="/ESM25", **kwargs):
        base = {
            "account_id": acct, "symbol": symbol,
            "underlying": "/ES", "description": "E-mini S&P Jun 2025",
            "qty": 2.0, "price": 5200.0, "market_value": 520000.0,
            "source_file": "futures-ts.csv",
        }
        base.update(kwargs)
        return base

    def test_insert_and_load(self, seeded_db):
        insert_futures([self._fut_rec()])
        df = load_futures_db()
        assert len(df) == 1
        row = df.iloc[0]
        assert row["symbol"] == "/ESM25"
        assert row["qty"] == pytest.approx(2.0)
        assert row["market_value"] == pytest.approx(520000.0)

    def test_delete_then_reinsert(self, seeded_db):
        insert_futures([self._fut_rec(symbol="/ESM25"), self._fut_rec(symbol="/CLM25")])
        assert len(load_futures_db()) == 2
        delete_futures_by_account("TS-FUT")
        assert len(load_futures_db()) == 0

    def test_empty_returns_zero(self, seeded_db):
        assert insert_futures([]) == 0

    def test_csv_parse_then_db_insert(self, tmp_path, seeded_db):
        from src.parsers.static_positions_csv import parse
        p = tmp_path / "futures-ts.csv"
        p.write_text(
            "Symbol,Expiry,Strike,Call/Put,Description,Qty,Price,Market Value,Underlying Symbol\n"
            "/ESM25,,,, E-mini S&P Jun 2025,2,5200.00,$520000.00,/ES\n"
            "/CLM25,,,, Crude Oil Jun 2025,-1,75.00,$-75000.00,/CL\n"
        )
        recs = parse(str(p), "TS-FUT", "futures")
        delete_futures_by_account("TS-FUT")
        written = insert_futures(recs)
        assert written == 2
        df = load_futures_db()
        assert set(df["symbol"]) == {"/ESM25", "/CLM25"}


# ── Crypto round-trip ─────────────────────────────────────────────────────────

class TestCryptoRoundTrip:
    def _cry_rec(self, acct="COINBASE", symbol="BTC", **kwargs):
        base = {
            "account_id": acct, "symbol": symbol,
            "name": "Bitcoin", "qty": 0.5, "price": 60000.0,
            "cost_basis": None, "market_value": 30000.0,
            "source_file": "crypto-coinbase.csv",
        }
        base.update(kwargs)
        return base

    def test_insert_and_load(self, seeded_db):
        insert_crypto([self._cry_rec()])
        df = load_crypto_db()
        assert len(df) == 1
        row = df.iloc[0]
        assert row["symbol"] == "BTC"
        assert row["qty"] == pytest.approx(0.5)
        assert row["market_value"] == pytest.approx(30000.0)

    def test_delete_then_reinsert(self, seeded_db):
        insert_crypto([self._cry_rec("COINBASE", "BTC"), self._cry_rec("COINBASE", "ETH")])
        assert len(load_crypto_db()) == 2
        delete_crypto_by_account("COINBASE")
        assert len(load_crypto_db()) == 0

    def test_empty_returns_zero(self, seeded_db):
        assert insert_crypto([]) == 0

    def test_csv_parse_then_db_insert(self, tmp_path, seeded_db):
        from src.parsers.static_positions_csv import parse
        p = tmp_path / "crypto-coinbase.csv"
        p.write_text(
            "Symbol,Expiry,Strike,Call/Put,Description,Qty,Price,Market Value,Underlying Symbol\n"
            "BTC,,,,Bitcoin,0.5,60000.00,$30000.00,\n"
            "ETH,,,,Ethereum,2.0,3000.00,$6000.00,\n"
        )
        recs = parse(str(p), "COINBASE", "crypto")
        delete_crypto_by_account("COINBASE")
        written = insert_crypto(recs)
        assert written == 2
        df = load_crypto_db()
        assert set(df["symbol"]) == {"BTC", "ETH"}


# ── Portfolio snapshots ───────────────────────────────────────────────────────

class TestPortfolioSnapshots:
    def test_write_and_query_snapshot(self, seeded_db):
        snap = {
            "TRADIER":     {"market_value": 100_000.0, "cost_basis": 80_000.0, "margin": 0.0},
            "TRADIER-OPT": {"market_value":   5_000.0, "cost_basis": None,     "margin": 0.0},
        }
        write_portfolio_snapshot("2026-04-25", snap)
        df = load_snapshot_periods()
        assert len(df) == 2
        accts = set(df["account_id"])
        assert "TRADIER" in accts
        assert "TRADIER-OPT" in accts

    def test_same_day_rerun_updates_not_duplicates(self, seeded_db):
        write_portfolio_snapshot("2026-04-25", {
            "TRADIER": {"market_value": 100_000.0, "cost_basis": 80_000.0, "margin": 0.0},
        })
        write_portfolio_snapshot("2026-04-25", {
            "TRADIER": {"market_value": 110_000.0, "cost_basis": 80_000.0, "margin": 0.0},
        })
        df = load_snapshot_periods()
        assert len(df) == 1
        assert df.iloc[0]["current_value"] == pytest.approx(110_000.0)

    def test_multiple_dates_accumulate(self, seeded_db):
        write_portfolio_snapshot("2026-04-01", {
            "TRADIER": {"market_value": 90_000.0, "cost_basis": 80_000.0, "margin": 0.0},
        })
        write_portfolio_snapshot("2026-04-25", {
            "TRADIER": {"market_value": 100_000.0, "cost_basis": 80_000.0, "margin": 0.0},
        })
        df = load_snapshot_periods()
        # latest snapshot is 2026-04-25
        assert df.iloc[0]["current_date"] == "2026-04-25"
        assert df.iloc[0]["current_value"] == pytest.approx(100_000.0)

    def test_account_group_filter(self, seeded_db):
        write_portfolio_snapshot("2026-04-25", {
            "TRADIER": {"market_value": 100_000.0, "cost_basis": 80_000.0, "margin": 0.0},
        })
        investment = load_snapshot_periods(account_group="investment")
        retirement = load_snapshot_periods(account_group="retirement")
        assert len(investment) >= 1
        assert len(retirement) == 0

    def test_empty_map_skipped(self, seeded_db):
        write_portfolio_snapshot("2026-04-25", {})
        df = load_snapshot_periods()
        assert len(df) == 0


# ── Ingest pipeline integration ───────────────────────────────────────────────

class TestIngestWithStaticPositions:
    """Verify that ingest.run() correctly wires options files → options_positions table."""

    def test_options_ingest_round_trip(self, tmp_path, seeded_db, monkeypatch):
        import ingest as ingest_mod
        from src.parsers.static_positions_csv import parse as static_parse

        # Write a minimal options CSV
        opts_csv = tmp_path / "options-trader.csv"
        opts_csv.write_text(
            "Symbol,Expiry,Strike,Call/Put,Description,Qty,Price,Market Value,Underlying Symbol\n"
            "QQQ 20260120C500,2026-01-20,500.00,Call,QQQ Call,-1,2.00,$-200.00,QQQ\n"
        )

        monkeypatch.setattr(ingest_mod, "ACCOUNTS",      [
            {"account_id": "TRADIER-OPT", "broker": "tradier", "account_type": "options",
             "account_group": "investment", "holder": None, "price_source": "static", "active": 1},
        ])
        monkeypatch.setattr(ingest_mod, "PARSERS",       [])
        monkeypatch.setattr(ingest_mod, "POSITION_FILES", [])
        monkeypatch.setattr(ingest_mod, "OPTIONS_FILES",  [(opts_csv, "TRADIER-OPT")])
        monkeypatch.setattr(ingest_mod, "FUTURES_FILES",  [])
        monkeypatch.setattr(ingest_mod, "CRYPTO_FILES",   [])

        # Stub snapshot so yfinance is never called
        monkeypatch.setattr(ingest_mod, "_compute_snapshot_map", lambda: {})

        ingest_mod.run(reset=False)

        df = load_options_db()
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "QQQ 20260120C500"
        assert df.iloc[0]["qty"] == pytest.approx(-1.0)

    def test_missing_options_file_skipped(self, tmp_path, seeded_db, monkeypatch):
        import ingest as ingest_mod

        monkeypatch.setattr(ingest_mod, "ACCOUNTS",      [
            {"account_id": "TRADIER-OPT", "broker": "tradier", "account_type": "options",
             "account_group": "investment", "holder": None, "price_source": "static", "active": 1},
        ])
        monkeypatch.setattr(ingest_mod, "PARSERS",       [])
        monkeypatch.setattr(ingest_mod, "POSITION_FILES", [])
        monkeypatch.setattr(ingest_mod, "OPTIONS_FILES",  [(tmp_path / "nonexistent.csv", "TRADIER-OPT")])
        monkeypatch.setattr(ingest_mod, "FUTURES_FILES",  [])
        monkeypatch.setattr(ingest_mod, "CRYPTO_FILES",   [])
        monkeypatch.setattr(ingest_mod, "_compute_snapshot_map", lambda: {})

        ingest_mod.run(reset=False)  # must not raise
        assert len(load_options_db()) == 0
