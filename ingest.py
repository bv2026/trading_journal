"""
Ingest all broker CSV files into data/journal.db.
Run: python ingest.py
Re-running is safe — all transactions are cleared and re-inserted each time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src import db
from src.parsers import robinhood, webull, tradestation, schwab, tradier, coinbase, fidelity

ACTIVITY = Path(__file__).parent / "activity"

ACCOUNTS = [
    {"account_id": "RH-BV",    "broker": "robinhood",    "account_type": "investment", "holder": "BV"},
    {"account_id": "RH-KD",    "broker": "robinhood",    "account_type": "investment", "holder": "KD"},
    {"account_id": "WEBULL",   "broker": "webull",       "account_type": "investment", "holder": None},
    {"account_id": "TS",       "broker": "tradestation", "account_type": "investment", "holder": None},
    {"account_id": "SCHWAB",   "broker": "schwab",       "account_type": "investment", "holder": None},
    {"account_id": "TRADIER",  "broker": "tradier",      "account_type": "investment", "holder": None},
    {"account_id": "COINBASE", "broker": "coinbase",     "account_type": "crypto",     "holder": None},
    {"account_id": "FIDELITY", "broker": "fidelity",     "account_type": "investment", "holder": None},
]

PARSERS = [
    (robinhood.parse,       ACTIVITY / "robinhood-inv-bv.csv",  "RH-BV"),
    (robinhood.parse,       ACTIVITY / "robinhood-inv-kd.csv",  "RH-KD"),
    (webull.parse_inv,      ACTIVITY / "WEBULL-inv.csv",        "WEBULL"),
    (webull.parse_cash,     ACTIVITY / "WEBULL-cash.csv",       "WEBULL"),
    (tradestation.parse,    ACTIVITY / "tdstation-cash.csv",    "TS"),
    (schwab.parse,          ACTIVITY / "schwab.csv",            "SCHWAB"),
    (tradier.parse,         ACTIVITY / "tradier.csv",           "TRADIER"),
    (coinbase.parse,        ACTIVITY / "coinbase-main.csv",     "COINBASE"),
    (fidelity.parse,        ACTIVITY / "fidelity_Investment_income_balance.csv", "FIDELITY"),
]


def run():
    print("Initializing database …")
    db.init_db()

    print("Clearing existing transactions …")
    db.clear_transactions()

    print("Upserting accounts …")
    db.upsert_accounts(ACCOUNTS)

    all_records: list[dict] = []

    for parse_fn, path, acct in PARSERS:
        if not path.exists():
            print(f"  SKIP  {acct}: file not found ({path.name})")
            continue
        try:
            # parse_inv / parse_cash take only filepath; robinhood/schwab/etc. take filepath + account_id
            import inspect
            sig = inspect.signature(parse_fn)
            if len(sig.parameters) >= 2:
                recs = parse_fn(str(path), acct)
            else:
                recs = parse_fn(str(path))
            print(f"  OK    {acct}: {len(recs):>5} records")
            all_records.extend(recs)
        except Exception as exc:
            print(f"  ERROR {acct}: {exc}")

    # Deduplicate by id (same row re-parsed should produce same id)
    seen: set[str] = set()
    unique = []
    for r in all_records:
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)

    dupes = len(all_records) - len(unique)
    print(f"\nTotal: {len(unique)} records ({dupes} duplicates removed)")

    db.insert_transactions(unique)
    print("Ingest complete.")


if __name__ == "__main__":
    run()
