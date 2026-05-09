"""
Schwab MCP response normalizer (schwab-smartspreads-file MCP).

MCP tools this module handles:
  get_equity_positions  → normalize_equity()
  get_futures_positions → normalize_futures()
  get_account_summary   → normalize_balances()
  get_transactions      → normalize_transactions()  (60-day window)

Equity positions response shape (get_equity_positions):
  {
    "positions": [
      {
        "symbol": "MAGY",
        "description": "Roundhill Magnificent Seven Cov Cll ETF",
        "asset_type": "COLLECTIVE_INVESTMENT",   # ETF | EQUITY | OPTION
        "quantity": 58.3124,
        "avg_price": 52.8172,        # per-share cost basis
        "market_value": 2685.29,     # total market value (live)
        "unrealized_pl": -394.61,
        "day_pl": -21.53,
        "maintenance_requirement": 805.59
      }
    ],
    "count": 17,
    "totals": { "market_value": 55493.41, ... }
  }

Futures positions response shape (get_futures_positions):
  {
    "futures_legs": [
      {
        "symbol": "/GCQ26",
        "description": "Gold Futures,Aug-2026, ETH",
        "expiration": "AUG 26",          # MMM YY format
        "side": "SHORT",                 # SHORT | LONG → sign of qty
        "quantity": 1,                   # always positive; side gives direction
        "trade_price": 4849.8,           # entry price (cost basis)
        "mark": 4646.5,                  # current live price
        "mark_value": 1590.0,            # spread-level value from SmartSpreads
        "pl_open_dollars": -20330.0,     # unrealized P&L in dollars
        "multiplier": 100,               # contract size ($ per point)
        "spread_id": "gc_calendar_1",
        "spread_name": "GC Gold Aug/Dec Calendar"
      }
    ]
  }

Account summary response shape (get_account_summary):
  {
    "liquidation_value": 32068.75,
    "equity": 32068.75,
    "margin_balance": -23424.66,   # negative = margin used
    "long_market_value": 55493.41,
    "long_option_market_value": 0.0
  }
"""
import re
from .base import is_occ_symbol, parse_occ, is_currency_entry, make_txn_id, parse_iso_date

# Schwab asset_type values
_EQUITY_TYPES  = {"EQUITY", "COLLECTIVE_INVESTMENT", "ETF"}
_OPTION_TYPES  = {"OPTION"}

# "AUG 26" → 2026-08-01 (day unknown; use 1st as placeholder)
_EXPIRY_RE = re.compile(r'^([A-Z]{3})\s+(\d{2})$')
_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

# Transaction type → (category, subcategory)
_TXN_TYPE_MAP = {
    "DIVIDEND_OR_INTEREST": ("dividend", "cash_div"),
    "INTEREST_ADJUSTMENT":  ("dividend", "cash_div"),
    "MARGIN_INTEREST":      ("margin_interest", "monthly"),
    "ACH_RECEIPT":          ("cash_flow", "deposit"),
    "ACH_DISBURSEMENT":     ("cash_flow", "withdrawal"),
    "WIRE_IN":              ("cash_flow", "deposit"),
    "WIRE_OUT":             ("cash_flow", "withdrawal"),
    "SERVICE_FEE":          ("fee", "service_fee"),
    "MISCELLANEOUS_JOURNAL": ("other", "journal"),
}


def _parse_expiry(expiry_str: str) -> str | None:
    """Convert 'AUG 26' → '2026-08-01' (day approximated as 1st)."""
    m = _EXPIRY_RE.match((expiry_str or "").strip().upper())
    if not m:
        return None
    month = _MONTH_MAP.get(m.group(1))
    if not month:
        return None
    year = 2000 + int(m.group(2))
    return f"{year:04d}-{month}-01"


def normalize_equity(
    equity_resp: dict,
    account_id: str = "SCHWAB",
) -> tuple[list[dict], list[dict]]:
    """
    Split get_equity_positions response into equity and option record lists.

    Schwab includes description in the response so name is populated directly
    (no yfinance name lookup needed for these positions).
    live price = market_value / quantity.

    Returns:
        (equity_records, option_records)
    """
    raw = equity_resp.get("positions", [])
    equity_records: list[dict] = []
    option_records: list[dict] = []

    for pos in raw:
        symbol     = str(pos.get("symbol", "")).strip()
        asset_type = str(pos.get("asset_type", "")).strip().upper()
        if not symbol:
            continue

        qty   = float(pos.get("quantity", 0) or 0)
        cost  = float(pos.get("avg_price", 0) or 0)      # per-share
        mv    = float(pos.get("market_value", 0) or 0)   # total
        desc  = str(pos.get("description", "") or "").strip()

        if is_currency_entry(symbol, cost * qty, qty):
            continue

        live_price = (mv / qty) if qty else None

        if asset_type in _OPTION_TYPES or is_occ_symbol(symbol):
            parsed = parse_occ(symbol) if is_occ_symbol(symbol) else None
            option_records.append({
                "account_id":   account_id,
                "symbol":       symbol,
                "underlying":   parsed["underlying"] if parsed else None,
                "expiry":       parsed["expiry"]     if parsed else None,
                "strike":       parsed["strike"]     if parsed else None,
                "call_put":     parsed["call_put"]   if parsed else None,
                "description":  desc or symbol,
                "qty":          qty,
                "price":        live_price,
                "market_value": mv,
                "data_source":  "mcp",
                "source_file":  None,
            })
        else:
            equity_records.append({
                "account_id":   account_id,
                "ticker":       symbol,
                "name":         desc or None,
                "shares":       qty,
                "cost_basis":   cost,
                "stored_price": live_price,
                "sector":       None,
                "industry":     None,
                "asset_type":   "ETF" if asset_type == "COLLECTIVE_INVESTMENT" else "Stock",
                "iv_rank":      None,
                "perf_ytd":     None,
                "atr_pct":      None,
                "data_source":  "mcp",
                "source_file":  None,
            })

    return equity_records, option_records


def normalize_futures(
    futures_resp: dict,
    account_id: str = "SCHWAB",
) -> list[dict]:
    """
    Convert get_futures_positions response to futures position records.

    Side (SHORT/LONG) determines sign of quantity.
    mark = live price; trade_price = entry price used for cost basis.
    market_value = signed_qty × mark × multiplier (notional P&L contribution).

    Returns:
        List of records suitable for db.insert_futures().
    """
    legs = futures_resp.get("futures_legs", [])
    records: list[dict] = []

    for leg in legs:
        symbol = str(leg.get("symbol", "")).strip()
        if not symbol:
            continue

        side        = str(leg.get("side", "LONG")).strip().upper()
        raw_qty     = float(leg.get("quantity", 0) or 0)
        signed_qty  = raw_qty if side == "LONG" else -raw_qty
        mark        = float(leg.get("mark", 0) or 0)
        trade_price = float(leg.get("trade_price", 0) or 0)
        multiplier  = float(leg.get("multiplier", 1) or 1)
        desc        = str(leg.get("description", "") or "").strip()
        expiry_raw  = str(leg.get("expiration", "") or "").strip()
        expiry      = _parse_expiry(expiry_raw)

        # Notional market value: signed_qty × mark × multiplier
        mv = signed_qty * mark * multiplier

        # Underlying = root symbol stripped of leading / and month/year suffix
        # /GCQ26 → GC,  /ZSX26 → ZS,  /VXMN26 → VXM
        root = symbol.lstrip("/")
        underlying = re.sub(r'[A-Z]\d{2,}$', '', root) or root

        records.append({
            "account_id":   account_id,
            "symbol":       symbol,
            "underlying":   underlying,
            "description":  desc,
            "qty":          signed_qty,
            "price":        mark,
            "market_value": mv,
            "data_source":  "mcp",
            "source_file":  None,
            # Extra fields stored in instruments table:
            "_expiry":      expiry,
            "_multiplier":  multiplier,
            "_trade_price": trade_price,
            "_spread_name": leg.get("spread_name"),
        })

    return records


def normalize_transactions(
    txn_resp: dict,
    account_id: str = "SCHWAB",
) -> list[dict]:
    """
    Convert get_transactions response to transaction records (60-day window).
    Incremental only — INSERT OR IGNORE deduplicates by id.

    Returns:
        List of records suitable for db.insert_transactions().
    """
    txns = txn_resp.get("transactions", []) if isinstance(txn_resp, dict) else []
    records: list[dict] = []

    for txn in txns:
        amount_raw = txn.get("netAmount") or txn.get("amount") or 0
        amount     = float(amount_raw or 0)
        date_str   = parse_iso_date(
            txn.get("tradeDate") or txn.get("settlementDate") or txn.get("date", "")
        )
        if not date_str:
            continue

        txn_type = str(
            txn.get("type") or txn.get("transactionType") or ""
        ).strip().upper()
        desc = str(txn.get("description", "") or "").strip()
        symbol = str(
            (txn.get("transferItems") or [{}])[0].get("instrument", {}).get("symbol", "")
            if isinstance(txn.get("transferItems"), list) else ""
        ).strip() or None

        category, subcategory = _TXN_TYPE_MAP.get(
            txn_type, ("other", "journal")
        )

        if category == "margin_interest":
            amount = -abs(amount)

        records.append({
            "id":          make_txn_id(account_id, date_str, amount, desc or txn_type),
            "account_id":  account_id,
            "date":        date_str,
            "category":    category,
            "subcategory": subcategory,
            "amount":      amount,
            "currency":    "USD",
            "symbol":      symbol,
            "description": desc[:500],
            "data_source": "mcp",
            "source_file": None,
        })

    return records


def normalize_instruments(
    equity_records: list[dict],
    option_records: list[dict],
    futures_records: list[dict],
) -> list[dict]:
    """Build instruments master-table records from all normalized position lists."""
    records: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for eq in equity_records:
        key = (eq["ticker"], "equity")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol":      eq["ticker"],
            "asset_class": "equity",
            "underlying":  None,
            "name":        eq.get("name"),    # Schwab provides description
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

    for opt in option_records:
        key = (opt["symbol"], "option")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol":      opt["symbol"],
            "asset_class": "option",
            "underlying":  opt.get("underlying"),
            "name":        None,
            "exchange":    None,
            "currency":    "USD",
            "sector":      None,
            "industry":    None,
            "expiry":      opt.get("expiry"),
            "strike":      opt.get("strike"),
            "call_put":    opt.get("call_put"),
            "tick_size":   None,
            "point_value": 100.0,
            "tradable":    None,
        })

    for fut in futures_records:
        key = (fut["symbol"], "future")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol":      fut["symbol"],
            "asset_class": "future",
            "underlying":  fut.get("underlying"),
            "name":        fut.get("description"),
            "exchange":    None,
            "currency":    "USD",
            "sector":      None,
            "industry":    None,
            "expiry":      fut.get("_expiry"),
            "strike":      None,
            "call_put":    None,
            "tick_size":   None,
            "point_value": fut.get("_multiplier"),
            "tradable":    None,
        })

    return records


def normalize_balances(summary_resp: dict) -> dict:
    """
    Extract equity, margin, and market value from get_account_summary response.

    Returns dict with keys: equity, margin, market_value, buying_power.
    """
    margin = float(summary_resp.get("margin_balance", 0) or 0)
    equity = float(summary_resp.get("equity", 0) or 0)
    # Schwab account-level value should be based on net liquidation/account value.
    # long_market_value is securities-only and can overstate totals when combined
    # with cash/margin components elsewhere.
    market_value = float(
        summary_resp.get("liquidation_value")
        or summary_resp.get("net_liquidation")
        or summary_resp.get("account_value")
        or summary_resp.get("equity")
        or 0
    )
    return {
        "equity":        equity,
        "market_value":  market_value,
        "buying_power":  float(summary_resp.get("buying_power", 0) or 0),
        "margin":        abs(margin) if margin < 0 else 0.0,
    }
