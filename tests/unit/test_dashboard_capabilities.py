# -*- coding: utf-8 -*-
"""Tests for the dashboard capability parity contract."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.services.dashboard_capabilities import (
    DASHBOARD_TABS,
    capabilities_by_tab,
    list_dashboard_capabilities,
    tab_capability_counts,
)


def test_current_dashboard_tabs_are_registered_in_order():
    assert DASHBOARD_TABS == (
        "Portfolio",
        "Yearly Summary",
        "By Account",
        "Positions",
        "Transactions",
        "Performance",
        "Broker MCP",
        "Settings",
    )


def test_each_dashboard_tab_has_required_capabilities():
    grouped = capabilities_by_tab()

    for tab in DASHBOARD_TABS:
        assert grouped[tab], f"{tab} has no registered capabilities"


def test_positions_tab_preserves_all_asset_class_subtabs():
    capability_ids = {item["capability_id"] for item in capabilities_by_tab()["Positions"]}

    assert {
        "positions.equity_subtab",
        "positions.options_subtab",
        "positions.futures_subtab",
        "positions.crypto_subtab",
    } <= capability_ids


def test_dashboard_capabilities_include_critical_settings_and_performance_paths():
    capability_ids = {item["capability_id"] for item in list_dashboard_capabilities()}

    assert {
        "performance.margin_adjusted_net_value",
        "settings.futures_equity_override",
        "settings.coinbase_cost_basis_adjustment",
        "broker_mcp.health_check",
    } <= capability_ids


def test_dashboard_capability_payload_is_json_serializable():
    payload = {
        "tabs": list(DASHBOARD_TABS),
        "capability_counts": tab_capability_counts(),
        "capabilities": list_dashboard_capabilities(),
    }

    encoded = json.dumps(payload)

    assert "positions.options_subtab" in encoded
