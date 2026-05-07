"""Webull broker module for the Trading Journal CLI.

Reads cached position/balance data from data/tmp/ and can call the configured
webull-openapi MCP server for transaction history exports.

Standalone usage:
    python src/cli/webull.py                # all accounts
    python src/cli/webull.py --json         # raw cached text
    python src/cli/webull.py --account MARGIN  # specific account type

Interactive (via menu.py):
    python src/cli/menu.py  →  Webull  →  account  →  action
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_project_root = str(_Path(__file__).resolve().parents[2])
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DATA_DIR = Path(_project_root) / "data" / "tmp"
IMPORT_DIR = Path.home() / "OneDrive" / "Home-Docs" / "tradelog" / "Import"

# Webull account IDs and labels
ACCOUNTS = {
    "8AGMH0413MK07EPRI7J4OOSVH9": {"label": "Individual Margin", "type": "MARGIN", "short": "MARGIN"},
    "T1GO54RC6UQ16MTGV2357FQ08A": {"label": "Individual Cash", "type": "CASH", "short": "CASH"},
    "86GENSJM3SFP0OKF4DQ32Q51SA": {"label": "Events Cash", "type": "EVENTS", "short": "EVENTS"},
    "FEH6SDTQIM83DG0SC936GGI9V9": {"label": "Futures", "type": "FUTURES", "short": "FUTURES"},
}

MARGIN_ACCOUNT_ID = "8AGMH0413MK07EPRI7J4OOSVH9"
ORDER_CSV_HEADERS = [
    "Date", "Time", "O/C", "L/S", "Ticker", "Sh/Contr",
    "Price", "Comm", "Amount", "Type/Mult",
]


# ---------------------------------------------------------------------------
# Data loading — parse the MCP text format
# ---------------------------------------------------------------------------

def _file_age_str(path: Path) -> str:
    if not path.exists():
        return "missing"
    age = time.time() - path.stat().st_mtime
    if age < 60:
        return "just now"
    if age < 3600:
        return f"{int(age // 60)}m ago"
    if age < 86400:
        return f"{int(age // 3600)}h ago"
    return f"{int(age // 86400)}d ago"


def _num(val: str) -> float:
    try:
        return float(val.strip().replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return 0.0


def parse_positions_text(text: str) -> list[dict]:
    """Parse Webull MCP position text into structured dicts."""
    positions = []
    # Match lines like: ACHR  Qty: 335.43142  Type: EQUITY  Cost:  6.05  Last:  6.43  Unrealized P&L:  127.57  Currency: USD
    pattern = re.compile(
        r'^\s+(\S+)\s+Qty:\s*([\d.]+)\s+Type:\s*(\S+)\s+Cost:\s*([\d.]+)\s+'
        r'Last:\s*([\d.]+)\s+Unrealized P&L:\s*([-\d.]+)',
        re.MULTILINE,
    )
    for m in pattern.finditer(text):
        positions.append({
            "symbol": m.group(1),
            "quantity": float(m.group(2)),
            "type": m.group(3),
            "avg_cost": float(m.group(4)),
            "last": float(m.group(5)),
            "unrealized_pnl": float(m.group(6)),
        })
    return positions


def parse_balance_text(text: str) -> dict:
    """Parse Webull MCP balance text into structured dict."""
    def _extract(label: str) -> float:
        m = re.search(rf'{re.escape(label)}:\s*([-\d.,]+|N/A)', text)
        if m and m.group(1) != "N/A":
            return _num(m.group(1))
        return 0.0

    return {
        "total_cash": _extract("Total Cash Balance"),
        "total_market_value": _extract("Total Market Value"),
        "unrealized_pnl": _extract("Total Unrealized P&L"),
        "net_liquidation": _extract("Net Liquidation"),
        "day_pnl": _extract("Day P&L"),
        "option_buying_power": _extract("Option Buying Power"),
        "day_buying_power": _extract("Day Buying Power"),
    }


def load_positions(account_id: str) -> list[dict]:
    path = DATA_DIR / f"wb_pos_{account_id}.txt"
    if not path.exists():
        return []
    return parse_positions_text(path.read_text("utf-8"))


def load_balance(account_id: str) -> dict:
    path = DATA_DIR / f"wb_bal_{account_id}.txt"
    if not path.exists():
        return {}
    return parse_balance_text(path.read_text("utf-8"))


# ---------------------------------------------------------------------------
# Transaction history export
# ---------------------------------------------------------------------------

def _server_config() -> tuple[str, dict[str, Any]]:
    from src.mcp_tools.health import load_mcp_servers

    servers = load_mcp_servers()
    if "webull-openapi" in servers:
        return "webull-openapi", servers["webull-openapi"]
    for name, server in servers.items():
        if "webull" in name.lower():
            return name, server
    raise RuntimeError("webull-openapi MCP server is not configured")


async def _call_webull_tool(tool_name: str, arguments: dict[str, Any], timeout_seconds: float = 60.0) -> str:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    _name, server = _server_config()
    command = server.get("command")
    if not command:
        raise RuntimeError("webull-openapi MCP server has no command configured")

    env = os.environ.copy()
    env.update(server.get("env") or {})
    env.setdefault("FASTMCP_LOG_LEVEL", "ERROR")
    env.setdefault("LOG_LEVEL", "ERROR")
    params = StdioServerParameters(
        command=str(command),
        args=[str(arg) for arg in (server.get("args") or [])],
        env=env,
        cwd=server.get("cwd"),
    )

    async def run() -> str:
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
        text = "\n".join(
            getattr(item, "text", "")
            for item in result.content
            if getattr(item, "text", "")
        )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        return str(payload.get("result") or text)

    return await asyncio.wait_for(run(), timeout_seconds)


def fetch_order_history(account_id: str, start: str, end: str, limit: int = 100) -> str:
    """Fetch Webull order history text through the configured MCP server."""
    limit = max(10, min(int(limit), 100))
    return asyncio.run(_call_webull_tool(
        "get_order_history",
        {"account_id": account_id, "start": start, "end": end, "limit": limit},
    ))


def parse_order_history_text(text: str) -> list[dict[str, str]]:
    """Parse Webull get_order_history formatted text into order dictionaries."""
    orders: list[dict[str, str]] = []
    for block in re.split(r"\[Order Entry \d+\]", text):
        if "Order Details" not in block:
            continue
        row: dict[str, str] = {}
        for line in block.splitlines():
            match = re.match(r"\s*([A-Za-z ]+):\s*(.*?)\s*$", line)
            if match:
                row[match.group(1).strip()] = match.group(2).strip()
        if row.get("Symbol"):
            orders.append(row)
    return orders


def _order_datetime(order: dict[str, str]) -> datetime | None:
    for key in ("Filled Time", "Place Time"):
        value = (order.get(key) or "").strip()
        if not value or value == "N/A":
            continue
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        return dt.astimezone(ZoneInfo("America/New_York"))

    raw_ts = (order.get("Filled Timestamp") or order.get("Place Timestamp") or "").strip()
    if raw_ts.isdigit():
        ts = int(raw_ts)
        # Webull's displayed timestamp can include milliseconds.
        if ts > 10_000_000_000:
            ts = ts // 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo("America/New_York"))
    return None


def orders_to_import_rows(orders: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for order in orders:
        status = (order.get("Status") or "").upper()
        qty = order.get("Filled Quantity") or ""
        if status != "FILLED" or not qty or qty == "0":
            continue
        dt = _order_datetime(order)
        if dt is None:
            continue
        rows.append({
            "Date": f"{dt.month}/{dt.day}/{dt.year}",
            "Time": f"{dt.hour}:{dt.minute:02d}:{dt.second:02d}",
            "O/C": (order.get("Side") or "").title(),
            "L/S": "",
            "Ticker": (order.get("Symbol") or "").upper(),
            "Sh/Contr": qty,
            "Price": order.get("Filled Price") or order.get("Limit Price") or "",
            "Comm": "",
            "Amount": "",
            "Type/Mult": "",
        })
    return rows


def export_order_history_csv(account_id: str, start: str, end: str, output_path: Path | None = None) -> Path:
    raw = fetch_order_history(account_id, start, end)
    rows = orders_to_import_rows(parse_order_history_text(raw))
    if output_path is None:
        short = ACCOUNTS.get(account_id, {}).get("short", account_id)
        output_path = IMPORT_DIR / f"Webull_{short}_Transactions_{start}_to_{end}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ORDER_CSV_HEADERS, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
    return output_path


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_balance(bal: dict, header: str = "") -> None:
    if header:
        print(f"\n{'='*60}")
        print(f"  {header}")
        print(f"{'='*60}")

    if not bal:
        print("  No balance data cached.")
        return

    nlv = bal.get("net_liquidation", 0)
    mv = bal.get("total_market_value", 0)
    cash = bal.get("total_cash", 0)
    margin = abs(cash) if cash < 0 else 0
    unreal = bal.get("unrealized_pnl", 0)
    day_pnl = bal.get("day_pnl", 0)

    print(f"  Net Liquidation: ${nlv:>12,.2f}")
    print(f"  Market Value:    ${mv:>12,.2f}")
    if margin:
        print(f"  Margin Used:     ${margin:>12,.2f}")
    else:
        print(f"  Cash:            ${cash:>12,.2f}")
    print(f"  Unrealized P/L:  ${unreal:>12,.2f}")
    print(f"  Day P/L:         ${day_pnl:>12,.2f}")


def display_positions(positions: list[dict]) -> None:
    if not positions:
        print("\n  No positions found.")
        return

    positions.sort(key=lambda p: abs(p.get("quantity", 0) * p.get("last", 0)), reverse=True)

    total_mv = 0.0
    total_cost = 0.0
    total_gl = 0.0

    print(f"\n  {'Symbol':<8} {'Qty':>10} {'Avg Cost':>10} {'Last':>10} "
          f"{'Mkt Value':>12} {'P/L':>12}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*12} {'-'*12}")

    for p in positions:
        sym = p["symbol"]
        qty = p["quantity"]
        cost = p["avg_cost"]
        last = p["last"]
        mv = qty * last
        gl = p["unrealized_pnl"]
        sign = "+" if gl >= 0 else ""

        total_mv += mv
        total_cost += qty * cost
        total_gl += gl

        print(f"  {sym:<8} {qty:>10.2f} ${cost:>9,.2f} ${last:>9,.2f} "
              f"${mv:>11,.2f} {sign}${gl:>10,.2f}")

    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*12} {'-'*12}")
    sign = "+" if total_gl >= 0 else ""
    print(f"  {'TOTAL':<8} {' '*10} {' '*10} {' '*10} "
          f"${total_mv:>11,.2f} {sign}${total_gl:>10,.2f}")


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------

def broker_menu() -> None:
    from src.cli.menu import print_header, prompt_choice

    while True:
        print_header("Webull")

        # Show data age
        pos_path = DATA_DIR / f"wb_pos_{MARGIN_ACCOUNT_ID}.txt"
        age = _file_age_str(pos_path)
        print(f"  Data cached: {age}")
        print(f"  Run 'sync positions' in Claude to refresh")

        # Check for any data
        has_data = any(
            (DATA_DIR / f"wb_pos_{aid}.txt").exists() for aid in ACCOUNTS
        )
        if not has_data:
            print("\n  No cached data. Run 'sync positions' in Claude first.")
            try:
                input("\n  Press Enter to go back...")
            except (EOFError, KeyboardInterrupt):
                print()
            return

        # Account selection
        acct_options = [info["label"] for info in ACCOUNTS.values()]
        acct_options.insert(0, "All Accounts Summary")
        choice = prompt_choice(acct_options, title="Account")
        if choice is None:
            return

        if choice == 0:
            # All accounts summary
            _show_all_accounts()
        else:
            acct_id = list(ACCOUNTS.keys())[choice - 1]
            info = ACCOUNTS[acct_id]
            _show_account(acct_id, info)

        print()
        try:
            input("  Press Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            print()
            return


def _show_all_accounts() -> None:
    total_mv = 0.0
    total_nlv = 0.0

    print(f"\n{'='*60}")
    print(f"  Webull — All Accounts")
    print(f"{'='*60}")

    for acct_id, info in ACCOUNTS.items():
        positions = load_positions(acct_id)
        bal = load_balance(acct_id) if acct_id == MARGIN_ACCOUNT_ID else {}

        mv = sum(p["quantity"] * p["last"] for p in positions) if positions else 0
        nlv = bal.get("net_liquidation", mv)
        n_pos = len(positions)

        total_mv += mv
        total_nlv += nlv if bal else mv

        status = f"{n_pos} positions" if n_pos else "empty"
        print(f"  {info['label']:<22} MV: ${mv:>11,.2f}  ({status})")

    print(f"  {'-'*50}")
    print(f"  {'TOTAL':<22} MV: ${total_mv:>11,.2f}")

    # Show margin account detail
    bal = load_balance(MARGIN_ACCOUNT_ID)
    if bal:
        print(f"\n  Margin Account Detail:")
        print(f"    Net Liquidation: ${bal.get('net_liquidation', 0):>12,.2f}")
        cash = bal.get("total_cash", 0)
        if cash < 0:
            print(f"    Margin Used:     ${abs(cash):>12,.2f}")
        print(f"    Day P/L:         ${bal.get('day_pnl', 0):>12,.2f}")


def _show_account(acct_id: str, info: dict) -> None:
    from src.cli.menu import prompt_choice

    label = info["label"]
    positions = load_positions(acct_id)
    bal = load_balance(acct_id) if acct_id == MARGIN_ACCOUNT_ID else {}

    options = ["Positions", "Balances"] if bal else ["Positions"]
    action = prompt_choice(options, title="Action")
    if action is None:
        return

    if action == 0:
        print(f"\n{'='*60}")
        print(f"  Webull — {label}")
        print(f"{'='*60}")
        display_positions(positions)
    elif action == 1 and bal:
        display_balance(bal, header=f"Webull — {label}")


# ---------------------------------------------------------------------------
# Register with menu system
# ---------------------------------------------------------------------------

try:
    from src.cli.menu import register_broker
    register_broker("Webull", sys.modules[__name__])
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="Webull portfolio CLI (reads cached sync data)",
    )
    parser.add_argument("--account", choices=["MARGIN", "CASH", "EVENTS", "FUTURES", "ALL"],
                        default="ALL", help="Account to show")
    parser.add_argument("--json", action="store_true", help="Raw cached text output")
    args = parser.parse_args()

    if args.account == "ALL":
        target_ids = list(ACCOUNTS.keys())
    else:
        target_ids = [aid for aid, info in ACCOUNTS.items() if info["short"] == args.account]

    for acct_id in target_ids:
        info = ACCOUNTS[acct_id]
        pos_path = DATA_DIR / f"wb_pos_{acct_id}.txt"

        if args.json:
            if pos_path.exists():
                print(f"--- {info['label']} ---")
                print(pos_path.read_text("utf-8"))
            continue

        positions = load_positions(acct_id)
        bal = load_balance(acct_id) if acct_id == MARGIN_ACCOUNT_ID else {}

        if bal:
            display_balance(bal, header=f"Webull — {info['label']}")
        if positions:
            if not bal:
                print(f"\n{'='*60}")
                print(f"  Webull — {info['label']}")
                print(f"{'='*60}")
            display_positions(positions)

    age = _file_age_str(DATA_DIR / f"wb_pos_{MARGIN_ACCOUNT_ID}.txt")
    print(f"\n  (data from {age})")


if __name__ == "__main__":
    _cli_main()
