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
  run_ingest             — re-load all broker CSVs into the database
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


def _metrics(df: pd.DataFrame) -> dict:
    cf     = df[df["category"] == "cash_flow"]
    crypto = df[df["category"] == "crypto_flow"]
    return {
        "deposits":    float(cf[cf["amount"] > 0]["amount"].sum()),
        "withdrawals": float(cf[cf["amount"] < 0]["amount"].sum()),
        "net_cash":    float(cf["amount"].sum()) + float(crypto["amount"].sum()),
        "dividends":   float(df[df["category"] == "dividend"]["amount"].sum()),
        "rewards":     float(df[df["category"] == "reward"]["amount"].sum()),
        "margin_int":  float(df[df["category"] == "margin_interest"]["amount"].sum()),
        "fees":        float(df[df["category"] == "fee"]["amount"].sum()),
    }


def _fmt_metrics(m: dict, label: str = "") -> dict:
    net_income = m["dividends"] + m["rewards"] + m["margin_int"] + m["fees"]
    return {
        "label":           label,
        "net_cash_flow":   round(m["net_cash"], 2),
        "dividends":       round(m["dividends"], 2),
        "rewards":         round(m["rewards"], 2),
        "margin_interest": round(m["margin_int"], 2),
        "fees":            round(m["fees"], 2),
        "net_income":      round(net_income, 2),
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

    result = _fmt_metrics(_metrics(df), label)
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

    rows = [_fmt_metrics(_metrics(df[df["year"] == yr]), str(yr)) for yr in years]
    rows.append(_fmt_metrics(_metrics(df), "TOTAL"))
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
        m = _fmt_metrics(_metrics(df[df["account_id"] == acct]), acct)
        broker = df[df["account_id"] == acct]["broker"].iloc[0]
        m["broker"] = broker
        rows.append(m)

    rows.append(_fmt_metrics(_metrics(df), "TOTAL"))
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
def run_ingest() -> str:
    """
    Re-ingest all broker CSV files from the activity/ folder into the database.
    Run this after dropping updated CSV exports into the activity/ folder.
    Returns a summary of records loaded per account.
    """
    ingest_script = ROOT / "ingest.py"
    if not ingest_script.exists():
        return "Error: ingest.py not found."

    result = subprocess.run(
        [sys.executable, str(ingest_script)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    output = result.stdout + result.stderr
    if result.returncode != 0:
        return f"Ingest failed (exit {result.returncode}):\n{output}"
    return output.strip()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
