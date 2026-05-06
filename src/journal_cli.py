"""Interactive terminal browser for trading-journal balances and positions.

Run:
    python -m src.journal_cli

The CLI reads the same SQLite database as the dashboard. It does not call broker
APIs directly; run the MCP sync workflow first when you want fresh positions.
"""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import db
from src.positions import load_all_positions
from src.mcp_tools.health import check_mcp_health


MCP_POSITION_ACCOUNTS = {
    "RH-BV",
    "WEBULL",
    "WEBULL-CASH",
    "WEBULL-EVENTS",
    "WEBULL-FUT",
    "TS",
    "SCHWAB",
    "TRADIER",
    "COINBASE",
}

MONEY_COLS = {"Market Value", "Cost Basis", "Margin", "Net Equity", "MARKET VALUE", "COST", "totalReturn"}
PRICE_COLS = {"PRICE", "price", "Cost_Basis"}
_HEALTH_CACHE: pd.DataFrame | None = None
REPO_ROOT = Path(__file__).resolve().parents[1]


def _money(v) -> str:
    try:
        if pd.isna(v):
            return ""
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _num(v, decimals: int = 4) -> str:
    try:
        if pd.isna(v):
            return ""
        return f"{float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _print_df(df: pd.DataFrame, *, max_rows: int = 200) -> None:
    if df.empty:
        print("No rows.")
        return
    out = df.head(max_rows).copy()
    for col in out.columns:
        if col in MONEY_COLS:
            out[col] = out[col].map(_money)
        elif col in PRICE_COLS:
            out[col] = out[col].map(lambda v: _num(v, 2))
        elif pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col].map(_num)
    print(out.to_string(index=False))
    if len(df) > max_rows:
        print(f"... {len(df) - max_rows:,} more rows omitted")


def show_mcp_health(*, force: bool = False) -> pd.DataFrame:
    global _HEALTH_CACHE

    print("\nMCP health")
    print("=" * 10)
    if _HEALTH_CACHE is None or force:
        print("Checking configured broker MCP servers...")
        rows = check_mcp_health()
        health = pd.DataFrame(rows)
        if "Tools" in health.columns:
            health["Tools"] = health["Tools"].astype(str)
        _HEALTH_CACHE = health
    else:
        print("Using cached MCP health for this CLI session.")
        health = _HEALTH_CACHE.copy()
    if "Tools" in health.columns:
        health["Tools"] = health["Tools"].astype(str)
    _print_df(health)

    bad = health[~health["Status"].isin(["OK"])] if not health.empty else health
    if not bad.empty:
        print("\nBalances below are DB-backed and may be stale for accounts with non-OK MCP health.")
    else:
        print("\nAll configured broker MCP servers responded. Balances below still reflect the last completed sync.")
    return health


def _load_accounts() -> pd.DataFrame:
    db.init_db()
    return db.load_account_settings()


def _load_positions() -> pd.DataFrame:
    return load_all_positions()


def _source_label(values: set[str]) -> str:
    if not values:
        return ""
    if values == {"MCP"}:
        return "MCP"
    if values == {"CSV"}:
        return "CSV"
    return "Mixed"


def _account_sources() -> dict[str, str]:
    """Return account_id -> MCP/CSV/Mixed based on stored position provenance."""
    sources: dict[str, set[str]] = {}

    def add(account_id, label: str) -> None:
        if not account_id or not label:
            return
        sources.setdefault(str(account_id), set()).add(label)

    with db.get_conn() as conn:
        for account_id, data_source, source_file in conn.execute(
            "SELECT account_id, data_source, source_file FROM positions"
        ):
            raw = str(data_source or "").strip().lower()
            if raw == "mcp":
                add(account_id, "MCP")
            elif raw == "csv" or source_file:
                add(account_id, "CSV")

        for account_id, data_source, source_file in conn.execute(
            "SELECT account_id, data_source, source_file FROM options_positions"
        ):
            raw = str(data_source or "").strip().lower()
            if raw == "mcp":
                add(account_id, "MCP")
            elif raw == "csv" or source_file:
                add(account_id, "CSV")

        for table in ("futures_positions", "crypto_positions"):
            for account_id, source_file in conn.execute(
                f"SELECT account_id, source_file FROM {table}"
            ):
                add(account_id, "CSV" if source_file else "MCP")

    return {account_id: _source_label(labels) for account_id, labels in sources.items()}


def _account_summary() -> pd.DataFrame:
    pos = _load_positions()
    accounts = _load_accounts()
    if accounts.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    account_sources = _account_sources()
    if not pos.empty:
        pos = pos.copy()
        pos["MARKET VALUE"] = pd.to_numeric(pos["MARKET VALUE"], errors="coerce").fillna(0)
        is_margin = pos["Ticker"].astype(str).str.upper().eq("MARGIN")
        mv = pos[~is_margin].groupby("Account")["MARKET VALUE"].sum()
        margin = pos[is_margin].groupby("Account")["MARKET VALUE"].sum().abs()
    else:
        mv = pd.Series(dtype=float)
        margin = pd.Series(dtype=float)

    for _, acct in accounts.sort_values("account_id").iterrows():
        if not bool(acct.get("active", 1)):
            continue
        account_id = str(acct["account_id"])
        market_value = float(mv.get(account_id, 0.0))
        margin_value = float(margin.get(account_id, 0.0))
        source = account_sources.get(
            account_id,
            "MCP" if account_id in MCP_POSITION_ACCOUNTS else "CSV",
        )
        if source == "CSV" and account_id in MCP_POSITION_ACCOUNTS:
            source = "CSV->MCP"
        rows.append({
            "Account": account_id,
            "Broker": acct.get("broker") or "",
            "Type": acct.get("account_type") or "",
            "Source": source,
            "Market Value": market_value,
            "Margin": margin_value,
            "Net Equity": market_value - margin_value,
        })

    cash = db.get_cash_balance()
    if cash > 0:
        rows.append({
            "Account": "CASH",
            "Broker": "Multi-Bank",
            "Type": "cash",
            "Source": "Manual",
            "Market Value": cash,
            "Margin": 0.0,
            "Net Equity": cash,
        })

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    total = {
        "Account": "TOTAL",
        "Broker": "",
        "Type": "",
        "Source": "",
        "Market Value": summary["Market Value"].sum(),
        "Margin": summary["Margin"].sum(),
        "Net Equity": summary["Net Equity"].sum(),
    }
    return pd.concat([summary, pd.DataFrame([total])], ignore_index=True)


def _account_ids() -> list[str]:
    summary = _account_summary()
    if summary.empty:
        return []
    return [a for a in summary["Account"].tolist() if a not in {"TOTAL", "CASH"}]


def show_overview() -> None:
    show_mcp_health()
    summary = _account_summary()
    print("\nAccount balances")
    print("=" * 16)
    _print_df(summary)

    total = summary[summary["Account"] == "TOTAL"]
    market_value = float(total["Market Value"].iloc[0]) if not total.empty else 0.0
    margin = float(total["Margin"].iloc[0]) if not total.empty else 0.0
    net_worth = float(total["Net Equity"].iloc[0]) if not total.empty else 0.0
    print()
    print(f"Net worth:    {_money(net_worth)}")
    print(f"Market value: {_money(market_value)}")
    print(f"Margin:       {_money(margin)}")


def show_account_menu() -> None:
    accounts = _account_ids()
    if not accounts:
        print("No active accounts found.")
        return

    while True:
        print("\nAccounts")
        for i, account_id in enumerate(accounts, start=1):
            print(f"{i}. {account_id}")
        print("0. Back")

        choice = input("Select account: ").strip()
        if choice in {"0", "q", "Q"}:
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(accounts)):
            print("Choose a listed account number.")
            continue
        show_positions(accounts[int(choice) - 1])


def show_positions(account_id: str | None = None) -> None:
    pos = _load_positions()
    if pos.empty:
        print("No positions found. Run an MCP sync or CSV ingest first.")
        return

    if account_id:
        pos = pos[pos["Account"].astype(str).str.upper() == account_id.upper()]
    pos = pos[pos["Ticker"].astype(str).str.upper() != "MARGIN"].copy()
    if pos.empty:
        print("No positions for that account.")
        return

    cols = [
        "Account", "asset_class", "Ticker", "Name", "TYPE", "sector",
        "Shares", "qty", "PRICE", "price", "MARKET VALUE", "COST", "totalReturn",
        "underlying", "expiry", "strike", "call_put",
    ]
    cols = [c for c in cols if c in pos.columns]
    pos = pos[cols].sort_values(["Account", "asset_class", "MARKET VALUE"], ascending=[True, True, False])
    title = f"Positions - {account_id}" if account_id else "All positions"
    print(f"\n{title}")
    print("=" * len(title))
    _print_df(pos)


def set_cash_balance() -> None:
    current = db.get_cash_balance()
    print(f"Current cash balance: {_money(current)}")
    raw = input("New cash balance, blank to cancel: ").strip().replace("$", "").replace(",", "")
    if not raw:
        return
    try:
        balance = float(raw)
    except ValueError:
        print("Enter a numeric balance.")
        return
    db.upsert_cash_balance(balance)
    print(f"Cash balance set to {_money(balance)}")


def _run_command(label: str, args: list[str], *, pause: bool = True) -> int:
    print(f"\n{label}")
    print("=" * len(label))
    print(" ".join(args))
    result = subprocess.run(args, cwd=REPO_ROOT)
    if result.returncode == 0:
        print(f"\n{label} completed.")
    else:
        print(f"\n{label} failed with exit code {result.returncode}.")
    if pause:
        input("Press Enter to continue...")
    return result.returncode


def _launch_dashboard() -> None:
    print("\nLaunching dashboard at http://localhost:8501 ...")
    args = [sys.executable, "-m", "streamlit", "run", "dashboard/app.py"]
    kwargs: dict = {
        "cwd": REPO_ROOT,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(args, **kwargs)
    print("Dashboard launch requested. Open http://localhost:8501 if it does not open automatically.")
    input("Press Enter to continue...")


def _stop_dashboard() -> None:
    if sys.platform != "win32":
        print("Stop dashboard is currently implemented for Windows only.")
        input("Press Enter to continue...")
        return

    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match 'streamlit' -and $_.CommandLine -match 'dashboard[/\\\\]app\\.py' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    _run_command(
        "Stop dashboard",
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
    )


def housekeeping_menu() -> None:
    while True:
        print("\nHousekeeping")
        print("1. Run incremental ingest")
        print("2. Rebuild database from CSVs")
        print("3. Write snapshot only")
        print("4. Sync Coinbase")
        print("5. Dry-run Coinbase sync")
        print("6. Launch dashboard")
        print("7. Stop dashboard")
        print("8. Run tests")
        print("0. Back")

        choice = input("Select: ").strip()
        if choice in {"0", "q", "Q"}:
            return
        if choice == "1":
            _run_command("Incremental ingest", [sys.executable, "-m", "src.ingest"])
        elif choice == "2":
            confirm = input("This rebuilds journal.db from source files. Continue? [y/N]: ").strip().lower()
            if confirm == "y":
                _run_command("Reset ingest", [sys.executable, "-m", "src.ingest", "--reset"])
        elif choice == "3":
            _run_command("Snapshot only", [sys.executable, "-m", "src.ingest", "--snapshot-only"])
        elif choice == "4":
            _run_command("Sync Coinbase", [sys.executable, "scripts/sync_coinbase.py"])
        elif choice == "5":
            _run_command("Dry-run Coinbase sync", [sys.executable, "scripts/sync_coinbase.py", "--dry-run"])
        elif choice == "6":
            _launch_dashboard()
        elif choice == "7":
            _stop_dashboard()
        elif choice == "8":
            _run_command("Run tests", [sys.executable, "-m", "pytest", "tests/", "-q"])
        else:
            print("Choose 0-8.")


def _broker_live_view() -> None:
    """Launch the standalone broker CLI menu (live API + cached data)."""
    from src.cli.menu import main_menu
    main_menu()


def main() -> int:
    db.init_db()
    while True:
        print("\nTrading Journal CLI")
        print("1. Account balances")
        print("2. Positions by account")
        print("3. All positions")
        print("4. Set cash balance")
        print("5. MCP health")
        print("6. Housekeeping")
        print("7. Broker Live View")
        print("0. Exit")

        choice = input("Select: ").strip()
        if choice in {"0", "q", "Q"}:
            return 0
        if choice == "1":
            show_overview()
        elif choice == "2":
            show_account_menu()
        elif choice == "3":
            show_positions()
        elif choice == "4":
            set_cash_balance()
        elif choice == "5":
            show_mcp_health(force=True)
        elif choice == "6":
            housekeeping_menu()
        elif choice == "7":
            _broker_live_view()
        else:
            print("Choose 0-7.")


if __name__ == "__main__":
    raise SystemExit(main())
