"""
Robinhood (trayd MCP) response normalizer.

MCP tools this module handles:
  get_positions → normalize_positions()
  get_portfolio → normalize_portfolio()
  list_accounts → account_map_from_list()

Active MCP server: mcp__aeae2ef5-2c58-4908-8c9d-937f5b4fbbbf__ (same trayd
service, reconnected with a new server ID — response format is identical).

Robinhood via trayd returns clean JSON (unlike Webull's text format).

get_positions response shape:
  {
    "success": true,
    "positions": [
      {
        "symbol":        "GOOGL",
        "quantity":      53.184252,    # float, fractional shares supported
        "avg_cost":      253.0126,     # per-share cost basis
        "current_price": 348.67,       # live price, embedded
        "market_value":  18543.75,     # total market value
        "gain_loss":     5087.47       # unrealized P&L
      }
    ]
  }

get_portfolio response shape:
  {
    "success": true,
    "equity":        200668.33,
    "cash":          -127646.2,   # negative = margin used
    "num_positions": 51
  }

list_accounts response shape:
  {
    "success": true,
    "accounts": [
      {
        "account_number": "869439976",
        "nickname": "",
        "type": "margin",
        "buying_power": "50546.81",
        "is_default": true
      }
    ]
  }

Account mapping:
  Robinhood account numbers map to journal account_ids via ACCOUNT_NUMBER_MAP
  below, data/config/robinhood_accounts.json, or ROBINHOOD_ACCOUNT_MAP.
"""
import json
import os
from pathlib import Path

from .base import is_currency_entry

# ── Account number → journal account_id ──────────────────────────────────────
# Update this map if account numbers change after re-linking.
ACCOUNT_NUMBER_MAP: dict[str, str] = {
    "869439976": "RH-BV",
    "550666960": "RH-KD",
}
ACCOUNT_MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "config" / "robinhood_accounts.json"


def load_account_number_map() -> dict[str, str]:
    """Load Robinhood account-number to journal account-id mappings.

    Defaults live in ACCOUNT_NUMBER_MAP. Operators can add/update mappings in
    data/config/robinhood_accounts.json or via ROBINHOOD_ACCOUNT_MAP JSON.
    """
    mapping = dict(ACCOUNT_NUMBER_MAP)
    if ACCOUNT_MAP_PATH.exists():
        try:
            data = json.loads(ACCOUNT_MAP_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                mapping.update({str(k): str(v) for k, v in data.items()})
        except (OSError, json.JSONDecodeError):
            pass
    env_value = os.environ.get("ROBINHOOD_ACCOUNT_MAP")
    if env_value:
        try:
            data = json.loads(env_value)
            if isinstance(data, dict):
                mapping.update({str(k): str(v) for k, v in data.items()})
        except json.JSONDecodeError:
            pass
    return mapping


def account_map_from_list(accounts_resp: dict) -> dict[str, str]:
    """
    Build {account_number: journal_account_id} from get_positions / list_accounts
    response.  Falls back to ACCOUNT_NUMBER_MAP for any number in the response.

    Unknown account numbers are returned as-is with a "RH-{number}" fallback id
    so data is never silently dropped.
    """
    mapping: dict[str, str] = {}
    for acct in accounts_resp.get("accounts", []):
        number = str(acct.get("account_number", "")).strip()
        if not number:
            continue
        journal_id = load_account_number_map().get(number, f"RH-{number}")
        mapping[number] = journal_id
    return mapping


def normalize_positions(
    positions_resp: dict,
    account_id: str = "RH-BV",
) -> list[dict]:
    """
    Convert a trayd get_positions response to equity position records.

    Robinhood only exposes equity positions via the MCP (no options or futures).
    All positions are treated as equity; fractional shares are preserved.

    Args:
        positions_resp: Raw dict from get_positions MCP tool.
        account_id:     Journal account_id (default: "RH-BV").

    Returns:
        List of records suitable for db.insert_positions().
    """
    raw = positions_resp.get("positions", [])
    records: list[dict] = []

    for pos in raw:
        symbol = str(pos.get("symbol", "")).strip()
        if not symbol:
            continue

        qty   = float(pos.get("quantity", 0) or 0)
        cost  = float(pos.get("avg_cost", 0) or 0)       # per-share
        price = float(pos.get("current_price", 0) or 0)  # live
        mv    = float(pos.get("market_value", 0) or 0)

        total_cost = cost * qty
        if is_currency_entry(symbol, total_cost, qty):
            continue

        records.append({
            "account_id":   account_id,
            "ticker":       symbol,
            "name":         None,
            "shares":       qty,
            "cost_basis":   cost,        # already per-share
            "stored_price": price,       # live from MCP
            "sector":       None,
            "industry":     None,
            "asset_type":   "Stock",
            "iv_rank":      None,
            "perf_ytd":     None,
            "atr_pct":      None,
            "data_source":  "mcp",
            "source_file":  None,
        })

    return records


def normalize_instruments(equity_records: list[dict]) -> list[dict]:
    """Build instruments master-table records for all equity positions."""
    seen: set[str] = set()
    records: list[dict] = []
    for eq in equity_records:
        sym = eq["ticker"]
        if sym in seen:
            continue
        seen.add(sym)
        records.append({
            "symbol":      sym,
            "asset_class": "equity",
            "underlying":  None,
            "name":        None,
            "exchange":    None,
            "currency":    "USD",
            "sector":      None,
            "industry":    None,
            "expiry":      None,
            "strike":      None,
            "call_put":    None,
            "tick_size":   None,
            "point_value": None,
            "tradable":    None,
        })
    return records


def normalize_portfolio(portfolio_resp: dict) -> dict:
    """
    Extract equity, cash, and margin from a trayd get_portfolio response.

    Returns dict with keys: equity, cash, margin.
    margin = abs(cash) when cash < 0 (borrowed funds).
    """
    equity = float(portfolio_resp.get("equity", 0) or 0)
    cash   = float(portfolio_resp.get("cash", 0) or 0)
    return {
        "equity": equity,
        "cash":   cash,
        "margin": abs(cash) if cash < 0 else 0.0,
    }
