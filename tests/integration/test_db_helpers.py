# -*- coding: utf-8 -*-
"""Integration tests for uncovered src/db.py helpers:
   clear_transactions, upsert_instruments, load_instruments,
   upsert_cash_balance, get_cash_balance, _migrate.
"""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import src.db as db_module
from src.db import (
    init_db, upsert_accounts, insert_transactions,
    clear_transactions, upsert_instruments, load_instruments,
    upsert_cash_balance, get_cash_balance,
    load_transactions,
    upsert_account_balances, load_account_balances,
    upsert_account_balance_adjustment,
    create_sync_run, complete_sync_run, load_sync_runs,
)


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    init_db()
    upsert_accounts([
        {"account_id": "RH-BV",  "broker": "robinhood", "account_type": "investment"},
        {"account_id": "WEBULL", "broker": "webull",    "account_type": "investment"},
    ])
    return db_path


def _txn(account_id="RH-BV", idx=0):
    return {
        "id":          f"tx_{account_id}_{idx}",
        "account_id":  account_id,
        "date":        "2026-04-01",
        "category":    "dividend",
        "subcategory": "cash_div",
        "amount":      25.0 + idx,
        "currency":    "USD",
        "symbol":      "AAPL",
        "description": f"AAPL dividend {idx}",
        "data_source": "csv",
        "source_file": "test.csv",
    }


# ── clear_transactions ────────────────────────────────────────────────────────

class TestClearTransactions:
    def test_clears_all_transactions(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        insert_transactions([_txn("RH-BV", 0), _txn("WEBULL", 1)])
        clear_transactions()
        df = load_transactions()
        assert len(df) == 0

    def test_clear_on_empty_is_safe(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        clear_transactions()  # should not raise
        assert load_transactions().empty

    def test_re_insert_after_clear(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        insert_transactions([_txn("RH-BV", 0)])
        clear_transactions()
        insert_transactions([_txn("RH-BV", 0)])
        assert len(load_transactions()) == 1


# ── upsert_instruments + load_instruments ─────────────────────────────────────

class TestInstruments:
    def _equity(self, ticker, name="Test Corp"):
        return {"symbol": ticker, "asset_class": "equity", "underlying": None,
                "name": name, "exchange": None, "currency": "USD",
                "sector": "Technology", "industry": None,
                "expiry": None, "strike": None, "call_put": None,
                "tick_size": None, "point_value": None, "tradable": None}

    def _option(self, symbol):
        return {"symbol": symbol, "asset_class": "option", "underlying": "AAPL",
                "name": None, "exchange": None, "currency": "USD",
                "sector": None, "industry": None,
                "expiry": "2026-06-19", "strike": 200.0, "call_put": "C",
                "tick_size": None, "point_value": 100.0, "tradable": None}

    def test_upsert_returns_row_count(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        n = upsert_instruments([self._equity("AAPL"), self._equity("MSFT")])
        assert n == 2

    def test_load_all_instruments(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_instruments([self._equity("AAPL"), self._option("AAPL260619C00200000")])
        df = load_instruments()
        assert len(df) == 2

    def test_load_filtered_by_asset_class(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_instruments([self._equity("AAPL"), self._option("AAPL260619C00200000")])
        df = load_instruments("equity")
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "AAPL"

    def test_upsert_replaces_on_conflict(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_instruments([self._equity("AAPL", name="Apple Old")])
        upsert_instruments([self._equity("AAPL", name="Apple New")])
        df = load_instruments("equity")
        assert df.iloc[0]["name"] == "Apple New"

    def test_upsert_preserves_metadata_when_incoming_null(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_instruments([self._equity("AAPL", name="Apple Inc")])
        upsert_instruments([{
            "symbol": "AAPL", "asset_class": "equity", "underlying": None,
            "name": None, "exchange": None, "currency": "USD",
            "sector": None, "industry": None, "expiry": None,
            "strike": None, "call_put": None, "tick_size": None,
            "point_value": None, "tradable": None,
        }])
        df = load_instruments("equity")
        assert df.iloc[0]["name"] == "Apple Inc"
        assert df.iloc[0]["sector"] == "Technology"

    def test_empty_input_returns_zero(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        assert upsert_instruments([]) == 0

    def test_load_from_missing_db_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "nonexistent.db")
        df = load_instruments()
        assert df.empty

    def test_sector_field_persisted(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_instruments([self._equity("AAPL")])
        df = load_instruments("equity")
        assert df.iloc[0]["sector"] == "Technology"


# ── upsert_cash_balance + get_cash_balance ────────────────────────────────────

class TestCashBalance:
    def test_set_and_get(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_cash_balance(18_500.0)
        assert get_cash_balance() == pytest.approx(18_500.0)

    def test_update_overwrites(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_cash_balance(10_000.0)
        upsert_cash_balance(20_000.0)
        assert get_cash_balance() == pytest.approx(20_000.0)

    def test_zero_balance(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_cash_balance(0.0)
        assert get_cash_balance() == pytest.approx(0.0)

    def test_missing_db_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "nonexistent.db")
        assert get_cash_balance() == 0.0

    def test_custom_account_id(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_cash_balance(5_000.0, account_id="SAVINGS")
        assert get_cash_balance("SAVINGS") == pytest.approx(5_000.0)
        assert get_cash_balance("CASH") == 0.0  # default CASH not set

    def test_not_yet_set_returns_zero(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        assert get_cash_balance() == 0.0


class TestAccountBalances:
    def test_upsert_and_load_preserves_cost_basis(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        written = upsert_account_balances([{
            "account_id": "RH-BV",
            "market_value": 100_000.0,
            "cost_basis": 82_500.0,
            "margin": 20_000.0,
            "net_equity": 80_000.0,
            "source": "Live MCP",
            "detail": "OK",
        }])
        assert written == 1

        df = load_account_balances()
        row = df[df["account_id"] == "RH-BV"].iloc[0]
        assert row["market_value"] == pytest.approx(100_000.0)
        assert row["cost_basis"] == pytest.approx(82_500.0)
        assert row["margin"] == pytest.approx(20_000.0)
        assert row["net_equity"] == pytest.approx(80_000.0)

    def test_legacy_record_without_cost_basis_is_allowed(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_account_balances([{
            "account_id": "WEBULL",
            "market_value": 50_000.0,
            "margin": 5_000.0,
            "net_equity": 45_000.0,
        }])
        df = load_account_balances()
        row = df[df["account_id"] == "WEBULL"].iloc[0]
        assert pd.isna(row["cost_basis"])

    def test_cost_basis_adjustment_is_added_to_broker_balance(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        upsert_accounts([{
            "account_id": "COINBASE",
            "broker": "coinbase",
            "account_type": "crypto",
        }])
        upsert_account_balances([{
            "account_id": "COINBASE",
            "market_value": 32_000.0,
            "cost_basis": 27_500.0,
            "margin": 0.0,
            "net_equity": 32_000.0,
        }])
        upsert_account_balance_adjustment("COINBASE", -250.0)

        df = load_account_balances()
        row = df[df["account_id"] == "COINBASE"].iloc[0]
        assert row["base_cost_basis"] == pytest.approx(27_500.0)
        assert row["cost_basis_adjustment"] == pytest.approx(-250.0)
        assert row["cost_basis"] == pytest.approx(27_250.0)

    def test_sync_run_id_is_preserved(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        sync_run_id = create_sync_run("balances.refresh", source="test")
        upsert_account_balances([{
            "account_id": "RH-BV",
            "market_value": 100_000.0,
            "cost_basis": 82_500.0,
            "margin": 20_000.0,
            "net_equity": 80_000.0,
            "sync_run_id": sync_run_id,
        }])
        df = load_account_balances()
        row = df[df["account_id"] == "RH-BV"].iloc[0]
        assert row["sync_run_id"] == sync_run_id


class TestSyncRuns:
    def test_create_complete_and_load_sync_run(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        sync_run_id = create_sync_run(
            "positions.refresh",
            source="mcp",
            metadata={"broker": "webull"},
        )
        complete_sync_run(
            sync_run_id,
            status="ok",
            row_counts={"positions": 2},
            warnings=["sample warning"],
            errors=[],
        )

        runs = load_sync_runs()
        row = runs[runs["sync_run_id"] == sync_run_id].iloc[0]
        assert row["operation"] == "positions.refresh"
        assert row["source"] == "mcp"
        assert row["status"] == "ok"
        assert '"positions": 2' in row["row_counts"]
        assert "sample warning" in row["warnings"]


# ── _migrate idempotency ──────────────────────────────────────────────────────

class TestMigrateIdempotent:
    def test_init_db_twice_does_not_raise(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        init_db()   # second call — _migrate() must be idempotent
        init_db()   # third call for good measure

    def test_schema_migrations_and_sync_columns_exist(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        with db_module.get_conn() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "schema_migrations" in tables
            assert "sync_runs" in tables
            for table in (
                "transactions",
                "positions",
                "options_positions",
                "futures_positions",
                "crypto_positions",
                "portfolio_snapshots",
                "account_balances",
            ):
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                assert "sync_run_id" in columns

            applied = {
                row[0]
                for row in conn.execute("SELECT name FROM schema_migrations")
            }
            assert "positions.sync_run_id" in applied

    def test_data_survives_re_init(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        insert_transactions([_txn("RH-BV", 0)])
        init_db()   # re-init after data written
        df = load_transactions()
        assert len(df) == 1

    def test_views_recreated_correctly(self, tmp_db, monkeypatch):
        """Views are dropped + recreated on init_db; check v_snapshot_periods loads."""
        from src.db import load_snapshot_periods, write_portfolio_snapshot
        from datetime import date
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        write_portfolio_snapshot(date.today().isoformat(),
                                 {"RH-BV": {"market_value": 100_000.0,
                                            "cost_basis": None, "margin": 20_000.0}})
        init_db()   # drop + recreate views
        snap = load_snapshot_periods()
        assert not snap.empty
        assert snap.iloc[0]["current_value"] == pytest.approx(80_000.0)
