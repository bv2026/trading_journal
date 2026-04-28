"""
Tradier MCP response normalizer.

Converts raw Tradier API response dicts (from the Tradier MCP tools) into
lists of records ready for insertion into the journal DB.

MCP tools this module handles:
  get_positions       → normalize_positions()
  get_account_history → normalize_history()
  get_market_quotes   → used by normalize_positions() for live pricing
  get_account_balances → normalize_balances()

Tradier positions response shape:
  {
    "positions": [
      {"symbol": "AMD", "dateAcquired": "...", "quantity": 40.0, "costBasis": 11150.0},
      {"symbol": "GOOGL270115C00360000", "dateAcquired": "...", "quantity": 1.0, "costBasis": 4200.0}
    ],
    "accountNumber": "6YB44166"
  }

  costBasis is TOTAL (not per-share). Negative quantity = short position.
  USD symbol = cash, skipped.
  OCC-format symbols are options, all others are equities.

Tradier account history response shape:
  {
    "events": [
      {"amount": 1000.0, "date": "...", "type": "journal",
       "journal": {"description": "ACH DEPOSIT", "quantity": "0"}},
      {"amount": 50.0, "date": "...", "type": "dividend",
       "dividend": {"description": "CASH DIV", "quantity": "10", "symbol": "AMD"}}
    ]
  }

Tradier market quotes response shape (get_market_quotes):
  {
    "quotes": [
      {"symbol": "AMD", "last": 142.50, ...},
      ...
    ]
  }
"""
import re
from .base import is_occ_symbol, parse_occ, parse_iso_date, make_txn_id

_SKIP_SYMBOLS = {"USD"}  # cash / internal markers

# Transaction categorization regexes (mirror CSV parser logic)
_ACH_DEPOSIT    = re.compile(r"ACH DEPOSIT",                          re.I)
_ACH_WITHDRAWAL = re.compile(r"ACH WITHDRAWAL|ACH DEBIT",             re.I)
_MARGIN_INT     = re.compile(r"FROM \d{2}/\d{2} THRU \d{2}/\d{2}",   re.I)
_DIVIDEND       = re.compile(r"CASH DIV|DIVIDEND|NON-QUALIFIED",      re.I)
_CLEARING_FEE   = re.compile(r"CLEARING FEE|AGENCY PROCESSING FEE",   re.I)


def _categorize(event_type: str, description: str, amount: float) -> tuple[str, str]:
    """Map a Tradier history event to (category, subcategory)."""
    if event_type == "ach":
        if amount >= 0:
            return "cash_flow", "deposit"
        return "cash_flow", "withdrawal"
    if event_type == "dividend":
        return "dividend", "cash_div"
    if event_type == "interest":
        return "margin_interest", "monthly"
    if event_type == "fee":
        return "fee", "clearing_fee"
    if event_type == "journal":
        desc = description or ""
        if _ACH_DEPOSIT.search(desc):
            return "cash_flow", "deposit"
        if _ACH_WITHDRAWAL.search(desc):
            return "cash_flow", "withdrawal"
        if _MARGIN_INT.search(desc):
            return "margin_interest", "monthly"
        if _DIVIDEND.search(desc):
            return "dividend", "cash_div"
        if _CLEARING_FEE.search(desc):
            return "fee", "clearing_fee"
        if desc.startswith("***") and amount > 0:
            return "dividend", "cash_div"
    return "other", "money_movement"


def normalize_positions(
    positions_resp: dict,
    quotes_resp: dict | None = None,
    account_id: str = "TRADIER",
) -> tuple[list[dict], list[dict]]:
    """
    Split a Tradier get_positions response into equity and option record lists.

    Args:
        positions_resp: Raw dict from get_positions MCP tool.
        quotes_resp:    Optional dict from get_market_quotes for live pricing.
                        Pass None to leave stored_price as NULL (yfinance will
                        price equities at dashboard load time).
        account_id:     Journal account_id to tag all records with.

    Returns:
        (equity_records, option_records)
        equity_records  → suitable for db.insert_positions()
        option_records  → suitable for db.insert_options()
    """
    raw = positions_resp.get("positions", [])

    # Build symbol → last price lookup from quotes if provided
    price_map: dict[str, float] = {}
    if quotes_resp:
        for q in quotes_resp.get("quotes", []):
            sym = q.get("symbol")
            price = q.get("last") or q.get("lastPrice") or q.get("close")
            if sym and price is not None:
                try:
                    price_map[sym] = float(price)
                except (ValueError, TypeError):
                    pass

    equity_records: list[dict] = []
    option_records: list[dict] = []

    for pos in raw:
        symbol = str(pos.get("symbol", "")).strip()
        if not symbol or symbol in _SKIP_SYMBOLS:
            continue

        qty       = float(pos.get("quantity", 0) or 0)
        cost_tot  = float(pos.get("costBasis", 0) or 0)

        if is_occ_symbol(symbol):
            parsed = parse_occ(symbol)
            if not parsed:
                continue
            option_records.append({
                "account_id":   account_id,
                "symbol":       symbol,
                "underlying":   parsed["underlying"],
                "expiry":       parsed["expiry"],
                "strike":       parsed["strike"],
                "call_put":     parsed["call_put"],
                "description":  symbol,
                "qty":          qty,
                "price":        None,    # no live price in positions response
                "market_value": None,
                "data_source":  "mcp",
                "source_file":  None,
            })
        else:
            live_price = price_map.get(symbol)
            per_share_cost = (cost_tot / qty) if qty else None
            equity_records.append({
                "account_id":   account_id,
                "ticker":       symbol,
                "name":         None,       # filled from yfinance at dashboard load
                "shares":       qty,
                "cost_basis":   per_share_cost,
                "stored_price": live_price,  # None if no quotes_resp provided
                "sector":       None,
                "industry":     None,
                "asset_type":   "Stock",
                "iv_rank":      None,
                "perf_ytd":     None,
                "atr_pct":      None,
                "data_source":  "mcp",
                "source_file":  None,
            })

    return equity_records, option_records


def normalize_history(
    history_resp: dict,
    account_id: str = "TRADIER",
) -> list[dict]:
    """
    Convert a Tradier get_account_history response to transaction records.

    Args:
        history_resp: Raw dict from get_account_history MCP tool.
        account_id:   Journal account_id to tag all records with.

    Returns:
        List of records suitable for db.insert_transactions().
    """
    events = history_resp.get("events", [])
    records: list[dict] = []

    for ev in events:
        amount = float(ev.get("amount", 0) or 0)
        date_str = parse_iso_date(ev.get("date", ""))
        if not date_str:
            continue

        event_type = str(ev.get("type", "")).strip().lower()

        # Pull description and symbol from nested event-type sub-dict
        sub = ev.get(event_type, {}) or {}
        description = str(sub.get("description", "") or "").strip()
        symbol = str(sub.get("symbol", "") or "").strip() or None

        category, subcategory = _categorize(event_type, description, amount)

        # Margin interest is always a cost
        if category == "margin_interest":
            amount = -abs(amount)

        records.append({
            "id":          make_txn_id(account_id, date_str, amount, description),
            "account_id":  account_id,
            "date":        date_str,
            "category":    category,
            "subcategory": subcategory,
            "amount":      amount,
            "currency":    "USD",
            "symbol":      symbol,
            "description": description[:500],
            "data_source": "mcp",
            "source_file": None,
        })

    return records


def normalize_balances(balances_resp: dict) -> dict:
    """
    Extract margin / cash info from a Tradier get_account_balances response.

    Returns a dict with keys: total_equity, margin_balance.
    Tradier currently only exposes totalEquity; margin is not broken out.
    """
    bal = balances_resp.get("balances", {}) or {}
    return {
        "total_equity":   float(bal.get("totalEquity", 0) or 0),
        "margin_balance": float(bal.get("marginBalance", 0) or 0),
        "cash":           float(bal.get("cash", bal.get("cashAvailable", 0)) or 0),
    }
