#!/usr/bin/env python3
"""Quick CLI to get or set the combined cash account balance.

Usage:
    python -m src.cash              # print current balance
    python -m src.cash 52400        # set balance to $52,400
    python -m src.cash 52,400       # commas OK
    python -m src.cash $52400       # dollar sign OK
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db import get_cash_balance, upsert_cash_balance, init_db


def main() -> None:
    init_db()  # ensure cash_accounts table exists

    if len(sys.argv) < 2:
        bal = get_cash_balance()
        if bal:
            print(f"Current cash balance: ${bal:,.2f}")
        else:
            print("No cash balance set. Run:  python -m src.cash <amount>")
        return

    raw = sys.argv[1].replace(",", "").replace("$", "").strip()
    try:
        amount = float(raw)
    except ValueError:
        print(f"Error: '{sys.argv[1]}' is not a valid number.")
        print("Usage:  python -m src.cash <amount>   e.g.  python -m src.cash 52400")
        sys.exit(1)

    upsert_cash_balance(amount)
    print(f"OK Cash balance set to ${amount:,.2f}")


if __name__ == "__main__":
    main()
