"""Canonical current-position read model.

The DB currently stores equities, options, futures, crypto, balances, and margin
sentinels in separate shapes. This service normalizes the current position view
into one stable contract for CLI, MCP, dashboard, and future UI frontends.
"""
from __future__ import annotations

import pandas as pd

from src.positions import load_all_positions

CANONICAL_COLUMNS = [
    "account_id",
    "symbol",
    "asset_class",
    "position_kind",
    "name",
    "underlying",
    "expiration",
    "strike",
    "call_put",
    "quantity",
    "price",
    "unit_cost",
    "cost_basis",
    "market_value",
    "unrealized_pnl",
    "sector",
    "industry",
    "source_file",
    "sync_run_id",
    "is_margin",
]


def _series(df: pd.DataFrame, column: str, default=None) -> pd.Series:
    if column in df.columns:
        return df[column]
    return pd.Series(default, index=df.index)


def _coalesce(df: pd.DataFrame, *columns: str) -> pd.Series:
    result = pd.Series(pd.NA, index=df.index)
    for column in columns:
        if column in df.columns:
            result = result.combine_first(df[column])
    return result


def load_current_positions(
    *,
    include_margin: bool = True,
    account_id: str | None = None,
    asset_class: str | None = None,
) -> pd.DataFrame:
    """Load current positions in the canonical read-model shape."""
    raw = load_all_positions()
    if raw.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    symbol = _series(raw, "Ticker", "").fillna("").astype(str)
    is_margin = symbol.str.upper().eq("MARGIN")

    canonical = pd.DataFrame(index=raw.index)
    canonical["account_id"] = _series(raw, "Account")
    canonical["symbol"] = symbol
    canonical["asset_class"] = _series(raw, "asset_class", "equity").fillna("equity")
    canonical["position_kind"] = _series(raw, "TYPE")
    canonical["name"] = _coalesce(raw, "Name", "name", "description")
    canonical["underlying"] = _series(raw, "underlying")
    canonical["expiration"] = _series(raw, "expiry")
    canonical["strike"] = pd.to_numeric(_series(raw, "strike"), errors="coerce")
    canonical["call_put"] = _series(raw, "call_put")
    canonical["quantity"] = pd.to_numeric(_coalesce(raw, "Shares", "qty"), errors="coerce")
    canonical["price"] = pd.to_numeric(_coalesce(raw, "PRICE", "price"), errors="coerce")
    canonical["unit_cost"] = pd.to_numeric(_coalesce(raw, "Cost_Basis", "cost_basis"), errors="coerce")
    canonical["cost_basis"] = pd.to_numeric(_coalesce(raw, "COST", "cost_basis"), errors="coerce")
    canonical["market_value"] = pd.to_numeric(_series(raw, "MARKET VALUE"), errors="coerce")
    canonical["unrealized_pnl"] = pd.to_numeric(_series(raw, "totalReturn"), errors="coerce")
    canonical["sector"] = _series(raw, "sector")
    canonical["industry"] = _series(raw, "industry")
    canonical["source_file"] = _series(raw, "source_file")
    canonical["sync_run_id"] = _series(raw, "sync_run_id")
    canonical["is_margin"] = is_margin

    canonical.loc[canonical["is_margin"], "asset_class"] = "margin"
    canonical.loc[canonical["is_margin"], "position_kind"] = "Margin"

    if not include_margin:
        canonical = canonical[~canonical["is_margin"]]
    if account_id:
        canonical = canonical[canonical["account_id"].astype(str).str.upper() == account_id.upper()]
    if asset_class:
        canonical = canonical[canonical["asset_class"].astype(str).str.lower() == asset_class.lower()]

    return canonical[CANONICAL_COLUMNS].reset_index(drop=True)


def summarize_positions(positions: pd.DataFrame) -> dict:
    """Summarize a canonical positions DataFrame."""
    if positions.empty:
        return {
            "position_count": 0,
            "market_value": 0.0,
            "margin": 0.0,
            "net_value": 0.0,
            "by_asset_class": [],
        }

    mv = pd.to_numeric(positions["market_value"], errors="coerce").fillna(0)
    is_margin = positions["is_margin"].fillna(False).astype(bool)
    market_value = float(mv[~is_margin].sum())
    margin = abs(float(mv[is_margin].sum()))

    non_margin = positions[~is_margin].copy()
    by_asset_class = []
    if not non_margin.empty:
        grouped = (
            non_margin.assign(market_value_numeric=mv[~is_margin])
            .groupby("asset_class")
            .agg(
                count=("symbol", "count"),
                market_value=("market_value_numeric", "sum"),
            )
            .reset_index()
            .round(2)
        )
        by_asset_class = grouped.to_dict(orient="records")

    return {
        "position_count": int((~is_margin).sum()),
        "market_value": round(market_value, 2),
        "margin": round(margin, 2),
        "net_value": round(market_value - margin, 2),
        "by_asset_class": by_asset_class,
    }
