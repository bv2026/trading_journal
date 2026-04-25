"""
Fidelity yearly investment income summary parser.

Reads the annual summary CSV exported from Fidelity's "Investment Income"
report (one row per calendar year).  Each year is expanded into up to four
transaction records:

  dividend       / cash_div    — annual dividend income
  margin_interest/ monthly     — annual interest cost (negative values)
  reward         / interest    — annual interest income (positive values, rare)
  cash_flow      / deposit     — total deposits for the year
  cash_flow      / withdrawal  — total withdrawals for the year

Only years >= START_YEAR are ingested.  Re-running ingest is safe: IDs are
keyed on (account_id, year, subcategory) so the same row always produces the
same ID, and ingest.py clears the table before re-inserting.

Expected CSV structure (Fidelity "Investment income Export"):
  Row 0-2 : report header / date range
  Row 3   : column headers — "Yearly, Beginning balance, ..., Ending balance"
  Row 4+  : one data row per year, newest first
  Final   : "Total" row followed by footnotes (parsing stops here)

Year cell formats handled:
  "2025"                        → year=2025, date=2025-12-31
  "2026(As of Apr-23-2026)"     → year=2026, date=2026-04-23
  "2015(As of Dec-01-2015)"     → year=2015, date=2015-12-01

To update: replace the Fidelity CSV and re-run `python ingest.py`.
The current-year row and any newly added future-year rows are picked up
automatically — no code changes required.
"""

import re
import pandas as pd
from .utils import parse_amount, make_id

# Only ingest years at or after this cutoff.
START_YEAR = 2020

_AS_OF_RE  = re.compile(r"\(As of (\w+-\d{1,2}-\d{4})\)", re.I)
_YEAR_RE   = re.compile(r"\b(\d{4})\b")

# Canonical column names as they appear after stripping whitespace.
_REQUIRED_COLS = {"Dividends", "Interest", "Deposits", "Withdrawals"}


def _year_date(year_str: str) -> tuple[int, str]:
    """Parse a Fidelity year cell into (year_int, 'YYYY-MM-DD').

    Handles plain years ("2025") and partial-year cells with an embedded
    "As of" date ("2026(As of Apr-23-2026)").  Raises ValueError if neither
    a 4-digit year nor a parseable date can be extracted.
    """
    m_ao = _AS_OF_RE.search(year_str)
    if m_ao:
        raw = m_ao.group(1)                 # e.g. "Apr-23-2026"
        date = pd.to_datetime(raw, format="%b-%d-%Y").strftime("%Y-%m-%d")
    else:
        m_yr = _YEAR_RE.search(year_str)
        if not m_yr:
            raise ValueError(f"Cannot parse year from: {year_str!r}")
        yr = int(m_yr.group(1))
        date = f"{yr}-12-31"

    m_yr = _YEAR_RE.search(year_str)
    if not m_yr:
        raise ValueError(f"Cannot extract year integer from: {year_str!r}")
    return int(m_yr.group(1)), date


def parse(filepath: str, account_id: str = "FIDELITY") -> list[dict]:
    """Parse a Fidelity yearly income summary CSV and return transaction dicts."""
    # The actual column header lives on row index 3; rows 0-2 are report metadata.
    try:
        df = pd.read_csv(filepath, skiprows=3, header=0,
                         encoding="utf-8-sig", on_bad_lines="skip")
    except Exception as exc:
        raise ValueError(f"Cannot read Fidelity CSV {filepath}: {exc}") from exc

    df.columns = df.columns.str.strip()

    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"Fidelity CSV missing expected columns: {missing}. "
            f"Found: {list(df.columns)}"
        )

    records: list[dict] = []

    for idx, row in df.iterrows():
        yr_str = str(row.iloc[0]).strip()

        # Stop at the "Total" summary row or blank lines / footnotes.
        if not yr_str or yr_str in ("nan", "") or yr_str.lower().startswith("total"):
            break

        try:
            yr, date = _year_date(yr_str)
        except ValueError:
            continue                        # skip unparseable rows silently

        if yr < START_YEAR:
            continue

        dividends   = parse_amount(row.get("Dividends",   0))
        interest    = parse_amount(row.get("Interest",    0))
        deposits    = parse_amount(row.get("Deposits",    0))
        withdrawals = parse_amount(row.get("Withdrawals", 0))

        def _rec(category: str, subcategory: str, amount: float) -> dict:
            return {
                # Stable ID: keyed on year + subcategory so the same annual
                # summary row always hashes to the same value regardless of filename.
                "id":          make_id(account_id, str(yr), 0, subcategory),
                "account_id":  account_id,
                "date":        date,
                "category":    category,
                "subcategory": subcategory,
                "amount":      amount,
                "currency":    "USD",
                "symbol":      None,
                "description": f"Fidelity {yr} annual {subcategory}",
                "source_file": filepath,
            }

        if dividends != 0:
            records.append(_rec("dividend", "cash_div", dividends))

        if interest != 0:
            # Fidelity reports margin/loan interest as a negative value.
            if interest < 0:
                records.append(_rec("margin_interest", "monthly", interest))
            else:
                records.append(_rec("reward", "interest", interest))

        if deposits != 0:
            records.append(_rec("cash_flow", "deposit", +abs(deposits)))

        if withdrawals != 0:
            records.append(_rec("cash_flow", "withdrawal", -abs(withdrawals)))

    return records
