"""TradeStation broker module for the Trading Journal CLI.

Reads cached data from data/tmp/ (saved during 'sync positions' in Claude).

Standalone usage:
    python src/cli/tradestation.py                # balances + positions
    python src/cli/tradestation.py --positions     # positions only
    python src/cli/tradestation.py --balances      # balances only
    python src/cli/tradestation.py --json          # raw JSON

Interactive (via menu.py):
    python src/cli/menu.py  →  TradeStation  →  action
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_project_root = str(_Path(__file__).resolve().parents[2])
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

DATA_DIR = Path(_project_root) / "data" / "tmp"
POSITIONS_FILE = DATA_DIR / "ts_positions.json"
BALANCES_FILE = DATA_DIR / "ts_balances.json"


# ---------------------------------------------------------------------------
# Data loading
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


def load_positions() -> dict:
    if not POSITIONS_FILE.exists():
        return {}
    return json.loads(POSITIONS_FILE.read_text("utf-8"))


def load_balances() -> dict:
    if not BALANCES_FILE.exists():
        return {}
    return json.loads(BALANCES_FILE.read_text("utf-8"))


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _num(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        return float(str(val).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return 0.0


def display_balances(data: dict, header: str = "") -> None:
    if header:
        print(f"\n{'='*60}")
        print(f"  {header}")
        print(f"{'='*60}")

    accounts = data.get("accounts", {})
    for acct_id, acct_data in accounts.items():
        combined = acct_data.get("combined", {})
        rt = acct_data.get("realTimeBalance", {})

        equity = _num(combined.get("currentEquity") or rt.get("Equity") or 0)
        cash = _num(combined.get("currentCashBalance") or rt.get("CashBalance") or 0)
        market_val = _num(combined.get("currentMarketValue") or rt.get("MarketValue") or 0)
        buying_power = _num(combined.get("currentBuyingPower") or rt.get("BuyingPower") or 0)
        cost = _num(combined.get("costOfPositions") or 0)
        today_pl = _num(combined.get("todaysProfitLoss") or rt.get("TodaysProfitLoss") or 0)
        unreal_pl = _num(combined.get("unrealizedProfitLoss") or 0)
        margin = abs(cash) if cash < 0 else 0

        print(f"  Account:        {acct_id}")
        print(f"  Equity:         ${equity:>12,.2f}")
        print(f"  Market Value:   ${market_val:>12,.2f}")
        print(f"  Cost Basis:     ${cost:>12,.2f}")
        if margin:
            print(f"  Margin Used:    ${margin:>12,.2f}")
        print(f"  Buying Power:   ${buying_power:>12,.2f}")
        print(f"  Today P/L:      ${today_pl:>12,.2f}")
        print(f"  Unrealized P/L: ${unreal_pl:>12,.2f}")


def display_positions(data: dict) -> None:
    positions = data.get("positions", [])
    if not positions:
        print("\n  No positions found.")
        return

    positions.sort(key=lambda p: abs(_num(p.get("marketValue", 0))), reverse=True)

    total_mv = 0.0
    total_gl = 0.0

    print(f"\n  {'Symbol':<8} {'Qty':>8} {'Type':<8} {'Mkt Value':>12} "
          f"{'P/L':>12} {'Direction':<10}")
    print(f"  {'-'*8} {'-'*8} {'-'*8} {'-'*12} {'-'*12} {'-'*10}")

    for p in positions:
        sym = p.get("symbol", "???")
        qty = _num(p.get("quantity", 0))
        asset = p.get("assetType", "")
        mv = _num(p.get("marketValue", 0))
        gl = _num(p.get("unrealizedProfitLoss", 0))
        direction = p.get("unrealizedProfitLossDirection", "")
        sign = "+" if gl >= 0 else ""

        total_mv += mv
        total_gl += gl

        print(f"  {sym:<8} {qty:>8.0f} {asset:<8} ${mv:>11,.2f} "
              f"{sign}${gl:>10,.2f}  {direction}")

    print(f"  {'-'*8} {'-'*8} {'-'*8} {'-'*12} {'-'*12}")
    sign = "+" if total_gl >= 0 else ""
    print(f"  {'TOTAL':<8} {' '*8} {' '*8} ${total_mv:>11,.2f} "
          f"{sign}${total_gl:>10,.2f}")


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------

def broker_menu() -> None:
    from src.cli.menu import print_header, prompt_choice

    while True:
        print_header("TradeStation")

        pos_age = _file_age_str(POSITIONS_FILE)
        bal_age = _file_age_str(BALANCES_FILE)
        print(f"  Data cached: positions ({pos_age}), balances ({bal_age})")
        print(f"  Run 'sync positions' in Claude to refresh")

        if not POSITIONS_FILE.exists() and not BALANCES_FILE.exists():
            print("\n  No cached data. Run 'sync positions' in Claude first.")
            try:
                input("\n  Press Enter to go back...")
            except (EOFError, KeyboardInterrupt):
                print()
            return

        options = [
            "Balances + Positions",
            "Balances only",
            "Positions only",
        ]
        choice = prompt_choice(options, title="Action")
        if choice is None:
            return

        if choice == 0:
            bal = load_balances()
            pos = load_positions()
            if bal:
                display_balances(bal, header="TradeStation — 11908624")
            if pos:
                display_positions(pos)
        elif choice == 1:
            bal = load_balances()
            if bal:
                display_balances(bal, header="TradeStation — 11908624")
            else:
                print("\n  No balance data cached.")
        elif choice == 2:
            pos = load_positions()
            if pos:
                display_positions(pos)
            else:
                print("\n  No position data cached.")

        print()
        try:
            input("  Press Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            print()
            return


# ---------------------------------------------------------------------------
# Register with menu system
# ---------------------------------------------------------------------------

try:
    from src.cli.menu import register_broker
    register_broker("TradeStation", sys.modules[__name__])
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="TradeStation portfolio CLI (reads cached sync data)",
    )
    parser.add_argument("--balances", action="store_true", help="Show balances only")
    parser.add_argument("--positions", action="store_true", help="Show positions only")
    parser.add_argument("--json", action="store_true", help="Raw JSON output")
    args = parser.parse_args()

    show_bal = True
    show_pos = True
    if args.balances and not args.positions:
        show_pos = False
    elif args.positions and not args.balances:
        show_bal = False

    bal = load_balances() if show_bal else {}
    pos = load_positions() if show_pos else {}

    if not bal and not pos:
        print("No cached data. Run 'sync positions' in Claude first.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps({"balances": bal, "positions": pos}, indent=2))
        return

    age = _file_age_str(POSITIONS_FILE)
    print(f"  (data from {age})")

    if bal:
        display_balances(bal, header="TradeStation — 11908624")
    if pos:
        display_positions(pos)


if __name__ == "__main__":
    _cli_main()
