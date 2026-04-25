"""
Trading Journal MCP Server

Exposes the portfolio journal database as Claude-callable tools.
Register in Claude Desktop config (see USAGE.md) then ask Claude
questions like "what were my total dividends in 2024?" directly in chat.

Tools:
  get_portfolio_summary  — overall KPIs + live net worth across all asset classes
  get_yearly_summary     — year-over-year breakdown table
  get_account_summary    — per-account breakdown table
  get_transactions       — filterable transaction log
  get_positions          — current positions from all asset classes with live prices
  get_performance        — account-level returns across standard lookback periods
  run_ingest             — re-load all broker CSVs into the database
  launch_dashboard       — start the Streamlit dashboard
"""

import sys
import json
import subprocess
from pathlib import Path

# Ensure project root is on the path so src.* imports work.
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from mcp.server.fastmcp import FastMCP
from src.db import DB_PATH, load_transactions, load_snapshot_periods
from src.metrics import compute_metrics, net_income as _net_income
from src.positions import load_all_positions, compute_net_worth

mcp = FastMCP("trading-journal")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load(year: int | None = None,
          account_id: str | None = None) -> pd.DataFrame:
    """Load transactions with optional year and account filters."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    df = load_transactions()
    df = df[df["category"] != "other"]
    df = df[df["subcategory"] != "internal_transfer"]
    if year:
        df = df[df["date"].dt.year == year]
    if account_id:
        df = df[df["account_id"] == account_id.upper()]
    return df


def _fmt_metrics(m: dict, label: str = "") -> dict:
    return {
        "label":           label,
        "net_cash_flow":   round(m["net_cash"], 2),
        "dividends":       round(m["dividends"], 2),
        "rewards":         round(m["rewards"], 2),
        "margin_interest": round(m["margin_int"], 2),
        "fees":            round(m["fees"], 2),
        "net_income":      round(_net_income(m), 2),
    }


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_portfolio_summary(year: int | None = None,
                          account_id: str | None = None) -> str:
    """
    Return overall portfolio KPIs — net cash flow, dividends, rewards,
    margin interest, fees, net income, and live net worth across all
    asset classes (equity, options, futures, crypto).

    Args:
        year:       Optional calendar year to filter (e.g. 2024).
        account_id: Optional account to filter (e.g. "RH-BV", "FIDELITY").
    """
    df = _load(year, account_id)
    if df.empty:
        return "No data found. Run run_ingest() first."

    label_parts = []
    if account_id:
        label_parts.append(account_id.upper())
    if year:
        label_parts.append(str(year))
    label = " · ".join(label_parts) if label_parts else "All accounts · All years"

    result = _fmt_metrics(compute_metrics(df), label)
    result["transaction_count"] = len(df)
    result["date_range"] = f"{df['date'].min().date()} → {df['date'].max().date()}"

    # Live net worth across all asset classes
    try:
        all_pos = load_all_positions()
        if account_id:
            all_pos = all_pos[all_pos["Account"].str.upper() == account_id.upper()]
        nw = compute_net_worth(all_pos)
        result["live_net_worth"]    = round(nw["net_worth"], 2)
        result["live_market_value"] = round(nw["market_value"], 2)
        result["live_margin"]       = round(nw["margin"], 2)
    except Exception:
        pass  # live prices are best-effort; failures must not block the tool

    return json.dumps(result, indent=2)


@mcp.tool()
def get_yearly_summary(account_id: str | None = None) -> str:
    """
    Return a year-over-year breakdown of portfolio metrics.

    Args:
        account_id: Optional account to filter (e.g. "SCHWAB").
    """
    df = _load(account_id=account_id)
    if df.empty:
        return "No data found. Run run_ingest() first."

    df["year"] = df["date"].dt.year
    years = sorted(df["year"].dropna().unique().astype(int))

    rows = [_fmt_metrics(compute_metrics(df[df["year"] == yr]), str(yr)) for yr in years]
    rows.append(_fmt_metrics(compute_metrics(df), "TOTAL"))
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_account_summary(year: int | None = None) -> str:
    """
    Return a per-account breakdown of portfolio metrics.

    Args:
        year: Optional calendar year to filter (e.g. 2023).
    """
    df = _load(year=year)
    if df.empty:
        return "No data found. Run run_ingest() first."

    accounts = sorted(df["account_id"].unique())
    rows = []
    for acct in accounts:
        m = _fmt_metrics(compute_metrics(df[df["account_id"] == acct]), acct)
        broker = df[df["account_id"] == acct]["broker"].iloc[0]
        m["broker"] = broker
        rows.append(m)

    rows.append(_fmt_metrics(compute_metrics(df), "TOTAL"))
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_transactions(category: str | None = None,
                     account_id: str | None = None,
                     year: int | None = None,
                     search: str | None = None,
                     limit: int = 50) -> str:
    """
    Query individual transactions with optional filters.

    Args:
        category:   Filter by category: cash_flow, dividend, reward,
                    margin_interest, fee, crypto_flow.
        account_id: Filter by account (e.g. "COINBASE").
        year:       Filter by calendar year.
        search:     Case-insensitive substring search on description.
        limit:      Maximum rows to return (default 50, max 500).
    """
    df = _load(year=year, account_id=account_id)
    if df.empty:
        return "No data found."

    if category:
        df = df[df["category"] == category.lower()]
    if search:
        df = df[df["description"].str.contains(search, case=False, na=False)]

    limit = min(limit, 500)
    df = df.sort_values("date", ascending=False).head(limit)

    cols = ["date", "account_id", "broker", "category", "subcategory",
            "amount", "currency", "symbol", "description"]
    df = df[cols].copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df["amount"] = df["amount"].round(2)

    return json.dumps({
        "count": len(df),
        "transactions": df.to_dict(orient="records"),
    }, indent=2)


@mcp.tool()
def get_positions(account_id: str | None = None,
                  asset_class: str | None = None,
                  sector: str | None = None,
                  position_type: str | None = None) -> str:
    """
    Return current portfolio positions across all asset classes (equity, options,
    futures, crypto) with live prices for equity and stored prices for the rest.

    Args:
        account_id:    Filter by account (e.g. "SCHWAB", "TRADIER-OPT", "COINBASE").
        asset_class:   Filter by class: equity | options | futures | crypto.
        sector:        Filter by sector (equity only, e.g. "Technology", "Income ETF").
        position_type: Filter by type (equity only, e.g. "Stock", "ETF").
    """
    pos = load_all_positions()
    if pos.empty:
        return ("No positions in database. Run ingest after adding position "
                "CSV files to the activity/ folder.")

    # Strip MARGIN rows — they are balance sentinels, not real positions
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
        return "No positions match the given filters."

    mv_col = pd.to_numeric(pos["MARKET VALUE"], errors="coerce")
    total_mv = float(mv_col.fillna(0).sum())

    # Equity-specific aggregates (cost / P&L only meaningful for equity)
    eq = pos[pos["asset_class"] == "equity"] if "asset_class" in pos.columns else pos
    total_cost = float(pd.to_numeric(eq.get("COST", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    total_pnl  = float(pd.to_numeric(eq.get("totalReturn", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())

    summary = {
        "total_market_value": round(total_mv, 2),
        "equity_cost":        round(total_cost, 2),
        "equity_unrealized_pnl": round(total_pnl, 2),
        "equity_return_pct":  round(total_pnl / total_cost * 100, 2) if total_cost else 0,
        "position_count":     len(pos),
        "by_asset_class":     (
            pos.groupby("asset_class")
               .agg(count=("Ticker", "count"),
                    market_value=("MARKET VALUE", "sum"))
               .reset_index()
               .round(2)
               .to_dict(orient="records")
        ) if "asset_class" in pos.columns else [],
    }

    # Equity sector breakdown
    if "sector" in pos.columns and total_mv:
        eq_sec = pos[pos["asset_class"] == "equity"].copy() if "asset_class" in pos.columns else pos
        if not eq_sec.empty:
            sec = (
                eq_sec.groupby("sector")
                      .agg(count=("Ticker", "count"),
                           market_value=("MARKET VALUE", "sum"),
                           pnl=("totalReturn", "sum"))
                      .sort_values("market_value", ascending=False)
                      .reset_index()
            )
            eq_mv = float(pd.to_numeric(eq_sec["MARKET VALUE"], errors="coerce").fillna(0).sum())
            sec["alloc_pct"] = (sec["market_value"] / eq_mv * 100).round(2) if eq_mv else 0
            summary["by_sector"] = sec.round(2).to_dict(orient="records")

    # Individual positions — include asset_class plus relevant per-class columns
    want_cols = ["Account", "asset_class", "Ticker", "Name", "TYPE", "sector",
                 "Shares", "PRICE", "Cost_Basis", "COST", "MARKET VALUE", "totalReturn",
                 "underlying", "expiry", "strike", "call_put", "qty", "price",
                 "PERF_YTD", "IV_Rank"]
    out_cols = [c for c in want_cols if c in pos.columns]
    positions = pos[out_cols].round(4).to_dict(orient="records")

    return json.dumps({
        "summary":   summary,
        "positions": positions,
    }, indent=2, default=str)


@mcp.tool()
def get_performance(account_id: str | None = None) -> str:
    """
    Return account-level portfolio returns across standard lookback periods
    (1-week, 1-month, 3-month, YTD, 1-year).

    Returns percentage changes computed from daily portfolio snapshots written
    at the end of each ingest run.  Periods with no prior snapshot yet show null.

    Args:
        account_id: Optional account to filter (e.g. "SCHWAB", "RH-BV").
    """
    snap = load_snapshot_periods()
    if snap.empty:
        return ("No snapshot data yet. Run `python ingest.py` at least once "
                "to record the first snapshot.  Historical periods accumulate "
                "with each subsequent run.")

    if account_id:
        snap = snap[snap["account_id"].str.upper() == account_id.upper()]
    if snap.empty:
        return "No snapshot data found for that account."

    # Also load live current values for accuracy
    try:
        all_pos = load_all_positions()
        live_mv = (
            all_pos[all_pos["Ticker"].str.upper() != "MARGIN"]
            .groupby("Account")["MARKET VALUE"]
            .sum()
            .reset_index()
            .rename(columns={"Account": "account_id", "MARKET VALUE": "current_live"})
        )
        live_mv["current_live"] = pd.to_numeric(live_mv["current_live"], errors="coerce")
        snap = snap.merge(live_mv, on="account_id", how="left")
        snap["current_value"] = snap["current_live"].combine_first(
            pd.to_numeric(snap["current_value"], errors="coerce")
        )
    except Exception:
        snap["current_value"] = pd.to_numeric(snap.get("current_value", pd.Series(dtype=float)), errors="coerce")

    def _pct(cur, prior):
        try:
            p = float(prior)
            c = float(cur)
            if pd.isna(p) or p == 0:
                return None
            return round((c - p) / p * 100, 2)
        except (TypeError, ValueError):
            return None

    rows = []
    for _, r in snap.iterrows():
        cur = r.get("current_value")
        rows.append({
            "account_id":  r["account_id"],
            "current_value": round(float(cur), 2) if pd.notna(cur) else None,
            "returns": {
                "1w":  _pct(cur, r.get("value_1w")),
                "1m":  _pct(cur, r.get("value_1m")),
                "3m":  _pct(cur, r.get("value_3m")),
                "ytd": _pct(cur, r.get("value_ytd_start")),
                "1y":  _pct(cur, r.get("value_1y")),
            },
        })

    # Portfolio total row
    valid_cur = pd.to_numeric(snap.get("current_value", pd.Series(dtype=float)), errors="coerce")
    tot_cur = float(valid_cur.fillna(0).sum())

    def _tot_pct(col):
        prior = pd.to_numeric(snap.get(col, pd.Series(dtype=float)), errors="coerce").dropna().sum()
        return _pct(tot_cur, prior) if prior else None

    rows.append({
        "account_id": "TOTAL",
        "current_value": round(tot_cur, 2),
        "returns": {
            "1w":  _tot_pct("value_1w"),
            "1m":  _tot_pct("value_1m"),
            "3m":  _tot_pct("value_3m"),
            "ytd": _tot_pct("value_ytd_start"),
            "1y":  _tot_pct("value_1y"),
        },
    })

    return json.dumps(rows, indent=2)


@mcp.tool()
def run_ingest(reset: bool = False) -> str:
    """
    Ingest broker CSV files from the activity/ folder into the database.

    By default runs incrementally — only new records are added, existing ones
    are left untouched.  Drop only the latest CSV export from each broker and
    call this; no need to re-download full history every time.

    Args:
        reset: If True, clears all existing transactions and reloads from
               every CSV currently in activity/ (full rebuild).  Use once
               after first setup or if you want a clean slate.
    """
    ingest_script = ROOT / "ingest.py"
    if not ingest_script.exists():
        return "Error: ingest.py not found."

    cmd = [sys.executable, str(ingest_script)]
    if reset:
        cmd.append("--reset")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    output = result.stdout + result.stderr
    if result.returncode != 0:
        return f"Ingest failed (exit {result.returncode}):\n{output}"
    return output.strip()


@mcp.tool()
def launch_dashboard() -> str:
    """
    Launch the Streamlit dashboard in the background and open it in the
    default browser at http://localhost:8501.
    """
    import webbrowser

    app = ROOT / "dashboard" / "app.py"
    if not app.exists():
        return "Error: dashboard/app.py not found."

    subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(app),
         "--server.headless", "true"],
        cwd=str(ROOT),
        creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
    )

    webbrowser.open("http://localhost:8501")
    return "Dashboard launched at http://localhost:8501"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
