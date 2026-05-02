# -*- coding: utf-8 -*-
"""Integration tests for portfolio_snapshots and v_snapshot_periods.

Covers:
  - Snapshot round-trip: write → read back via v_snapshot_periods
  - Net-value fix: v_snapshot_periods returns (market_value - margin) for all
    period columns, not raw market_value
  - Performance deltas are apples-to-apples (net vs net)
  - NULL handling when historical snapshots don't exist yet
  - Cash balance excluded from snapshot (tracked separately)
"""
import sqlite3
from datetime import date, timedelta
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import src.db as db_module
from src.db import init_db, upsert_accounts, write_portfolio_snapshot, load_snapshot_periods


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_snapshot.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    init_db()
    upsert_accounts([
        {"account_id": "RH-BV",  "broker": "robinhood",    "account_type": "investment", "holder": None},
        {"account_id": "WEBULL", "broker": "webull",       "account_type": "investment", "holder": None},
        {"account_id": "SCHWAB", "broker": "schwab",       "account_type": "investment", "holder": None},
    ])
    return db_path


def _conn(db_path):
    return sqlite3.connect(db_path)


def _write(db_path, snap_date: date, account_id: str, market_value: float, margin: float = 0.0):
    write_portfolio_snapshot(
        snap_date.isoformat(),
        {account_id: {"market_value": market_value, "cost_basis": None, "margin": margin}},
    )


# ── Basic round-trip ──────────────────────────────────────────────────────────

class TestSnapshotRoundTrip:
    def test_write_and_read_back(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        _write(tmp_db, today, "RH-BV", 100_000.0, margin=30_000.0)
        snap = load_snapshot_periods()
        assert len(snap) == 1
        row = snap[snap["account_id"] == "RH-BV"].iloc[0]
        # current_value should be net (market_value - margin)
        assert row["current_value"] == pytest.approx(70_000.0)

    def test_no_snapshots_returns_empty(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        snap = load_snapshot_periods()
        assert snap.empty

    def test_upsert_same_day_overwrites(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        _write(tmp_db, today, "RH-BV", 100_000.0, margin=30_000.0)
        _write(tmp_db, today, "RH-BV", 120_000.0, margin=40_000.0)
        snap = load_snapshot_periods()
        row = snap[snap["account_id"] == "RH-BV"].iloc[0]
        assert row["current_value"] == pytest.approx(80_000.0)  # 120k - 40k


# ── Net-value fix: historical periods must subtract margin ─────────────────────

class TestSnapshotNetValue:
    def _seed_history(self, tmp_db, today: date):
        """Write snapshots at today, 1W ago, 1M ago, 3M ago, 1Y ago, YTD-start."""
        offsets = {
            "today":    0,
            "1w":       8,   # >7 days
            "1m":      32,   # >30 days
            "3m":      92,   # >90 days
            "1y":     366,   # >365 days
        }
        ytd_start = date(today.year, 1, 1) - timedelta(days=1)

        for label, days in offsets.items():
            snap_date = today - timedelta(days=days)
            _write(tmp_db, snap_date, "RH-BV",
                   market_value=200_000.0 + days * 100,  # different each date
                   margin=50_000.0)

        # YTD start (Dec 31 of prior year)
        _write(tmp_db, ytd_start, "RH-BV", market_value=180_000.0, margin=45_000.0)

    def test_value_1w_is_net(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        self._seed_history(tmp_db, today)
        snap = load_snapshot_periods()
        row = snap[snap["account_id"] == "RH-BV"].iloc[0]
        # raw market_value 1W ago = 200_000 + 8*100 = 200_800; margin = 50_000
        assert row["value_1w"] == pytest.approx(200_800.0 - 50_000.0)

    def test_value_1m_is_net(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        self._seed_history(tmp_db, today)
        snap = load_snapshot_periods()
        row = snap[snap["account_id"] == "RH-BV"].iloc[0]
        assert row["value_1m"] == pytest.approx(200_000.0 + 32*100 - 50_000.0)

    def test_value_3m_is_net(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        self._seed_history(tmp_db, today)
        snap = load_snapshot_periods()
        row = snap[snap["account_id"] == "RH-BV"].iloc[0]
        assert row["value_3m"] == pytest.approx(200_000.0 + 92*100 - 50_000.0)

    def test_value_1y_is_net(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        self._seed_history(tmp_db, today)
        snap = load_snapshot_periods()
        row = snap[snap["account_id"] == "RH-BV"].iloc[0]
        assert row["value_1y"] == pytest.approx(200_000.0 + 366*100 - 50_000.0)

    def test_no_margin_account_net_equals_mv(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        _write(tmp_db, today, "WEBULL", 50_000.0, margin=0.0)
        _write(tmp_db, today - timedelta(days=8), "WEBULL", 48_000.0, margin=0.0)
        snap = load_snapshot_periods()
        row = snap[snap["account_id"] == "WEBULL"].iloc[0]
        assert row["current_value"] == pytest.approx(50_000.0)
        assert row["value_1w"]      == pytest.approx(48_000.0)

    def test_missing_historical_period_is_null(self, tmp_db, monkeypatch):
        """When no snapshot exists for a period, value should be NULL/NaN."""
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        # Only write today — no history
        _write(tmp_db, today, "SCHWAB", 75_000.0, margin=10_000.0)
        snap = load_snapshot_periods()
        row = snap[snap["account_id"] == "SCHWAB"].iloc[0]
        assert pd.isna(row["value_1w"])
        assert pd.isna(row["value_1m"])

    def test_performance_delta_is_net_vs_net(self, tmp_db, monkeypatch):
        """Current net equity vs 1W-ago net equity — margin excluded from both sides."""
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        _write(tmp_db, today, "RH-BV",
               market_value=300_000.0, margin=100_000.0)   # net = 200_000
        _write(tmp_db, today - timedelta(days=8), "RH-BV",
               market_value=280_000.0, margin=80_000.0)    # net = 200_000 (same)

        snap = load_snapshot_periods()
        row = snap[snap["account_id"] == "RH-BV"].iloc[0]

        current_net = row["current_value"]   # 200_000
        prior_net   = row["value_1w"]        # 200_000

        dollar_change = current_net - prior_net
        assert dollar_change == pytest.approx(0.0), (
            "Margin increase shouldn't look like a loss in performance"
        )

    def test_performance_delta_reflects_real_gain(self, tmp_db, monkeypatch):
        """A genuine market gain shows as positive even with stable margin."""
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        _write(tmp_db, today, "RH-BV",
               market_value=320_000.0, margin=100_000.0)   # net = 220_000
        _write(tmp_db, today - timedelta(days=8), "RH-BV",
               market_value=300_000.0, margin=100_000.0)   # net = 200_000

        snap = load_snapshot_periods()
        row = snap[snap["account_id"] == "RH-BV"].iloc[0]
        dollar_change = row["current_value"] - row["value_1w"]
        assert dollar_change == pytest.approx(20_000.0)


# ── Multi-account ─────────────────────────────────────────────────────────────

class TestMultiAccountSnapshot:
    def test_each_account_has_own_row(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        write_portfolio_snapshot(today.isoformat(), {
            "RH-BV":  {"market_value": 100_000.0, "cost_basis": None, "margin": 30_000.0},
            "WEBULL": {"market_value":  50_000.0, "cost_basis": None, "margin": 10_000.0},
            "SCHWAB": {"market_value":  75_000.0, "cost_basis": None, "margin":  0.0},
        })
        snap = load_snapshot_periods()
        assert len(snap) == 3
        assert set(snap["account_id"]) == {"RH-BV", "WEBULL", "SCHWAB"}

    def test_accounts_without_history_get_null_periods(self, tmp_db, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_db)
        today = date.today()
        # RH-BV has history; WEBULL does not
        _write(tmp_db, today - timedelta(days=10), "RH-BV", 90_000.0, margin=20_000.0)
        write_portfolio_snapshot(today.isoformat(), {
            "RH-BV":  {"market_value": 100_000.0, "cost_basis": None, "margin": 30_000.0},
            "WEBULL": {"market_value":  50_000.0, "cost_basis": None, "margin":  0.0},
        })
        snap = load_snapshot_periods()
        rhbv_row   = snap[snap["account_id"] == "RH-BV"].iloc[0]
        webull_row = snap[snap["account_id"] == "WEBULL"].iloc[0]
        assert not pd.isna(rhbv_row["value_1w"])
        assert pd.isna(webull_row["value_1w"])
