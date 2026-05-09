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


def test_operations_status_endpoint_returns_accounts_and_health(monkeypatch):
    monkeypatch.setattr(
        api_main.db,
        "load_account_operations_status",
        lambda: api_main.pd.DataFrame([
            {
                "account_id": "RH-BV",
                "broker": "robinhood",
                "account_type": "equity",
                "active": 1,
                "raw_sources": "mcp",
                "eq_last_ingested_at": "2026-05-09T20:00:00Z",
                "opt_last_ingested_at": None,
                "fut_last_ingested_at": None,
                "cry_last_ingested_at": None,
                "txn_last_created_at": "2026-05-09T20:00:00Z",
                "last_snapshot_date": "2026-05-09",
            }
        ]),
    )
    monkeypatch.setattr(
        api_main,
        "check_mcp_health",
        lambda: [{"Broker": "Robinhood", "Status": "OK", "Tools": 10}],
    )

    response = _client().get("/operations/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["operation"] == "operations.status"
    assert payload["data"]["accounts"][0]["account_id"] == "RH-BV"
    assert payload["data"]["accounts"][0]["source_signal"] == "MCP"
    assert payload["data"]["health"][0]["Broker"] == "Robinhood"


def test_dashboard_portfolio_endpoint_serializes_dashboard_sections(monkeypatch):
    monkeypatch.setattr(api_main.portfolio, "load_transactions_filtered", lambda: api_main.pd.DataFrame([
        {"date": api_main.pd.Timestamp("2026-01-01"), "account_id": "RH-BV", "broker": "robinhood", "category": "dividend", "subcategory": "cash_div", "amount": 25.0, "symbol": "AAPL"},
    ]))
    monkeypatch.setattr(api_main, "load_all_positions", lambda: api_main.pd.DataFrame([
        {"Account": "RH-BV", "Ticker": "AAPL", "MARKET VALUE": 1200.0},
    ]))
    monkeypatch.setattr(api_main, "load_positions_from_db", lambda: api_main.pd.DataFrame([
        {"Account": "RH-BV", "Ticker": "AAPL", "Name": "Apple", "Shares": 10, "Cost_Basis": 100.0, "PRICE": 120.0, "COST": 1000.0, "MARKET VALUE": 1200.0, "totalReturn": 200.0, "sector": "Technology"},
    ]))
    monkeypatch.setattr(api_main, "load_options_from_db", lambda: api_main.pd.DataFrame())
    monkeypatch.setattr(api_main, "load_futures_from_db", lambda: api_main.pd.DataFrame())
    monkeypatch.setattr(api_main, "load_crypto_from_db", lambda: api_main.pd.DataFrame())
    monkeypatch.setattr(api_main.db, "load_account_balances", lambda: api_main.pd.DataFrame())
    monkeypatch.setattr(api_main.db, "get_cash_balance", lambda: 100.0)
    monkeypatch.setattr(api_main.db, "get_accounts_by_type", lambda account_type: [])

    response = _client().get("/dashboard/portfolio")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operation"] == "dashboard.portfolio"
    data = payload["data"]
    assert data["net_worth"]["net_worth"] == 1300.0
    assert data["transaction_kpis"]["Div+Rewards"] == 25.0
    assert data["account_summary"]
    assert data["asset_class_breakdown"]
    assert data["sector_summary"]


def test_dashboard_performance_endpoint_serializes_summary_and_returns(monkeypatch):
    monkeypatch.setattr(api_main, "load_all_positions", lambda: api_main.pd.DataFrame([
        {"Account": "RH-BV", "Ticker": "AAPL", "MARKET VALUE": 1200.0},
    ]))
    monkeypatch.setattr(api_main.db, "load_snapshot_periods", lambda: api_main.pd.DataFrame([
        {"account_id": "RH-BV", "value_1w": 1000.0, "value_1m": 900.0, "value_3m": None, "value_1y": None, "value_ytd_start": 800.0},
    ]))
    monkeypatch.setattr(api_main.db, "get_cash_balance", lambda: 100.0)

    response = _client().get("/dashboard/performance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operation"] == "dashboard.performance"
    assert payload["data"]["summary"][0]["Account"] == "RH-BV"
    assert payload["data"]["returns"][0]["1-Week"] == 20.0
    assert payload["data"]["has_snapshots"] is True


def test_dashboard_yearly_summary_endpoint_serializes_tables(monkeypatch):
    monkeypatch.setattr(api_main.portfolio, "load_transactions_filtered", lambda: api_main.pd.DataFrame([
        {"date": api_main.pd.Timestamp("2025-01-01"), "account_id": "RH-BV", "category": "cash_flow", "subcategory": "deposit", "amount": 100.0},
        {"date": api_main.pd.Timestamp("2026-01-01"), "account_id": "RH-BV", "category": "dividend", "subcategory": "cash_div", "amount": 25.0},
    ]))

    response = _client().get("/dashboard/yearly-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operation"] == "dashboard.yearly_summary"
    assert payload["data"]["summary"][0]["Metric"] == "Deposits"
    assert payload["data"]["income_breakdown"][0]["Type"] == "cash_div"


def test_dashboard_by_account_endpoint_serializes_pivots_and_crypto_flow(monkeypatch):
    monkeypatch.setattr(api_main.portfolio, "load_transactions_filtered", lambda: api_main.pd.DataFrame([
        {"date": api_main.pd.Timestamp("2026-01-01"), "account_id": "RH-BV", "category": "dividend", "subcategory": "cash_div", "amount": 25.0},
        {"date": api_main.pd.Timestamp("2026-01-02"), "account_id": "COINBASE", "category": "crypto_flow", "subcategory": "usd_deposit", "amount": 100.0},
        {"date": api_main.pd.Timestamp("2026-01-03"), "account_id": "COINBASE", "category": "crypto_flow", "subcategory": "crypto_sent", "amount": -40.0},
    ]))

    response = _client().get("/dashboard/by-account")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operation"] == "dashboard.by_account"
    assert payload["data"]["net_cash_flow"]
    assert payload["data"]["div_rewards"]
    assert payload["data"]["margin_fees"]
    assert payload["data"]["crypto_flow"]["net"] == 60.0


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
