"""Positions tab calculations shared by dashboard and future UI surfaces."""
from __future__ import annotations

from typing import Any

import pandas as pd


EQUITY_COLUMNS = [
    "Ticker", "Name", "sector", "Market_Value", "Total_Cost",
    "PnL", "Return_%", "Dividends",
]
OPTION_COLUMNS = [
    "symbol", "underlying", "expiry", "strike", "call_put",
    "qty", "price", "MARKET VALUE", "description",
]
FUTURES_COLUMNS = ["Ticker", "underlying", "description", "qty", "price", "MARKET VALUE"]
CRYPTO_COLUMNS = ["Ticker", "name", "qty", "price", "cost_basis", "MARKET VALUE"]


def broker_filter_options(transactions: pd.DataFrame) -> list[str]:
    """Return sorted broker names used by the Positions tab broker filter."""

    if transactions.empty or "broker" not in transactions.columns:
        return []
    return sorted(transactions["broker"].dropna().unique())


def account_broker_map(transactions: pd.DataFrame) -> pd.Series:
    """Return account_id -> broker mapping for position filtering."""

    if transactions.empty or not {"account_id", "broker"} <= set(transactions.columns):
        return pd.Series(dtype=object)
    return transactions[["account_id", "broker"]].drop_duplicates().set_index("account_id")["broker"]


def filter_positions_by_broker(
    frame: pd.DataFrame,
    account_to_broker: pd.Series,
    selected_brokers: list[str],
) -> pd.DataFrame:
    """Filter a dashboard position frame by selected broker names."""

    if frame.empty or "Account" not in frame.columns:
        return frame
    return frame[frame["Account"].map(account_to_broker).isin(selected_brokers)].copy()


def equity_positions_summary(
    positions: pd.DataFrame,
    transactions: pd.DataFrame,
) -> dict[str, Any]:
    """Aggregate the Positions > Equity sub-tab by ticker."""

    if positions.empty:
        return {
            "holdings": pd.DataFrame(columns=EQUITY_COLUMNS),
            "totals": _equity_totals(pd.DataFrame()),
        }

    source = positions[positions["Ticker"].str.upper() != "MARGIN"].copy()
    for column in ["COST", "MARKET VALUE", "totalReturn"]:
        if column in source.columns:
            source[column] = pd.to_numeric(source[column], errors="coerce")

    holdings = (
        source.groupby(["Ticker", "Name", "sector"])
        .agg(
            Market_Value=("MARKET VALUE", "sum"),
            Total_Cost=("COST", "sum"),
            PnL=("totalReturn", "sum"),
        )
        .reset_index()
        .sort_values("Market_Value", ascending=False)
    )
    holdings["Return_%"] = (
        holdings["PnL"] / holdings["Total_Cost"].replace(0, float("nan")) * 100
    ).round(2)

    dividends = _dividends_by_symbol(transactions)
    holdings = holdings.merge(dividends, on="Ticker", how="left")
    holdings["Dividends"] = holdings["Dividends"].fillna(0)

    return {
        "holdings": holdings[EQUITY_COLUMNS],
        "totals": _equity_totals(holdings),
    }


def _dividends_by_symbol(transactions: pd.DataFrame) -> pd.DataFrame:
    if transactions.empty or not {"category", "symbol", "amount"} <= set(transactions.columns):
        return pd.DataFrame(columns=["Ticker", "Dividends"])
    return (
        transactions[transactions["category"] == "dividend"]
        .groupby("symbol")["amount"]
        .sum()
        .reset_index()
        .rename(columns={"symbol": "Ticker", "amount": "Dividends"})
    )


def _equity_totals(holdings: pd.DataFrame) -> dict[str, float]:
    if holdings.empty:
        return {
            "market_value": 0.0,
            "total_cost": 0.0,
            "pnl": 0.0,
            "return_pct": 0.0,
            "dividends": 0.0,
        }
    market_value = float(holdings["Market_Value"].fillna(0).sum())
    total_cost = float(holdings["Total_Cost"].fillna(0).sum())
    pnl = float(holdings["PnL"].fillna(0).sum())
    dividends = float(holdings["Dividends"].fillna(0).sum())
    return {
        "market_value": market_value,
        "total_cost": total_cost,
        "pnl": pnl,
        "return_pct": pnl / total_cost * 100 if total_cost else 0.0,
        "dividends": dividends,
    }


def option_account_groups(options: pd.DataFrame) -> dict[str, Any]:
    """Prepare account groups and totals for the Options sub-tab."""

    prepared = _coerce_numeric(options, ["qty", "price", "MARKET VALUE", "strike"])
    return {
        "total_contracts": len(prepared),
        "total_market_value": _market_value(prepared),
        "groups": _account_groups(
            prepared,
            display_columns=OPTION_COLUMNS,
            label_kind="contracts",
            money_label="MV",
            signed=False,
        ),
    }


def futures_account_groups(futures: pd.DataFrame) -> dict[str, Any]:
    """Prepare account groups and totals for the Futures sub-tab."""

    prepared = _coerce_numeric(futures, ["qty", "price", "MARKET VALUE"])
    return {
        "total_contracts": len(prepared),
        "net_market_value": _market_value(prepared),
        "groups": _account_groups(
            prepared,
            display_columns=FUTURES_COLUMNS,
            label_kind="contracts",
            money_label="Net MV",
            signed=True,
        ),
    }


def crypto_positions_summary(crypto: pd.DataFrame) -> dict[str, Any]:
    """Prepare table and totals for the Crypto sub-tab."""

    prepared = _coerce_numeric(crypto, ["qty", "price", "cost_basis", "MARKET VALUE"])
    total_mv = _market_value(prepared)
    total_cost = (
        float(pd.to_numeric(prepared["cost_basis"], errors="coerce").fillna(0).sum())
        if "cost_basis" in prepared.columns else 0.0
    )
    show_cols = _available_columns(prepared, CRYPTO_COLUMNS)
    table = (
        prepared[show_cols]
        .sort_values("MARKET VALUE", ascending=False)
        .reset_index(drop=True)
        if show_cols else pd.DataFrame()
    )
    return {
        "table": table,
        "show_columns": show_cols,
        "holding_count": len(prepared),
        "market_value": total_mv,
        "cost_basis": total_cost,
        "pnl": total_mv - total_cost,
    }


def _coerce_numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    prepared = frame.copy()
    for column in columns:
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    return prepared


def _market_value(frame: pd.DataFrame) -> float:
    if frame.empty or "MARKET VALUE" not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame["MARKET VALUE"], errors="coerce").fillna(0).sum())


def _account_groups(
    frame: pd.DataFrame,
    *,
    display_columns: list[str],
    label_kind: str,
    money_label: str,
    signed: bool,
) -> list[dict[str, Any]]:
    groups = []
    if frame.empty:
        return groups

    for account, group in frame.groupby("Account"):
        account_mv = _market_value(group)
        money = f"${account_mv:+,.0f}" if signed else f"${account_mv:,.0f}"
        show_cols = _available_columns(group, display_columns)
        table = (
            group[show_cols]
            .sort_values("MARKET VALUE", ascending=False)
            .reset_index(drop=True)
        )
        groups.append({
            "account": account,
            "market_value": account_mv,
            "show_columns": show_cols,
            "table": table,
            "label": f"**{account}** — {len(group)} {label_kind} · {money_label} {money}",
        })
    return groups


def _available_columns(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    return [column for column in columns if column in frame.columns]
