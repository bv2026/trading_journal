"""
Coinbase transaction CSV parser.

Cash flow classification logic
───────────────────────────────
External cash flows (real money moving between bank/PayPal and Coinbase):

  Deposit                    → crypto_flow / usd_deposit       (+)
  Withdrawal / Admin Debit   → crypto_flow / usd_withdrawal    (−)
  Buy/Adv.Buy funded by bank → crypto_flow / bank_purchase     (+)
    Detection: Notes field contains a bank name or "PayPal".
    Rationale: "Bought 3500 USDC using UMB" or "Bought BTC using
    JPMORGAN CHASE BANK" means real money left your bank account.
  Receive (external wallet)  → crypto_flow / crypto_received   (+)
  Send    (external wallet)  → crypto_flow / crypto_sent       (−)

Internal flows — skipped for cash flow purposes:
  Buy / Convert using "Cash (USD)"  — spending existing Coinbase USD balance
  Advanced trades on *-USDC pairs   — trading with existing USDC on platform
  Convert (USDC → other crypto)     — internal rebalancing
  All Sell transactions              — proceeds stay on Coinbase as USD balance
  Portfolio Transfer                 — internal Coinbase movement
  Retail Staking Transfer/Unstaking  — internal staking bookkeeping
  Futures types                      — excluded per user request

Other tracked items:
  Staking Income / Reward Income / etc. → reward / <subcategory>
  Fees on all Buy/Sell trades            → fee / trading_fee
"""

import re
import pandas as pd
from .utils import parse_amount, make_id

# Transaction types treated as trades (check Notes for bank funding)
_BUY_TYPES = {
    "Buy",
    "Advanced Trade Buy",
    "Convert",           # USDC→crypto: internal, but check Notes just in case
}

_SELL_TYPES = {
    "Sell",
    "Advanced Trade Sell",
}

_REWARD_MAP = {
    "Staking Income":                  "staking",
    "Reward Income":                   "reward_income",
    "Learning Reward":                 "learning_reward",
    "Credit Card Reward":              "credit_card_reward",
    "Subscription Rebates (24 Hours)": "subscription_rebate",
    "Incentives Rewards Payout":       "incentive",
}

_SKIP = {
    "Portfolio Transfer",
    "Retail Staking Transfer",
    "Retail Unstaking Transfer",
    "Cfm Funding",
    "Derivatives Settlement",
    "FCM Futures USDC Sell",
    "FCM Futures USDC Sell Additional Encumberment",
    "FCM Futures USDC Sell Additional Encumberment Rollup",
}

# Notes patterns that indicate real bank/PayPal money (not existing Coinbase balance)
_BANK_RE = re.compile(
    r"using\s+.*(bank|credit union|huntington|jpmorgan|chase|wells fargo|"
    r"umb|citibank|national bank|paypal)",
    re.IGNORECASE,
)

# Notes patterns that indicate purely internal Coinbase balance usage
_INTERNAL_RE = re.compile(
    r"using\s+cash\s+\(usd\)|"      # existing USD balance
    r"using\s+usdc|"                  # existing USDC balance
    r"\bon\s+\w+-usdc\b|"            # advanced trade on *-USDC pair
    r"\bon\s+\w+-usd\b|"             # advanced trade on *-USD pair
    r"converted\s+\d",               # Convert transaction
    re.IGNORECASE,
)


def _is_bank_funded(notes: str) -> bool:
    """Return True if the Notes field indicates the purchase was funded from a bank/PayPal."""
    if _INTERNAL_RE.search(notes):
        return False
    return bool(_BANK_RE.search(notes))


def parse(filepath: str, account_id: str = "COINBASE") -> list[dict]:
    # Coinbase CSV: 2 preamble rows, then "ID,Timestamp,..." header
    header_idx: int | None = None
    with open(filepath, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if line.startswith("ID,Timestamp"):
                header_idx = i
                break
    if header_idx is None:
        raise ValueError(f"Cannot find transaction header in {filepath}")

    df = pd.read_csv(filepath, skiprows=header_idx, encoding="utf-8", on_bad_lines="skip")
    df.columns = df.columns.str.strip()

    records: list[dict] = []

    for idx, row in df.iterrows():
        txn_type = str(row.get("Transaction Type", "")).strip()
        if not txn_type or txn_type == "nan":
            continue
        if txn_type in _SKIP:
            continue

        asset = str(row.get("Asset", "")).strip()
        if asset == "nan":
            asset = "USD"

        total_amt = parse_amount(row.get("Total (inclusive of fees and/or spread)", ""))
        if total_amt == 0:
            total_amt = parse_amount(row.get("Subtotal", ""))

        fee_amt = parse_amount(row.get("Fees and/or Spread", ""))

        ts = str(row.get("Timestamp", "")).strip()
        try:
            date = pd.to_datetime(ts, utc=True).strftime("%Y-%m-%d")
        except Exception:
            continue

        txn_id = str(row.get("ID", "")).strip()
        notes  = str(row.get("Notes", "")).strip()
        if notes == "nan":
            notes = ""
        desc = f"{txn_type}: {notes}" if notes else txn_type

        def _rec(category, subcategory, amount, currency=None):
            return {
                "id":          txn_id if txn_id and txn_id != "nan"
                               else make_id(account_id, filepath, idx),
                "account_id":  account_id,
                "date":        date,
                "category":    category,
                "subcategory": subcategory,
                "amount":      amount,
                "currency":    currency or asset,
                "symbol":      asset,
                "description": desc[:500],
                "source_file": filepath,
            }

        # ── Buy transactions ───────────────────────────────────────────────
        if txn_type in _BUY_TYPES:
            # Only capture as cash inflow if funded directly from bank/PayPal.
            # Purchases using existing Coinbase USD/USDC balance are internal.
            if _is_bank_funded(notes):
                records.append(_rec("crypto_flow", "bank_purchase", +abs(total_amt), "USD"))

            # Capture fees regardless of funding source
            if fee_amt != 0:
                records.append({
                    "id":          make_id(account_id, filepath, f"{idx}_fee"),
                    "account_id":  account_id,
                    "date":        date,
                    "category":    "fee",
                    "subcategory": "trading_fee",
                    "amount":      -abs(fee_amt),
                    "currency":    "USD",
                    "symbol":      asset,
                    "description": f"Fee: {txn_type} {asset}",
                    "source_file": filepath,
                })
            continue

        # ── Sell transactions — internal (proceeds stay on Coinbase) ───────
        if txn_type in _SELL_TYPES:
            if fee_amt != 0:
                records.append({
                    "id":          make_id(account_id, filepath, f"{idx}_fee"),
                    "account_id":  account_id,
                    "date":        date,
                    "category":    "fee",
                    "subcategory": "trading_fee",
                    "amount":      -abs(fee_amt),
                    "currency":    "USD",
                    "symbol":      asset,
                    "description": f"Fee: {txn_type} {asset}",
                    "source_file": filepath,
                })
            continue

        # ── Rewards & staking ──────────────────────────────────────────────
        if txn_type in _REWARD_MAP:
            records.append(_rec("reward", _REWARD_MAP[txn_type], total_amt))
            continue

        # ── Direct deposits / withdrawals ──────────────────────────────────
        if txn_type == "Deposit":
            records.append(_rec("crypto_flow", "usd_deposit", +abs(total_amt), "USD"))
            continue

        if txn_type in ("Withdrawal", "Admin Debit"):
            records.append(_rec("crypto_flow", "usd_withdrawal", -abs(total_amt), "USD"))
            continue

        # ── External wallet transfers ──────────────────────────────────────
        if txn_type == "Receive":
            records.append(_rec("crypto_flow", "crypto_received", +abs(total_amt), "USD"))
            continue

        if txn_type == "Send":
            records.append(_rec("crypto_flow", "crypto_sent", -abs(total_amt), "USD"))
            continue

    return records
