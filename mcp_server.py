"""
Trading Journal MCP Server

Exposes the portfolio journal database as Claude-callable tools.
Register in Claude Desktop config (see USAGE.md) then ask Claude
questions like "what were my total dividends in 2024?" directly in chat.

Tools:
  get_portfolio_summary  — overall KPIs with optional year/account filters
  get_yearly_summary     — year-over-year breakdown table
  get_account_summary    — per-account breakdown table
  get_transactions       — filterable transaction log
  get_positions          — current positions from TRADEPOSITIONS.xlsx
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
from src.db import DB_PATH, load_transactions
from src.metrics import compute_metrics, net_income as _net_income
from src.positions import load_positions

mcp = FastMCP("trading-journal")

POSITIONS_FILE = ROOT / "activity" / "TRADEPOSITIONS.xlsx"


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
    margin interest, fees, and net income.

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
                  sector: str | None = None,
                  position_type: str | None = None) -> str:
    """
    Return current portfolio positions from TRADEPOSITIONS.xlsx.
    Includes market value, cost, unrealized P&L, sector, and type.

    Args:
        account_id:    Filter by account (e.g. "SCHWAB", "RH-BV", "TRADIER").
        sector:        Filter by sector (e.g. "Technology", "Financial").
        position_type: Filter by type (e.g. "Stock", "ETF", "Option").
    """
    if not POSITIONS_FILE.exists():
        return f"TRADEPOSITIONS.xlsx not found at {POSITIONS_FILE}"

    all_pos = load_positions(POSITIONS_FILE)
    if all_pos.empty:
        return "No position data loaded."

    margin = all_pos[all_pos["Ticker"] == "MARGIN"].copy()
    pos    = all_pos[all_pos["Ticker"] != "MARGIN"].copy()

    # Apply filters
    if account_id:
        pos = pos[pos["Account"].str.upper() == account_id.upper()]
    if sector:
        pos = pos[pos["sector"].str.lower() == sector.lower()]
    if position_type and "TYPE" in pos.columns:
        pos = pos[pos["TYPE"].str.lower() == position_type.lower()]

    if pos.empty:
        return "No positions match the given filters."

    # Portfolio-level summary
    total_mv     = float(pos["MARKET VALUE"].sum())
    total_cost   = float(pos["COST"].sum())
    total_pnl    = float(pos["totalReturn"].sum())
    total_margin = abs(float(margin["MARKET VALUE"].sum())) if not margin.empty else 0.0

    summary = {
        "total_market_value":    round(total_mv, 2),
        "total_cost":            round(total_cost, 2),
        "unrealized_pnl":        round(total_pnl, 2),
        "total_return_pct":      round(total_pnl / total_cost * 100, 2) if total_cost else 0,
        "total_margin_borrowed": round(total_margin, 2),
        "position_count":        len(pos),
    }

    # Sector breakdown
    sec = (
        pos.groupby("sector")
           .agg(count=("Ticker", "count"),
                market_value=("MARKET VALUE", "sum"),
                pnl=("totalReturn", "sum"))
           .sort_values("market_value", ascending=False)
           .reset_index()
    )
    sec["alloc_pct"] = (sec["market_value"] / total_mv * 100).round(2)
    sec = sec.round(2).to_dict(orient="records")

    # Individual positions
    want_cols = ["Account", "Ticker", "Name", "TYPE", "sector",
                 "Shares", "PRICE", "Cost_Basis", "COST", "MARKET VALUE", "totalReturn",
                 "PERF_YTD", "IV_Rank"]
    out_cols = [c for c in want_cols if c in pos.columns]
    positions = pos[out_cols].round(4).to_dict(orient="records")

    return json.dumps({
        "summary":   summary,
        "by_sector": sec,
        "positions": positions,
    }, indent=2)


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
