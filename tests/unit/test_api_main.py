# -*- coding: utf-8 -*-
"""Tests for the read-only FastAPI backend."""
from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api import main as api_main


def _client() -> TestClient:
    return TestClient(api_main.create_app())


def test_health_endpoint_returns_receipt():
    response = _client().get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["operation"] == "api.health"
    assert payload["data"]["ready"] is True


def test_dashboard_capabilities_endpoint_exposes_tab_contract():
    response = _client().get("/dashboard/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operation"] == "dashboard.capabilities"
    assert payload["data"]["tab_count"] == 8
    assert "Positions" in payload["data"]["tabs"]
    capability_ids = {
        item["capability_id"]
        for item in payload["data"]["capabilities"]
    }
    assert "positions.crypto_subtab" in capability_ids


def test_portfolio_summary_endpoint_routes_query_params(monkeypatch):
    seen = {}

    def fake_summary(**kwargs):
        seen.update(kwargs)
        return {"label": "RH-BV 2026", "transaction_count": 3}

    monkeypatch.setattr(api_main.portfolio, "get_portfolio_summary", fake_summary)

    response = _client().get(
        "/portfolio/summary",
        params={"year": 2026, "account": "RH-BV", "include_live_net_worth": "false"},
    )

    assert response.status_code == 200
    assert seen == {
        "year": 2026,
        "account_id": "RH-BV",
        "include_live_net_worth": False,
    }
    payload = response.json()
    assert payload["operation"] == "portfolio.summary"
    assert payload["data"]["transaction_count"] == 3


def test_positions_endpoint_returns_empty_receipt(monkeypatch):
    monkeypatch.setattr(api_main.portfolio, "get_positions_report", lambda **kwargs: None)

    response = _client().get("/portfolio/positions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "empty"
    assert payload["operation"] == "portfolio.positions"
    assert payload["warnings"]


def test_yearly_summary_endpoint_routes_account_param(monkeypatch):
    seen = {}

    def fake_yearly_summary(**kwargs):
        seen.update(kwargs)
        return [{"label": "2026", "net_income": 42.0}]

    monkeypatch.setattr(api_main.portfolio, "get_yearly_summary", fake_yearly_summary)

    response = _client().get("/portfolio/yearly-summary", params={"account": "RH-BV"})

    assert response.status_code == 200
    assert seen == {"account_id": "RH-BV"}
    payload = response.json()
    assert payload["operation"] == "portfolio.yearly_summary"
    assert payload["count"] == 1
    assert payload["data"][0]["label"] == "2026"


def test_account_summary_endpoint_routes_year_param(monkeypatch):
    seen = {}

    def fake_account_summary(**kwargs):
        seen.update(kwargs)
        return [{"label": "SCHWAB", "broker": "schwab", "net_income": 99.0}]

    monkeypatch.setattr(api_main.portfolio, "get_account_summary", fake_account_summary)

    response = _client().get("/portfolio/account-summary", params={"year": 2026})

    assert response.status_code == 200
    assert seen == {"year": 2026}
    payload = response.json()
    assert payload["operation"] == "portfolio.account_summary"
    assert payload["count"] == 1
    assert payload["data"][0]["label"] == "SCHWAB"


def test_transactions_endpoint_enforces_limit_and_returns_payload(monkeypatch):
    seen = {}

    def fake_transactions(**kwargs):
        seen.update(kwargs)
        return {"count": 1, "transactions": [{"symbol": "AAPL"}]}

    monkeypatch.setattr(api_main.portfolio, "query_transactions", fake_transactions)

    response = _client().get(
        "/transactions",
        params={"category": "dividend", "account": "RH-BV", "year": 2026, "search": "AAPL", "limit": 5},
    )

    assert response.status_code == 200
    assert seen == {
        "category": "dividend",
        "account_id": "RH-BV",
        "year": 2026,
        "search": "AAPL",
        "limit": 5,
    }
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"]["transactions"][0]["symbol"] == "AAPL"


def test_performance_endpoint_returns_count(monkeypatch):
    monkeypatch.setattr(
        api_main.portfolio,
        "get_performance_report",
        lambda account_id=None: [{"account_id": "TOTAL", "current_value": 100.0}],
    )

    response = _client().get("/portfolio/performance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operation"] == "portfolio.performance"
    assert payload["count"] == 1
