"""Tradier broker module for the Trading Journal CLI.

Calls the Tradier REST API directly using a long-lived access token.

Standalone usage:
    python src/cli/tradier.py                # balances + positions
    python src/cli/tradier.py --positions     # positions only
    python src/cli/tradier.py --balances      # balances only
    python src/cli/tradier.py --json          # raw JSON

Interactive (via menu.py):
    python src/cli/menu.py  →  Tradier  →  action
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
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://api.tradier.com/v1"
ACCOUNT_NUMBER = "6YB44166"
ACCESS_TOKEN = "nCU7yailI9bUNgPp3Aspj7VC5GR1"

# Also save to data/tmp/ for the ingest pipeline
DATA_DIR = Path(_project_root) / "data" / "tmp"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def fetch_positions(account: str = ACCOUNT_NUMBER) -> dict:
    """Fetch positions from Tradier API."""
    r = httpx.get(
        f"{BASE_URL}/accounts/{account}/positions",
        headers=_headers(),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def fetch_balances(account: str = ACCOUNT_NUMBER) -> dict:
    """Fetch account balances from Tradier API."""
    r = httpx.get(
        f"{BASE_URL}/accounts/{account}/balances",
        headers=_headers(),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def fetch_quotes(symbols: list[str]) -> dict:
    """Fetch market quotes for a list of symbols."""
    if not symbols:
        return {}
    r = httpx.get(
        f"{BASE_URL}/markets/quotes",
        headers=_headers(),
        params={"symbols": ",".join(symbols)},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Display helpers
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

    bal = data.get("balances", {})
    equity = _num(bal.get("total_equity"))
    cash = _num(bal.get("total_cash"))
    market_val = _num(bal.get("market_value"))
    stock_bp = _num(bal.get("stock_buying_power"))
    option_bp = _num(bal.get("option_buying_power"))
    pending_cash = _num(bal.get("pending_cash"))
    uncleared = _num(bal.get("uncleared_funds"))

    margin = bal.get("margin", {})
    margin_used = 0.0
    if margin:
        # margin account details
        stock_val = _num(margin.get("stock_long_value", 0))
        option_val = _num(margin.get("option_long_value", 0))
        market_val = stock_val + option_val if stock_val or option_val else market_val
        margin_used = max(0, market_val - equity)

    print(f"  Account:        {ACCOUNT_NUMBER}")
    print(f"  Total Equity:   ${equity:>12,.2f}")
    print(f"  Market Value:   ${market_val:>12,.2f}")
    if margin_used:
        print(f"  Margin Used:    ${margin_used:>12,.2f}")
    print(f"  Cash:           ${cash:>12,.2f}")
    print(f"  Stock BP:       ${stock_bp:>12,.2f}")
    print(f"  Option BP:      ${option_bp:>12,.2f}")
    if pending_cash:
        print(f"  Pending Cash:   ${pending_cash:>12,.2f}")
    if uncleared:
        print(f"  Uncleared:      ${uncleared:>12,.2f}")


def display_positions(positions: list[dict], quotes: dict | None = None) -> None:
    if not positions:
        print("\n  No positions found.")
        return

    # Build quote lookup
    quote_map: dict[str, dict] = {}
    if quotes:
        q = quotes.get("quotes", {}).get("quote", [])
        if isinstance(q, dict):
            q = [q]
        for qt in q:
            quote_map[qt.get("symbol", "")] = qt

    # Separate equity and options
    equities = []
    options = []
    for p in positions:
        sym = p.get("symbol", "")
        if len(sym) > 10 or any(c.isdigit() for c in sym[:4]):
            # OCC-style option symbol
            options.append(p)
        else:
            equities.append(p)

    # Display equities
    if equities:
        total_mv = 0.0
        total_gl = 0.0
        total_cost = 0.0

        print(f"\n  {'Symbol':<8} {'Qty':>6} {'Last':>10} {'Mkt Value':>12} "
              f"{'Cost':>12} {'P/L':>12} {'P/L%':>8}")
        print(f"  {'-'*8} {'-'*6} {'-'*10} {'-'*12} {'-'*12} {'-'*12} {'-'*8}")

        equities.sort(key=lambda p: abs(_num(p.get("cost_basis", 0))), reverse=True)

        for p in equities:
            sym = p.get("symbol", "???")
            qty = _num(p.get("quantity", 0))
            cost = _num(p.get("cost_basis", 0))

            qt = quote_map.get(sym, {})
            last = _num(qt.get("last", 0))
            mv = last * qty if last else cost  # fallback to cost if no quote

            gl = mv - cost
            gl_pct = (gl / cost * 100) if cost else 0
            sign = "+" if gl >= 0 else ""

            total_mv += mv
            total_gl += gl
            total_cost += cost

            print(f"  {sym:<8} {qty:>6.0f} ${last:>9,.2f} ${mv:>11,.2f} "
                  f"${cost:>11,.2f} {sign}${gl:>10,.2f} {sign}{gl_pct:>6.1f}%")

        print(f"  {'-'*8} {'-'*6} {'-'*10} {'-'*12} {'-'*12} {'-'*12} {'-'*8}")
        sign = "+" if total_gl >= 0 else ""
        gl_pct = (total_gl / total_cost * 100) if total_cost else 0
        print(f"  {'TOTAL':<8} {' '*6} {' '*10} ${total_mv:>11,.2f} "
              f"${total_cost:>11,.2f} {sign}${total_gl:>10,.2f} {sign}{gl_pct:>6.1f}%")

    # Display options
    if options:
        print(f"\n  Options Positions:")
        print(f"  {'Symbol':<25} {'Qty':>5} {'Cost':>10}")
        print(f"  {'-'*25} {'-'*5} {'-'*10}")
        for p in options:
            sym = p.get("symbol", "???")
            qty = _num(p.get("quantity", 0))
            cost = _num(p.get("cost_basis", 0))
            print(f"  {sym:<25} {qty:>5.0f} ${cost:>9,.2f}")


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------

def broker_menu() -> None:
    from src.cli.menu import print_header, prompt_choice

    while True:
        print_header("Tradier")

        options = [
            "Balances + Positions (live)",
            "Balances only",
            "Positions only",
        ]
        choice = prompt_choice(options, title="Action")
        if choice is None:
            return

        try:
            if choice in (0, 1):
                print("\n  Fetching balances...")
                bal = fetch_balances()
                display_balances(bal, header=f"Tradier — {ACCOUNT_NUMBER}")

            if choice in (0, 2):
                print("  Fetching positions...")
                pos_data = fetch_positions()
                positions = pos_data.get("positions", {}).get("position", [])
                if isinstance(positions, dict):
                    positions = [positions]

                # Get quotes for equity symbols
                equity_syms = [
                    p["symbol"] for p in positions
                    if not any(c.isdigit() for c in p.get("symbol", "")[:4])
                    and len(p.get("symbol", "")) <= 10
                ]
                quotes = fetch_quotes(equity_syms) if equity_syms else {}
                display_positions(positions, quotes)

        except httpx.HTTPStatusError as e:
            print(f"\n  API error: {e.response.status_code} — {e.response.text[:200]}")
        except Exception as e:
            print(f"\n  Error: {e}")

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
    register_broker("Tradier", sys.modules[__name__])
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="Tradier portfolio CLI (live API)",
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

    try:
        bal = fetch_balances() if show_bal else {}
        pos_data = fetch_positions() if show_pos else {}
    except httpx.HTTPStatusError as e:
        print(f"API error: {e.response.status_code}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps({"balances": bal, "positions": pos_data}, indent=2))
        return

    if bal:
        display_balances(bal, header=f"Tradier — {ACCOUNT_NUMBER}")

    if pos_data:
        positions = pos_data.get("positions", {}).get("position", [])
        if isinstance(positions, dict):
            positions = [positions]
        equity_syms = [
            p["symbol"] for p in positions
            if not any(c.isdigit() for c in p.get("symbol", "")[:4])
            and len(p.get("symbol", "")) <= 10
        ]
        quotes = fetch_quotes(equity_syms) if equity_syms else {}
        display_positions(positions, quotes)


if __name__ == "__main__":
    _cli_main()
