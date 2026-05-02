#!/usr/bin/env python3
"""Quick CLI to get or set margin overrides for brokerage accounts.

Margin overrides persist across syncs — use for accounts where the broker API
doesn't return reliable margin data (e.g. Tradier).

Usage:
    python margin.py                    # list all overrides
    python margin.py TRADIER            # show override for one account
    python margin.py TRADIER 36072      # set override to $36,072
    python margin.py TRADIER 36,072     # commas OK
    python margin.py TRADIER 0          # clear override (computed margin resumes)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.db import get_margin_override, upsert_margin_override, init_db, get_conn


def _list_all() -> None:
    init_db()
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT account_id, margin, updated_at FROM margin_overrides ORDER BY account_id"
            ).fetchall()
    except Exception:
        rows = []
    if not rows:
        print("No margin overrides set.")
        print("Usage:  python margin.py <ACCOUNT> <AMOUNT>")
        return
    print(f"{'Account':<15}  {'Margin':>12}  Updated")
    print("-" * 42)
    for acct, margin, updated in rows:
        print(f"{acct:<15}  ${margin:>11,.2f}  {updated or ''}")


def main() -> None:
    init_db()

    if len(sys.argv) == 1:
        _list_all()
        return

    account_id = sys.argv[1].upper()

    if len(sys.argv) == 2:
        val = get_margin_override(account_id)
        if val is None:
            print(f"No margin override set for {account_id}. "
                  f"Run:  python margin.py {account_id} <amount>")
        else:
            print(f"{account_id} margin override: ${val:,.2f}")
        return

    raw = sys.argv[2].replace(",", "").replace("$", "").strip()
    try:
        amount = float(raw)
    except ValueError:
        print(f"Error: '{sys.argv[2]}' is not a valid number.")
        sys.exit(1)

    if amount == 0:
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM margin_overrides WHERE account_id=?", (account_id,)
            )
            conn.commit()
        print(f"OK Margin override cleared for {account_id} (computed margin will resume on next sync)")
    else:
        amount = abs(amount)
        upsert_margin_override(account_id, amount)
        print(f"OK {account_id} margin override set to ${amount:,.2f}")


if __name__ == "__main__":
    main()
