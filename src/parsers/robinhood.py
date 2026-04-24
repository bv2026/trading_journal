"""Robinhood CSV parser — handles both BV and KD account files."""
import pandas as pd
from .utils import parse_amount, parse_date, make_id

# Trans Code → (category, subcategory | None for sign-based)
_CATEGORY_MAP = {
    "ACH":    ("cash_flow",       None),        # sign determines deposit/withdrawal
    "WIRE":   ("cash_flow",       None),
    "XENT":   ("cash_flow",       "withdrawal"),  # transfer to Robinhood Spending
    "CDIV":   ("dividend",        "cash_div"),
    "MDIV":   ("dividend",        "manufactured_div"),
    "MINT":   ("margin_interest", "aggregated_margin"),
    "GOLD":   ("fee",             "subscription_fee"),
    "INT":    ("reward",          "interest"),
    "GDBP":   ("reward",          "securities_lending"),
    "IADJ":   ("other",           "interest_adj"),
}

# Codes we explicitly skip (trades, expirations, splits, etc.)
_SKIP_CODES = {
    "Buy", "Sell", "BTO", "BTC", "STO", "STC",
    "OEXP", "OASGN", "OEXRC", "FUTSWP",
    "SPL", "RSPL", "JNLS", "JNLC", "MISC",
}


def parse(filepath: str, account_id: str) -> list[dict]:
    df = pd.read_csv(filepath, encoding="utf-8-sig", on_bad_lines="skip")
    df.columns = [c.strip() for c in df.columns]

    records = []
    for idx, row in df.iterrows():
        code = str(row.get("Trans Code", "")).strip()
        if not code or code == "nan" or code in _SKIP_CODES:
            continue
        if code not in _CATEGORY_MAP:
            continue  # unrecognized code — likely a trade variant

        category, subcategory = _CATEGORY_MAP[code]
        amount = parse_amount(row.get("Amount", ""))

        if subcategory is None:
            subcategory = "deposit" if amount > 0 else "withdrawal"

        date = parse_date(row.get("Activity Date", ""))
        if not date:
            continue

        symbol = str(row.get("Instrument", "")).strip()
        desc = str(row.get("Description", "")).replace("\n", " ").strip()

        records.append({
            "id":          make_id(account_id, filepath, idx),
            "account_id":  account_id,
            "date":        date,
            "category":    category,
            "subcategory": subcategory,
            "amount":      amount,
            "currency":    "USD",
            "symbol":      symbol if symbol and symbol != "nan" else None,
            "description": desc[:500],
            "source_file": filepath,
        })

    return records
