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
from datetime import date as _date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src import db
from src.parsers import robinhood, webull, tradestation, schwab, tradier, coinbase, fidelity
from src.parsers import positions_csv

ACTIVITY = Path(__file__).parent / "activity"

ACCOUNTS = [
    # ── Equity accounts (live yfinance prices) ────────────────────────────────
    {"account_id": "RH-BV",    "broker": "robinhood",    "account_type": "equity",
     "account_group": "investment", "holder": "BV",   "price_source": "live",   "active": 1},
    {"account_id": "RH-KD",    "broker": "robinhood",    "account_type": "equity",
     "account_group": "investment", "holder": "KD",   "price_source": "live",   "active": 1},
    {"account_id": "WEBULL",       "broker": "webull", "account_type": "equity",
     "account_group": "investment", "holder": None, "price_source": "live", "active": 1},
    {"account_id": "WEBULL-CASH",  "broker": "webull", "account_type": "equity",
     "account_group": "investment", "holder": None, "price_source": "live", "active": 1},
    {"account_id": "WEBULL-EVENTS","broker": "webull", "account_type": "equity",
     "account_group": "investment", "holder": None, "price_source": "live", "active": 1},
    {"account_id": "WEBULL-FUT",   "broker": "webull", "account_type": "futures",
     "account_group": "investment", "holder": None, "price_source": "live", "active": 1},
    {"account_id": "TS",       "broker": "tradestation", "account_type": "equity",
     "account_group": "investment", "holder": None,   "price_source": "live",   "active": 1},
    {"account_id": "SCHWAB",   "broker": "schwab",       "account_type": "equity",
     "account_group": "investment", "holder": None,   "price_source": "live",   "active": 1},
    {"account_id": "TRADIER",  "broker": "tradier",      "account_type": "equity",
     "account_group": "investment", "holder": None,   "price_source": "live",   "active": 1},
    {"account_id": "FIDELITY", "broker": "fidelity",     "account_type": "equity",
     "account_group": "investment", "holder": None,   "price_source": "live",   "active": 1},
    # ── Crypto account (transactions only; positions via CRYPTO_FILES) ─────────
    {"account_id": "COINBASE", "broker": "coinbase",     "account_type": "crypto",
     "account_group": "investment", "holder": None,   "price_source": "static", "active": 1},
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

# Per-account equity positions CSVs — always fully replaced on each ingest.
POSITION_FILES = [
    (ACTIVITY / "positions-scwb.csv",      "SCHWAB"),
    (ACTIVITY / "positions-trader.csv",    "TRADIER"),
    (ACTIVITY / "positions-tradestn.csv",  "TS"),
    (ACTIVITY / "positions-rh-bv.csv",     "RH-BV"),
    (ACTIVITY / "positions-rh-kd.csv",     "RH-KD"),
    (ACTIVITY / "positions-webull.csv",    "WEBULL"),
    (ACTIVITY / "positions-fidelity.csv",  "FIDELITY"),
    (ACTIVITY / "positions-coinbase.csv",  "COINBASE"),
]



def _compute_snapshot_map() -> dict[str, dict]:
    """Build per-account market-value summary for portfolio_snapshots.

    Equity accounts: live prices via yfinance (calls load_positions_from_db).
    Static accounts: market_value stored in DB at ingest time.
    """
    from src.positions import load_positions_from_db  # avoid circular import at module level

    snap: dict[str, dict] = {}

    # ── Equity (live prices) ──────────────────────────────────────────────────
    eq_df = load_positions_from_db()
    if not eq_df.empty and "MARKET VALUE" in eq_df.columns:
        is_margin = eq_df["Ticker"].str.upper() == "MARGIN"
        for acct, grp in eq_df.groupby("Account"):
            grp_margin = is_margin.reindex(grp.index, fill_value=False)
            mv = float(pd.to_numeric(
                grp.loc[~grp_margin, "MARKET VALUE"], errors="coerce"
            ).fillna(0).sum())
            margin = abs(float(pd.to_numeric(
                grp.loc[grp_margin, "MARKET VALUE"], errors="coerce"
            ).fillna(0).sum()))
            cost = float(pd.to_numeric(
                grp.loc[~grp_margin, "COST"], errors="coerce"
            ).fillna(0).sum()) if "COST" in grp.columns else 0.0
            snap[str(acct)] = {"market_value": mv, "cost_basis": cost, "margin": margin}

    # ── Options ───────────────────────────────────────────────────────────────
    opt_df = db.load_options_db()
    if not opt_df.empty:
        for acct, grp in opt_df.groupby("account_id"):
            mv = float(pd.to_numeric(grp["market_value"], errors="coerce").fillna(0).sum())
            snap.setdefault(str(acct), {"market_value": 0.0, "cost_basis": None, "margin": 0.0})
            snap[str(acct)]["market_value"] += mv

    # ── Futures ───────────────────────────────────────────────────────────────
    fut_df = db.load_futures_db()
    if not fut_df.empty:
        for acct, grp in fut_df.groupby("account_id"):
            mv = float(pd.to_numeric(grp["market_value"], errors="coerce").fillna(0).sum())
            snap.setdefault(str(acct), {"market_value": 0.0, "cost_basis": None, "margin": 0.0})
            snap[str(acct)]["market_value"] += mv

    # ── Crypto ────────────────────────────────────────────────────────────────
    cry_df = db.load_crypto_db()
    if not cry_df.empty:
        for acct, grp in cry_df.groupby("account_id"):
            mv = float(pd.to_numeric(grp["market_value"], errors="coerce").fillna(0).sum())
            snap.setdefault(str(acct), {"market_value": 0.0, "cost_basis": None, "margin": 0.0})
            snap[str(acct)]["market_value"] += mv

    return snap


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

    # ── Equity positions (always fully replaced per account) ──────────────────
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

    # ── Sector/industry enrichment ────────────────────────────────────────────
    print("\nEnriching instrument sectors via yfinance …")
    try:
        from src.enrichment import enrich_sectors
        enriched = enrich_sectors()
        print(f"  Enriched {enriched} instrument(s) with sector/industry data.")
    except Exception as exc:
        print(f"  WARNING sector enrichment failed: {exc}")

    # ── Portfolio snapshot ────────────────────────────────────────────────────
    print("\nWriting portfolio snapshot …")
    try:
        snap_map = _compute_snapshot_map()
        if snap_map:
            today = _date.today().isoformat()
            db.write_portfolio_snapshot(today, snap_map)
            print(f"  Snapshot — {len(snap_map)} accounts written for {today}")
        else:
            print("  Snapshot — no positions found, skipped")
    except Exception as exc:
        print(f"  WARNING snapshot failed: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest broker CSVs into journal.db")
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear all transactions and reload from scratch (use after first setup "
             "or when switching from an older version of this tool).",
    )
    args = parser.parse_args()
    run(reset=args.reset)
