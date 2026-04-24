"""Charles Schwab CSV parser."""
import pandas as pd
from .utils import parse_amount, parse_date, make_id

_ACTION_MAP = {
    "Cash Dividend":      ("dividend",        "cash_div"),
    "Non-Qualified Div":  ("dividend",        "nonqualified_div"),
    "Reinvest Dividend":  ("dividend",        "reinvested_div"),
    "Rein Sub Inc Pmt":   ("dividend",        "reinvested_sub_income"),
    "Substitute Inc Pmt": ("dividend",        "substitute_income"),
    "Pr Yr Div Reinvest": ("dividend",        "prior_yr_div"),
    "MoneyLink Transfer": ("cash_flow",       None),   # sign determines direction
    "Margin Interest":    ("margin_interest", "monthly"),
    "Credit Interest":    ("reward",          "interest"),
}

# Actions we skip entirely (trades, sweeps, corporate actions)
_SKIP_ACTIONS = {
    "Buy", "Sell", "Buy to Open", "Sell to Open", "Buy to Close", "Sell to Close",
    "Reinvest Shares", "Assigned", "Expired", "Reverse Split",
    "Futures MM Sweep", "Journal",
}


def parse(filepath: str, account_id: str = "SCHWAB") -> list[dict]:
    df = pd.read_csv(filepath, encoding="utf-8-sig", on_bad_lines="skip")
    df.columns = [c.strip() for c in df.columns]

    records = []
    for idx, row in df.iterrows():
        action = str(row.get("Action", "")).strip()
        if not action or action == "nan" or action in _SKIP_ACTIONS:
            continue
        if action not in _ACTION_MAP:
            continue

        category, subcategory = _ACTION_MAP[action]
        amount = parse_amount(row.get("Amount", ""))

        if subcategory is None:
            subcategory = "deposit" if amount > 0 else "withdrawal"

        date = parse_date(row.get("Date", ""))
        if not date:
            continue

        symbol = str(row.get("Symbol", "")).strip()
        desc = str(row.get("Description", "")).strip()

        records.append({
            "id":          make_id(account_id, filepath, idx),
            "account_id":  account_id,
            "date":        date,
            "category":    category,
            "subcategory": subcategory,
            "amount":      amount,
            "currency":    "USD",
            "symbol":      symbol if symbol and symbol != "nan" else None,
            "description": f"{action}: {desc}"[:500],
            "source_file": filepath,
        })

        # Capture non-zero commissions from any row as a separate fee record
        fees = parse_amount(row.get("Fees & Comm", ""))
        if fees != 0:
            records.append({
                "id":          make_id(account_id, filepath, f"{idx}_fee"),
                "account_id":  account_id,
                "date":        date,
                "category":    "fee",
                "subcategory": "commission",
                "amount":      -abs(fees),
                "currency":    "USD",
                "symbol":      symbol if symbol and symbol != "nan" else None,
                "description": f"Commission: {desc}"[:500],
                "source_file": filepath,
            })

    return records
