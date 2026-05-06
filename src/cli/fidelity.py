"""Fidelity broker module for the Trading Journal CLI.

Reads positions directly from the CSV file in activity/.

Standalone usage:
    python src/cli/fidelity.py              # positions
    python src/cli/fidelity.py --json       # raw JSON

Interactive (via menu.py):
    python src/cli/menu.py  →  Fidelity  →  action
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_project_root = str(_Path(__file__).resolve().parents[2])
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

CSV_PATH = Path(_project_root) / "activity" / "positions-fidelity.csv"
ACCOUNT_ID = "FIDELITY"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _num(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        return float(str(val).strip().replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return 0.0


def load_positions() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    positions = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("Ticker", "").strip()
            if not ticker or ticker == "MARGIN":
                continue
            positions.append({
                "ticker": ticker,
                "name": row.get(" Name ", row.get("Name", "")).strip(),
                "stored_price": _num(row.get(" PRICE ", row.get("PRICE", 0))),
                "sector": row.get("sector", "").strip(),
                "shares": _num(row.get("Sh/Contr", row.get("Shares", 0))),
                "cost_basis": _num(row.get(" COST BASIS ", row.get("COST BASIS", 0))),
            })
    return positions


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_positions(positions: list[dict], header: str = "") -> None:
    if header:
        print(f"\n{'='*60}")
        print(f"  {header}")
        print(f"{'='*60}")

    if not positions:
        print("\n  No positions found. Run 'python ingest.py' to import CSV.")
        return

    positions.sort(key=lambda p: abs(_num(p.get("shares", 0)) * _num(p.get("stored_price", 0) or p.get("cost_basis", 0))), reverse=True)

    total_mv = 0.0
    total_cost = 0.0

    print(f"\n  {'Symbol':<8} {'Shares':>8} {'Price':>10} {'Mkt Value':>12} "
          f"{'Cost':>12} {'P/L':>12} {'P/L%':>8}")
    print(f"  {'-'*8} {'-'*8} {'-'*10} {'-'*12} {'-'*12} {'-'*12} {'-'*8}")

    for p in positions:
        sym = p.get("ticker", "???")
        shares = _num(p.get("shares", 0))
        price = _num(p.get("stored_price", 0))
        cost_per = _num(p.get("cost_basis", 0))
        mv = shares * price if price else shares * cost_per
        cost = shares * cost_per
        gl = mv - cost
        gl_pct = (gl / cost * 100) if cost else 0
        sign = "+" if gl >= 0 else ""

        total_mv += mv
        total_cost += cost

        print(f"  {sym:<8} {shares:>8.3f} ${price:>9,.2f} ${mv:>11,.2f} "
              f"${cost:>11,.2f} {sign}${gl:>10,.2f} {sign}{gl_pct:>6.1f}%")

    total_gl = total_mv - total_cost
    sign = "+" if total_gl >= 0 else ""
    gl_pct = (total_gl / total_cost * 100) if total_cost else 0
    print(f"  {'-'*8} {'-'*8} {'-'*10} {'-'*12} {'-'*12} {'-'*12} {'-'*8}")
    print(f"  {'TOTAL':<8} {' '*8} {' '*10} ${total_mv:>11,.2f} "
          f"${total_cost:>11,.2f} {sign}${total_gl:>10,.2f} {sign}{gl_pct:>6.1f}%")


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------

def broker_menu() -> None:
    from src.cli.menu import print_header, prompt_choice

    while True:
        print_header("Fidelity")
        print(f"  Data source: journal.db (from CSV import)")
        print(f"  Run 'python ingest.py' to refresh from CSV exports")

        options = [
            "Positions",
        ]
        choice = prompt_choice(options, title="Action")
        if choice is None:
            return

        if choice == 0:
            positions = load_positions()
            display_positions(positions, header=f"Fidelity — {ACCOUNT_ID}")

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
    register_broker("Fidelity", sys.modules[__name__])
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="Fidelity portfolio CLI (reads from journal.db)",
    )
    parser.add_argument("--json", action="store_true", help="Raw JSON output")
    args = parser.parse_args()

    positions = load_positions()

    if not positions:
        print("No Fidelity data. Run 'python ingest.py' first.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(positions, indent=2, default=str))
        return

    display_positions(positions, header=f"Fidelity — {ACCOUNT_ID}")


if __name__ == "__main__":
    _cli_main()
