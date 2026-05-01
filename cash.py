#!/usr/bin/env python3
"""Quick CLI to get or set the combined cash account balance.

Usage:
    python cash.py              # print current balance
    python cash.py 52400        # set balance to $52,400
    python cash.py 52,400       # commas OK
    python cash.py $52400       # dollar sign OK
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.db import get_cash_balance, upsert_cash_balance, init_db


def main() -> None:
    init_db()  # ensure cash_accounts table exists

    if len(sys.argv) < 2:
        bal = get_cash_balance()
        if bal:
            print(f"Current cash balance: ${bal:,.2f}")
        else:
            print("No cash balance set. Run:  python cash.py <amount>")
        return

    raw = sys.argv[1].replace(",", "").replace("$", "").strip()
    try:
        amount = float(raw)
    except ValueError:
        print(f"Error: '{sys.argv[1]}' is not a valid number.")
        print("Usage:  python cash.py <amount>   e.g.  python cash.py 52400")
        sys.exit(1)

    upsert_cash_balance(amount)
    print(f"OK Cash balance set to ${amount:,.2f}")


if __name__ == "__main__":
    main()
