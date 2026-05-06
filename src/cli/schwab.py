"""Schwab broker module for the Trading Journal CLI.

Reads cached data from data/tmp/ (saved during 'sync positions' in Claude).
Schwab MCP is a cloud connector — no standalone API access.

Standalone usage:
    python src/cli/schwab.py                # equity + futures
    python src/cli/schwab.py --equity       # equity only
    python src/cli/schwab.py --futures      # futures only
    python src/cli/schwab.py --json         # raw JSON

Interactive (via menu.py):
    python src/cli/menu.py  →  Schwab  →  action
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
EQUITY_FILE = DATA_DIR / "schwab_equity.json"
FUTURES_FILE = DATA_DIR / "schwab_futures.json"
SUMMARY_FILE = DATA_DIR / "schwab_summary.json"


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


def _num(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        return float(str(val).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return 0.0


def load_equity() -> dict:
    if not EQUITY_FILE.exists():
        return {}
    return json.loads(EQUITY_FILE.read_text("utf-8"))


def load_futures() -> dict:
    if not FUTURES_FILE.exists():
        return {}
    return json.loads(FUTURES_FILE.read_text("utf-8"))


def load_summary() -> dict:
    if not SUMMARY_FILE.exists():
        return {}
    return json.loads(SUMMARY_FILE.read_text("utf-8"))


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_summary(data: dict, header: str = "") -> None:
    if header:
        print(f"\n{'='*60}")
        print(f"  {header}")
        print(f"{'='*60}")

    if not data:
        print("  No summary data cached.")
        return

    # Schwab summary format varies — try common keys
    nlv = _num(data.get("net_liquidation") or data.get("liquidation_value") or 0)
    equity = _num(data.get("equity") or data.get("total_equity") or 0)
    margin = _num(data.get("margin_balance") or data.get("margin_used") or 0)
    bp = _num(data.get("buying_power") or 0)
    mv = _num(data.get("market_value") or data.get("long_market_value") or 0)
    maint = _num(data.get("maintenance_requirement") or 0)

    if nlv:
        print(f"  Net Liquidation: ${nlv:>12,.2f}")
    if equity:
        print(f"  Equity:          ${equity:>12,.2f}")
    if mv:
        print(f"  Market Value:    ${mv:>12,.2f}")
    if margin:
        print(f"  Margin Balance:  ${margin:>12,.2f}")
    if bp:
        print(f"  Buying Power:    ${bp:>12,.2f}")
    if maint:
        print(f"  Maintenance Req: ${maint:>12,.2f}")

    # If none of the above matched, dump top-level keys
    if not any([nlv, equity, mv]):
        for k, v in data.items():
            if isinstance(v, (int, float)):
                print(f"  {k:<20} ${v:>12,.2f}")
            elif isinstance(v, str) and v.replace(".", "").replace("-", "").isdigit():
                print(f"  {k:<20} ${_num(v):>12,.2f}")


def display_equity(data: dict) -> None:
    positions = data.get("positions", [])
    if not positions:
        print("\n  No equity positions found.")
        return

    positions.sort(key=lambda p: abs(_num(p.get("market_value", 0))), reverse=True)

    total_mv = 0.0
    total_gl = 0.0
    total_day = 0.0

    print(f"\n  {'Symbol':<8} {'Qty':>8} {'Mkt Value':>12} "
          f"{'P/L':>12} {'P/L%':>8} {'Day P/L':>10}")
    print(f"  {'-'*8} {'-'*8} {'-'*12} {'-'*12} {'-'*8} {'-'*10}")

    for p in positions:
        sym = p.get("symbol", "???")
        qty = _num(p.get("quantity", 0))
        mv = _num(p.get("market_value", 0))
        gl = _num(p.get("unrealized_pl", 0))
        gl_pct = _num(p.get("unrealized_pl_pct", 0))
        day = _num(p.get("day_pl", 0))
        sign = "+" if gl >= 0 else ""
        dsign = "+" if day >= 0 else ""

        total_mv += mv
        total_gl += gl
        total_day += day

        print(f"  {sym:<8} {qty:>8.2f} ${mv:>11,.2f} "
              f"{sign}${gl:>10,.2f} {sign}{gl_pct:>6.1f}% {dsign}${day:>8,.2f}")

    print(f"  {'-'*8} {'-'*8} {'-'*12} {'-'*12} {'-'*8} {'-'*10}")
    sign = "+" if total_gl >= 0 else ""
    dsign = "+" if total_day >= 0 else ""
    print(f"  {'TOTAL':<8} {' '*8} ${total_mv:>11,.2f} "
          f"{sign}${total_gl:>10,.2f} {' '*8} {dsign}${total_day:>8,.2f}")


def display_futures(data: dict) -> None:
    positions = data.get("positions", data.get("legs", []))
    if not positions:
        print("\n  No futures positions found.")
        return

    print(f"\n  Futures Positions:")
    print(f"  {'Symbol':<15} {'Side':<6} {'Qty':>5} {'Trade':>10} "
          f"{'Mark':>10} {'P/L':>12}")
    print(f"  {'-'*15} {'-'*6} {'-'*5} {'-'*10} {'-'*10} {'-'*12}")

    total_pl = 0.0
    for p in positions:
        sym = p.get("symbol", p.get("leg_symbol", "???"))
        side = p.get("side", p.get("position_type", "???"))
        qty = _num(p.get("qty", p.get("quantity", 0)))
        trade = _num(p.get("trade_price", p.get("avg_price", 0)))
        mark = _num(p.get("mark", p.get("last_price", 0)))
        pl = _num(p.get("pnl", p.get("unrealized_pl", 0)))
        sign = "+" if pl >= 0 else ""
        total_pl += pl

        print(f"  {sym:<15} {side:<6} {qty:>5.0f} ${trade:>9,.2f} "
              f"${mark:>9,.2f} {sign}${pl:>10,.2f}")

    print(f"  {'-'*15} {'-'*6} {'-'*5} {'-'*10} {'-'*10} {'-'*12}")
    sign = "+" if total_pl >= 0 else ""
    print(f"  {'TOTAL':<15} {' '*6} {' '*5} {' '*10} {' '*10} {sign}${total_pl:>10,.2f}")


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------

def broker_menu() -> None:
    from src.cli.menu import print_header, prompt_choice

    while True:
        print_header("Schwab")

        eq_age = _file_age_str(EQUITY_FILE)
        fut_age = _file_age_str(FUTURES_FILE)
        sum_age = _file_age_str(SUMMARY_FILE)
        print(f"  Data cached: equity ({eq_age}), futures ({fut_age}), summary ({sum_age})")
        print(f"  Run 'sync positions' in Claude to refresh")

        if not any(f.exists() for f in [EQUITY_FILE, FUTURES_FILE, SUMMARY_FILE]):
            print("\n  No cached data. Run 'sync positions' in Claude first.")
            try:
                input("\n  Press Enter to go back...")
            except (EOFError, KeyboardInterrupt):
                print()
            return

        options = [
            "Account Summary + All Positions",
            "Account Summary only",
            "Equity Positions",
            "Futures Positions",
        ]
        choice = prompt_choice(options, title="Action")
        if choice is None:
            return

        if choice == 0:
            summary = load_summary()
            if summary:
                display_summary(summary, header="Schwab — Account Summary")
            eq = load_equity()
            if eq:
                display_equity(eq)
            fut = load_futures()
            if fut:
                display_futures(fut)
        elif choice == 1:
            summary = load_summary()
            display_summary(summary, header="Schwab — Account Summary")
        elif choice == 2:
            eq = load_equity()
            if eq:
                display_equity(eq)
            else:
                print("\n  No equity data cached.")
        elif choice == 3:
            fut = load_futures()
            if fut:
                display_futures(fut)
            else:
                print("\n  No futures data cached.")

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
    register_broker("Schwab", sys.modules[__name__])
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="Schwab portfolio CLI (reads cached sync data)",
    )
    parser.add_argument("--equity", action="store_true", help="Show equity only")
    parser.add_argument("--futures", action="store_true", help="Show futures only")
    parser.add_argument("--json", action="store_true", help="Raw JSON output")
    args = parser.parse_args()

    show_eq = True
    show_fut = True
    if args.equity and not args.futures:
        show_fut = False
    elif args.futures and not args.equity:
        show_eq = False

    summary = load_summary()
    eq = load_equity() if show_eq else {}
    fut = load_futures() if show_fut else {}

    if not summary and not eq and not fut:
        print("No cached data. Run 'sync positions' in Claude first.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps({"summary": summary, "equity": eq, "futures": fut}, indent=2))
        return

    age = _file_age_str(EQUITY_FILE)
    print(f"  (data from {age})")

    if summary:
        display_summary(summary, header="Schwab — Account Summary")
    if eq:
        display_equity(eq)
    if fut:
        display_futures(fut)


if __name__ == "__main__":
    _cli_main()
