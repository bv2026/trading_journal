# -*- coding: utf-8 -*-
"""Unit tests for the unified non-interactive CLI."""
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.cli import main as cli_main


def _json_from_stdout(capsys):
    return json.loads(capsys.readouterr().out)


def test_portfolio_summary_outputs_service_payload(monkeypatch, capsys):
    monkeypatch.setattr(
        cli_main.portfolio,
        "get_portfolio_summary",
        lambda **kwargs: {
            "label": "All accounts · All years",
            "net_cash_flow": 10.0,
            "transaction_count": 2,
        },
    )

    code = cli_main.main(["portfolio", "summary", "--year", "2026", "--no-live"])

    assert code == 0
    payload = _json_from_stdout(capsys)
    assert payload["transaction_count"] == 2


def test_portfolio_summary_empty_returns_receipt(monkeypatch, capsys):
    monkeypatch.setattr(cli_main.portfolio, "get_portfolio_summary", lambda **kwargs: None)

    code = cli_main.main(["portfolio", "summary"])

    assert code == 1
    payload = _json_from_stdout(capsys)
    assert payload["status"] == "empty"
    assert payload["operation"] == "portfolio.summary"


def test_transactions_command_routes_filters(monkeypatch, capsys):
    seen = {}

    def fake_query(**kwargs):
        seen.update(kwargs)
        return {"count": 0, "transactions": []}

    monkeypatch.setattr(cli_main.portfolio, "query_transactions", fake_query)

    code = cli_main.main([
        "transactions",
        "--category", "dividend",
        "--account", "RH-BV",
        "--year", "2026",
        "--search", "AAPL",
        "--limit", "5",
    ])

    assert code == 0
    assert seen == {
        "category": "dividend",
        "account_id": "RH-BV",
        "year": 2026,
        "search": "AAPL",
        "limit": 5,
    }
    assert _json_from_stdout(capsys)["count"] == 0


def test_ingest_snapshot_writes_receipt(monkeypatch, capsys):
    monkeypatch.setattr(cli_main.db, "init_db", lambda: None)
    monkeypatch.setattr(
        cli_main.ingest_module,
        "_compute_snapshot_map",
        lambda: {"RH-BV": {"market_value": 100.0, "cost_basis": None, "margin": 0.0}},
    )
    written = {}

    def fake_write(snapshot_date, snap_map):
        written["snapshot_date"] = snapshot_date
        written["snap_map"] = snap_map

    monkeypatch.setattr(cli_main.db, "write_portfolio_snapshot", fake_write)

    code = cli_main.main(["ingest", "snapshot", "--as-of", "2026-05-08"])

    assert code == 0
    assert written["snapshot_date"] == "2026-05-08"
    payload = _json_from_stdout(capsys)
    assert payload["operation"] == "ingest.snapshot"
    assert payload["account_count"] == 1


def test_account_cash_set_outputs_receipt(monkeypatch, capsys):
    calls = {}
    monkeypatch.setattr(cli_main.db, "init_db", lambda: None)
    monkeypatch.setattr(
        cli_main.db,
        "upsert_cash_balance",
        lambda amount, account_id="CASH": calls.update(amount=amount, account_id=account_id),
    )

    code = cli_main.main(["account", "cash", "set", "1234.56", "--account", "SAVINGS"])

    assert code == 0
    assert calls == {"amount": 1234.56, "account_id": "SAVINGS"}
    payload = _json_from_stdout(capsys)
    assert payload["operation"] == "account.cash.set"
    assert payload["balance"] == 1234.56


def test_account_margin_set_suppresses_legacy_print(monkeypatch, capsys):
    def fake_set_margin(account, amount):
        print("legacy noisy output")
        return {"account_id": account, "amount": amount, "action": "set+persisted"}

    monkeypatch.setattr(cli_main.mcp_ingest, "set_margin", fake_set_margin)

    code = cli_main.main(["account", "margin", "set", "TRADIER", "100"])

    assert code == 0
    payload = _json_from_stdout(capsys)
    assert payload["operation"] == "account.margin.set"
    assert payload["account_id"] == "TRADIER"
    assert "legacy noisy output" not in json.dumps(payload)


def test_health_outputs_row_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli_main,
        "check_mcp_health",
        lambda: [{"Broker": "Webull", "Status": "OK"}],
    )

    code = cli_main.main(["health"])

    assert code == 0
    payload = _json_from_stdout(capsys)
    assert payload["operation"] == "health"
    assert payload["row_count"] == 1


def test_dashboard_capabilities_outputs_parity_contract(capsys):
    code = cli_main.main(["dashboard", "capabilities"])

    assert code == 0
    payload = _json_from_stdout(capsys)
    assert payload["operation"] == "dashboard.capabilities"
    assert payload["tabs"] == [
        "Portfolio",
        "Yearly Summary",
        "By Account",
        "Positions",
        "Transactions",
        "Performance",
        "Broker MCP",
        "Settings",
    ]
    assert payload["capability_counts"]["Positions"] >= 4
    capability_ids = {item["capability_id"] for item in payload["capabilities"]}
    assert "positions.crypto_subtab" in capability_ids
    assert "settings.save_all" in capability_ids
