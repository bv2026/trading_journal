"""
TradeStation MCP response normalizer.

MCP tools this module handles:
  get-positions-details   → normalize_positions()
  get-balances-details    → normalize_balances()
  get-historical-orders-detailed → normalize_orders() (deposits/dividends only)

TradeStation positions response shape:
  {
    "positions": [
      {
        "accountId": "11908624",
        "positionId": "275135802",
        "symbol": "ACHR",                     # equity
        "assetType": "Stock",
        "quantity": "326",                     # string, negative = short
        "longShort": "Long",
        "marketValue": 1895.69,               # live, embedded — no price fetch needed
        "unrealizedProfitLoss": -105.63
      },
      {
        "symbol": "SPY 260618P665",            # TS option format (NOT OCC)
        "assetType": "StockOption",
        "quantity": "-1",
        "marketValue": -576,
        "unrealizedProfitLoss": 74
      },
      {
        "symbol": "SPXW 260428C7370",          # IndexOption
        "assetType": "IndexOption",
        "quantity": "-2",
        "marketValue": -5,
        "unrealizedProfitLoss": 43
      }
    ]
  }

  cost_basis is NOT returned directly.
  Derived: cost_basis_total = market_value - unrealized_pnl

TradeStation balances response shape:
  {
    "accounts": {
      "11908624": {
        "combined": {
          "currentMarketValue": 51643.29,
          "currentCashBalance": -21190.78,   # negative = margin used
          "currentEquity": 30452.51,
          "costOfPositions": 59142.95
        }
      }
    }
  }

TS → OCC symbol conversion (stored in DB for cross-broker consistency):
  "SPY 260618P665" → "SPY260618P00665000"
  "SPXW 260428C7370" → "SPXW260428C07370000"
"""
from .base import (
    is_ts_option_symbol, parse_ts_option,
    is_currency_entry, make_txn_id, parse_iso_date,
)

# Asset types TradeStation returns
_EQUITY_TYPES = {"Stock", "ETF"}
_OPTION_TYPES = {"StockOption", "IndexOption"}
_FUTURE_TYPES = {"Future", "Forex"}


def normalize_positions(
    positions_resp: dict,
    account_id: str = "TS",
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Split a TradeStation get-positions-details response into equity, option,
    and futures record lists.

    Args:
        positions_resp: Raw dict from get-positions-details MCP tool.
        account_id:     Journal account_id (default: "TS").

    Returns:
        (equity_records, option_records, futures_records)
        equity_records  → db.insert_positions()
        option_records  → db.insert_options()
        futures_records → db.insert_futures()

    Cost basis derivation (TS doesn't return it directly):
        cost_basis_total = market_value - unrealized_pnl
    """
    raw = positions_resp.get("positions", [])

    equity_records:  list[dict] = []
    option_records:  list[dict] = []
    futures_records: list[dict] = []

    for pos in raw:
        symbol     = str(pos.get("symbol", "")).strip()
        asset_type = str(pos.get("assetType", "")).strip()
        if not symbol:
            continue

        qty        = float(pos.get("quantity", 0) or 0)
        mv         = float(pos.get("marketValue", 0) or 0)
        unreal_pnl = float(pos.get("unrealizedProfitLoss", 0) or 0)

        if is_currency_entry(symbol, mv, qty):
            continue  # cash / currency balance entry

        cost_tot = mv - unreal_pnl  # total cost basis

        if asset_type in _EQUITY_TYPES or (
            asset_type not in _OPTION_TYPES | _FUTURE_TYPES
            and not is_ts_option_symbol(symbol)
        ):
            per_share_cost  = (cost_tot / qty) if qty else None
            per_share_price = (mv / qty)       if qty else None
            equity_records.append({
                "account_id":   account_id,
                "ticker":       symbol,
                "name":         None,
                "shares":       qty,
                "cost_basis":   per_share_cost,
                "stored_price": per_share_price,   # live price from MCP
                "sector":       None,
                "industry":     None,
                "asset_type":   "Stock",
                "iv_rank":      None,
                "perf_ytd":     None,
                "atr_pct":      None,
                "data_source":  "mcp",
                "source_file":  None,
            })

        elif asset_type in _OPTION_TYPES or is_ts_option_symbol(symbol):
            parsed = parse_ts_option(symbol)
            if not parsed:
                continue
            price_per_contract = (mv / qty) if qty else None
            option_records.append({
                "account_id":   account_id,
                "symbol":       parsed["occ_symbol"],  # stored in OCC format
                "underlying":   parsed["underlying"],
                "expiry":       parsed["expiry"],
                "strike":       parsed["strike"],
                "call_put":     parsed["call_put"],
                "description":  symbol,               # keep TS format as description
                "qty":          qty,
                "price":        price_per_contract,
                "market_value": mv,
                "data_source":  "mcp",
                "source_file":  None,
            })

        elif asset_type in _FUTURE_TYPES:
            price_per_contract = (mv / qty) if qty else None
            futures_records.append({
                "account_id":   account_id,
                "symbol":       symbol,
                "underlying":   symbol[:2] if len(symbol) > 2 else symbol,
                "description":  symbol,
                "qty":          qty,
                "price":        price_per_contract,
                "market_value": mv,
                "data_source":  "mcp",
                "source_file":  None,
            })

    return equity_records, option_records, futures_records


def normalize_instruments(
    equity_records: list[dict],
    option_records: list[dict],
    futures_records: list[dict] | None = None,
) -> list[dict]:
    """
    Build instruments master-table records from already-normalized position lists.
    Sector/industry for equities are left NULL for yfinance to fill later.
    """
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

    for fut in (futures_records or []):
        key = (fut["symbol"], "future")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol":      fut["symbol"],
            "asset_class": "future",
            "underlying":  fut.get("underlying"),
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


def normalize_balances(balances_resp: dict, account_id: str = "TS") -> dict:
    """
    Extract equity, cash, and margin info from get-balances-details response.

    Returns dict with keys: market_value, equity, cash_balance, margin.
    margin = abs(cash_balance) when cash_balance < 0.
    """
    accounts_map = balances_resp.get("accounts", {})
    # Try journal account_id first, then the numeric TS account key, then first key
    acct_data = (
        accounts_map.get(account_id)
        or next(iter(accounts_map.values()), None)
        or {}
    )
    combined  = acct_data.get("combined", {}) or {}

    cash = float(
        combined.get("currentCashBalance")
        or combined.get("cashBalance")
        or combined.get("cash")
        or 0
    )
    margin = abs(cash) if cash < 0 else 0.0
    equity = float(
        combined.get("currentEquity")
        or combined.get("accountBalance")
        or combined.get("netLiq")
        or combined.get("netLiquidation")
        or 0
    )
    market_value = float(
        combined.get("currentMarketValue")
        or combined.get("marketValue")
        or combined.get("totalMarketValue")
        or 0
    )
    if market_value <= 0 and equity > 0:
        market_value = equity + margin

    return {
        "market_value": market_value,
        "equity":       equity,
        "cash_balance": cash,
        "margin":       margin,
        "cost_of_positions": float(combined.get("costOfPositions", 0) or 0),
    }
