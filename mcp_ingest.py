"""
MCP-first ingest: write broker positions and transactions fetched from MCP
tools directly into the journal DB.

This module is designed to be called by Claude (the AI assistant) in-session
after Claude has already called the broker MCP tools and collected the raw
responses.  Claude passes the response dicts to the write_* functions here;
no Python-side MCP connection is needed.

Usage pattern (Claude calls this via Bash after fetching from MCP):

    python -c "
    import sys; sys.path.insert(0, '.')
    from mcp_ingest import write_tradier
    positions_resp = <paste positions MCP response>
    quotes_resp    = <paste quotes MCP response>
    write_tradier(positions_resp, quotes_resp)
    "

Or import in a session script:

    from mcp_ingest import write_tradier
    write_tradier(positions_resp, quotes_resp)
"""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src import db
from src.fetchers import tradier as tradier_fetcher


# ── Tradier ────────────────────────────────────────────────────────────────────

def write_tradier(
    positions_resp: dict,
    quotes_resp: dict | None = None,
    history_resp: dict | None = None,
    account_id: str = "TRADIER",
    *,
    dry_run: bool = False,
) -> dict:
    """
    Normalize Tradier MCP responses and write them to the journal DB.

    Args:
        positions_resp: Response from get_positions MCP tool.
        quotes_resp:    Optional response from get_market_quotes (for live
                        equity prices). If None, equities are written without
                        stored_price (yfinance prices them at dashboard load).
        history_resp:   Optional response from get_account_history.
        account_id:     Journal account ID to write to (default: "TRADIER").
        dry_run:        If True, parse and return counts but don't write to DB.

    Returns:
        Dict with keys: equity_count, option_count, txn_count.
    """
    db.init_db()

    eq_recs, opt_recs = tradier_fetcher.normalize_positions(
        positions_resp, quotes_resp, account_id
    )
    txn_recs: list[dict] = []
    if history_resp:
        txn_recs = tradier_fetcher.normalize_history(history_resp, account_id)

    if dry_run:
        print(f"[dry-run] {account_id}: {len(eq_recs)} equity, "
              f"{len(opt_recs)} options, {len(txn_recs)} txns — nothing written")
        return {"equity_count": len(eq_recs), "option_count": len(opt_recs),
                "txn_count": len(txn_recs)}

    # Write equity positions (full replace for this account)
    db.delete_positions_by_account(account_id)
    eq_written = db.insert_positions(eq_recs) if eq_recs else 0

    # Write option positions (full replace for this account)
    db.delete_options_by_account(account_id)
    opt_written = db.insert_options(opt_recs) if opt_recs else 0

    # Write transactions (incremental — INSERT OR IGNORE deduplicates by id)
    txn_written = db.insert_transactions(txn_recs) if txn_recs else 0

    print(f"[{account_id}] equity={eq_written}  options={opt_written}  txns={txn_written}")
    return {"equity_count": eq_written, "option_count": opt_written,
            "txn_count": txn_written}


# ── CLI helper ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Write pre-fetched MCP data into the journal DB."
    )
    parser.add_argument("--broker", required=True,
                        choices=["tradier"],
                        help="Which broker's data to write.")
    parser.add_argument("--positions", metavar="FILE",
                        help="Path to JSON file with get_positions response.")
    parser.add_argument("--quotes", metavar="FILE",
                        help="Path to JSON file with get_market_quotes response.")
    parser.add_argument("--history", metavar="FILE",
                        help="Path to JSON file with get_account_history response.")
    parser.add_argument("--account-id", default=None,
                        help="Override journal account_id (default per broker).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse only; do not write to DB.")

    args = parser.parse_args()

    def _load(path: str | None) -> dict | None:
        if not path:
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    if args.broker == "tradier":
        pos = _load(args.positions)
        if not pos:
            print("ERROR: --positions is required for tradier", file=sys.stderr)
            sys.exit(1)
        result = write_tradier(
            positions_resp=pos,
            quotes_resp=_load(args.quotes),
            history_resp=_load(args.history),
            account_id=args.account_id or "TRADIER",
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2))
