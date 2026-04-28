"""Shared utilities for all MCP-based broker fetchers."""
import re
import hashlib
from datetime import datetime, timezone


# OCC option symbol: underlying + YYMMDD + C/P + 8-digit strike (strike * 1000)
# Examples: GOOGL270115C00360000, SPXW260429P06870000, QQQ260618C00670000
_OCC_RE = re.compile(
    r'^(?P<underlying>[A-Z0-9]+?)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$'
)


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
