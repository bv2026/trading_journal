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
from src.fetchers import tradestation as ts_fetcher
from src.fetchers import webull as webull_fetcher
from src.fetchers import robinhood as rh_fetcher
from src.fetchers import schwab as schwab_fetcher


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
    """Sum shares × stored_price across equity records (used for computed margin)."""
    total = 0.0
    for r in eq_recs:
        shares = r.get("shares") or 0
        price  = r.get("stored_price") or 0
        total += float(shares) * float(price)
    return total


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

    # Upsert instruments master table
    instr_recs = tradier_fetcher.normalize_instruments(eq_recs, opt_recs)
    instr_written = db.upsert_instruments(instr_recs) if instr_recs else 0

    _enrich()
    print(f"[{account_id}] equity={eq_written}  options={opt_written}  "
          f"txns={txn_written}  instruments={instr_written}")
    return {"equity_count": eq_written, "option_count": opt_written,
            "txn_count": txn_written, "instrument_count": instr_written}


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

    for wb_id, positions_text in positions_by_wb_id.items():
        journal_id = id_map.get(wb_id)
        if not journal_id:
            print(f"  SKIP  unknown webull account {wb_id} — not in CLASS_TO_ACCOUNT_ID map")
            continue

        parsed = webull_fetcher.parse_positions_text(positions_text)
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
            bal = webull_fetcher.parse_balance_text(balance_by_wb_id[wb_id])
            print(f"  [{journal_id}] MV={bal['market_value']:.2f}  "
                  f"margin={bal['margin']:.2f}  net_liq={bal['net_liquidation']:.2f}")

        print(f"  [{journal_id}] equity={eq_w}  options={opt_w}  "
              f"futures={fut_w}  crypto={cry_w}  instruments={instr_w}")
        per_account[journal_id] = {
            "equity": eq_w, "options": opt_w, "futures": fut_w,
            "crypto": cry_w, "instruments": instr_w,
        }
        for k in ("equity", "options", "futures", "crypto", "instruments"):
            totals[k] += per_account[journal_id].get(k, 0)

    _enrich()
    return {"per_account": per_account, "totals": totals}


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
    RH-KD requires separate trayd credentials and falls back to CSV ingest.

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
    dry_run: bool = False,
) -> dict:
    """
    Normalize Schwab MCP responses and write them to the journal DB.

    Args:
        equity_resp:   Response from get_equity_positions MCP tool.
        futures_resp:  Optional response from get_futures_positions.
        summary_resp:  Optional response from get_account_summary (for balance info).
        txn_resp:      Optional response from get_transactions (60-day window).
        account_id:    Journal account_id (default: "SCHWAB").
        margin_mode:   How to determine margin debt to store:
                         "balance"  — use abs(margin_balance) from summary response (default).
                         "computed" — gross_MV (sum positions×price) minus equity.
                         "csv"      — preserve whatever MARGIN sentinel is in the DB.
        dry_run:       Parse only; do not write to DB.

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

    txn_written = db.insert_transactions(txn_recs) if txn_recs else 0

    instr_recs = schwab_fetcher.normalize_instruments(eq_recs, opt_recs, fut_recs)
    instr_written = db.upsert_instruments(instr_recs) if instr_recs else 0

    # Margin sentinel
    margin = 0.0
    if summary_resp:
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
    _insert_margin_sentinel(account_id, margin)

    _enrich()
    print(f"[{account_id}] equity={eq_written}  options={opt_written}  "
          f"futures={fut_written}  txns={txn_written}  instruments={instr_written}  "
          f"margin=${margin:,.0f}")
    return {"equity_count": eq_written, "option_count": opt_written,
            "futures_count": fut_written, "txn_count": txn_written,
            "instrument_count": instr_written, "margin": margin}


# ── CLI helper ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Write pre-fetched MCP broker data into the journal DB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mcp_ingest.py --broker tradier --positions pos.json --quotes quotes.json
  python mcp_ingest.py --broker schwab  --equity eq.json --summary summary.json
  python mcp_ingest.py --broker ts      --positions pos.json --balances bal.json
  python mcp_ingest.py --broker robinhood --positions pos.json --portfolio port.json
        """,
    )
    parser.add_argument("--broker", required=True,
                        choices=["tradier", "schwab", "tradestation", "ts", "robinhood", "rh"],
                        help="Which broker's data to write.")
    parser.add_argument("--positions",  metavar="FILE", help="JSON file: positions response.")
    parser.add_argument("--equity",     metavar="FILE", help="JSON file: equity positions (Schwab).")
    parser.add_argument("--futures",    metavar="FILE", help="JSON file: futures positions.")
    parser.add_argument("--quotes",     metavar="FILE", help="JSON file: market quotes.")
    parser.add_argument("--history",    metavar="FILE", help="JSON file: account history.")
    parser.add_argument("--balances",   metavar="FILE", help="JSON file: balances response.")
    parser.add_argument("--summary",    metavar="FILE", help="JSON file: account summary (Schwab).")
    parser.add_argument("--portfolio",  metavar="FILE", help="JSON file: portfolio response (RH).")
    parser.add_argument("--account-id", metavar="ID",   default=None,
                        help="Override journal account_id (default per broker).")
    parser.add_argument("--margin-mode", default="balance",
                        choices=["balance", "computed", "csv"],
                        help="How to derive margin debt (default: balance).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse only; do not write to DB.")

    args = parser.parse_args()

    def _load(path: str | None) -> dict | None:
        if not path:
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

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
            account_id=args.account_id or "TRADIER",
            dry_run=args.dry_run,
        )

    elif broker == "schwab":
        eq = _load(args.equity) or _load(args.positions)
        if not eq:
            print("ERROR: --equity (or --positions) required for schwab", file=sys.stderr)
            sys.exit(1)
        result = write_schwab(
            equity_resp  = eq,
            futures_resp = _load(args.futures),
            summary_resp = _load(args.summary) or _load(args.balances),
            account_id   = args.account_id or "SCHWAB",
            margin_mode  = args.margin_mode,
            dry_run      = args.dry_run,
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

    print(json.dumps(result, indent=2))
