"""Tradier CSV parser — MONEY_MOVEMENTS rows only."""
import re
import pandas as pd
from .utils import parse_amount, parse_date, make_id

_ACH_DEPOSIT     = re.compile(r"ACH DEPOSIT",                          re.I)
_ACH_WITHDRAWAL  = re.compile(r"ACH WITHDRAWAL|ACH DEBIT",             re.I)
_MARGIN_INTEREST = re.compile(r"FROM \d{2}/\d{2} THRU \d{2}/\d{2}",   re.I)
_DIVIDEND        = re.compile(r"CASH DIV|DIVIDEND|NON-QUALIFIED",      re.I)
_OPTION_SETTLE   = re.compile(r"\b(CALL|PUT)\s+\w+\s+\d{2}/\d{2}/\d{2}", re.I)
_CLEARING_FEE    = re.compile(r"CLEARING FEE|AGENCY PROCESSING FEE",   re.I)


def _categorize(desc: str, amount: float) -> tuple[str, str]:
    if _ACH_DEPOSIT.search(desc):
        return "cash_flow", "deposit"
    if _ACH_WITHDRAWAL.search(desc):
        return "cash_flow", "withdrawal"
    if _MARGIN_INTEREST.search(desc):
        return "margin_interest", "monthly"
    if _DIVIDEND.search(desc):
        return "dividend", "cash_div"
    if _OPTION_SETTLE.search(desc):
        return "other", "option_settlement"
    if _CLEARING_FEE.search(desc):
        return "fee", "clearing_fee"
    # ADR entries without "processing fee" in description are typically dividends
    if desc.startswith("***") and amount > 0:
        return "dividend", "cash_div"
    return "other", "money_movement"


def parse(filepath: str, account_id: str = "TRADIER") -> list[dict]:
    df = pd.read_csv(filepath, encoding="utf-8-sig")
    # Strip surrounding quotes from column names Tradier includes
    df.columns = [c.strip().strip('"') for c in df.columns]

    records = []
    for idx, row in df.iterrows():
        row_type = str(row.get("Type", "")).strip().strip('"')
        if row_type != "MONEY_MOVEMENTS":
            continue

        desc   = str(row.get("Description", "")).strip().strip('"')
        amount = parse_amount(row.get("Net Amount", ""))
        date   = parse_date(row.get("Trade Date", ""))
        if not date:
            continue

        category, subcategory = _categorize(desc, amount)

        # Margin interest is always a cost — ensure sign is negative
        if category == "margin_interest":
            amount = -abs(amount)

        symbol = str(row.get("Symbol", "")).strip().strip('"')

        records.append({
            "id":          make_id(account_id, date, amount, desc),
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
