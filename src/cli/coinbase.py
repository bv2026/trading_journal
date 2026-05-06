"""Coinbase broker module for the Trading Journal CLI.

Calls the Coinbase API directly using the same credentials as coinbase-derivatives-mcp.
Requires: COINBASE_API_KEY_NAME and COINBASE_API_PRIVATE_KEY_FILE env vars,
or the coinbase_derivatives_mcp package on PYTHONPATH.

Standalone usage:
    python src/cli/coinbase.py              # balances (spot holdings)
    python src/cli/coinbase.py --json       # raw JSON

Interactive (via menu.py):
    python src/cli/menu.py  →  Coinbase  →  action
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_project_root = str(_Path(__file__).resolve().parents[2])
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

# Add coinbase MCP source to path
_coinbase_src = r"C:\work\coinbase-api\src"
if _coinbase_src not in _sys.path:
    _sys.path.insert(0, _coinbase_src)

import argparse
import json
import os
import sys
from typing import Any

# Set env vars for coinbase config (same as claude_desktop_config.json)
os.environ.setdefault(
    "COINBASE_API_KEY_NAME",
    "organizations/6074317f-6c56-4822-b99c-f5b40321d37a/apiKeys/9430b569-f13f-4452-979a-863f6676628b",
)
os.environ.setdefault("COINBASE_API_PRIVATE_KEY_FILE", r"C:\work\KEYS\cdp_api_key.json")
os.environ.setdefault("COINBASE_API_BASE_URL", "https://api.coinbase.com")

from coinbase_derivatives_mcp.config import load_config
from coinbase_derivatives_mcp.client import CoinbaseClient
from coinbase_derivatives_mcp.balances import normalize_account_balances


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def _get_client() -> CoinbaseClient:
    config = load_config()
    return CoinbaseClient(config)


def fetch_balances() -> list[dict]:
    """Fetch spot balances from Coinbase, filtered to non-dust positions."""
    from decimal import Decimal

    client = _get_client()
    raw = client.list_accounts(limit=250)
    accounts_payload = raw.get("accounts", [])

    # Wrap in expected format for normalize
    if isinstance(accounts_payload, list):
        accounts_payload = {"accounts": accounts_payload}

    # Use client's spot price lookup for USD valuation
    def price_lookup(asset: str):
        return client.get_spot_usd_price(asset)

    rows = normalize_account_balances(
        accounts_payload,
        price_lookup=price_lookup,
        min_usd_value=Decimal("1"),
    )
    return rows


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _num(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def display_balances(rows: list[dict], header: str = "") -> None:
    if header:
        print(f"\n{'='*60}")
        print(f"  {header}")
        print(f"{'='*60}")

    if not rows:
        print("\n  No holdings found.")
        return

    # Separate cash and spot
    cash_assets = {"USD", "USDC", "USDT"}
    cash_rows = [r for r in rows if r.get("asset", "") in cash_assets]
    spot_rows = [r for r in rows if r.get("asset", "") not in cash_assets]

    # Sort spot by USD value descending
    spot_rows.sort(key=lambda r: _num(r.get("usd_value", 0)), reverse=True)

    total_usd = 0.0

    # Cash
    if cash_rows:
        print(f"\n  Cash & Stablecoins:")
        for r in cash_rows:
            asset = r.get("asset", "???")
            total = _num(r.get("total", 0))
            usd = _num(r.get("usd_value", total))
            total_usd += usd
            print(f"    {asset:<8} {total:>14,.2f}  (${usd:>10,.2f})")

    # Spot holdings
    if spot_rows:
        print(f"\n  {'Asset':<10} {'Quantity':>14} {'Price':>10} {'Value':>12}")
        print(f"  {'-'*10} {'-'*14} {'-'*10} {'-'*12}")

        for r in spot_rows:
            asset = r.get("asset", "???")
            total = _num(r.get("total", 0))
            price = _num(r.get("price_usd", 0))
            usd = _num(r.get("usd_value", 0))
            total_usd += usd

            if price > 1000:
                print(f"  {asset:<10} {total:>14.6f} ${price:>9,.0f} ${usd:>11,.2f}")
            elif price > 1:
                print(f"  {asset:<10} {total:>14.4f} ${price:>9,.2f} ${usd:>11,.2f}")
            else:
                print(f"  {asset:<10} {total:>14.2f} ${price:>9,.4f} ${usd:>11,.2f}")

        print(f"  {'-'*10} {'-'*14} {'-'*10} {'-'*12}")

    print(f"\n  Total Portfolio Value: ${total_usd:>12,.2f}")


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------

def broker_menu() -> None:
    from src.cli.menu import print_header, prompt_choice

    while True:
        print_header("Coinbase")

        options = [
            "Portfolio (live)",
        ]
        choice = prompt_choice(options, title="Action")
        if choice is None:
            return

        try:
            if choice == 0:
                print("\n  Fetching balances...")
                rows = fetch_balances()
                display_balances(rows, header="Coinbase — Spot Portfolio")
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
    register_broker("Coinbase", sys.modules[__name__])
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="Coinbase portfolio CLI (live API via coinbase-derivatives-mcp)",
    )
    parser.add_argument("--json", action="store_true", help="Raw JSON output")
    args = parser.parse_args()

    try:
        rows = fetch_balances()
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return

    display_balances(rows, header="Coinbase — Spot Portfolio")


if __name__ == "__main__":
    _cli_main()
