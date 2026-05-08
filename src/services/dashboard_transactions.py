"""Yearly Summary and By Account dashboard calculations."""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import pandas as pd

from src.metrics import compute_metrics, net_income


MetricFn = Callable[[dict], float]

METRIC_DEFS: tuple[tuple[str, MetricFn], ...] = (
    ("Deposits", lambda metrics: metrics["deposits"]),
    ("Withdrawals", lambda metrics: metrics["withdrawals"]),
    ("Net Cash", lambda metrics: metrics["net_cash"]),
    ("Dividends", lambda metrics: metrics["dividends"]),
    ("Rewards", lambda metrics: metrics["rewards"]),
    ("Div + Rewards", lambda metrics: metrics["dividends"] + metrics["rewards"]),
    ("Margin Interest", lambda metrics: metrics["margin_int"]),
    ("Fees", lambda metrics: metrics["fees"]),
    ("Net Income", net_income),
)

CRYPTO_INFLOW_SUBS: tuple[str, ...] = (
    "usd_deposit",
    "bank_purchase",
    "crypto_received",
)
CRYPTO_OUTFLOW_SUBS: tuple[str, ...] = (
    "usd_withdrawal",
    "crypto_sent",
)
CRYPTO_LABELS: dict[str, str] = {
    "usd_deposit": "USD Deposited (direct)",
    "bank_purchase": "Bought Crypto via Bank / PayPal",
    "crypto_received": "Crypto Received (external wallet)",
    "usd_withdrawal": "USD Withdrawn",
    "crypto_sent": "Crypto Sent (external wallet)",
}

TRANSACTION_DISPLAY_COLUMNS: list[str] = [
    "date",
    "account_id",
    "broker",
    "category",
    "subcategory",
    "amount",
    "currency",
    "symbol",
    "description",
]


def current_year_pair(today: dt.date | None = None) -> tuple[int, int]:
    """Return previous/current year pair for dashboard comparative tables."""

    current = (today or dt.date.today()).year
    return current - 1, current


def available_comparison_years(df: pd.DataFrame, today: dt.date | None = None) -> list[int]:
    """Return previous/current year columns that exist in the provided data."""

    if df.empty:
        return []

    source = _with_year(df)
    available = set(source["year"].dropna().unique().astype(int))
    return [year for year in current_year_pair(today) if year in available]


def yearly_summary_table(df: pd.DataFrame, today: dt.date | None = None) -> pd.DataFrame:
    """Build rows for the Yearly Summary tab."""

    source = _with_year(df)
    year_cols = available_comparison_years(source, today)

    rows: list[dict[str, Any]] = []
    for label, metric_fn in METRIC_DEFS:
        row: dict[str, Any] = {"Metric": label}
        for year in year_cols:
            row[year] = metric_fn(compute_metrics(source[source["year"] == year]))
        row["ALL"] = metric_fn(compute_metrics(source))
        rows.append(row)
    return pd.DataFrame(rows)


def income_breakdown_by_type(df: pd.DataFrame, today: dt.date | None = None) -> pd.DataFrame:
    """Build the Yearly Summary income breakdown table."""

    source = _with_year(df)
    income = source[source["category"].isin(["dividend", "reward"]) & (source["amount"] > 0)]
    if income.empty:
        return pd.DataFrame()

    year_cols = available_comparison_years(source, today)
    pivot = income.groupby(["subcategory", "year"])["amount"].sum().unstack(fill_value=0)
    income_year_cols = [year for year in year_cols if year in pivot.columns]
    pivot["ALL"] = pivot.sum(axis=1)
    return (
        pivot[income_year_cols + ["ALL"]]
        .reset_index()
        .rename(columns={"subcategory": "Type"})
        .sort_values("ALL", ascending=False)
        .reset_index(drop=True)
    )


def account_metric_pivot(
    df: pd.DataFrame,
    *,
    all_accounts: list[str],
    selected_accounts: list[str],
    metric_fn: MetricFn,
    today: dt.date | None = None,
) -> pd.DataFrame:
    """Build Account x comparison years + ALL pivot with a TOTAL row."""

    source = _with_year(df)
    pivot_years = available_comparison_years(source, today)
    selected = [account for account in all_accounts if account in selected_accounts]
    rows: dict[str, dict[Any, float]] = {}
    for account in selected:
        account_df = source[source["account_id"] == account]
        rows[account] = {
            year: metric_fn(compute_metrics(account_df[account_df["year"] == year]))
            for year in pivot_years
        }
        rows[account]["ALL"] = metric_fn(compute_metrics(account_df))

    pivot = pd.DataFrame(rows).T.reset_index().rename(columns={"index": "Account"})
    totals: dict[Any, Any] = {"Account": "TOTAL"}
    for year in pivot_years:
        totals[year] = metric_fn(compute_metrics(source[source["year"] == year]))
    totals["ALL"] = metric_fn(compute_metrics(source))
    pivot = pd.concat([pivot, pd.DataFrame([totals])], ignore_index=True)
    return pivot[["Account"] + pivot_years + ["ALL"]]


def by_account_pivots(
    df: pd.DataFrame,
    *,
    all_accounts: list[str],
    selected_accounts: list[str],
    today: dt.date | None = None,
) -> dict[str, pd.DataFrame]:
    """Return all By Account pivot tables."""

    return {
        "net_cash_flow": account_metric_pivot(
            df,
            all_accounts=all_accounts,
            selected_accounts=selected_accounts,
            metric_fn=lambda metrics: metrics["net_cash"],
            today=today,
        ),
        "div_rewards": account_metric_pivot(
            df,
            all_accounts=all_accounts,
            selected_accounts=selected_accounts,
            metric_fn=lambda metrics: metrics["dividends"] + metrics["rewards"],
            today=today,
        ),
        "margin_fees": account_metric_pivot(
            df,
            all_accounts=all_accounts,
            selected_accounts=selected_accounts,
            metric_fn=lambda metrics: metrics["margin_int"] + metrics["fees"],
            today=today,
        ),
    }


def crypto_flow_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Return Coinbase crypto-flow tables and totals for the By Account tab."""

    crypto = df[df["category"] == "crypto_flow"] if not df.empty else pd.DataFrame()
    if crypto.empty:
        return {
            "has_crypto_flow": False,
            "total_in": 0.0,
            "total_out": 0.0,
            "net": 0.0,
            "inflows": pd.DataFrame(),
            "outflows": pd.DataFrame(),
        }

    total_in = float(crypto[crypto["amount"] > 0]["amount"].sum())
    total_out = float(crypto[crypto["amount"] < 0]["amount"].sum())
    inflows = _crypto_rows(crypto, CRYPTO_INFLOW_SUBS)
    outflows = _crypto_rows(crypto, CRYPTO_OUTFLOW_SUBS)
    inflows.loc[len(inflows)] = ["Total In", total_in, ""]
    outflows.loc[len(outflows)] = ["Total Out", total_out, ""]

    return {
        "has_crypto_flow": True,
        "total_in": total_in,
        "total_out": total_out,
        "net": total_in + total_out,
        "inflows": inflows,
        "outflows": outflows,
    }


def _crypto_rows(crypto: pd.DataFrame, subcategories: tuple[str, ...]) -> pd.DataFrame:
    rows = []
    for subcategory in subcategories:
        value = float(crypto[crypto["subcategory"] == subcategory]["amount"].sum())
        count = int((crypto["subcategory"] == subcategory).sum())
        rows.append({
            "Type": CRYPTO_LABELS[subcategory],
            "Amount": value,
            "Txns": count,
        })
    return pd.DataFrame(rows)


def transaction_filter_options(df: pd.DataFrame) -> dict[str, list[Any]]:
    """Return filter choices for the Transactions tab."""

    if df.empty:
        return {"categories": [], "brokers": [], "years": []}

    brokers = sorted(df["broker"].dropna().unique()) if "broker" in df.columns else []
    return {
        "categories": sorted(df["category"].dropna().unique()),
        "brokers": brokers,
        "years": sorted(df["date"].dt.year.dropna().unique().astype(int), reverse=True),
    }


def filtered_transactions_table(
    df: pd.DataFrame,
    *,
    categories: list[str],
    brokers: list[str],
    years: list[int],
    search: str,
) -> pd.DataFrame:
    """Build the filtered/sorted Transactions tab table."""

    if df.empty:
        return pd.DataFrame(columns=TRANSACTION_DISPLAY_COLUMNS)

    txns = df[df["category"].isin(categories)].copy()
    if "broker" in txns.columns:
        txns = txns[txns["broker"].isin(brokers)]
    if years:
        txns = txns[txns["date"].dt.year.isin(years)]
    if search:
        txns = txns[txns["description"].str.contains(search, case=False, na=False)]

    display_cols = [col for col in TRANSACTION_DISPLAY_COLUMNS if col in txns.columns]
    return txns.sort_values("date", ascending=False)[display_cols].reset_index(drop=True)


def _with_year(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    source = df.copy()
    source["year"] = source["date"].dt.year
    return source
