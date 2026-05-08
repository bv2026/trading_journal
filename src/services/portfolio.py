"""Portfolio query services shared by MCP, CLI, and dashboard callers.

This module is intentionally UI-free and transport-free: it returns Python
dicts/lists/DataFrames instead of JSON strings, Streamlit widgets, or terminal
text. Thin adapters can format the returned data for their own surface.
"""
from __future__ import annotations

import pandas as pd

from src import db
from src.metrics import compute_metrics, net_income as _net_income
from src.positions import compute_net_worth, load_all_positions
from src.services.position_read_model import load_current_positions, summarize_positions


def load_transactions_filtered(
    *,
    year: int | None = None,
    account_id: str | None = None,
    include_other: bool = False,
    include_internal_transfers: bool = False,
) -> pd.DataFrame:
    """Load journal transactions with the standard reporting filters applied."""
    if not db.DB_PATH.exists():
        return pd.DataFrame()

    df = db.load_transactions()
    if df.empty:
        return df

    if not include_other and "category" in df.columns:
        df = df[df["category"] != "other"]
    if not include_internal_transfers and "subcategory" in df.columns:
        df = df[df["subcategory"] != "internal_transfer"]
    if year:
        df = df[df["date"].dt.year == year]
    if account_id:
        df = df[df["account_id"] == account_id.upper()]
    return df


def format_metrics(metrics: dict, label: str = "") -> dict:
    """Format the canonical metrics dict into API/report-friendly fields."""
    return {
        "label": label,
        "net_cash_flow": round(metrics["net_cash"], 2),
        "dividends": round(metrics["dividends"], 2),
        "rewards": round(metrics["rewards"], 2),
        "margin_interest": round(metrics["margin_int"], 2),
        "fees": round(metrics["fees"], 2),
        "net_income": round(_net_income(metrics), 2),
    }


def get_portfolio_summary(
    *,
    year: int | None = None,
    account_id: str | None = None,
    include_live_net_worth: bool = True,
) -> dict | None:
    """Return portfolio KPI summary, or None when no transactions match."""
    df = load_transactions_filtered(year=year, account_id=account_id)
    if df.empty:
        return None

    label_parts: list[str] = []
    if account_id:
        label_parts.append(account_id.upper())
    if year:
        label_parts.append(str(year))
    label = " · ".join(label_parts) if label_parts else "All accounts · All years"

    result = format_metrics(compute_metrics(df), label)
    result["transaction_count"] = len(df)
    result["date_range"] = f"{df['date'].min().date()} → {df['date'].max().date()}"

    if include_live_net_worth:
        try:
            all_pos = load_all_positions()
            if account_id and not all_pos.empty:
                all_pos = all_pos[all_pos["Account"].str.upper() == account_id.upper()]
            nw = compute_net_worth(all_pos)
            result["live_net_worth"] = round(nw["net_worth"], 2)
            result["live_market_value"] = round(nw["market_value"], 2)
            result["live_margin"] = round(nw["margin"], 2)
        except Exception:
            # Live pricing remains best-effort for compatibility with existing MCP behavior.
            pass

    return result


def get_yearly_summary(*, account_id: str | None = None) -> list[dict] | None:
    """Return year-over-year metrics rows plus a TOTAL row."""
    df = load_transactions_filtered(account_id=account_id)
    if df.empty:
        return None

    df = df.copy()
    df["year"] = df["date"].dt.year
    years = sorted(df["year"].dropna().unique().astype(int))

    rows = [
        format_metrics(compute_metrics(df[df["year"] == year]), str(year))
        for year in years
    ]
    rows.append(format_metrics(compute_metrics(df), "TOTAL"))
    return rows


def get_account_summary(*, year: int | None = None) -> list[dict] | None:
    """Return per-account metrics rows plus a TOTAL row."""
    df = load_transactions_filtered(year=year)
    if df.empty:
        return None

    rows: list[dict] = []
    for account in sorted(df["account_id"].unique()):
        account_df = df[df["account_id"] == account]
        row = format_metrics(compute_metrics(account_df), account)
        row["broker"] = account_df["broker"].iloc[0]
        rows.append(row)

    rows.append(format_metrics(compute_metrics(df), "TOTAL"))
    return rows


def query_transactions(
    *,
    category: str | None = None,
    account_id: str | None = None,
    year: int | None = None,
    search: str | None = None,
    limit: int = 50,
) -> dict | None:
    """Return a filtered transaction payload for API/MCP callers."""
    df = load_transactions_filtered(year=year, account_id=account_id)
    if df.empty:
        return None

    if category:
        df = df[df["category"] == category.lower()]
    if search:
        df = df[df["description"].str.contains(search, case=False, na=False)]

    limit = min(limit, 500)
    df = df.sort_values("date", ascending=False).head(limit)

    cols = [
        "date", "account_id", "broker", "category", "subcategory",
        "amount", "currency", "symbol", "description",
    ]
    df = df[cols].copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df["amount"] = df["amount"].round(2)

    return {
        "count": len(df),
        "transactions": df.to_dict(orient="records"),
    }


def get_positions_report(
    *,
    account_id: str | None = None,
    asset_class: str | None = None,
    sector: str | None = None,
    position_type: str | None = None,
) -> dict | None:
    """Return current positions summary and row payload, or None when empty."""
    canonical = load_current_positions(
        include_margin=False,
        account_id=account_id,
        asset_class=asset_class,
    )
    if canonical.empty:
        raw = load_all_positions()
        if raw.empty:
            return None
        return {"summary": None, "positions": [], "canonical_positions": []}

    if sector:
        canonical = canonical[canonical["sector"].str.lower() == sector.lower()]
    if position_type:
        canonical = canonical[canonical["position_kind"].str.lower() == position_type.lower()]

    if canonical.empty:
        return {"summary": None, "positions": [], "canonical_positions": []}

    pos = load_all_positions()
    if pos.empty:
        return None

    pos = pos[pos["Ticker"].str.upper() != "MARGIN"]

    if account_id:
        pos = pos[pos["Account"].str.upper() == account_id.upper()]
    if asset_class:
        pos = pos[pos["asset_class"].str.lower() == asset_class.lower()]
    if sector and "sector" in pos.columns:
        pos = pos[pos["sector"].str.lower() == sector.lower()]
    if position_type and "TYPE" in pos.columns:
        pos = pos[pos["TYPE"].str.lower() == position_type.lower()]

    if pos.empty:
        return {"summary": None, "positions": [], "canonical_positions": []}

    canonical_summary = summarize_positions(canonical)
    total_mv = canonical_summary["market_value"]

    eq = pos[pos["asset_class"] == "equity"] if "asset_class" in pos.columns else pos
    total_cost = float(
        pd.to_numeric(eq.get("COST", pd.Series(dtype=float)), errors="coerce")
        .fillna(0)
        .sum()
    )
    total_pnl = float(
        pd.to_numeric(eq.get("totalReturn", pd.Series(dtype=float)), errors="coerce")
        .fillna(0)
        .sum()
    )

    summary = {
        "total_market_value": round(total_mv, 2),
        "net_value": canonical_summary["net_value"],
        "margin": canonical_summary["margin"],
        "equity_cost": round(total_cost, 2),
        "equity_unrealized_pnl": round(total_pnl, 2),
        "equity_return_pct": round(total_pnl / total_cost * 100, 2) if total_cost else 0,
        "position_count": canonical_summary["position_count"],
        "by_asset_class": canonical_summary["by_asset_class"],
    }

    if "sector" in pos.columns and total_mv:
        eq_sec = pos[pos["asset_class"] == "equity"].copy() if "asset_class" in pos.columns else pos
        if not eq_sec.empty:
            sec = (
                eq_sec.groupby("sector")
                .agg(
                    count=("Ticker", "count"),
                    market_value=("MARKET VALUE", "sum"),
                    pnl=("totalReturn", "sum"),
                )
                .sort_values("market_value", ascending=False)
                .reset_index()
            )
            eq_mv = float(pd.to_numeric(eq_sec["MARKET VALUE"], errors="coerce").fillna(0).sum())
            sec["alloc_pct"] = (sec["market_value"] / eq_mv * 100).round(2) if eq_mv else 0
            summary["by_sector"] = sec.round(2).to_dict(orient="records")

    want_cols = [
        "Account", "asset_class", "Ticker", "Name", "TYPE", "sector",
        "Shares", "PRICE", "Cost_Basis", "COST", "MARKET VALUE", "totalReturn",
        "underlying", "expiry", "strike", "call_put", "qty", "price",
        "PERF_YTD", "IV_Rank",
    ]
    out_cols = [col for col in want_cols if col in pos.columns]
    positions = pos[out_cols].round(4).to_dict(orient="records")
    canonical_positions = canonical.round(4).to_dict(orient="records")

    return {
        "summary": summary,
        "positions": positions,
        "canonical_positions": canonical_positions,
    }


def get_performance_report(*, account_id: str | None = None) -> list[dict] | None:
    """Return account-level performance rows plus a TOTAL row."""
    snap = db.load_snapshot_periods()
    if snap.empty:
        return None

    if account_id:
        snap = snap[snap["account_id"].str.upper() == account_id.upper()]
    if snap.empty:
        return []

    try:
        all_pos = load_all_positions()
        is_margin = all_pos["Ticker"].str.upper() == "MARGIN"
        live_mv = (
            all_pos[~is_margin]
            .groupby("Account")["MARKET VALUE"]
            .sum()
            .reset_index()
            .rename(columns={"Account": "account_id", "MARKET VALUE": "current_live"})
        )
        live_mv["current_live"] = pd.to_numeric(live_mv["current_live"], errors="coerce")
        margin_mv = (
            all_pos[is_margin]
            .groupby("Account")["MARKET VALUE"]
            .sum()
            .abs()
            .reset_index()
            .rename(columns={"Account": "account_id", "MARKET VALUE": "margin_live"})
        )
        snap = snap.merge(live_mv, on="account_id", how="left")
        snap = snap.merge(margin_mv, on="account_id", how="left")
        snap["current_value"] = snap["current_live"].combine_first(
            pd.to_numeric(snap["current_value"], errors="coerce")
        )
        snap["margin_live"] = pd.to_numeric(snap["margin_live"], errors="coerce").fillna(0)
        snap["net_value"] = snap["current_value"] - snap["margin_live"]
    except Exception:
        snap["current_value"] = pd.to_numeric(
            snap.get("current_value", pd.Series(dtype=float)), errors="coerce"
        )
        snap["net_value"] = snap["current_value"]

    def pct(current, prior):
        try:
            prior_float = float(prior)
            current_float = float(current)
            if pd.isna(prior_float) or prior_float == 0:
                return None
            return round((current_float - prior_float) / prior_float * 100, 2)
        except (TypeError, ValueError):
            return None

    rows: list[dict] = []
    for _, row in snap.iterrows():
        net = row.get("net_value")
        rows.append({
            "account_id": row["account_id"],
            "current_value": round(float(net), 2) if pd.notna(net) else None,
            "returns": {
                "1w": pct(net, row.get("value_1w")),
                "1m": pct(net, row.get("value_1m")),
                "3m": pct(net, row.get("value_3m")),
                "ytd": pct(net, row.get("value_ytd_start")),
                "1y": pct(net, row.get("value_1y")),
            },
        })

    valid_net = pd.to_numeric(snap.get("net_value", pd.Series(dtype=float)), errors="coerce")
    total_net = float(valid_net.fillna(0).sum())

    def total_pct(column: str):
        prior = pd.to_numeric(snap.get(column, pd.Series(dtype=float)), errors="coerce").dropna().sum()
        return pct(total_net, prior) if prior else None

    rows.append({
        "account_id": "TOTAL",
        "current_value": round(total_net, 2),
        "returns": {
            "1w": total_pct("value_1w"),
            "1m": total_pct("value_1m"),
            "3m": total_pct("value_3m"),
            "ytd": total_pct("value_ytd_start"),
            "1y": total_pct("value_1y"),
        },
    })
    return rows
