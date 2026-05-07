# -*- coding: utf-8 -*-
"""Integration tests for the full ingest pipeline.

These tests:
  1. Write synthetic broker CSV files to a tmp activity/ folder.
  2. Point the DB layer at a tmp SQLite file (via monkeypatch).
  3. Run the ingest pipeline end-to-end.
  4. Query the resulting database and assert on the loaded records.

No real broker files or the production database are touched.
"""
import sqlite3
from pathlib import Path
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import src.db as db_module
from src.db import init_db, upsert_accounts, clear_transactions, insert_transactions, load_transactions, delete_by_account


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a fresh temp file for the duration of each test."""
    db_path = tmp_path / "test_journal.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    # Also patch get_conn so it uses the redirected path
    import src.db as db_mod2
    monkeypatch.setattr(db_mod2, "DB_PATH", db_path)
    return db_path


@pytest.fixture()
def initialised_db(tmp_db):
    """Yield a db_path with the schema already initialised."""
    init_db()
    return tmp_db


# ── Helpers ───────────────────────────────────────────────────────────────────

_ACCOUNTS = [
    {"account_id": "RH-BV",   "broker": "robinhood", "account_type": "investment", "holder": "BV"},
    {"account_id": "COINBASE", "broker": "coinbase",  "account_type": "crypto",     "holder": None},
    {"account_id": "FIDELITY", "broker": "fidelity",  "account_type": "investment", "holder": None},
]

def _rh_csv(tmp_path: Path) -> Path:
    content = (
        "Activity Date,Trans Code,Instrument,Description,Amount\n"
        "01/10/2024,ACH,,Wire transfer in,2000.00\n"
        "01/15/2024,ACH,,Wire transfer out,-500.00\n"
        "02/01/2024,CDIV,AAPL,Apple dividend,35.50\n"
        "03/01/2024,MINT,,Margin interest,-22.00\n"
        "03/05/2024,GOLD,,Robinhood Gold,-5.00\n"
        "03/10/2024,INT,,Interest earned,1.25\n"
    )
    p = tmp_path / "rh.csv"
    p.write_text(content, encoding="utf-8")
    return p


def _cb_csv(tmp_path: Path) -> Path:
    content = (
        "You can use this transaction report,,,,,,,\n"
        "Your transactions,,,,,,,\n"
        "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Spot Price Currency,Spot Price at Transaction,Subtotal,"
        "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n"
        "CB001,2024-02-01T09:00:00Z,Deposit,USD,500,,1.00,500.00,500.00,0.00,\n"
        "CB002,2024-02-10T09:00:00Z,Withdrawal,USD,200,,1.00,200.00,200.00,0.00,\n"
        "CB003,2024-02-15T09:00:00Z,Buy,BTC,0.01,USD,40000,400.00,402.99,2.99,"
        "Bought 0.01 BTC using JPMorgan Chase Bank\n"
        "CB004,2024-02-20T09:00:00Z,Staking Income,ETH,0.01,USD,2000,20.00,20.00,0.00,\n"
        "CB005,2024-02-25T09:00:00Z,Receive,BTC,0.05,USD,40000,2000.00,2000.00,0.00,\n"
        "CB006,2024-03-01T09:00:00Z,Send,ETH,0.1,USD,2500,250.00,250.00,0.00,\n"
    )
    p = tmp_path / "cb.csv"
    p.write_text(content, encoding="utf-8")
    return p


def _fidelity_csv_content() -> str:
    """Fidelity yearly income CSV content (3 metadata rows, then header + data)."""
    return (
        "Fidelity Investment Income Report\n"
        "Date Range: Jan 2020 - Dec 2024\n"
        "Generated: 2025-01-01\n"
        "Yearly,Beginning balance,Dividends,Interest,Deposits,Withdrawals,Ending balance\n"
        "2024,10000.00,1200.00,0.00,5000.00,2000.00,14200.00\n"
        "2023,8000.00,950.00,0.00,3000.00,1000.00,10950.00\n"
        "Total,18000.00,2150.00,0.00,8000.00,3000.00,25150.00\n"
    )


def _fidelity_csv(tmp_path: Path, name: str = "fidelity.csv") -> Path:
    p = tmp_path / name
    p.write_text(_fidelity_csv_content(), encoding="utf-8")
    return p


# ── Schema / DB layer ─────────────────────────────────────────────────────────

class TestDbLayer:
    def test_init_creates_tables(self, tmp_db):
        init_db()
        with sqlite3.connect(tmp_db) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "transactions" in tables
        assert "accounts" in tables

    def test_upsert_accounts(self, initialised_db):
        upsert_accounts(_ACCOUNTS)
        with sqlite3.connect(initialised_db) as conn:
            rows = conn.execute("SELECT account_id FROM accounts").fetchall()
        ids = {r[0] for r in rows}
        assert "RH-BV"   in ids
        assert "COINBASE" in ids

    def test_upsert_accounts_preserves_user_settings(self, initialised_db):
        upsert_accounts(_ACCOUNTS)
        with sqlite3.connect(initialised_db) as conn:
            conn.execute(
                "UPDATE accounts SET price_source='static', active=0 WHERE account_id='RH-BV'"
            )
            conn.commit()

        upsert_accounts(_ACCOUNTS)

        with sqlite3.connect(initialised_db) as conn:
            row = conn.execute(
                "SELECT price_source, active FROM accounts WHERE account_id='RH-BV'"
            ).fetchone()
        assert row == ("static", 0)

    def test_insert_and_load_roundtrip(self, initialised_db):
        upsert_accounts(_ACCOUNTS)
        records = [
            {"id": "aaa", "account_id": "RH-BV", "date": "2024-01-10",
             "category": "cash_flow", "subcategory": "deposit", "amount": 1000.0,
             "currency": "USD", "symbol": None, "description": "Test",
             "source_file": "test.csv"},
        ]
        insert_transactions(records)
        df = load_transactions()
        assert len(df) == 1
        assert float(df["amount"].iloc[0]) == pytest.approx(1000.0)

    def test_clear_transactions(self, initialised_db):
        upsert_accounts(_ACCOUNTS)
        insert_transactions([
            {"id": "bbb", "account_id": "RH-BV", "date": "2024-01-10",
             "category": "cash_flow", "subcategory": "deposit", "amount": 500.0,
             "currency": "USD", "symbol": None, "description": "Test",
             "source_file": "test.csv"},
        ])
        clear_transactions()
        with sqlite3.connect(initialised_db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert count == 0

    def test_duplicate_id_ignored(self, initialised_db):
        """insert_transactions with a duplicate id must silently skip the second insert."""
        upsert_accounts(_ACCOUNTS)
        rec = {"id": "dup", "account_id": "RH-BV", "date": "2024-01-10",
               "category": "cash_flow", "subcategory": "deposit", "amount": 100.0,
               "currency": "USD", "symbol": None, "description": "A",
               "source_file": "test.csv"}
        first  = insert_transactions([rec])
        second = insert_transactions([rec])
        assert first  == 1
        assert second == 0   # INSERT OR IGNORE — nothing inserted the second time
        with sqlite3.connect(initialised_db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert count == 1

    def test_insert_returns_inserted_count(self, initialised_db):
        """insert_transactions returns number of rows actually written."""
        upsert_accounts(_ACCOUNTS)
        recs = [
            {"id": f"r{i}", "account_id": "RH-BV", "date": "2024-01-10",
             "category": "cash_flow", "subcategory": "deposit", "amount": float(i),
             "currency": "USD", "symbol": None, "description": f"rec {i}",
             "source_file": "test.csv"}
            for i in range(5)
        ]
        n = insert_transactions(recs)
        assert n == 5

    def test_delete_by_account(self, initialised_db):
        """delete_by_account removes only that account's records."""
        upsert_accounts(_ACCOUNTS)
        insert_transactions([
            {"id": "a1", "account_id": "RH-BV",    "date": "2024-01-10",
             "category": "cash_flow", "subcategory": "deposit", "amount": 100.0,
             "currency": "USD", "symbol": None, "description": "A", "source_file": "x"},
            {"id": "b1", "account_id": "COINBASE", "date": "2024-01-10",
             "category": "crypto_flow", "subcategory": "usd_deposit", "amount": 200.0,
             "currency": "USD", "symbol": None, "description": "B", "source_file": "y"},
        ])
        delete_by_account("COINBASE")
        with sqlite3.connect(initialised_db) as conn:
            rows = conn.execute("SELECT account_id FROM transactions").fetchall()
        ids = [r[0] for r in rows]
        assert "COINBASE" not in ids
        assert "RH-BV" in ids


# ── Full pipeline integration ─────────────────────────────────────────────────

class TestFullPipeline:
    def _run_ingest(self, tmp_path, tmp_db, rh_path, cb_path):
        """Run a mini ingest using only two parsers against the tmp DB."""
        from src.parsers import robinhood, coinbase

        init_db()
        clear_transactions()
        upsert_accounts(_ACCOUNTS)

        recs = robinhood.parse(str(rh_path), "RH-BV")
        recs += coinbase.parse(str(cb_path), "COINBASE")

        # Dedup
        seen: set = set()
        unique = []
        for r in recs:
            if r["id"] not in seen:
                seen.add(r["id"])
                unique.append(r)
        insert_transactions(unique)
        return recs

    def test_robinhood_records_loaded(self, tmp_path, initialised_db):
        rh = _rh_csv(tmp_path)
        cb = _cb_csv(tmp_path)
        self._run_ingest(tmp_path, initialised_db, rh, cb)

        df = load_transactions()
        rh_rows = df[df["account_id"] == "RH-BV"]
        assert len(rh_rows) == 6

    def test_coinbase_records_loaded(self, tmp_path, initialised_db):
        rh = _rh_csv(tmp_path)
        cb = _cb_csv(tmp_path)
        self._run_ingest(tmp_path, initialised_db, rh, cb)

        df = load_transactions()
        cb_rows = df[df["account_id"] == "COINBASE"]
        # Deposit, Withdrawal, bank_purchase + fee, staking, receive, send = 7 records
        assert len(cb_rows) >= 6

    def test_deposit_category_in_db(self, tmp_path, initialised_db):
        rh = _rh_csv(tmp_path)
        cb = _cb_csv(tmp_path)
        self._run_ingest(tmp_path, initialised_db, rh, cb)

        df = load_transactions()
        deposits = df[(df["category"] == "cash_flow") & (df["subcategory"] == "deposit")]
        assert len(deposits) >= 1
        assert all(deposits["amount"] > 0)

    def test_bank_purchase_in_db(self, tmp_path, initialised_db):
        rh = _rh_csv(tmp_path)
        cb = _cb_csv(tmp_path)
        self._run_ingest(tmp_path, initialised_db, rh, cb)

        df = load_transactions()
        bp = df[df["subcategory"] == "bank_purchase"]
        assert len(bp) == 1
        assert float(bp["amount"].iloc[0]) == pytest.approx(402.99)

    def test_staking_reward_in_db(self, tmp_path, initialised_db):
        rh = _rh_csv(tmp_path)
        cb = _cb_csv(tmp_path)
        self._run_ingest(tmp_path, initialised_db, rh, cb)

        df = load_transactions()
        staking = df[df["subcategory"] == "staking"]
        assert len(staking) == 1

    def test_crypto_wallet_transfers_in_db(self, tmp_path, initialised_db):
        rh = _rh_csv(tmp_path)
        cb = _cb_csv(tmp_path)
        self._run_ingest(tmp_path, initialised_db, rh, cb)

        df = load_transactions()
        received = df[df["subcategory"] == "crypto_received"]
        sent     = df[df["subcategory"] == "crypto_sent"]
        assert len(received) == 1
        assert len(sent) == 1
        assert float(received["amount"].iloc[0]) > 0
        assert float(sent["amount"].iloc[0])     < 0

    def test_metrics_after_ingest(self, tmp_path, initialised_db):  # noqa: E301
        """End-to-end: ingest → load → compute_metrics gives correct totals."""
        from src.metrics import compute_metrics, net_income

        rh = _rh_csv(tmp_path)
        cb = _cb_csv(tmp_path)
        self._run_ingest(tmp_path, initialised_db, rh, cb)

        df = load_transactions()
        df = df[df["category"] != "other"]
        df = df[df["subcategory"] != "internal_transfer"]

        m = compute_metrics(df)

        # RH: +2000 deposit, -500 withdrawal
        # CB: +500 usd_deposit, -200 usd_withdrawal, +402.99 bank_purchase
        expected_net_cash = 2000 - 500 + 500 - 200 + 402.99
        assert m["net_cash"] == pytest.approx(expected_net_cash, rel=1e-3)

        # RH: 35.50 dividend
        assert m["dividends"] == pytest.approx(35.50)

        # RH: -22.00 margin interest
        assert m["margin_int"] == pytest.approx(-22.00)

        # Fees: RH GOLD -5.00 + CB trading fee -2.99
        assert m["fees"] == pytest.approx(-7.99, rel=1e-2)

        # Net income = dividends + rewards + margin_int + fees
        ni = net_income(m)
        assert ni == pytest.approx(m["dividends"] + m["rewards"] + m["margin_int"] + m["fees"])


# ── Incremental ingest & reset behaviour ─────────────────────────────────────

class TestIncrementalAndReset:
    def test_incremental_adds_only_new_records(self, initialised_db):
        """Second insert of an overlapping batch should add only the new rows."""
        upsert_accounts(_ACCOUNTS)

        batch1 = [
            {"id": f"inc{i}", "account_id": "RH-BV", "date": "2024-01-01",
             "category": "cash_flow", "subcategory": "deposit", "amount": float(i * 100),
             "currency": "USD", "symbol": None, "description": f"rec {i}",
             "source_file": "history.csv"}
            for i in range(3)
        ]
        n1 = insert_transactions(batch1)
        assert n1 == 3

        # Overlap: same 3 IDs + 2 genuinely new ones
        batch2 = batch1 + [
            {"id": f"inc{i}", "account_id": "RH-BV", "date": "2024-02-01",
             "category": "cash_flow", "subcategory": "deposit", "amount": float(i * 100),
             "currency": "USD", "symbol": None, "description": f"rec {i}",
             "source_file": "update.csv"}
            for i in range(3, 5)
        ]
        n2 = insert_transactions(batch2)
        assert n2 == 2  # only new records

        with sqlite3.connect(initialised_db) as conn:
            total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert total == 5

    def test_reset_clears_then_reinserts(self, tmp_path, initialised_db, monkeypatch):
        """--reset should clear all existing transactions then reload from scratch."""
        from src import ingest as ingest_mod
        from src.parsers import robinhood as rh_parser

        rh = _rh_csv(tmp_path)
        monkeypatch.setattr(ingest_mod, "ACCOUNTS", [_ACCOUNTS[0]])
        monkeypatch.setattr(ingest_mod, "PARSERS",  [(rh_parser.parse, rh, "RH-BV")])
        monkeypatch.setattr(ingest_mod, "ALWAYS_REFRESH", set())

        ingest_mod.run(reset=False)
        with sqlite3.connect(initialised_db) as conn:
            count_first = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

        # reset=True should clear then reload — same final count, not doubled
        ingest_mod.run(reset=True)
        with sqlite3.connect(initialised_db) as conn:
            count_reset = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

        assert count_reset == count_first

    def test_fidelity_always_refresh_no_duplicates(self, tmp_path, initialised_db, monkeypatch):
        """Running ingest twice without --reset must not double Fidelity rows."""
        from src import ingest as ingest_mod
        from src.parsers import fidelity as fid_parser

        fid = _fidelity_csv(tmp_path)
        monkeypatch.setattr(ingest_mod, "ACCOUNTS", [_ACCOUNTS[2]])  # FIDELITY
        monkeypatch.setattr(ingest_mod, "PARSERS",  [(fid_parser.parse, fid, "FIDELITY")])
        monkeypatch.setattr(ingest_mod, "ALWAYS_REFRESH", {"FIDELITY"})

        ingest_mod.run(reset=False)
        with sqlite3.connect(initialised_db) as conn:
            count1 = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE account_id='FIDELITY'"
            ).fetchone()[0]

        # Second run — ALWAYS_REFRESH deletes then re-inserts; count must be identical
        ingest_mod.run(reset=False)
        with sqlite3.connect(initialised_db) as conn:
            count2 = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE account_id='FIDELITY'"
            ).fetchone()[0]

        assert count1 > 0
        assert count2 == count1

    def test_fidelity_id_is_filename_independent(self, tmp_path):
        """Same Fidelity annual data must yield identical IDs regardless of CSV filename."""
        from src.parsers import fidelity as fid_parser

        path_a = _fidelity_csv(tmp_path, "fidelity_v1.csv")
        path_b = _fidelity_csv(tmp_path, "fidelity_v2.csv")

        recs_a = fid_parser.parse(str(path_a), "FIDELITY")
        recs_b = fid_parser.parse(str(path_b), "FIDELITY")

        ids_a = {r["id"] for r in recs_a}
        ids_b = {r["id"] for r in recs_b}

        assert len(ids_a) > 0
        assert ids_a == ids_b
