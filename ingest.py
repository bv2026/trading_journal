"""
Ingest broker CSV files into data/journal.db.

Default (incremental):
    python ingest.py
    Only new records are added; existing ones are left untouched.
    Drop only the latest CSV export from each broker — no need to re-download
    full history every time.

Full rebuild:
    python ingest.py --reset
    Clears all transactions and reloads from every CSV currently in activity/.
    Use this once after first setup or whenever you want a clean slate.

Special cases:
    Fidelity — yearly summary file is always refreshed (current-year figures
    change as the year progresses), regardless of --reset.
"""
import sys
import argparse
import inspect
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src import db
from src.parsers import robinhood, webull, tradestation, schwab, tradier, coinbase, fidelity
from src.parsers import positions_csv

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

# Accounts whose CSVs are yearly summaries rather than running transaction logs.
# These are always fully refreshed so mid-year updates are picked up.
ALWAYS_REFRESH = {"FIDELITY"}

PARSERS = [
    (robinhood.parse,       ACTIVITY / "robinhood-inv-bv.csv",                          "RH-BV"),
    (robinhood.parse,       ACTIVITY / "robinhood-inv-kd.csv",                          "RH-KD"),
    (webull.parse_inv,      ACTIVITY / "WEBULL-inv.csv",                                "WEBULL"),
    (webull.parse_cash,     ACTIVITY / "WEBULL-cash.csv",                               "WEBULL"),
    (tradestation.parse,    ACTIVITY / "tdstation-cash.csv",                            "TS"),
    (schwab.parse,          ACTIVITY / "schwab.csv",                                    "SCHWAB"),
    (tradier.parse,         ACTIVITY / "tradier.csv",                                   "TRADIER"),
    (coinbase.parse,        ACTIVITY / "coinbase-main.csv",                             "COINBASE"),
    (fidelity.parse,        ACTIVITY / "fidelity_Investment_income_balance.csv",        "FIDELITY"),
]

# Per-account positions CSVs — always fully replaced on each ingest.
POSITION_FILES = [
    (ACTIVITY / "positions-scwb.csv",     "SCHWAB"),
    (ACTIVITY / "positions-trader.csv",   "TRADIER"),
    (ACTIVITY / "positions-tradestn.csv", "TS"),
    (ACTIVITY / "positions-rh-bv.csv",    "RH-BV"),
    (ACTIVITY / "positions-rh-kd.csv",    "RH-KD"),
    (ACTIVITY / "positions-webull.csv",   "WEBULL"),
    (ACTIVITY / "positions-fidelity.csv", "FIDELITY"),
]


def run(reset: bool = False) -> None:
    print("Initializing database …")
    db.init_db()
    db.upsert_accounts(ACCOUNTS)

    if reset:
        print("--reset: clearing all existing transactions …")
        db.clear_transactions()

    total_new = 0
    total_skipped = 0

    for parse_fn, path, acct in PARSERS:
        if not path.exists():
            print(f"  SKIP  {acct}: file not found ({path.name})")
            continue

        try:
            sig = inspect.signature(parse_fn)
            recs = parse_fn(str(path), acct) if len(sig.parameters) >= 2 else parse_fn(str(path))
        except Exception as exc:
            print(f"  ERROR {acct}: {exc}")
            continue

        # Fidelity is a yearly summary whose current-year row changes over time.
        # Always delete and re-insert so updated figures are picked up.
        if acct in ALWAYS_REFRESH and not reset:
            db.delete_by_account(acct)

        # Deduplicate within the parsed batch (e.g. two Webull files for same account)
        seen_in_batch: set[str] = set()
        unique_recs = []
        for r in recs:
            if r["id"] not in seen_in_batch:
                seen_in_batch.add(r["id"])
                unique_recs.append(r)

        inserted = db.insert_transactions(unique_recs)
        skipped  = len(unique_recs) - inserted
        total_new     += inserted
        total_skipped += skipped

        status = f"{inserted:>5} new"
        if skipped:
            status += f"  ({skipped} already in DB)"
        print(f"  OK    {acct}: {status}")

    print(f"\nDone — {total_new} new records added, {total_skipped} already existed.")
    if total_skipped and not reset:
        print("Tip: run with --reset to do a full rebuild from all CSV files.")

    # ── Positions CSVs (always fully replaced per account) ────────────────────
    pos_total = 0
    for path, acct in POSITION_FILES:
        if not path.exists():
            continue
        try:
            recs = positions_csv.parse(str(path), acct)
        except Exception as exc:
            print(f"  ERROR positions {acct}: {exc}")
            continue

        db.delete_positions_by_account(acct)
        written = db.insert_positions(recs)
        pos_total += written
        print(f"  OK    positions {acct}: {written} rows")

    if pos_total:
        print(f"\nPositions — {pos_total} rows written across accounts.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest broker CSVs into journal.db")
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear all transactions and reload from scratch (use after first setup "
             "or when switching from an older version of this tool).",
    )
    args = parser.parse_args()
    run(reset=args.reset)
