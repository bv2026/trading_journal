# -*- coding: utf-8 -*-
"""Integration tests for the positions DB pipeline.

Covers the full round-trip:
  CSV parse → delete_positions_by_account → insert_positions
  → load_positions_db → load_positions_from_db (with mocked prices)

yfinance is monkey-patched so tests run offline and deterministically.
"""
import math
from pathlib import Path

import pandas as pd
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import src.db as db_module
from src.db import (
    init_db, upsert_accounts,
    delete_positions_by_account, insert_positions, load_positions_db,
    insert_options, insert_futures, insert_crypto,
)
from src.parsers.positions_csv import parse
import src.positions as positions_module


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_journal.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(positions_module, "DB_PATH", db_path, raising=False)
    return db_path


@pytest.fixture()
def seeded_db(tmp_db):
    init_db()
    upsert_accounts([
        {"account_id": "SCHWAB",  "broker": "schwab",  "account_type": "investment", "holder": None},
        {"account_id": "TRADIER", "broker": "tradier", "account_type": "investment", "holder": None},
    ])
    return tmp_db


@pytest.fixture()
def mock_prices(monkeypatch):
    """Patch _fetch_live_prices to return deterministic prices without network."""
    def _fake_prices(tickers):
        return {t: 100.0 for t in tickers}
    monkeypatch.setattr(positions_module, "_fetch_live_prices", _fake_prices)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_positions_csv(tmp_path: Path, rows: list[dict], name: str = "positions-test.csv") -> Path:
    p = tmp_path / name
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _pos_row(ticker, shares=10.0, cost_basis=50.0, sector="Technology") -> dict:
    return {
        "Ticker": ticker, "Name": f"{ticker} Inc",
        "Sh/Contr": shares, "COST BASIS": f"${cost_basis:.2f}",
        "PRICE": "$100.00",
        "COST": f"${shares * cost_basis:.2f}",
        "MARKET VALUE": f"${shares * 100:.2f}",
        "totalReturn": f"${shares * (100 - cost_basis):.2f}",
        "sector": sector, "industry": "Software",
        "IV RANK": "40%", "PERF YTD": "10%", "ATR %": "2%", "TYPE": "Stock",
    }


def _margin_row(balance: float) -> dict:
    sign = "-" if balance < 0 else ""
    return {
        "Ticker": "MARGIN", "Name": "Margin Balance",
        "MARKET VALUE": f"$({abs(balance):,.2f})" if balance < 0 else f"${balance:,.2f}",
        "Sh/Contr": None, "COST BASIS": None, "PRICE": None,
        "COST": None, "totalReturn": None,
        "sector": None, "industry": None,
        "IV RANK": None, "PERF YTD": None, "ATR %": None, "TYPE": None,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPositionsRoundTrip:

    def test_insert_and_load(self, seeded_db):
        recs = [
            {"account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
             "shares": 10.0, "cost_basis": 150.0, "sector": "Technology",
             "industry": "Hardware", "asset_type": "Stock",
             "iv_rank": 40.0, "perf_ytd": 5.0, "atr_pct": 2.0,
             "source_file": "test.csv"},
        ]
        insert_positions(recs)
        pos = load_positions_db()
        assert len(pos) == 1
        assert pos.iloc[0]["Ticker"] == "AAPL"
        assert pos.iloc[0]["Shares"] == pytest.approx(10.0)
        assert pos.iloc[0]["Cost_Basis"] == pytest.approx(150.0)

    def test_delete_then_reinsert_replaces_account(self, seeded_db):
        recs = [{"account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
                 "shares": 10.0, "cost_basis": 150.0, "sector": "Technology",
                 "industry": None, "asset_type": None,
                 "iv_rank": None, "perf_ytd": None, "atr_pct": None,
                 "source_file": "test.csv"}]
        insert_positions(recs)
        assert len(load_positions_db()) == 1

        delete_positions_by_account("SCHWAB")
        new_recs = [{"account_id": "SCHWAB", "ticker": "MSFT", "name": "Microsoft",
                     "shares": 5.0, "cost_basis": 300.0, "sector": "Technology",
                     "industry": None, "asset_type": None,
                     "iv_rank": None, "perf_ytd": None, "atr_pct": None,
                     "source_file": "test.csv"}]
        insert_positions(new_recs)
        pos = load_positions_db()
        assert len(pos) == 1
        assert pos.iloc[0]["Ticker"] == "MSFT"

    def test_csv_parse_and_insert(self, tmp_path, seeded_db):
        csv_path = _write_positions_csv(tmp_path, [_pos_row("AAPL"), _pos_row("MSFT")])
        recs = parse(str(csv_path), "SCHWAB")
        assert len(recs) == 2
        delete_positions_by_account("SCHWAB")
        insert_positions(recs)
        pos = load_positions_db()
        assert set(pos["Ticker"]) == {"AAPL", "MSFT"}

    def test_margin_stored_and_loaded(self, tmp_path, seeded_db):
        """MARGIN row must land in DB with the correct (negative) cost_basis."""
        csv_path = _write_positions_csv(tmp_path, [
            _pos_row("AAPL"),
            _margin_row(-25000.0),
        ])
        recs = parse(str(csv_path), "SCHWAB")
        margin_recs = [r for r in recs if r["ticker"] == "MARGIN"]
        assert len(margin_recs) == 1
        assert margin_recs[0]["cost_basis"] == pytest.approx(-25000.0)

        insert_positions(recs)
        pos = load_positions_db()
        margin_row = pos[pos["Ticker"] == "MARGIN"]
        assert len(margin_row) == 1
        assert margin_row.iloc[0]["Cost_Basis"] == pytest.approx(-25000.0)

    def test_load_positions_from_db_computes_derived_cols(self, tmp_path, seeded_db, mock_prices, monkeypatch):
        """load_positions_from_db must compute COST, MARKET VALUE, totalReturn."""
        # Point DB load at the test DB
        monkeypatch.setattr(
            positions_module,
            "load_positions_from_db",
            positions_module.load_positions_from_db,
        )
        recs = [{"account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
                 "shares": 10.0, "cost_basis": 80.0, "sector": "Technology",
                 "industry": None, "asset_type": None,
                 "iv_rank": None, "perf_ytd": None, "atr_pct": None,
                 "source_file": "test.csv"}]
        insert_positions(recs)

        # Re-point DB_PATH in db module so load_positions_db picks up the test DB
        monkeypatch.setattr(db_module, "DB_PATH", seeded_db)

        pos = positions_module.load_positions_from_db()
        aapl = pos[pos["Ticker"] == "AAPL"].iloc[0]

        # mock price = 100.0, shares = 10, cost_basis = 80
        assert aapl["PRICE"]         == pytest.approx(100.0)
        assert aapl["COST"]          == pytest.approx(800.0)   # 10 × 80
        assert aapl["MARKET VALUE"]  == pytest.approx(1000.0)  # 10 × 100
        assert aapl["totalReturn"]   == pytest.approx(200.0)   # 1000 - 800

    def test_margin_market_value_from_cost_basis(self, tmp_path, seeded_db, mock_prices):
        """MARGIN rows must get MARKET VALUE = cost_basis (no yfinance lookup)."""
        recs = [
            {"account_id": "SCHWAB", "ticker": "MARGIN", "name": "Margin",
             "shares": None, "cost_basis": -25000.0, "sector": None,
             "industry": None, "asset_type": None,
             "iv_rank": None, "perf_ytd": None, "atr_pct": None,
             "source_file": "test.csv"},
        ]
        insert_positions(recs)
        pos = positions_module.load_positions_from_db()
        margin = pos[pos["Ticker"] == "MARGIN"].iloc[0]
        assert margin["MARKET VALUE"] == pytest.approx(-25000.0)
        assert margin["COST"]         == pytest.approx(0.0)
        assert margin["totalReturn"]  == pytest.approx(0.0)

    def test_multiple_accounts_coexist(self, seeded_db):
        """Positions from two accounts must both appear in load_positions_db."""
        insert_positions([
            {"account_id": "SCHWAB",  "ticker": "AAPL", "name": "Apple",
             "shares": 10.0, "cost_basis": 150.0, "sector": "Technology",
             "industry": None, "asset_type": None,
             "iv_rank": None, "perf_ytd": None, "atr_pct": None,
             "source_file": "test.csv"},
            {"account_id": "TRADIER", "ticker": "MSFT", "name": "Microsoft",
             "shares": 5.0, "cost_basis": 300.0, "sector": "Technology",
             "industry": None, "asset_type": None,
             "iv_rank": None, "perf_ytd": None, "atr_pct": None,
             "source_file": "test.csv"},
        ])
        pos = load_positions_db()
        assert set(pos["Account"]) == {"SCHWAB", "TRADIER"}
        assert set(pos["Ticker"])  == {"AAPL", "MSFT"}


# ── load_all_positions + static loaders ───────────────────────────────────────

@pytest.fixture()
def full_seeded_db(tmp_db):
    """DB seeded with equity, options, futures, and crypto accounts."""
    init_db()
    upsert_accounts([
        {"account_id": "SCHWAB",      "broker": "schwab",       "account_type": "equity",
         "account_group": "investment", "holder": None, "price_source": "live",   "active": 1},
        {"account_id": "TRADIER-OPT", "broker": "tradier",      "account_type": "options",
         "account_group": "investment", "holder": None, "price_source": "static", "active": 1},
        {"account_id": "TS-FUT",      "broker": "tradestation", "account_type": "futures",
         "account_group": "investment", "holder": None, "price_source": "static", "active": 1},
        {"account_id": "COINBASE",    "broker": "coinbase",     "account_type": "crypto",
         "account_group": "investment", "holder": None, "price_source": "static", "active": 1},
    ])
    return tmp_db


class TestLoadAllPositions:
    def test_load_options_from_db(self, full_seeded_db):
        insert_options([{
            "account_id": "TRADIER-OPT", "symbol": "QQQ 20260120C500",
            "underlying": "QQQ", "expiry": "2026-01-20", "strike": 500.0,
            "call_put": "Call", "description": "QQQ Call",
            "qty": -1.0, "price": 2.0, "market_value": -200.0,
            "source_file": "options-trader.csv",
        }])
        df = positions_module.load_options_from_db()
        assert len(df) == 1
        assert df.iloc[0]["Account"] == "TRADIER-OPT"
        assert df.iloc[0]["Ticker"] == "QQQ 20260120C500"
        assert df.iloc[0]["MARKET VALUE"] == pytest.approx(-200.0)
        assert df.iloc[0]["asset_class"] == "options"

    def test_load_futures_from_db(self, full_seeded_db):
        insert_futures([{
            "account_id": "TS-FUT", "symbol": "/ESM25",
            "underlying": "/ES", "description": "E-mini S&P",
            "qty": 2.0, "price": 5200.0, "market_value": 520_000.0,
            "source_file": "futures-ts.csv",
        }])
        df = positions_module.load_futures_from_db()
        assert len(df) == 1
        assert df.iloc[0]["Account"] == "TS-FUT"
        assert df.iloc[0]["Ticker"] == "/ESM25"
        assert df.iloc[0]["MARKET VALUE"] == pytest.approx(520_000.0)
        assert df.iloc[0]["asset_class"] == "futures"

    def test_load_crypto_from_db(self, full_seeded_db):
        insert_crypto([{
            "account_id": "COINBASE", "symbol": "BTC",
            "name": "Bitcoin", "qty": 0.5, "price": 60_000.0,
            "cost_basis": None, "market_value": 30_000.0,
            "source_file": "crypto.csv",
        }])
        df = positions_module.load_crypto_from_db()
        assert len(df) == 1
        assert df.iloc[0]["Account"] == "COINBASE"
        assert df.iloc[0]["Ticker"] == "BTC"
        assert df.iloc[0]["MARKET VALUE"] == pytest.approx(30_000.0)
        assert df.iloc[0]["asset_class"] == "crypto"

    def test_load_all_positions_includes_all_asset_classes(self, full_seeded_db, monkeypatch):
        monkeypatch.setattr(positions_module, "_fetch_live_prices", lambda tickers: {t: 100.0 for t in tickers})

        insert_positions([{
            "account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
            "shares": 10.0, "cost_basis": 80.0, "sector": "Technology",
            "industry": None, "asset_type": None,
            "iv_rank": None, "perf_ytd": None, "atr_pct": None,
            "source_file": "test.csv",
        }])
        insert_options([{
            "account_id": "TRADIER-OPT", "symbol": "QQQ 20260120C500",
            "underlying": "QQQ", "expiry": "2026-01-20", "strike": 500.0,
            "call_put": "Call", "description": "QQQ Call",
            "qty": -1.0, "price": 2.0, "market_value": -200.0,
            "source_file": "options-trader.csv",
        }])
        insert_futures([{
            "account_id": "TS-FUT", "symbol": "/ESM25",
            "underlying": "/ES", "description": "E-mini",
            "qty": 1.0, "price": 5200.0, "market_value": 100_000.0,
            "source_file": "futures-ts.csv",
        }])
        insert_crypto([{
            "account_id": "COINBASE", "symbol": "BTC",
            "name": "Bitcoin", "qty": 0.5, "price": 60_000.0,
            "cost_basis": None, "market_value": 30_000.0,
            "source_file": "crypto.csv",
        }])

        all_pos = positions_module.load_all_positions()
        assert "asset_class" in all_pos.columns
        assert set(all_pos["asset_class"]) == {"equity", "options", "futures", "crypto"}
        assert "AAPL" in all_pos["Ticker"].values
        assert "QQQ 20260120C500" in all_pos["Ticker"].values
        assert "/ESM25" in all_pos["Ticker"].values
        assert "BTC" in all_pos["Ticker"].values

    def test_load_all_positions_only_equity(self, full_seeded_db, monkeypatch):
        monkeypatch.setattr(positions_module, "_fetch_live_prices", lambda tickers: {t: 100.0 for t in tickers})

        insert_positions([{
            "account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
            "shares": 5.0, "cost_basis": 100.0, "sector": "Technology",
            "industry": None, "asset_type": None,
            "iv_rank": None, "perf_ytd": None, "atr_pct": None,
            "source_file": "test.csv",
        }])
        all_pos = positions_module.load_all_positions()
        assert set(all_pos["asset_class"]) == {"equity"}
        assert len(all_pos) == 1

    def test_load_all_positions_empty_db(self, full_seeded_db, monkeypatch):
        monkeypatch.setattr(positions_module, "_fetch_live_prices", lambda tickers: {})
        all_pos = positions_module.load_all_positions()
        assert all_pos.empty

    def test_compute_net_worth_with_all_asset_classes(self, full_seeded_db, monkeypatch):
        monkeypatch.setattr(positions_module, "_fetch_live_prices", lambda tickers: {t: 100.0 for t in tickers})

        insert_positions([
            {"account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
             "shares": 10.0, "cost_basis": 80.0, "sector": "Technology",
             "industry": None, "asset_type": None,
             "iv_rank": None, "perf_ytd": None, "atr_pct": None,
             "source_file": "test.csv"},
            {"account_id": "SCHWAB", "ticker": "MARGIN", "name": "Margin",
             "shares": None, "cost_basis": -25_000.0, "sector": None,
             "industry": None, "asset_type": None,
             "iv_rank": None, "perf_ytd": None, "atr_pct": None,
             "source_file": "test.csv"},
        ])
        insert_options([{
            "account_id": "TRADIER-OPT", "symbol": "QQQ C500",
            "underlying": "QQQ", "expiry": "2026-01-20", "strike": 500.0,
            "call_put": "Call", "description": "QQQ Call",
            "qty": 1.0, "price": 2.0, "market_value": 500.0,
            "source_file": "opts.csv",
        }])
        insert_crypto([{
            "account_id": "COINBASE", "symbol": "BTC",
            "name": "Bitcoin", "qty": 0.5, "price": 60_000.0,
            "cost_basis": None, "market_value": 30_000.0,
            "source_file": "crypto.csv",
        }])

        all_pos = positions_module.load_all_positions()
        nw = positions_module.compute_net_worth(all_pos)

        # equity AAPL: 10 shares × $100 = $1000
        # options: $500 market value
        # crypto: $30,000 market value
        # MARGIN: $25,000 debt
        assert nw["market_value"] == pytest.approx(1000.0 + 500.0 + 30_000.0)
        assert nw["margin"]       == pytest.approx(25_000.0)
        assert nw["net_worth"]    == pytest.approx(1000.0 + 500.0 + 30_000.0 - 25_000.0)


# ── Snapshot write via ingest pipeline ────────────────────────────────────────

class TestSnapshotViaIngest:
    """Verify that ingest.run() writes a portfolio snapshot at the end of each run."""

    def test_ingest_writes_snapshot(self, tmp_path, full_seeded_db, monkeypatch):
        import ingest as ingest_mod
        from src.db import load_snapshot_periods, write_portfolio_snapshot

        # Seed equity positions so there's something to snapshot
        insert_positions([{
            "account_id": "SCHWAB", "ticker": "AAPL", "name": "Apple",
            "shares": 10.0, "cost_basis": 80.0, "sector": "Technology",
            "industry": None, "asset_type": None,
            "iv_rank": None, "perf_ytd": None, "atr_pct": None,
            "source_file": "test.csv",
        }])

        # Stub yfinance and snapshot computation so the test runs offline
        monkeypatch.setattr(positions_module, "_fetch_live_prices",
                            lambda tickers: {t: 100.0 for t in tickers})

        # Provide a deterministic snapshot map (bypasses the yfinance call inside
        # _compute_snapshot_map, which we already test at the unit level)
        monkeypatch.setattr(
            ingest_mod, "_compute_snapshot_map",
            lambda: {"SCHWAB": {"market_value": 1000.0, "cost_basis": 800.0, "margin": 0.0}},
        )
        monkeypatch.setattr(ingest_mod, "ACCOUNTS",      [
            {"account_id": "SCHWAB", "broker": "schwab", "account_type": "equity",
             "account_group": "investment", "holder": None,
             "price_source": "live", "active": 1},
        ])
        monkeypatch.setattr(ingest_mod, "PARSERS",        [])
        monkeypatch.setattr(ingest_mod, "POSITION_FILES", [])
        monkeypatch.setattr(ingest_mod, "OPTIONS_FILES",  [])
        monkeypatch.setattr(ingest_mod, "FUTURES_FILES",  [])
        monkeypatch.setattr(ingest_mod, "CRYPTO_FILES",   [])

        ingest_mod.run(reset=False)

        snap = load_snapshot_periods()
        assert len(snap) >= 1
        accts = set(snap["account_id"])
        assert "SCHWAB" in accts

    def test_ingest_same_day_rerun_updates_snapshot(self, tmp_path, full_seeded_db, monkeypatch):
        import ingest as ingest_mod
        from src.db import load_snapshot_periods

        def _run_with_mv(mv):
            monkeypatch.setattr(
                ingest_mod, "_compute_snapshot_map",
                lambda: {"SCHWAB": {"market_value": mv, "cost_basis": None, "margin": 0.0}},
            )
            monkeypatch.setattr(ingest_mod, "ACCOUNTS", [
                {"account_id": "SCHWAB", "broker": "schwab", "account_type": "equity",
                 "account_group": "investment", "holder": None,
                 "price_source": "live", "active": 1},
            ])
            for attr in ("PARSERS", "POSITION_FILES", "OPTIONS_FILES",
                         "FUTURES_FILES", "CRYPTO_FILES"):
                monkeypatch.setattr(ingest_mod, attr, [])
            ingest_mod.run(reset=False)

        _run_with_mv(1_000.0)
        _run_with_mv(1_100.0)   # second run same day — should update, not duplicate

        snap = load_snapshot_periods()
        schwab = snap[snap["account_id"] == "SCHWAB"]
        assert len(schwab) == 1
        assert schwab.iloc[0]["current_value"] == pytest.approx(1_100.0)
