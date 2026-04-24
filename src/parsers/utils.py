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


def make_id(account_id: str, source_file: str, row_idx: int) -> str:
    key = f"{account_id}|{source_file}|{row_idx}"
    return hashlib.md5(key.encode()).hexdigest()
