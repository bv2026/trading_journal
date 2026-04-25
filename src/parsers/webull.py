"""Webull CSV parser — investment and cash account variants."""
import pandas as pd
from .utils import parse_amount, parse_date, make_id


def _row_to_record(row, idx: int, account_id: str, filepath: str) -> dict | None:
    amount = parse_amount(row.get("AMOUNT", ""))
    date = parse_date(row.get("DATE", ""))
    if not date:
        return None
    desc = str(row.get("DESCRIPTION", "")).strip()
    return {
        "id":          make_id(account_id, date, amount, desc),
        "account_id":  account_id,
        "date":        date,
        "amount":      amount,
        "currency":    "USD",
        "symbol":      None,
        "description": desc[:500],
        "source_file": filepath,
    }


def parse_inv(filepath: str, account_id: str = "WB-INV") -> list[dict]:
    df = pd.read_csv(filepath, encoding="utf-8-sig")
    df.columns = [c.strip().upper() for c in df.columns]

    records = []
    for idx, row in df.iterrows():
        typ = str(row.get("TYPE", "")).strip()
        if typ in ("Trade", "Option Exercise", ""):
            continue

        base = _row_to_record(row, idx, account_id, filepath)
        if base is None:
            continue

        desc = base["description"].lower()
        if typ == "Dividends":
            base["category"], base["subcategory"] = "dividend", "cash_div"
        elif typ == "Interest":
            # "Margin loan interest" = cost; "Margin Rebate" etc. = reward
            if "margin loan" in desc or "margin interest" in desc:
                base["category"], base["subcategory"] = "margin_interest", "monthly"
            else:
                base["category"], base["subcategory"] = "reward", "interest"
        elif typ in ("Transfer", "Cash Transfer"):
            base["category"] = "cash_flow"
            base["subcategory"] = (
                "internal_transfer" if "internal" in desc
                else ("deposit" if base["amount"] > 0 else "withdrawal")
            )
        elif typ == "Other":
            base["category"], base["subcategory"] = "other", "other"
        else:
            continue

        records.append(base)
    return records


def parse_cash(filepath: str, account_id: str = "WB-CASH") -> list[dict]:
    df = pd.read_csv(filepath, encoding="utf-8-sig")
    df.columns = [c.strip().upper() for c in df.columns]

    records = []
    for idx, row in df.iterrows():
        typ = str(row.get("TYPE", "")).strip()
        if not typ or typ == "nan":
            continue

        base = _row_to_record(row, idx, account_id, filepath)
        if base is None:
            continue

        desc = base["description"].lower()
        if typ in ("Transfer", "Cash Transfer"):
            base["category"] = "cash_flow"
            base["subcategory"] = (
                "internal_transfer" if "internal" in desc
                else ("deposit" if base["amount"] > 0 else "withdrawal")
            )
        elif typ == "Interest":
            base["category"], base["subcategory"] = "reward", "interest"
        elif typ == "Platform Rewards":
            base["category"], base["subcategory"] = "reward", "platform_reward"
        elif typ == "Fees":
            base["category"], base["subcategory"] = "fee", "platform_fee"
        else:
            continue

        records.append(base)
    return records
