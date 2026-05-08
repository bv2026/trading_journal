"""Performance tab calculations shared by dashboard and future UI surfaces."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


SNAPSHOT_COLUMNS = [
    "account_id",
    "value_1w",
    "value_1m",
    "value_3m",
    "value_1y",
    "value_ytd_start",
]


def performance_tables(
    *,
    all_positions: pd.DataFrame,
    snapshot_periods: pd.DataFrame,
    cash_balance: float,
) -> dict[str, Any]:
    """Build the Performance tab summary and returns tables."""

    perf = _performance_frame(all_positions, snapshot_periods)
    if perf.empty:
        return {
            "summary": pd.DataFrame(),
            "returns": pd.DataFrame(),
            "has_snapshots": not snapshot_periods.empty,
        }

    perf["net_value"] = perf["current_value"] - perf["margin"]
    total_net = float(perf["net_value"].sum() + cash_balance)

    summary_rows = []
    for _, row in perf.iterrows():
        net = row["net_value"]
        value_1w = row.get("value_1w", float("nan"))
        summary_rows.append({
            "Account": row["account_id"],
            "Current Value": net,
            "1W Ago": value_1w,
            "$ Change": change(net, value_1w),
            "% Change": pct_return(net, value_1w),
        })

    if cash_balance > 0:
        summary_rows.append({
            "Account": "CASH",
            "Current Value": cash_balance,
            "1W Ago": cash_balance,
            "$ Change": 0.0,
            "% Change": 0.0,
        })

    total_1w = _total_prior(perf, "value_1w") + (cash_balance if cash_balance > 0 else 0.0)
    summary_rows.append({
        "Account": "TOTAL",
        "Current Value": total_net,
        "1W Ago": total_1w,
        "$ Change": change(total_net, total_1w),
        "% Change": pct_return(total_net, total_1w),
    })

    return_rows = []
    for _, row in perf.iterrows():
        net = row["net_value"]
        return_rows.append({
            "Account": row["account_id"],
            "1-Week": pct_return(net, row.get("value_1w", float("nan"))),
            "1-Month": pct_return(net, row.get("value_1m", float("nan"))),
            "3-Month": pct_return(net, row.get("value_3m", float("nan"))),
            "YTD": pct_return(net, row.get("value_ytd_start", float("nan"))),
            "1-Year": pct_return(net, row.get("value_1y", float("nan"))),
        })

    return_rows.append({
        "Account": "TOTAL",
        "1-Week": pct_return(total_net, _total_prior(perf, "value_1w")),
        "1-Month": pct_return(total_net, _total_prior(perf, "value_1m")),
        "3-Month": pct_return(total_net, _total_prior(perf, "value_3m")),
        "YTD": pct_return(total_net, _total_prior(perf, "value_ytd_start")),
        "1-Year": pct_return(total_net, _total_prior(perf, "value_1y")),
    })

    return {
        "summary": pd.DataFrame(summary_rows),
        "returns": pd.DataFrame(return_rows),
        "has_snapshots": not snapshot_periods.empty,
    }


def _performance_frame(all_positions: pd.DataFrame, snapshot_periods: pd.DataFrame) -> pd.DataFrame:
    if all_positions.empty:
        return pd.DataFrame()

    is_margin = all_positions["Ticker"].str.upper() == "MARGIN"
    live_mv = (
        all_positions[~is_margin]
        .groupby("Account")["MARKET VALUE"]
        .sum()
        .reset_index()
        .rename(columns={"Account": "account_id", "MARKET VALUE": "current_value"})
    )
    live_mv["current_value"] = pd.to_numeric(live_mv["current_value"], errors="coerce")

    margin_mv = (
        all_positions[is_margin]
        .groupby("Account")["MARKET VALUE"]
        .sum()
        .abs()
        .reset_index()
        .rename(columns={"Account": "account_id", "MARKET VALUE": "margin"})
    )

    if not snapshot_periods.empty:
        available = [column for column in SNAPSHOT_COLUMNS if column in snapshot_periods.columns]
        perf = live_mv.merge(snapshot_periods[available], on="account_id", how="left")
    else:
        perf = live_mv.copy()
        for column in SNAPSHOT_COLUMNS:
            if column != "account_id":
                perf[column] = float("nan")

    perf = perf.merge(margin_mv, on="account_id", how="left")
    perf["margin"] = pd.to_numeric(perf["margin"], errors="coerce").fillna(0.0)
    return perf


def pct_return(current, prior) -> float:
    """Return percent change or NaN using the current dashboard behavior."""

    if pd.isna(prior) or prior == 0:
        return float("nan")
    return (current - prior) / prior * 100


def change(current, prior) -> float:
    """Return dollar change or NaN using the current dashboard behavior."""

    return float("nan") if pd.isna(prior) else current - prior


def _total_prior(perf: pd.DataFrame, column: str) -> float:
    valid = perf[column].dropna()
    return valid.sum() if not valid.empty else float("nan")


def is_nan(value) -> bool:
    """Small helper for tests and callers that need to check NaN values."""

    return isinstance(value, float) and math.isnan(value)
