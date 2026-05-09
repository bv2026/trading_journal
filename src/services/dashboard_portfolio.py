"""Portfolio tab calculations shared by dashboard and future UI surfaces."""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from src.metrics import net_income
from src.positions import compute_net_worth


def _numeric(series: pd.Series | Any) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def portfolio_kpi_row(metrics: dict) -> dict[str, float]:
    """Return the Portfolio tab transaction KPI row."""

    return {
        "Cash In/Out": metrics["net_cash"],
        "Div+Rewards": metrics["dividends"] + metrics["rewards"],
        "Costs": metrics["margin_int"] + metrics["fees"],
        "Net Income": net_income(metrics),
    }


def net_worth_banner(
    *,
    account_balances: pd.DataFrame,
    all_positions: pd.DataFrame,
    cash_balance: float,
) -> dict[str, float]:
    """Compute net worth, market value, and borrowed margin for the banner."""

    if not account_balances.empty:
        market_value = float(_numeric(account_balances["market_value"]).fillna(0).sum())
        margin = float(_numeric(account_balances["margin"]).fillna(0).sum())
        net_worth = float(_numeric(account_balances["net_equity"]).fillna(0).sum())
        return {
            "net_worth": net_worth,
            "market_value": market_value,
            "margin": margin,
            "source": "account_balances",
        }

    net_worth_data = compute_net_worth(all_positions)
    return {
        "net_worth": float(net_worth_data["net_worth"] + cash_balance),
        "market_value": float(net_worth_data["market_value"] + cash_balance),
        "margin": float(net_worth_data["margin"]),
        "source": "positions",
    }


def split_equity_margin(pos_all: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split the equity position frame into real positions and MARGIN rows."""

    if pos_all.empty:
        return pd.DataFrame(), pd.DataFrame()

    pos_all = pos_all.copy()
    is_margin = pos_all["Ticker"].str.upper() == "MARGIN"
    margin_df = pos_all[is_margin].copy()
    pos = pos_all[~is_margin].copy()

    for col in [
        "PRICE", "Shares", "Cost_Basis", "COST", "MARKET VALUE",
        "totalReturn", "IV_Rank", "PERF_YTD", "ATR_pct",
    ]:
        if col in pos.columns:
            pos[col] = _numeric(pos[col])
    if "MARKET VALUE" in margin_df.columns:
        margin_df["MARKET VALUE"] = _numeric(margin_df["MARKET VALUE"])

    return pos, margin_df


def account_summary(
    *,
    pos: pd.DataFrame,
    margin_df: pd.DataFrame,
    opts_all: pd.DataFrame,
    futs_all: pd.DataFrame,
    cry_all: pd.DataFrame,
    account_balances: pd.DataFrame,
    transactions: pd.DataFrame,
    all_accounts: list[str],
    selected_accounts: list[str],
    cash_balance: float,
) -> pd.DataFrame:
    """Build the Portfolio tab account summary table."""

    if not account_balances.empty:
        rows: list[dict[str, Any]] = []
        for _, balance in account_balances.iterrows():
            account_id = str(balance.get("account_id") or "")
            broker = balance.get("broker")
            rows.append({
                "Account": account_id,
                "Broker": broker if pd.notna(broker) else ("Multi-Bank" if account_id == "CASH" else ""),
                "Market Value": float(balance.get("market_value") or 0.0),
                "Cost Basis": balance.get("cost_basis"),
                "Margin": float(balance.get("margin") or 0.0),
                "Net Equity": float(balance.get("net_equity") or 0.0),
            })
        summary = pd.DataFrame(rows)
        return _append_account_total(summary)

    pos_by_acct = (
        pos.groupby("Account")
        .agg(
            Market_Value=("MARKET VALUE", "sum"),
            Total_Cost=("COST", "sum"),
            PnL=("totalReturn", "sum"),
        )
        .reset_index()
        if not pos.empty else pd.DataFrame(columns=["Account", "Market_Value", "Total_Cost", "PnL"])
    )
    margin_by_acct = (
        margin_df.groupby("Account")["MARKET VALUE"]
        .sum()
        .reset_index()
        .rename(columns={"MARKET VALUE": "Margin"})
        if not margin_df.empty else pd.DataFrame(columns=["Account", "Margin"])
    )
    non_equity_mv = _non_equity_market_value(opts_all, futs_all, cry_all)
    broker_map = (
        transactions[["account_id", "broker"]].drop_duplicates()
        .set_index("account_id")["broker"].to_dict()
        if not transactions.empty and {"account_id", "broker"} <= set(transactions.columns)
        else {}
    )

    rows = []
    for account in [acct for acct in all_accounts if acct in selected_accounts]:
        equity_mv = float(pos_by_acct.loc[pos_by_acct["Account"] == account, "Market_Value"].sum())
        cost_basis = float(pos_by_acct.loc[pos_by_acct["Account"] == account, "Total_Cost"].sum())
        margin = abs(float(margin_by_acct.loc[margin_by_acct["Account"] == account, "Margin"].sum()))
        other_mv = float(non_equity_mv.loc[non_equity_mv["Account"] == account, "Other_MV"].sum())
        market_value = equity_mv + other_mv
        rows.append({
            "Account": account,
            "Broker": broker_map.get(account, ""),
            "Market Value": market_value,
            "Cost Basis": cost_basis,
            "Margin": margin,
            "Net Equity": market_value - margin,
        })

    if cash_balance > 0:
        rows.append({
            "Account": "CASH",
            "Broker": "Multi-Bank",
            "Market Value": cash_balance,
            "Cost Basis": cash_balance,
            "Margin": 0.0,
            "Net Equity": cash_balance,
        })

    return _append_account_total(pd.DataFrame(rows))


def _append_account_total(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    total = {
        "Account": "TOTAL",
        "Broker": "",
        "Market Value": _numeric(summary["Market Value"]).fillna(0).sum(),
        "Cost Basis": _numeric(summary["Cost Basis"]).fillna(0).sum(),
        "Margin": _numeric(summary["Margin"]).fillna(0).sum(),
        "Net Equity": _numeric(summary["Net Equity"]).fillna(0).sum(),
    }
    return pd.concat([summary, pd.DataFrame([total])], ignore_index=True)


def _non_equity_market_value(*frames: pd.DataFrame) -> pd.DataFrame:
    non_equity_frames = []
    for frame in frames:
        if not frame.empty and {"Account", "MARKET VALUE"} <= set(frame.columns):
            selected = frame[["Account", "MARKET VALUE"]].copy()
            selected["MARKET VALUE"] = _numeric(selected["MARKET VALUE"])
            non_equity_frames.append(selected)

    if not non_equity_frames:
        return pd.DataFrame(columns=["Account", "Other_MV"])

    return (
        pd.concat(non_equity_frames, ignore_index=True)
        .groupby("Account")["MARKET VALUE"]
        .sum()
        .reset_index()
        .rename(columns={"MARKET VALUE": "Other_MV"})
    )


def asset_class_breakdown(
    *,
    pos: pd.DataFrame,
    opts_all: pd.DataFrame,
    futs_all: pd.DataFrame,
    cry_all: pd.DataFrame,
    cash_balance: float,
    crypto_accounts: set[str],
) -> pd.DataFrame:
    """Build the Portfolio tab asset-class summary table."""

    if pos.empty:
        stocks_mv = 0.0
        crypto_from_pos = 0.0
    else:
        is_crypto_pos = pos["Account"].isin(crypto_accounts) if crypto_accounts else pd.Series(False, index=pos.index)
        crypto_from_pos = float(_numeric(pos.loc[is_crypto_pos, "MARKET VALUE"]).fillna(0).sum())
        stocks_mv = float(_numeric(pos.loc[~is_crypto_pos, "MARKET VALUE"]).fillna(0).sum())

    opts_mv = _frame_market_value(opts_all)
    futs_mv = _frame_market_value(futs_all)
    crypto_mv = crypto_from_pos + _frame_market_value(cry_all)
    total_mv = stocks_mv + opts_mv + futs_mv + crypto_mv + cash_balance

    rows = [
        {"Asset Class": "Stocks", "Market Value": stocks_mv},
        {"Asset Class": "Options", "Market Value": opts_mv},
        {"Asset Class": "Futures", "Market Value": futs_mv},
        {"Asset Class": "Crypto", "Market Value": crypto_mv},
        {"Asset Class": "Cash", "Market Value": cash_balance},
        {"Asset Class": "TOTAL", "Market Value": total_mv},
    ]
    result = pd.DataFrame(rows)
    result["Allocation"] = (
        result["Market Value"] / total_mv * 100 if total_mv else 0
    ).round(1)
    return result


def _frame_market_value(frame: pd.DataFrame) -> float:
    if frame.empty or "MARKET VALUE" not in frame.columns:
        return 0.0
    return float(_numeric(frame["MARKET VALUE"]).fillna(0).sum())


def futures_by_commodity(futs_all: pd.DataFrame) -> pd.DataFrame:
    """Group futures positions by commodity root."""

    if futs_all.empty:
        return pd.DataFrame(columns=["Commodity", "Contracts", "Net_MV"])

    futures = futs_all[futs_all["Ticker"] != "_FUTURES_ADJ_"].copy()
    futures["MARKET VALUE"] = _numeric(futures["MARKET VALUE"])
    futures["Root"] = futures["Ticker"].apply(_futures_root)
    futures["qty"] = _numeric(futures.get("qty", pd.Series(dtype=float)))
    return (
        futures.groupby("Root")
        .agg(Contracts=("qty", "count"), Net_MV=("MARKET VALUE", "sum"))
        .reset_index()
        .rename(columns={"Root": "Commodity"})
        .sort_values("Net_MV", key=lambda values: values.abs(), ascending=False)
        .reset_index(drop=True)
    )


def _futures_root(ticker: str) -> str:
    symbol = str(ticker)
    # Keep VXM contracts distinct by expiry (e.g. /VXMH27 vs /VXMU27).
    if symbol.startswith("/VXM"):
        return symbol
    match = re.match(r"(/[A-Z]+)(?=[A-Z]\d{2})", symbol)
    return match.group(1) if match else symbol


def collapsed_sector_labels(pos: pd.DataFrame) -> pd.Series:
    """Return the Portfolio tab's collapsed sector labels."""

    if pos.empty:
        return pd.Series(dtype=object)

    etf_sectors = {"Fixed Income", "Broad Market", "International"}
    labels = pos["sector"].copy()
    labels = labels.where(~labels.isin(etf_sectors), "ETF")
    if "TYPE" in pos.columns:
        is_etf_type = pos["TYPE"].str.upper().eq("ETF") & (labels != "Income ETF")
        labels = labels.where(~is_etf_type, "ETF")
    return labels.replace("Unknown", "Other")


def sector_allocation(pos: pd.DataFrame, sector_labels: pd.Series | None = None) -> pd.DataFrame:
    """Build sector allocation chart data."""

    if pos.empty:
        return pd.DataFrame(columns=["sector", "MARKET VALUE"])

    labels = sector_labels if sector_labels is not None else collapsed_sector_labels(pos)
    return (
        pd.Series(labels.values, name="sector")
        .to_frame()
        .assign(mv=_numeric(pos["MARKET VALUE"]).fillna(0).values)
        .groupby("sector")["mv"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
        .rename(columns={"mv": "MARKET VALUE"})
    )


def sector_summary(
    *,
    pos: pd.DataFrame,
    transactions: pd.DataFrame,
    sector_labels: pd.Series | None = None,
) -> pd.DataFrame:
    """Build the Portfolio tab sector summary table."""

    if pos.empty:
        return pd.DataFrame()

    labels = sector_labels if sector_labels is not None else collapsed_sector_labels(pos)
    total_mv = float(_numeric(pos["MARKET VALUE"]).fillna(0).sum())

    source = pos.copy()
    source["sector"] = labels.values
    result = (
        source.groupby("sector")
        .agg(
            Market_Value=("MARKET VALUE", "sum"),
            Total_Cost=("COST", "sum"),
            PnL=("totalReturn", "sum"),
        )
        .reset_index()
        .sort_values("Market_Value", ascending=False)
    )
    result["Alloc_%"] = (result["Market_Value"] / total_mv * 100).round(2) if total_mv else 0
    result["Return_%"] = (result["PnL"] / result["Total_Cost"] * 100).round(2)

    dividends = pd.DataFrame(columns=["sector", "Dividends"])
    if not transactions.empty and {"category", "symbol", "amount"} <= set(transactions.columns):
        pos_with_collapsed = pos[["Ticker", "sector"]].copy()
        pos_with_collapsed["sector"] = labels.values
        dividends = (
            transactions[transactions["category"] == "dividend"]
            .merge(
                pos_with_collapsed.drop_duplicates("Ticker"),
                left_on="symbol",
                right_on="Ticker",
                how="inner",
            )
            .groupby("sector")["amount"]
            .sum()
            .reset_index()
            .rename(columns={"amount": "Dividends"})
        )

    result = result.merge(dividends, on="sector", how="left")
    result["Dividends"] = result["Dividends"].fillna(0)
    return result
