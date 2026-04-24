"""TradeStation cash-activity CSV parser."""
import pandas as pd
from .utils import parse_amount, parse_date, make_id


import re as _re

# Matches TradeStation margin interest format: "12.50000%05/01-05/30  $738"
_TS_MARGIN_RE = _re.compile(r"^\d+\.\d+%\d{2}/\d{2}")


def _categorize(desc: str, amount: float) -> tuple[str, str]:
    u = desc.strip().upper()
    if u.startswith("CASH RECEIVED ACH") or u.startswith("ACH DEPOSIT"):
        return "cash_flow", "deposit"
    if u.startswith("RETURNED ACH") or u.startswith("RETURN ACH"):
        # Bounced / returned deposit or associated fee
        return ("fee", "platform_fee") if "FEE" in u else ("cash_flow", "withdrawal")
    if u.startswith("ACH WITHDRAWAL") or u.startswith("ACH DEBIT"):
        return "cash_flow", "withdrawal"
    if u.startswith("JNL TO") or u.startswith("JNL FROM") or u.startswith("JNL FRM"):
        return "cash_flow", "internal_transfer"
    if u.startswith("TRADESTATION CHARGES"):
        return "fee", "platform_fee"
    if u.startswith("FPL REVENUE") or u.startswith("FPL INTEREST"):
        return "reward", "securities_lending"
    if u.startswith("SHORT ACCT"):
        return "other", "mark_to_market"
    if _TS_MARGIN_RE.match(desc.strip()):
        return "margin_interest", "monthly"
    # Everything else in the cash file is a dividend distribution from a fund
    return "dividend", "cash_div"


def parse(filepath: str, account_id: str = "TS") -> list[dict]:
    df = pd.read_csv(filepath, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]

    records = []
    for idx, row in df.iterrows():
        desc = str(row.get("Description", "")).strip()
        if not desc or desc == "nan":
            continue

        amount = parse_amount(row.get("Amount", ""))
        date = parse_date(row.get("Date", ""))
        if not date:
            continue

        category, subcategory = _categorize(desc, amount)

        records.append({
            "id":          make_id(account_id, filepath, idx),
            "account_id":  account_id,
            "date":        date,
            "category":    category,
            "subcategory": subcategory,
            "amount":      amount,
            "currency":    "USD",
            "symbol":      None,
            "description": desc[:500],
            "source_file": filepath,
        })

    return records
