"""
Coinbase transaction CSV parser.
Cash flow logic mirrors C:\\work\\trading-activity\\src\\reports.py exactly:
  - USD Deposits/Withdrawals  → crypto_flow / usd_deposit|usd_withdrawal
  - Buy/Sell USDC             → crypto_flow / usdc_purchased|usdc_sold  (USD ↔ USDC)
  - Send/Receive              → crypto_flow / crypto_sent|crypto_received
  - Portfolio Transfer        → skipped (internal Coinbase movement)
  - Staking/Rewards           → reward / <subcategory>
  - Fees from Buy/Sell trades → fee / trading_fee
"""
import pandas as pd
from .utils import parse_amount, make_id

_TRADE_TYPES = {
    "Buy", "Sell",
    "Advanced Trade Buy", "Advanced Trade Sell",
    "Convert",
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
    "Portfolio Transfer",          # internal Coinbase movement, not external cash
    "Retail Staking Transfer",     # moves staked crypto, not new income
    "Retail Unstaking Transfer",
    "Cfm Funding",
    "Derivatives Settlement",
    "FCM Futures USDC Sell Additional Encumberment",
    "FCM Futures USDC Sell Additional Encumberment Rollup",
}


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
        desc   = f"{txn_type}: {notes}" if notes and notes != "nan" else txn_type

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

        # ── Trades (Buy / Sell / Advanced Trade / Convert) ────────────────
        if txn_type in _TRADE_TYPES:
            is_buy  = "Buy"  in txn_type or txn_type == "Convert"
            is_sell = "Sell" in txn_type

            # USDC buys/sells = real USD ↔ USDC cash flow
            # Use abs() then sign-by-direction, mirroring the existing report
            if asset == "USDC":
                if is_buy:
                    records.append(_rec("crypto_flow", "usdc_purchased", -abs(total_amt), "USD"))
                elif is_sell:
                    records.append(_rec("crypto_flow", "usdc_sold", abs(total_amt), "USD"))

            # Capture fees from ALL buy/sell trades
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

        # ── Rewards & staking ─────────────────────────────────────────────
        if txn_type in _REWARD_MAP:
            records.append(_rec("reward", _REWARD_MAP[txn_type], total_amt))
            continue

        # ── Explicit deposits / withdrawals ───────────────────────────────
        if txn_type == "Deposit":
            records.append(_rec("crypto_flow", "usd_deposit",    +abs(total_amt), "USD"))
            continue

        if txn_type in ("Withdrawal", "Admin Debit"):
            records.append(_rec("crypto_flow", "usd_withdrawal", -abs(total_amt), "USD"))
            continue

        if txn_type == "Receive":
            records.append(_rec("crypto_flow", "crypto_received", +abs(total_amt), "USD"))
            continue

        if txn_type == "Send":
            records.append(_rec("crypto_flow", "crypto_sent",    -abs(total_amt), "USD"))
            continue

    return records
