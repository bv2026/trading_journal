"""
Webull MCP response normalizer.

Webull has 4 account types; all are fetched and written to separate journal
account_ids:

  Account class          Journal account_id   Account type
  ─────────────────────  ───────────────────  ────────────
  INDIVIDUAL_MARGIN      WEBULL               equity (live)
  INDIVIDUAL_CASH        WEBULL-CASH          equity (live)
  EVENTS_CASH            WEBULL-EVENTS        equity (live)
  FUTURES                WEBULL-FUT           futures

⚠️  The Webull MCP returns formatted TEXT in the `result` field, not raw JSON.
All parse_* functions below consume that text string.

MCP tools this module handles:
  get_account_list      → account_map_from_list()
  get_account_positions → parse_positions_text()  then  normalize_positions()
  get_account_balance   → parse_balance_text()    then  normalize_balances()

Position text format (one line per holding):
  "       AMD  Qty:  5.19412  Type: EQUITY  Cost:     283.16  Last:     321.00  Unrealized P&L:     196.57  Currency: USD"

  Cost  = per-share cost basis  (NOT total — already per unit)
  Last  = current live price
  Type  = EQUITY | OPTION | FUTURE | CRYPTO

Balance text format:
  "Total Cash Balance:    -37654.94"
  "Total Market Value:    75177.30"
  "Net Liquidation:       37522.36"

  Negative cash balance = margin used.
"""
import re
from .base import is_occ_symbol, parse_occ, is_currency_entry

# ── Account class → journal account_id mapping ────────────────────────────────
# Keyed by account_class from get_account_list; stable even if internal IDs
# change after re-linking.
CLASS_TO_ACCOUNT_ID: dict[str, str] = {
    "INDIVIDUAL_MARGIN": "WEBULL",
    "INDIVIDUAL_CASH":   "WEBULL-CASH",
    "EVENTS_CASH":       "WEBULL-EVENTS",
    "FUTURES":           "WEBULL-FUT",
}

# ── Text parsing regexes ──────────────────────────────────────────────────────

# Position line: symbol  Qty: X  Type: Y  Cost: Z  Last: W  Unrealized P&L: V  Currency: U
_POS_RE = re.compile(
    r'^\s*(?P<symbol>\S+)\s+'
    r'Qty:\s*(?P<qty>[-\d.]+)\s+'
    r'Type:\s*(?P<asset_type>\w+)\s+'
    r'Cost:\s*(?P<cost>[-\d.]+)\s+'
    r'Last:\s*(?P<last>[-\d.]+)\s+'
    r'Unrealized P&L:\s*(?P<unreal_pnl>[-\d.]+)\s+'
    r'Currency:\s*(?P<currency>\w+)'
)

_CASH_RE   = re.compile(r'Total Cash Balance:\s*([-\d.]+)')
_MV_RE     = re.compile(r'Total Market Value:\s*([-\d.]+)')
_NETLIQ_RE = re.compile(r'Net Liquidation:\s*([-\d.]+)')


def _extract_float(text: str, pattern: re.Pattern, default: float = 0.0) -> float:
    m = pattern.search(text)
    if not m:
        return default
    try:
        return float(m.group(1))
    except ValueError:
        return default


# ── Account map ───────────────────────────────────────────────────────────────

def account_map_from_list(account_list_result: str) -> dict[str, str]:
    """
    Parse get_account_list result text into {webull_account_id: journal_account_id}.

    Falls back to CLASS_TO_ACCOUNT_ID for any class not in the map.
    """
    mapping: dict[str, str] = {}
    # Lines like: "1. ID: 8AGMH...  Number: 5KM79869  Type: MARGIN  Class: INDIVIDUAL_MARGIN  Label: ..."
    for m in re.finditer(
        r'\d+\.\s+ID:\s*(\S+).*?Class:\s*(\S+)',
        account_list_result
    ):
        wb_id     = m.group(1)
        wb_class  = m.group(2)
        journal_id = CLASS_TO_ACCOUNT_ID.get(wb_class)
        if journal_id:
            mapping[wb_id] = journal_id
    return mapping


# ── Position text parser ──────────────────────────────────────────────────────

def parse_positions_text(result_text: str) -> list[dict]:
    """
    Parse the raw `result` string from get_account_positions into a list of
    dicts with keys: symbol, qty, asset_type, cost_per_unit, last_price,
    unreal_pnl, currency.

    Returns empty list if result contains "No data available" or no matches.
    """
    if "No data available" in result_text or not result_text.strip():
        return []

    rows: list[dict] = []
    for line in result_text.splitlines():
        m = _POS_RE.match(line)
        if not m:
            continue
        try:
            rows.append({
                "symbol":       m.group("symbol").strip(),
                "qty":          float(m.group("qty")),
                "asset_type":   m.group("asset_type").upper(),
                "cost_per_unit": float(m.group("cost")),   # per-share/per-unit
                "last_price":   float(m.group("last")),
                "unreal_pnl":   float(m.group("unreal_pnl")),
                "currency":     m.group("currency"),
            })
        except ValueError:
            continue
    return rows


# ── Normalizers ───────────────────────────────────────────────────────────────

def normalize_positions(
    parsed_rows: list[dict],
    account_id: str,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """
    Route parsed position rows into equity, option, futures, and crypto record lists.

    Args:
        parsed_rows: Output of parse_positions_text().
        account_id:  Journal account_id (e.g. "WEBULL", "WEBULL-FUT").

    Returns:
        (equity_records, option_records, futures_records, crypto_records)
    """
    equity_records:  list[dict] = []
    option_records:  list[dict] = []
    futures_records: list[dict] = []
    crypto_records:  list[dict] = []

    for row in parsed_rows:
        symbol     = row["symbol"]
        qty        = row["qty"]
        cost       = row["cost_per_unit"]   # already per-unit
        last       = row["last_price"]
        asset_type = row["asset_type"]      # EQUITY | OPTION | FUTURE | CRYPTO

        total_cost = cost * qty

        if is_currency_entry(symbol, total_cost, qty):
            continue

        if asset_type == "EQUITY":
            equity_records.append({
                "account_id":   account_id,
                "ticker":       symbol,
                "name":         None,
                "shares":       qty,
                "cost_basis":   cost,
                "stored_price": last,
                "sector":       None,
                "industry":     None,
                "asset_type":   "Stock",
                "iv_rank":      None,
                "perf_ytd":     None,
                "atr_pct":      None,
                "data_source":  "mcp",
                "source_file":  None,
            })

        elif asset_type == "OPTION":
            # Webull may return OCC symbols for options
            parsed = parse_occ(symbol) if is_occ_symbol(symbol) else None
            mv = last * qty * 100 if last else None  # options: price is per-share
            option_records.append({
                "account_id":   account_id,
                "symbol":       symbol,
                "underlying":   parsed["underlying"] if parsed else None,
                "expiry":       parsed["expiry"]     if parsed else None,
                "strike":       parsed["strike"]     if parsed else None,
                "call_put":     parsed["call_put"]   if parsed else None,
                "description":  symbol,
                "qty":          qty,
                "price":        last,
                "market_value": mv,
                "data_source":  "mcp",
                "source_file":  None,
            })

        elif asset_type == "FUTURE":
            mv = last * qty if last else None
            futures_records.append({
                "account_id":   account_id,
                "symbol":       symbol,
                "underlying":   symbol[:2] if len(symbol) > 2 else symbol,
                "description":  symbol,
                "qty":          qty,
                "price":        last,
                "market_value": mv,
                "data_source":  "mcp",
                "source_file":  None,
            })

        elif asset_type == "CRYPTO":
            mv = last * qty if last else None
            crypto_records.append({
                "account_id":   account_id,
                "symbol":       symbol,
                "name":         None,
                "qty":          qty,
                "price":        last,
                "cost_basis":   cost * qty,  # total cost for crypto
                "market_value": mv,
                "data_source":  "mcp",
                "source_file":  None,
            })

    return equity_records, option_records, futures_records, crypto_records


def normalize_instruments(
    equity_records: list[dict],
    option_records: list[dict],
    futures_records: list[dict],
    crypto_records: list[dict],
) -> list[dict]:
    """Build instruments master-table records from normalized position lists."""
    records: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for eq in equity_records:
        key = (eq["ticker"], "equity")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol": eq["ticker"], "asset_class": "equity",
            "underlying": None, "name": None, "exchange": None,
            "currency": "USD", "sector": None, "industry": None,
            "expiry": None, "strike": None, "call_put": None,
            "tick_size": None, "point_value": None, "tradable": None,
        })

    for opt in option_records:
        key = (opt["symbol"], "option")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol": opt["symbol"], "asset_class": "option",
            "underlying": opt.get("underlying"), "name": None, "exchange": None,
            "currency": "USD", "sector": None, "industry": None,
            "expiry": opt.get("expiry"), "strike": opt.get("strike"),
            "call_put": opt.get("call_put"),
            "tick_size": None, "point_value": 100.0, "tradable": None,
        })

    for fut in futures_records:
        key = (fut["symbol"], "future")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol": fut["symbol"], "asset_class": "future",
            "underlying": fut.get("underlying"), "name": None, "exchange": None,
            "currency": "USD", "sector": None, "industry": None,
            "expiry": None, "strike": None, "call_put": None,
            "tick_size": None, "point_value": None, "tradable": None,
        })

    for cry in crypto_records:
        key = (cry["symbol"], "crypto")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol": cry["symbol"], "asset_class": "crypto",
            "underlying": None, "name": None, "exchange": None,
            "currency": "USD", "sector": None, "industry": None,
            "expiry": None, "strike": None, "call_put": None,
            "tick_size": None, "point_value": None, "tradable": None,
        })

    return records


def parse_balance_text(result_text: str) -> dict:
    """
    Parse the raw `result` string from get_account_balance.

    Returns dict with keys: market_value, cash_balance, net_liquidation, margin.
    """
    cash = _extract_float(result_text, _CASH_RE)
    return {
        "market_value":    _extract_float(result_text, _MV_RE),
        "cash_balance":    cash,
        "net_liquidation": _extract_float(result_text, _NETLIQ_RE),
        "margin":          abs(cash) if cash < 0 else 0.0,
    }
