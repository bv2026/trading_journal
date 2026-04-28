"""Shared utilities for all MCP-based broker fetchers."""
import re
import hashlib
from datetime import datetime, timezone


# OCC option symbol: underlying + YYMMDD + C/P + 8-digit strike (strike * 1000)
# Examples: GOOGL270115C00360000, SPXW260429P06870000, QQQ260618C00670000
_OCC_RE = re.compile(
    r'^(?P<underlying>[A-Z0-9]+?)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$'
)

# ISO 4217 currency codes that brokers sometimes return as position symbols to
# represent a cash balance (e.g. Webull returns "USD" for uninvested cash).
# Some of these are also valid equity tickers (e.g. USD = ProShares Ultra
# Semiconductors ETF).  We disambiguate by cost-per-unit: a cash entry has
# cost/unit ≈ $1.00; a real equity at that ticker will have a very different price.
_CURRENCY_CODES = frozenset({
    "USD", "EUR", "GBP", "CAD", "JPY", "CHF", "AUD", "NZD",
    "HKD", "SGD", "MXN", "BRL", "INR", "CNY", "KRW",
})


# TradeStation option symbol format: "{underlying} {YYMMDD}{C/P}{strike}"
# Examples: "MSFT 260717C425", "SPXW 260428C7370", "SPY 260618P665"
# Strike is a plain integer/decimal — no zero-padding unlike OCC.
_TS_OPT_RE = re.compile(
    r'^(?P<underlying>[A-Z0-9]+)\s+'
    r'(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})'
    r'(?P<cp>[CP])'
    r'(?P<strike>\d+(?:\.\d+)?)$'
)


def is_ts_option_symbol(symbol: str) -> bool:
    return bool(_TS_OPT_RE.match(symbol))


def parse_ts_option(symbol: str) -> dict | None:
    """Parse a TradeStation option symbol into components."""
    m = _TS_OPT_RE.match(symbol)
    if not m:
        return None
    year = 2000 + int(m.group("yy"))
    expiry = f"{year:04d}-{m.group('mm')}-{m.group('dd')}"
    strike = float(m.group("strike"))
    underlying = m.group("underlying")
    cp = m.group("cp")

    # Build OCC-format symbol for storage consistency across brokers:
    # {underlying}{YYMMDD}{C/P}{8-digit strike*1000}
    strike_int = round(strike * 1000)
    occ = f"{underlying}{m.group('yy')}{m.group('mm')}{m.group('dd')}{cp}{strike_int:08d}"

    return {
        "underlying": underlying,
        "expiry":     expiry,
        "call_put":   cp,
        "strike":     strike,
        "occ_symbol": occ,
    }


def is_currency_entry(symbol: str, total_cost: float, quantity: float) -> bool:
    """
    Return True when a position with this symbol is a broker cash/currency
    balance entry, NOT a real security.

    Rule: symbol is a known ISO currency code AND cost-per-unit is within 2%
    of 1.0 (i.e., 1 unit costs ~$1).

    This safely handles "USD" being both the ProShares Ultra Semiconductors
    ETF (cost ≈ $70/share) and Webull's representation of uninvested USD cash
    (cost = $1.00/unit).  Any broker returning EUR/GBP/etc. as a position is
    also caught here.
    """
    if symbol not in _CURRENCY_CODES:
        return False
    if not quantity:
        return False
    cost_per_unit = abs(total_cost) / abs(quantity)
    return cost_per_unit < 1.02   # ≤ $1.02 per unit → treat as cash


def is_occ_symbol(symbol: str) -> bool:
    return bool(_OCC_RE.match(symbol))


def parse_occ(symbol: str) -> dict | None:
    """Parse OCC option symbol into components. Returns None if not an option symbol."""
    m = _OCC_RE.match(symbol)
    if not m:
        return None
    year = 2000 + int(m.group("yy"))
    expiry = f"{year:04d}-{m.group('mm')}-{m.group('dd')}"
    strike = int(m.group("strike")) / 1000.0
    return {
        "underlying": m.group("underlying"),
        "expiry":     expiry,
        "call_put":   m.group("cp"),
        "strike":     strike,
    }


def make_txn_id(account_id: str, date: str, amount: float, description: str) -> str:
    raw = f"{account_id}|{date}|{amount:.4f}|{description[:80]}"
    return hashlib.md5(raw.encode()).hexdigest()


def parse_iso_date(ts: str) -> str | None:
    """Convert ISO-8601 timestamp to YYYY-MM-DD string."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None
