import re
import hashlib
from dateutil.parser import parse as _dateutil_parse


def parse_amount(val) -> float:
    """Parse currency strings like ($1,234.56), $1,234.56, -1,234.56 to float."""
    if val is None:
        return 0.0
    s = str(val).strip().replace("\xa0", "")
    if not s or s in ("-", "N/A", "nan", ""):
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = re.sub(r"[$,\s]", "", s)
    if s.startswith("-"):
        negative = True
        s = s[1:]
    try:
        result = float(s)
        return -result if negative else result
    except ValueError:
        return 0.0


def parse_date(val) -> str | None:
    """Return YYYY-MM-DD from various date/datetime formats."""
    if not val:
        return None
    s = str(val).strip()
    if not s or s == "nan":
        return None
    # Schwab "04/09/2026 as of 04/08/2026" — take the first date
    s = re.split(r"\s+as\s+of\s+", s, flags=re.IGNORECASE)[0].strip()
    # Drop time/timezone portion "08/15/2024 00:00:00 EDT"
    s = s.split()[0]
    try:
        return _dateutil_parse(s).strftime("%Y-%m-%d")
    except Exception:
        return None


def make_id(account_id: str, date: str, amount: float | str, note: str = "") -> str:
    """Stable content-based transaction ID.

    Keyed on (account_id, date, amount, note) so the same transaction always
    produces the same hash regardless of which CSV file it came from or what
    row it sits on.  This enables incremental ingest: re-downloading only the
    last N days of activity and running ingest.py will add only genuinely new
    records without duplicating anything already in the database.

    *note* should be discriminating enough to distinguish two transactions on
    the same day with the same dollar amount (e.g. include the description or
    transaction type).  For brokers that supply a native stable ID (Coinbase),
    pass that ID directly as the record's ``id`` field instead of calling this.
    """
    try:
        amt_str = f"{float(amount):.4f}"
    except (TypeError, ValueError):
        amt_str = str(amount)
    key = f"{account_id}|{date}|{amt_str}|{note[:120]}"
    return hashlib.md5(key.encode()).hexdigest()
