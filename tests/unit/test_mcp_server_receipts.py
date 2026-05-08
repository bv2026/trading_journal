# -*- coding: utf-8 -*-
"""Unit tests for structured MCP tool receipts."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src import mcp_server


def _loads(text: str) -> dict:
    return json.loads(text)


def test_portfolio_summary_returns_structured_envelope(monkeypatch):
    monkeypatch.setattr(
        mcp_server._portfolio,
        "get_portfolio_summary",
        lambda **kwargs: {"transaction_count": 3},
    )

    payload = _loads(mcp_server.get_portfolio_summary(year=2026))

    assert payload["status"] == "ok"
    assert payload["operation"] == "portfolio.summary"
    assert payload["data"]["transaction_count"] == 3
    assert payload["filters"]["year"] == 2026
    assert payload["warnings"] == []
    assert payload["errors"] == []


def test_portfolio_summary_empty_returns_empty_receipt(monkeypatch):
    monkeypatch.setattr(mcp_server._portfolio, "get_portfolio_summary", lambda **kwargs: None)

    payload = _loads(mcp_server.get_portfolio_summary())

    assert payload["status"] == "empty"
    assert payload["operation"] == "portfolio.summary"
    assert "Run run_ingest" in payload["message"]


def test_set_margin_returns_receipt_and_captures_legacy_stdout(monkeypatch):
    def fake_set_margin(account_id, amount):
        print("legacy margin output")
        return {"account_id": account_id.upper(), "amount": amount, "action": "set+persisted"}

    monkeypatch.setattr(mcp_server._ingest, "set_margin", fake_set_margin)

    payload = _loads(mcp_server.set_margin("tradier", 123.0))

    assert payload["operation"] == "account.margin.set"
    assert payload["data"]["account_id"] == "TRADIER"
    assert payload["data"]["amount"] == 123.0
    assert payload["output"] == "legacy margin output"


def test_refresh_positions_returns_partial_on_broker_error(monkeypatch):
    def fake_write_tradier(**kwargs):
        raise ValueError("bad payload")

    monkeypatch.setattr(mcp_server._ingest, "write_tradier", fake_write_tradier)

    payload = _loads(mcp_server.refresh_positions(tradier_positions_json='{"positions": []}'))

    assert payload["status"] == "partial"
    assert payload["operation"] == "positions.refresh"
    assert payload["brokers_requested"] == ["TRADIER"]
    assert payload["data"]["TRADIER"]["error"] == "bad payload"
    assert payload["errors"] == ["TRADIER: bad payload"]


def test_refresh_positions_empty_payload_warns(monkeypatch):
    monkeypatch.setattr(
        "src.enrichment.enrich_sectors",
        lambda: 0,
    )

    payload = _loads(mcp_server.refresh_positions())

    assert payload["status"] == "ok"
    assert payload["operation"] == "positions.refresh"
    assert payload["broker_count"] == 0
    assert payload["warnings"] == [
        "No broker payloads were provided; no position rows were refreshed."
    ]


def test_run_ingest_success_receipt(monkeypatch):
    class Result:
        returncode = 0
        stdout = "done"
        stderr = ""

    monkeypatch.setattr(mcp_server.subprocess, "run", lambda *args, **kwargs: Result())

    payload = _loads(mcp_server.run_ingest(reset=True))

    assert payload["status"] == "ok"
    assert payload["operation"] == "ingest.csv"
    assert payload["reset"] is True
    assert payload["output"] == "done"


def test_run_ingest_error_receipt(monkeypatch):
    class Result:
        returncode = 2
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(mcp_server.subprocess, "run", lambda *args, **kwargs: Result())

    payload = _loads(mcp_server.run_ingest())

    assert payload["status"] == "error"
    assert payload["operation"] == "ingest.csv"
    assert payload["returncode"] == 2
    assert payload["errors"] == ["boom"]
