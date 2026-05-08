"""Dashboard capability inventory used to preserve migration parity.

This module is intentionally data-only. It gives CLI, MCP, tests, and future
dashboard implementations a shared contract for the tabs and behaviours that
the current Streamlit dashboard exposes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DASHBOARD_TABS: tuple[str, ...] = (
    "Portfolio",
    "Yearly Summary",
    "By Account",
    "Positions",
    "Transactions",
    "Performance",
    "Broker MCP",
    "Settings",
)


@dataclass(frozen=True)
class DashboardCapability:
    """One user-visible dashboard behaviour that must survive refactors."""

    tab: str
    capability_id: str
    name: str
    description: str
    data_sources: tuple[str, ...]
    required_for_migration: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["data_sources"] = list(self.data_sources)
        return payload


CAPABILITIES: tuple[DashboardCapability, ...] = (
    DashboardCapability(
        "Global Controls",
        "global.date_range_filter",
        "Date range filter",
        "Filters transaction-driven dashboard views by selected date range.",
        ("transactions",),
    ),
    DashboardCapability(
        "Global Controls",
        "global.account_filter",
        "Account filter",
        "Filters transaction-driven dashboard views by selected account IDs.",
        ("transactions", "account_settings"),
    ),
    DashboardCapability(
        "Global Controls",
        "global.include_internal_transfers",
        "Internal transfer toggle",
        "Controls whether internal_transfer rows are included in transaction metrics.",
        ("transactions",),
    ),
    DashboardCapability(
        "Global Controls",
        "global.refresh",
        "Refresh",
        "Clears cached dashboard data and reruns the page.",
        ("streamlit_cache",),
    ),
    DashboardCapability(
        "Portfolio",
        "portfolio.net_worth_banner",
        "Net worth banner",
        "Shows net worth, market value, and margin borrowed from account balances or positions fallback.",
        ("account_balances", "positions", "options_positions", "futures_positions", "crypto_positions", "account_settings"),
    ),
    DashboardCapability(
        "Portfolio",
        "portfolio.transaction_kpis",
        "Transaction KPI row",
        "Shows cash flow, dividends, rewards, costs, and net income for the active filters.",
        ("transactions",),
    ),
    DashboardCapability(
        "Portfolio",
        "portfolio.account_summary",
        "Account summary",
        "Shows broker/account market value, cost basis, margin, and net equity with a total row.",
        ("account_balances", "positions", "options_positions", "futures_positions", "crypto_positions"),
    ),
    DashboardCapability(
        "Portfolio",
        "portfolio.asset_class_breakdown",
        "Asset class breakdown",
        "Breaks market value out by stocks, options, futures, crypto, cash, and total allocation.",
        ("positions", "options_positions", "futures_positions", "crypto_positions", "account_settings"),
    ),
    DashboardCapability(
        "Portfolio",
        "portfolio.futures_by_commodity",
        "Futures by commodity",
        "Groups futures rows by contract root and shows net market value by commodity.",
        ("futures_positions",),
    ),
    DashboardCapability(
        "Portfolio",
        "portfolio.sector_allocation",
        "Sector allocation chart",
        "Shows equity sector allocation with ETF and income ETF buckets collapsed.",
        ("positions",),
    ),
    DashboardCapability(
        "Portfolio",
        "portfolio.positions_by_account",
        "Positions by account",
        "Shows equity and option details grouped under account expanders.",
        ("positions", "options_positions", "transactions"),
    ),
    DashboardCapability(
        "Portfolio",
        "portfolio.sector_summary",
        "Sector summary",
        "Shows market value, cost, P&L, allocation, return, and lifetime dividends by sector.",
        ("positions", "transactions"),
    ),
    DashboardCapability(
        "Yearly Summary",
        "yearly.summary_table",
        "Year-over-year summary",
        "Compares deposits, withdrawals, net cash, income, costs, and net income for prior year, current year, and all time.",
        ("transactions",),
    ),
    DashboardCapability(
        "Yearly Summary",
        "yearly.income_breakdown",
        "Income breakdown by type",
        "Shows dividend and reward subcategory income by prior year, current year, and all time.",
        ("transactions",),
    ),
    DashboardCapability(
        "By Account",
        "by_account.net_cash_flow",
        "Net cash flow by account",
        "Shows account pivot of net cash flow by prior year, current year, and all time.",
        ("transactions",),
    ),
    DashboardCapability(
        "By Account",
        "by_account.div_rewards",
        "Dividends and rewards by account",
        "Shows account pivot of dividends plus rewards by prior year, current year, and all time.",
        ("transactions",),
    ),
    DashboardCapability(
        "By Account",
        "by_account.margin_fees",
        "Margin and fees by account",
        "Shows account pivot of margin interest plus fees by prior year, current year, and all time.",
        ("transactions",),
    ),
    DashboardCapability(
        "By Account",
        "by_account.crypto_flow",
        "Coinbase crypto flow",
        "Shows crypto inflow, outflow, and net movement buckets for external Coinbase movements.",
        ("transactions",),
    ),
    DashboardCapability(
        "Positions",
        "positions.broker_filter",
        "Broker filter",
        "Filters all position sub-tabs by broker.",
        ("transactions", "positions", "options_positions", "futures_positions", "crypto_positions"),
    ),
    DashboardCapability(
        "Positions",
        "positions.equity_subtab",
        "Equity sub-tab",
        "Aggregates equity positions by symbol with market value, cost, P&L, return, dividends, and footer totals.",
        ("positions", "transactions"),
    ),
    DashboardCapability(
        "Positions",
        "positions.options_subtab",
        "Options sub-tab",
        "Shows option contracts by account with underlying, expiry, strike, call/put, quantity, price, market value, and totals.",
        ("options_positions", "transactions"),
    ),
    DashboardCapability(
        "Positions",
        "positions.futures_subtab",
        "Futures sub-tab",
        "Shows futures contracts by account with quantity, price, market value, and net totals.",
        ("futures_positions", "transactions"),
    ),
    DashboardCapability(
        "Positions",
        "positions.crypto_subtab",
        "Crypto sub-tab",
        "Shows crypto holdings with quantity, price, cost basis, market value, and P&L totals.",
        ("crypto_positions", "transactions"),
    ),
    DashboardCapability(
        "Transactions",
        "transactions.filters",
        "Transaction filters",
        "Filters transaction table by category, broker, year, and description search.",
        ("transactions",),
    ),
    DashboardCapability(
        "Transactions",
        "transactions.table",
        "Transaction table",
        "Shows date, account, broker, category, subcategory, amount, currency, symbol, and description.",
        ("transactions",),
    ),
    DashboardCapability(
        "Transactions",
        "transactions.csv_download",
        "CSV download",
        "Exports the currently filtered transaction table as CSV.",
        ("transactions",),
    ),
    DashboardCapability(
        "Performance",
        "performance.portfolio_summary",
        "Portfolio summary",
        "Shows current value, 1-week prior value, dollar change, and percent change by account plus cash and total rows.",
        ("portfolio_snapshots", "positions", "account_settings"),
    ),
    DashboardCapability(
        "Performance",
        "performance.returns",
        "Portfolio returns",
        "Shows 1-week, 1-month, 3-month, YTD, and 1-year returns by account and total.",
        ("portfolio_snapshots", "positions", "account_settings"),
    ),
    DashboardCapability(
        "Performance",
        "performance.margin_adjusted_net_value",
        "Margin-adjusted net value",
        "Calculates account performance from current market value minus borrowed margin.",
        ("positions", "portfolio_snapshots"),
    ),
    DashboardCapability(
        "Broker MCP",
        "broker_mcp.health_check",
        "MCP health check",
        "Runs configured broker MCP health checks and displays status/tool counts.",
        ("mcp_health",),
    ),
    DashboardCapability(
        "Broker MCP",
        "broker_mcp.cli_module_review",
        "CLI module review",
        "Shows which broker CLI modules are safe for dashboard button usage or should stay terminal-first.",
        ("static_dashboard_metadata",),
    ),
    DashboardCapability(
        "Broker MCP",
        "broker_mcp.live_checks",
        "Live broker checks",
        "Provides explicit button-triggered Coinbase and Tradier balance checks.",
        ("coinbase_api", "tradier_api"),
    ),
    DashboardCapability(
        "Settings",
        "settings.cash_balance",
        "Cash balance editor",
        "Edits the cash and savings balance used in portfolio net worth.",
        ("account_settings",),
    ),
    DashboardCapability(
        "Settings",
        "settings.account_settings",
        "Account settings editor",
        "Edits account active state, price source, and margin fallback settings.",
        ("account_settings",),
    ),
    DashboardCapability(
        "Settings",
        "settings.futures_equity_override",
        "Futures equity override",
        "Edits Schwab futures equity override and applies the adjustment row immediately.",
        ("account_settings", "futures_positions"),
    ),
    DashboardCapability(
        "Settings",
        "settings.coinbase_cost_basis_adjustment",
        "Coinbase cost basis adjustment",
        "Edits the Coinbase cost basis adjustment applied to broker-reported basis.",
        ("account_settings",),
    ),
    DashboardCapability(
        "Settings",
        "settings.save_all",
        "Save all settings",
        "Persists settings changes, clears cached data, and reruns the dashboard.",
        ("account_settings", "futures_positions", "streamlit_cache"),
    ),
)


def list_dashboard_capabilities() -> list[dict[str, Any]]:
    """Return all dashboard capabilities as JSON-serializable dictionaries."""

    return [capability.to_dict() for capability in CAPABILITIES]


def capabilities_by_tab() -> dict[str, list[dict[str, Any]]]:
    """Group dashboard capabilities by current Streamlit tab name."""

    grouped: dict[str, list[dict[str, Any]]] = {
        tab: [] for tab in ("Global Controls", *DASHBOARD_TABS)
    }
    for capability in CAPABILITIES:
        grouped.setdefault(capability.tab, []).append(capability.to_dict())
    return grouped


def tab_capability_counts() -> dict[str, int]:
    """Return capability counts for each tab/global section."""

    return {tab: len(items) for tab, items in capabilities_by_tab().items()}
