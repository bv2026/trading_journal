"""
MCP-first ingest: write broker positions and transactions fetched from MCP
tools directly into the journal DB.

This module is designed to be called by Claude (the AI assistant) in-session
after Claude has already called the broker MCP tools and collected the raw
responses.  Claude passes the response dicts to the write_* functions here;
no Python-side MCP connection is needed.

Usage pattern (Claude calls this via Bash after fetching from MCP):

    python -c "
    from src.mcp_ingest import write_tradier
    positions_resp = <paste positions MCP response>
    quotes_resp    = <paste quotes MCP response>
    write_tradier(positions_resp, quotes_resp)
    "

Or import in a session script:

    from src.mcp_ingest import write_tradier
    write_tradier(positions_resp, quotes_resp)
"""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db
from src.fetchers import tradier as tradier_fetcher
from src.fetchers import tradestation as ts_fetcher
from src.fetchers import webull as webull_fetcher
from src.fetchers import robinhood as rh_fetcher
from src.fetchers import schwab as schwab_fetcher
from src.fetchers import coinbase as coinbase_fetcher


def _enrich() -> None:
    """Run sector/industry enrichment after any MCP write.  Failures are non-fatal."""
    try:
        from src.enrichment import enrich_sectors
        n = enrich_sectors()
        if n:
            print(f"  [enrich] {n} instrument(s) enriched with sector/industry data")
    except Exception as exc:
        print(f"  [enrich] WARNING: sector enrichment failed: {exc}")

# ── Margin helpers ─────────────────────────────────────────────────────────────

def _get_existing_margin(account_id: str) -> float:
    """Return the current MARGIN sentinel value for an account (0 if none)."""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT cost_basis FROM positions WHERE account_id=? AND ticker='MARGIN'",
                (account_id,),
            ).fetchone()
            if row and row[0] is not None:
                return abs(float(row[0]))
    except Exception:
        pass
    return 0.0


def _insert_margin_sentinel(account_id: str, margin: float) -> None:
    """
    Write a MARGIN sentinel row into positions so the dashboard accounts for
    margin debt.  The dashboard reads cost_basis for MARGIN rows directly
    (load_positions_from_db stores it as MARKET VALUE), so cost_basis is stored
    as a negative value representing the debt.
    """
    if margin <= 0:
        return
    db.insert_positions([{
        "account_id":   account_id,
        "ticker":       "MARGIN",
        "name":         "Margin Balance",
        "shares":       1,
        "cost_basis":   -margin,
        "stored_price": None,
        "sector":       None,
        "industry":     None,
        "asset_type":   "margin",
        "iv_rank":      None,
        "perf_ytd":     None,
        "atr_pct":      None,
        "data_source":  "mcp",
        "source_file":  None,
    }])


def _gross_mv_from_records(eq_recs: list[dict]) -> float:
    """Sum shares × stored_price across equity records (used for computed margin).

    Falls back to cost_basis when stored_price is None (e.g. a symbol that had no
    quote in the MCP response — e.g. Tradier's USD position which is not returned
    by get_market_quotes even though it is a real equity position).
    """
    total = 0.0
    for r in eq_recs:
        shares = float(r.get("shares") or 0)
        price  = r.get("stored_price")
        if price is None:
            price = r.get("cost_basis")   # per-share cost as fallback
        total += shares * float(price or 0)
    return total


# ── Tradier ────────────────────────────────────────────────────────────────────

def write_tradier(
    positions_resp: dict,
    quotes_resp: dict | None = None,
    history_resp: dict | None = None,
    balances_resp: dict | None = None,
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
        balances_resp:  Optional response from get_account_balances (for margin).
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

    # Upsert instruments master table
    instr_recs = tradier_fetcher.normalize_instruments(eq_recs, opt_recs)
    instr_written = db.upsert_instruments(instr_recs) if instr_recs else 0

    # Margin sentinel — check for a persistent override first
    override = db.get_margin_override(account_id)
    if override is not None:
        margin = override
        print(f"[{account_id}] margin override active — using ${margin:,.0f}")
    else:
        # Tradier's balance API exposes totalEquity but not marginBalance directly.
        # Compute margin as gross equity MV minus account equity when balance is provided.
        margin = 0.0
        if balances_resp:
            bal = tradier_fetcher.normalize_balances(balances_resp)
            if bal["margin_balance"] != 0:
                margin = abs(bal["margin_balance"])
            elif bal["total_equity"] > 0:
                gross_mv = _gross_mv_from_records(eq_recs)
                margin = max(0.0, gross_mv - bal["total_equity"])
            print(f"[{account_id}] balances — equity={bal['total_equity']:.2f}  margin={margin:.2f}")
    _insert_margin_sentinel(account_id, margin)

    _enrich()
    print(f"[{account_id}] equity={eq_written}  options={opt_written}  "
          f"txns={txn_written}  instruments={instr_written}  margin=${margin:,.0f}")
    return {"equity_count": eq_written, "option_count": opt_written,
            "txn_count": txn_written, "instrument_count": instr_written, "margin": margin}


# ── TradeStation ──────────────────────────────────────────────────────────────

def write_tradestation(
    positions_resp: dict,
    balances_resp: dict | None = None,
    account_id: str = "TS",
    *,
    margin_mode: str = "balance",
    dry_run: bool = False,
) -> dict:
    """
    Normalize TradeStation get-positions-details (and optionally get-balances-details)
    responses and write them to the journal DB.

    Args:
        positions_resp: Response from get-positions-details MCP tool.
        balances_resp:  Optional response from get-balances-details.
        account_id:     Journal account_id (default: "TS").
        margin_mode:    How to determine margin debt to store:
                          "balance"  — use currentCashBalance from balances response (default).
                          "computed" — gross_MV (sum positions×price) minus currentEquity.
                          "csv"      — preserve whatever MARGIN sentinel is already in the DB.
        dry_run:        Parse only; do not write to DB.

    Returns:
        Dict with keys: equity_count, option_count, futures_count, instrument_count, margin.
    """
    db.init_db()

    eq_recs, opt_recs, fut_recs = ts_fetcher.normalize_positions(
        positions_resp, account_id
    )

    if dry_run:
        print(f"[dry-run] {account_id}: {len(eq_recs)} equity, "
              f"{len(opt_recs)} options, {len(fut_recs)} futures — nothing written")
        return {"equity_count": len(eq_recs), "option_count": len(opt_recs),
                "futures_count": len(fut_recs)}

    csv_margin = _get_existing_margin(account_id)

    db.delete_positions_by_account(account_id)
    eq_written = db.insert_positions(eq_recs) if eq_recs else 0

    db.delete_options_by_account(account_id)
    opt_written = db.insert_options(opt_recs) if opt_recs else 0

    db.delete_futures_by_account(account_id)
    fut_written = db.insert_futures(fut_recs) if fut_recs else 0

    instr_recs = ts_fetcher.normalize_instruments(eq_recs, opt_recs, fut_recs)
    instr_written = db.upsert_instruments(instr_recs) if instr_recs else 0

    # Margin sentinel
    margin = 0.0
    if balances_resp:
        bal = ts_fetcher.normalize_balances(balances_resp, account_id)
        if margin_mode == "computed":
            gross_mv = _gross_mv_from_records(eq_recs)
            margin = max(0.0, gross_mv - bal["equity"])
        else:  # "balance"
            margin = bal["margin"]
        print(f"[{account_id}] balances — MV={bal['market_value']:.2f}  "
              f"equity={bal['equity']:.2f}  margin={margin:.2f}  mode={margin_mode}")
    elif margin_mode == "csv":
        margin = csv_margin
    _insert_margin_sentinel(account_id, margin)

    _enrich()
    print(f"[{account_id}] equity={eq_written}  options={opt_written}  "
          f"futures={fut_written}  instruments={instr_written}  margin=${margin:,.0f}")
    return {"equity_count": eq_written, "option_count": opt_written,
            "futures_count": fut_written, "instrument_count": instr_written,
            "margin": margin}


# ── Webull ────────────────────────────────────────────────────────────────────

def write_webull(
    account_list_result: str,
    positions_by_wb_id: dict[str, str],
    balance_by_wb_id: dict[str, str] | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """
    Normalize Webull MCP responses for all 4 accounts and write to journal DB.

    Args:
        account_list_result:  Raw `result` text from get_account_list.
        positions_by_wb_id:   {webull_account_id: positions result text}
                              from get_account_positions for each account.
        balance_by_wb_id:     Optional {webull_account_id: balance result text}
                              from get_account_balance.
        dry_run:              Parse only; do not write to DB.

    Returns:
        Dict with per-account counts and totals.
    """
    db.init_db()

    # Build webull_id → journal_id map from account list
    id_map = webull_fetcher.account_map_from_list(account_list_result)

    totals = {"equity": 0, "options": 0, "futures": 0, "crypto": 0, "instruments": 0}
    per_account: dict[str, dict] = {}
    errors: list[str] = []

    for wb_id, positions_text in positions_by_wb_id.items():
        journal_id = id_map.get(wb_id)
        if not journal_id:
            print(f"  SKIP  unknown webull account {wb_id} — not in CLASS_TO_ACCOUNT_ID map")
            continue

        raw_text = positions_text
        if isinstance(positions_text, dict):
            raw_text = positions_text.get("result") or json.dumps(positions_text)
        if not isinstance(raw_text, str):
            raw_text = str(raw_text)
        if "Traceback" in raw_text and "UnicodeEncodeError" in raw_text:
            errors.append(f"{journal_id}: positions payload contains traceback text, not MCP result")
            print(f"  ERROR {journal_id}: invalid positions payload (traceback text detected)")
            continue

        parsed = webull_fetcher.parse_positions_text(raw_text)
        eq_recs, opt_recs, fut_recs, cry_recs = webull_fetcher.normalize_positions(
            parsed, journal_id
        )

        if dry_run:
            print(f"  [dry-run] {journal_id}: {len(eq_recs)} equity  "
                  f"{len(opt_recs)} options  {len(fut_recs)} futures  "
                  f"{len(cry_recs)} crypto")
            per_account[journal_id] = {
                "equity": len(eq_recs), "options": len(opt_recs),
                "futures": len(fut_recs), "crypto": len(cry_recs),
            }
            continue

        db.delete_positions_by_account(journal_id)
        eq_w = db.insert_positions(eq_recs) if eq_recs else 0

        db.delete_options_by_account(journal_id)
        opt_w = db.insert_options(opt_recs) if opt_recs else 0

        db.delete_futures_by_account(journal_id)
        fut_w = db.insert_futures(fut_recs) if fut_recs else 0

        db.delete_crypto_by_account(journal_id)
        cry_w = db.insert_crypto(cry_recs) if cry_recs else 0

        instr_recs = webull_fetcher.normalize_instruments(
            eq_recs, opt_recs, fut_recs, cry_recs
        )
        instr_w = db.upsert_instruments(instr_recs) if instr_recs else 0

        if balance_by_wb_id and wb_id in balance_by_wb_id:
            bal_text = balance_by_wb_id[wb_id]
            if isinstance(bal_text, dict):
                bal_text = bal_text.get("result") or json.dumps(bal_text)
            bal = webull_fetcher.parse_balance_text(str(bal_text))
            print(f"  [{journal_id}] MV={bal['market_value']:.2f}  "
                  f"margin={bal['margin']:.2f}  net_liq={bal['net_liquidation']:.2f}")
            _insert_margin_sentinel(journal_id, bal["margin"])

        print(f"  [{journal_id}] equity={eq_w}  options={opt_w}  "
              f"futures={fut_w}  crypto={cry_w}  instruments={instr_w}")
        per_account[journal_id] = {
            "equity": eq_w, "options": opt_w, "futures": fut_w,
            "crypto": cry_w, "instruments": instr_w,
        }
        for k in ("equity", "options", "futures", "crypto", "instruments"):
            totals[k] += per_account[journal_id].get(k, 0)

    _enrich()
    if not per_account and errors:
        raise ValueError("Webull ingest failed: " + "; ".join(errors))
    return {"per_account": per_account, "totals": totals, "errors": errors}


# ── Coinbase ─────────────────────────────────────────────────────────────

def write_coinbase(
    positions_resp: dict | list,
    account_id: str = "COINBASE",
    *,
    dry_run: bool = False,
) -> dict:
    """Normalize Coinbase MCP positions and write crypto rows to the DB."""
    db.init_db()

    cry_recs = coinbase_fetcher.normalize_positions(positions_resp, account_id)
    fut_recs = coinbase_fetcher.normalize_futures(positions_resp, account_id)
    instr_recs = coinbase_fetcher.normalize_instruments(cry_recs, fut_recs)

    if dry_run:
        print(
            f"[dry-run] {account_id}: {len(cry_recs)} crypto/cash rows, "
            f"{len(fut_recs)} futures rows — nothing written"
        )
        return {
            "crypto_count": len(cry_recs),
            "futures_count": len(fut_recs),
            "instrument_count": len(instr_recs),
        }

    # Coinbase used to be imported as static equity-style rows from positions CSVs.
    # Clear both stores so the MCP view becomes authoritative.
    db.delete_positions_by_account(account_id)
    db.delete_crypto_by_account(account_id)
    db.delete_futures_by_account(account_id)
    cry_written = db.insert_crypto(cry_recs) if cry_recs else 0
    fut_written = db.insert_futures(fut_recs) if fut_recs else 0
    instr_written = db.upsert_instruments(instr_recs) if instr_recs else 0

    print(f"[{account_id}] crypto={cry_written}  futures={fut_written}  instruments={instr_written}")
    return {
        "crypto_count": cry_written,
        "futures_count": fut_written,
        "instrument_count": instr_written,
    }


# ── Robinhood ─────────────────────────────────────────────────────────────────

def write_robinhood(
    positions_resp: dict,
    portfolio_resp: dict | None = None,
    account_id: str = "RH-BV",
    *,
    margin_mode: str = "balance",
    dry_run: bool = False,
) -> dict:
    """
    Normalize trayd get_positions (and optionally get_portfolio) responses and
    write equity positions to the journal DB.

    Robinhood only exposes equities via MCP; options are not available.
    Use account_id to select the journal account for each linked Trayd profile.

    Args:
        positions_resp: Response from trayd get_positions MCP tool.
        portfolio_resp: Optional response from trayd get_portfolio (for margin info).
        account_id:     Journal account_id (default: "RH-BV").
        margin_mode:    How to determine margin debt to store:
                          "balance"  — use abs(cash) from portfolio response (default).
                          "computed" — gross_MV (sum positions×price) minus equity.
                          "csv"      — preserve whatever MARGIN sentinel is in the DB.
        dry_run:        Parse only; do not write to DB.

    Returns:
        Dict with keys: equity_count, instrument_count, margin.
    """
    db.init_db()

    eq_recs = rh_fetcher.normalize_positions(positions_resp, account_id)

    if dry_run:
        print(f"[dry-run] {account_id}: {len(eq_recs)} equity — nothing written")
        return {"equity_count": len(eq_recs)}

    csv_margin = _get_existing_margin(account_id)

    db.delete_positions_by_account(account_id)
    eq_written = db.insert_positions(eq_recs) if eq_recs else 0

    instr_recs = rh_fetcher.normalize_instruments(eq_recs)
    instr_written = db.upsert_instruments(instr_recs) if instr_recs else 0

    # Margin sentinel
    margin = 0.0
    if portfolio_resp:
        port = rh_fetcher.normalize_portfolio(portfolio_resp)
        if margin_mode == "computed":
            gross_mv = _gross_mv_from_records(eq_recs)
            margin = max(0.0, gross_mv - port["equity"])
        else:  # "balance"
            margin = port["margin"]
        print(f"[{account_id}] portfolio — equity={port['equity']:.2f}  "
              f"cash={port['cash']:.2f}  margin={margin:.2f}  mode={margin_mode}")
    elif margin_mode == "csv":
        margin = csv_margin
    _insert_margin_sentinel(account_id, margin)

    _enrich()
    print(f"[{account_id}] equity={eq_written}  instruments={instr_written}  margin=${margin:,.0f}")
    return {"equity_count": eq_written, "instrument_count": instr_written, "margin": margin}


# ── Schwab ────────────────────────────────────────────────────────────────────

def write_schwab(
    equity_resp: dict,
    futures_resp: dict | None = None,
    summary_resp: dict | None = None,
    txn_resp: dict | None = None,
    account_id: str = "SCHWAB",
    *,
    margin_mode: str = "balance",
    futures_account_value: float | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Normalize Schwab MCP responses and write them to the journal DB.

    Args:
        equity_resp:           Response from get_equity_positions MCP tool.
        futures_resp:          Optional response from get_futures_positions.
        summary_resp:          Optional response from get_account_summary (for balance info).
        txn_resp:              Optional response from get_transactions (60-day window).
        account_id:            Journal account_id (default: "SCHWAB").
        margin_mode:           How to determine margin debt to store:
                                 "balance"  — use abs(margin_balance) from summary (default).
                                 "computed" — gross_MV minus equity.
                                 "csv"      — preserve existing MARGIN sentinel.
        futures_account_value: Actual Schwab Futures Account Value from the balance page
                               (e.g. 5415.0).  When provided, an adjustment row
                               "_FUTURES_ADJ_" is written so that sum(futures MV) equals
                               this value.  Without this, the sum of notional leg MVs
                               differs from the sub-account equity by the unrealized
                               futures P&L + initial margin basis.
        dry_run:               Parse only; do not write to DB.

    Returns:
        Dict with keys: equity_count, option_count, futures_count, txn_count,
        instrument_count, margin.
    """
    db.init_db()

    eq_recs, opt_recs = schwab_fetcher.normalize_equity(equity_resp, account_id)
    fut_recs: list[dict] = []
    if futures_resp:
        fut_recs = schwab_fetcher.normalize_futures(futures_resp, account_id)
    txn_recs: list[dict] = []
    if txn_resp:
        txn_recs = schwab_fetcher.normalize_transactions(txn_resp, account_id)

    if dry_run:
        print(f"[dry-run] {account_id}: {len(eq_recs)} equity, "
              f"{len(opt_recs)} options, {len(fut_recs)} futures, "
              f"{len(txn_recs)} txns — nothing written")
        return {"equity_count": len(eq_recs), "option_count": len(opt_recs),
                "futures_count": len(fut_recs), "txn_count": len(txn_recs)}

    csv_margin = _get_existing_margin(account_id)

    db.delete_positions_by_account(account_id)
    eq_written = db.insert_positions(eq_recs) if eq_recs else 0

    db.delete_options_by_account(account_id)
    opt_written = db.insert_options(opt_recs) if opt_recs else 0

    db.delete_futures_by_account(account_id)
    fut_written = db.insert_futures(fut_recs) if fut_recs else 0

    # Use persisted futures equity override if no value passed explicitly
    if futures_account_value is None:
        futures_account_value = db.get_futures_equity_override(account_id)
    else:
        # Persist explicit override so downstream account-summary views use the
        # same value that was applied for this ingest run.
        db.upsert_futures_equity_override(account_id, float(futures_account_value))

    # Futures account value adjustment row.
    # The sum of individual notional MVs (signed_qty × mark × multiplier) differs from
    # the actual Schwab Futures sub-account equity by the margin basis and settled P&L.
    # When futures_account_value is provided, write a correction row so the dashboard
    # Account Summary shows the correct total.
    if futures_account_value is not None:
        notional_sum = sum(r.get("market_value") or 0.0 for r in fut_recs)
        adj = futures_account_value - notional_sum
        if abs(adj) > 0.01:
            db.insert_futures([{
                "account_id":   account_id,
                "symbol":       "_FUTURES_ADJ_",
                "underlying":   None,
                "description":  "Futures account equity adjustment",
                "qty":          0,
                "price":        None,
                "market_value": adj,
                "data_source":  "computed",
                "source_file":  None,
                "_expiry":      None,
                "_multiplier":  None,
                "_trade_price": None,
                "_spread_name": None,
            }])
            fut_written += 1
            print(f"[{account_id}] futures adj — notional={notional_sum:+,.2f}  "
                  f"target={futures_account_value:,.2f}  adj={adj:+,.2f}")

    txn_written = db.insert_transactions(txn_recs) if txn_recs else 0

    instr_recs = schwab_fetcher.normalize_instruments(eq_recs, opt_recs, fut_recs)
    instr_written = db.upsert_instruments(instr_recs) if instr_recs else 0

    # Margin sentinel — check for a persistent override first
    override = db.get_margin_override(account_id)
    if override is not None:
        margin = override
        print(f"[{account_id}] margin override active — using ${margin:,.0f}")
    elif summary_resp:
        bal = schwab_fetcher.normalize_balances(summary_resp)
        if margin_mode == "computed":
            gross_mv = _gross_mv_from_records(eq_recs)
            margin = max(0.0, gross_mv - bal["equity"])
        else:  # "balance"
            margin = bal["margin"]
        print(f"[{account_id}] balances — MV={bal['market_value']:.2f}  "
              f"equity={bal['equity']:.2f}  margin={margin:.2f}  mode={margin_mode}")
    elif margin_mode == "csv":
        margin = csv_margin
    else:
        margin = 0.0
    _insert_margin_sentinel(account_id, margin)

    _enrich()
    print(f"[{account_id}] equity={eq_written}  options={opt_written}  "
          f"futures={fut_written}  txns={txn_written}  instruments={instr_written}  "
          f"margin=${margin:,.0f}")
    return {"equity_count": eq_written, "option_count": opt_written,
            "futures_count": fut_written, "txn_count": txn_written,
            "instrument_count": instr_written, "margin": margin}


# ── Margin override ────────────────────────────────────────────────────────────

def set_margin(account_id: str, amount: float, *, persist: bool = True) -> dict:
    """
    Set the margin debt for an account.

    When persist=True (default), also writes to margin_overrides so the value
    survives future syncs.  Pass amount=0 to clear both the sentinel and the
    override (computed margin will resume on next sync).

    Args:
        account_id: Journal account_id (e.g. "TRADIER", "SCHWAB").
        amount:     Margin balance in USD (positive number, e.g. 36072).
                    Pass 0 to clear the override and remove the margin sentinel.
        persist:    Write to margin_overrides table so the value survives syncs.

    Returns:
        Dict with account_id, amount, and action taken.
    """
    db.init_db()
    account_id = account_id.upper()

    if amount < 0:
        amount = abs(amount)  # accept negative input gracefully

    if amount == 0:
        with db.get_conn() as conn:
            conn.execute(
                "DELETE FROM positions WHERE account_id=? AND ticker='MARGIN'",
                (account_id,),
            )
            conn.execute(
                "DELETE FROM margin_overrides WHERE account_id=?",
                (account_id,),
            )
            conn.commit()
        print(f"[{account_id}] MARGIN sentinel and override cleared")
        return {"account_id": account_id, "amount": 0.0, "action": "cleared"}

    if persist:
        db.upsert_margin_override(account_id, amount)
    _insert_margin_sentinel(account_id, amount)
    action = "set+persisted" if persist else "set"
    print(f"[{account_id}] MARGIN set to ${amount:,.0f}  (persist={persist})")
    return {"account_id": account_id, "amount": amount, "action": action}


# ── CLI helper ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Write pre-fetched MCP broker data into the journal DB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.mcp_ingest --broker tradier  --positions pos.json --quotes quotes.json
  python -m src.mcp_ingest --broker schwab   --equity eq.json --summary summary.json
  python -m src.mcp_ingest --broker ts       --positions pos.json --balances bal.json
  python -m src.mcp_ingest --broker robinhood --positions pos.json --portfolio port.json
  python -m src.mcp_ingest --broker webull   --account-list al.txt --positions-map pm.json

  # Set margin directly (no broker data needed)
  python -m src.mcp_ingest --set-margin RH-BV 25000
  python -m src.mcp_ingest --set-margin SCHWAB 0        # clear margin
        """,
    )
    parser.add_argument("--broker",
                        choices=["tradier", "schwab", "tradestation", "ts",
                                 "robinhood", "rh", "webull", "coinbase"],
                        help="Which broker's data to write.")
    parser.add_argument("--set-margin", nargs=2, metavar=("ACCOUNT", "AMOUNT"),
                        help="Set margin for ACCOUNT to AMOUNT (USD). Pass 0 to clear.")
    parser.add_argument("--positions",      metavar="FILE", help="JSON file: positions response.")
    parser.add_argument("--equity",         metavar="FILE", help="JSON file: equity positions (Schwab).")
    parser.add_argument("--futures",        metavar="FILE", help="JSON file: futures positions.")
    parser.add_argument("--quotes",         metavar="FILE", help="JSON file: market quotes.")
    parser.add_argument("--history",        metavar="FILE", help="JSON file: account history.")
    parser.add_argument("--balances",       metavar="FILE", help="JSON file: balances response.")
    parser.add_argument("--summary",        metavar="FILE", help="JSON file: account summary (Schwab).")
    parser.add_argument("--portfolio",      metavar="FILE", help="JSON file: portfolio response (RH).")
    parser.add_argument("--account-list",   metavar="FILE", help="Text file: account list result (Webull).")
    parser.add_argument("--positions-map",  metavar="FILE", help="JSON file: {wb_id: positions_text} (Webull).")
    parser.add_argument("--balances-map",   metavar="FILE", help="JSON file: {wb_id: balance_text} (Webull).")
    parser.add_argument("--account-id", metavar="ID",   default=None,
                        help="Override journal account_id (default per broker).")
    parser.add_argument("--margin-mode", default="balance",
                        choices=["balance", "computed", "csv"],
                        help="How to derive margin debt (default: balance).")
    parser.add_argument("--futures-equity", metavar="AMOUNT", type=float, default=None,
                        help="Schwab Futures Account Value from balance page (e.g. 5415). "
                             "When provided, writes a correction row so futures sum "
                             "equals this value instead of the notional leg total.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse only; do not write to DB.")

    args = parser.parse_args()

    # ── set-margin shortcut ────────────────────────────────────────────────────
    if args.set_margin:
        acct, raw_amt = args.set_margin
        try:
            amt = float(raw_amt.replace(",", "").replace("$", ""))
        except ValueError:
            print(f"ERROR: AMOUNT must be a number, got {raw_amt!r}", file=sys.stderr)
            sys.exit(1)
        result = set_margin(acct, amt)
        print(json.dumps(result))
        sys.exit(0)

    if not args.broker:
        parser.error("--broker is required unless --set-margin is used")

    def _load(path: str | None) -> dict | None:
        if not path:
            return None
        if not Path(path).exists():
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            print("Fetch the MCP response first and save it to that path.", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _load_text(path: str | None) -> str | None:
        if not path:
            return None
        if not Path(path).exists():
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            print("Fetch the MCP response first and save it to that path.", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            return f.read()

    broker = args.broker.lower()
    result: dict = {}

    if broker == "tradier":
        pos = _load(args.positions)
        if not pos:
            print("ERROR: --positions required for tradier", file=sys.stderr)
            sys.exit(1)
        result = write_tradier(
            positions_resp=pos,
            quotes_resp=_load(args.quotes),
            history_resp=_load(args.history),
            balances_resp=_load(args.balances),
            account_id=args.account_id or "TRADIER",
            dry_run=args.dry_run,
        )

    elif broker == "schwab":
        eq = _load(args.equity) or _load(args.positions)
        if not eq:
            print("ERROR: --equity (or --positions) required for schwab", file=sys.stderr)
            sys.exit(1)
        result = write_schwab(
            equity_resp           = eq,
            futures_resp          = _load(args.futures),
            summary_resp          = _load(args.summary) or _load(args.balances),
            account_id            = args.account_id or "SCHWAB",
            margin_mode           = args.margin_mode,
            futures_account_value = args.futures_equity,
            dry_run               = args.dry_run,
        )

    elif broker in ("tradestation", "ts"):
        pos = _load(args.positions)
        if not pos:
            print("ERROR: --positions required for tradestation", file=sys.stderr)
            sys.exit(1)
        result = write_tradestation(
            positions_resp = pos,
            balances_resp  = _load(args.balances),
            account_id     = args.account_id or "TS",
            margin_mode    = args.margin_mode,
            dry_run        = args.dry_run,
        )

    elif broker in ("robinhood", "rh"):
        pos = _load(args.positions)
        if not pos:
            print("ERROR: --positions required for robinhood", file=sys.stderr)
            sys.exit(1)
        result = write_robinhood(
            positions_resp = pos,
            portfolio_resp = _load(args.portfolio) or _load(args.balances),
            account_id     = args.account_id or "RH-BV",
            margin_mode    = args.margin_mode,
            dry_run        = args.dry_run,
        )

    elif broker == "webull":
        acct_list = _load_text(args.account_list)
        if not acct_list:
            print("ERROR: --account-list required for webull", file=sys.stderr)
            sys.exit(1)
        pos_map = _load(args.positions_map)
        if not pos_map:
            print("ERROR: --positions-map required for webull", file=sys.stderr)
            sys.exit(1)
        result = write_webull(
            account_list_result = acct_list,
            positions_by_wb_id  = pos_map,
            balance_by_wb_id    = _load(args.balances_map),
            dry_run             = args.dry_run,
        )

    elif broker == "coinbase":
        pos = _load(args.positions)
        if not pos:
            print("ERROR: --positions required for coinbase", file=sys.stderr)
            sys.exit(1)
        result = write_coinbase(
            positions_resp = pos,
            account_id     = args.account_id or "COINBASE",
            dry_run        = args.dry_run,
        )

    print(json.dumps(result, indent=2))
