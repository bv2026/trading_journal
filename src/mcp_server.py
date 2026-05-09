"""
Trading Journal MCP Server

Exposes the portfolio journal database as Claude-callable tools.
Register in Claude Desktop config (see docs/USAGE.md) then ask Claude
questions like "what were my total dividends in 2024?" directly in chat.

Tools:
  get_portfolio_summary  — overall KPIs + live net worth across all asset classes
  get_yearly_summary     — year-over-year breakdown table
  get_account_summary    — per-account breakdown table
  get_transactions       — filterable transaction log
  get_positions          — current positions from all asset classes with live prices
  get_performance        — account-level returns across standard lookback periods
  set_margin             — directly set margin balance for an account
  refresh_positions      — fetch live positions from broker APIs and write to DB
  run_ingest             — re-load all broker CSVs into the database
"""

import sys
import json
import subprocess
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

# Ensure project root is on the path so src.* imports work.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import mcp_ingest as _ingest

from mcp.server.fastmcp import FastMCP
from src.services import portfolio as _portfolio

mcp = FastMCP("trading-journal")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_response(
    *,
    operation: str,
    status: str = "ok",
    data=None,
    message: str | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    **fields,
) -> str:
    payload = {
        "status": status,
        "operation": operation,
        "generated_at": _now(),
        "warnings": warnings or [],
        "errors": errors or [],
    }
    if message:
        payload["message"] = message
    if data is not None:
        payload["data"] = data
    payload.update(fields)
    return json.dumps(payload, indent=2, default=str)


def _capture_stdout(fn, *args, **kwargs):
    buffer = StringIO()
    with redirect_stdout(buffer):
        result = fn(*args, **kwargs)
    return result, buffer.getvalue()


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_portfolio_summary(year: int | None = None,
                          account_id: str | None = None) -> str:
    """
    Return overall portfolio KPIs — net cash flow, dividends, rewards,
    margin interest, fees, net income, and live net worth across all
    asset classes (equity, options, futures, crypto).

    Args:
        year:       Optional calendar year to filter (e.g. 2024).
        account_id: Optional account to filter (e.g. "RH-BV", "FIDELITY").
    """
    result = _portfolio.get_portfolio_summary(year=year, account_id=account_id)
    if result is None:
        return _json_response(
            operation="portfolio.summary",
            status="empty",
            message="No data found. Run run_ingest() first.",
            filters={"year": year, "account_id": account_id},
        )

    return _json_response(
        operation="portfolio.summary",
        data=result,
        filters={"year": year, "account_id": account_id},
    )


@mcp.tool()
def get_yearly_summary(account_id: str | None = None) -> str:
    """
    Return a year-over-year breakdown of portfolio metrics.

    Args:
        account_id: Optional account to filter (e.g. "SCHWAB").
    """
    rows = _portfolio.get_yearly_summary(account_id=account_id)
    if rows is None:
        return _json_response(
            operation="portfolio.yearly_summary",
            status="empty",
            message="No data found. Run run_ingest() first.",
            filters={"account_id": account_id},
        )
    return _json_response(
        operation="portfolio.yearly_summary",
        data=rows,
        row_count=len(rows),
        filters={"account_id": account_id},
    )


@mcp.tool()
def get_account_summary(year: int | None = None) -> str:
    """
    Return a per-account breakdown of portfolio metrics.

    Args:
        year: Optional calendar year to filter (e.g. 2023).
    """
    rows = _portfolio.get_account_summary(year=year)
    if rows is None:
        return _json_response(
            operation="portfolio.account_summary",
            status="empty",
            message="No data found. Run run_ingest() first.",
            filters={"year": year},
        )
    return _json_response(
        operation="portfolio.account_summary",
        data=rows,
        row_count=len(rows),
        filters={"year": year},
    )


@mcp.tool()
def get_transactions(category: str | None = None,
                     account_id: str | None = None,
                     year: int | None = None,
                     search: str | None = None,
                     limit: int = 50) -> str:
    """
    Query individual transactions with optional filters.

    Args:
        category:   Filter by category: cash_flow, dividend, reward,
                    margin_interest, fee, crypto_flow.
        account_id: Filter by account (e.g. "COINBASE").
        year:       Filter by calendar year.
        search:     Case-insensitive substring search on description.
        limit:      Maximum rows to return (default 50, max 500).
    """
    result = _portfolio.query_transactions(
        category=category,
        account_id=account_id,
        year=year,
        search=search,
        limit=limit,
    )
    if result is None:
        return _json_response(
            operation="transactions.query",
            status="empty",
            message="No data found.",
            filters={
                "category": category,
                "account_id": account_id,
                "year": year,
                "search": search,
                "limit": limit,
            },
        )
    return _json_response(
        operation="transactions.query",
        data=result,
        row_count=result["count"],
        filters={
            "category": category,
            "account_id": account_id,
            "year": year,
            "search": search,
            "limit": limit,
        },
    )


@mcp.tool()
def get_positions(account_id: str | None = None,
                  asset_class: str | None = None,
                  sector: str | None = None,
                  position_type: str | None = None) -> str:
    """
    Return current portfolio positions across all asset classes (equity, options,
    futures, crypto) with live prices for equity and stored prices for the rest.

    Args:
        account_id:    Filter by account (e.g. "SCHWAB", "TRADIER-OPT", "COINBASE").
        asset_class:   Filter by class: equity | options | futures | crypto.
        sector:        Filter by sector (equity only, e.g. "Technology", "Income ETF").
        position_type: Filter by type (equity only, e.g. "Stock", "ETF").
    """
    result = _portfolio.get_positions_report(
        account_id=account_id,
        asset_class=asset_class,
        sector=sector,
        position_type=position_type,
    )
    if result is None:
        return _json_response(
            operation="positions.report",
            status="empty",
            message=("No positions in database. Run ingest after adding position "
                     "CSV files to the activity/ folder."),
            filters={
                "account_id": account_id,
                "asset_class": asset_class,
                "sector": sector,
                "position_type": position_type,
            },
        )
    if not result["positions"]:
        return _json_response(
            operation="positions.report",
            status="empty",
            message="No positions match the given filters.",
            data=result,
            filters={
                "account_id": account_id,
                "asset_class": asset_class,
                "sector": sector,
                "position_type": position_type,
            },
        )
    return _json_response(
        operation="positions.report",
        data=result,
        row_count=len(result["positions"]),
        filters={
            "account_id": account_id,
            "asset_class": asset_class,
            "sector": sector,
            "position_type": position_type,
        },
    )


@mcp.tool()
def get_performance(account_id: str | None = None) -> str:
    """
    Return account-level portfolio returns across standard lookback periods
    (1-week, 1-month, 3-month, YTD, 1-year).

    Returns percentage changes computed from daily portfolio snapshots written
    at the end of each ingest run.  Periods with no prior snapshot yet show null.

    Args:
        account_id: Optional account to filter (e.g. "SCHWAB", "RH-BV").
    """
    rows = _portfolio.get_performance_report(account_id=account_id)
    if rows is None:
        return _json_response(
            operation="portfolio.performance",
            status="empty",
            message=("No snapshot data yet. Run `python -m src.ingest` at least once "
                     "to record the first snapshot. Historical periods accumulate "
                     "with each subsequent run."),
            filters={"account_id": account_id},
        )
    if not rows:
        return _json_response(
            operation="portfolio.performance",
            status="empty",
            message="No snapshot data found for that account.",
            filters={"account_id": account_id},
        )
    return _json_response(
        operation="portfolio.performance",
        data=rows,
        row_count=len(rows),
        filters={"account_id": account_id},
    )


@mcp.tool()
def run_ingest(reset: bool = False) -> str:
    """
    Ingest broker CSV files from the activity/ folder into the database.

    By default runs incrementally — only new records are added, existing ones
    are left untouched.  Drop only the latest CSV export from each broker and
    call this; no need to re-download full history every time.

    Args:
        reset: If True, clears all existing transactions and reloads from
               every CSV currently in activity/ (full rebuild).  Use once
               after first setup or if you want a clean slate.
    """
    cmd = [sys.executable, "-m", "src.ingest"]
    if reset:
        cmd.append("--reset")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    output = result.stdout + result.stderr
    if result.returncode != 0:
        return _json_response(
            operation="ingest.csv",
            status="error",
            message=f"Ingest failed with exit code {result.returncode}.",
            errors=[output.strip()] if output.strip() else [],
            command=cmd,
            returncode=result.returncode,
            reset=reset,
        )
    return _json_response(
        operation="ingest.csv",
        message="Ingest completed.",
        command=cmd,
        returncode=result.returncode,
        reset=reset,
        output=output.strip(),
    )


@mcp.tool()
def set_margin(account_id: str, amount: float) -> str:
    """
    Directly set the margin balance for an account without a full position refresh.

    Use this when you know the current margin balance from a broker statement,
    app, or quick balance check, and just want to update that one number.

    Args:
        account_id: The journal account ID (e.g. "RH-BV", "SCHWAB", "TS",
                    "TRADIER"). Case-insensitive.
        amount:     Margin balance in USD as a positive number (e.g. 25000 for
                    $25,000 borrowed). Pass 0 to clear the margin sentinel
                    (i.e. account has no margin debt).

    Returns:
        Confirmation message with the account and amount stored.

    Examples:
        set_margin("RH-BV", 25000)    # $25k margin on Robinhood
        set_margin("SCHWAB", 0)        # clear margin (paid off / not using)
        set_margin("TS", 12500.50)     # TradeStation with $12,500.50 margin
    """
    result, output = _capture_stdout(_ingest.set_margin, account_id, amount)
    message = (
        f"Margin cleared for {result['account_id']} (set to $0)."
        if result["action"] == "cleared"
        else f"Margin for {result['account_id']} set to ${result['amount']:,.2f}."
    )
    return _json_response(
        operation="account.margin.set",
        message=message,
        data=result,
        output=output.strip(),
    )


@mcp.tool()
def refresh_positions(
    schwab_equity_json: str | None = None,
    schwab_futures_json: str | None = None,
    schwab_summary_json: str | None = None,
    schwab_txn_json: str | None = None,
    tradier_positions_json: str | None = None,
    tradier_quotes_json: str | None = None,
    tradier_history_json: str | None = None,
    ts_positions_json: str | None = None,
    ts_balances_json: str | None = None,
    rh_positions_json: str | None = None,
    rh_portfolio_json: str | None = None,
    coinbase_positions_json: str | None = None,
    webull_account_list: str | None = None,
    webull_positions_json: str | None = None,
    webull_balances_json: str | None = None,
    margin_mode: str = "balance",
) -> str:
    """
    Write pre-fetched MCP broker data into the journal database in one call.

    Call each broker's MCP tools first (get_equity_positions, get_positions, etc.),
    then pass their raw JSON responses here as strings.  Any broker whose data is
    omitted is left unchanged in the DB.

    After writing positions, the instrument sector/industry table is enriched via
    yfinance for any equity symbols that still have NULL sector.

    Args:
        schwab_equity_json:     JSON string from schwab get_equity_positions.
        schwab_futures_json:    JSON string from schwab get_futures_positions.
        schwab_summary_json:    JSON string from schwab get_account_summary.
        schwab_txn_json:        JSON string from schwab get_transactions.
        tradier_positions_json: JSON string from tradier get_positions.
        tradier_quotes_json:    JSON string from tradier get_market_quotes.
        tradier_history_json:   JSON string from tradier get_account_history.
        ts_positions_json:      JSON string from TradeStation get-positions-details.
        ts_balances_json:       JSON string from TradeStation get-balances-details.
        rh_positions_json:      JSON string from trayd get_positions (Robinhood).
        rh_portfolio_json:      JSON string from trayd get_portfolio.
        coinbase_positions_json: JSON string from Coinbase MCP balances/positions.
        webull_account_list:    Raw result text from webull get_account_list.
        webull_positions_json:  JSON string: {"webull_account_id": "positions text", ...}.
        webull_balances_json:   JSON string: {"webull_account_id": "balance text", ...}.
        margin_mode:            How margin debt is derived for Schwab, TS, and Robinhood:
                                  "balance"  — use the value reported by the broker balance/
                                               summary/portfolio endpoint (default).
                                  "computed" — gross market value of positions minus net equity.
                                  "csv"      — preserve whatever MARGIN sentinel is already in
                                               the DB (e.g. from a prior CSV ingest).

    Returns:
        JSON summary of rows written per broker.
    """
    def _j(s: str | None) -> dict | None:
        return json.loads(s) if s else None

    summary: dict[str, dict] = {}
    errors: list[str] = []
    warnings: list[str] = []
    brokers_requested: list[str] = []

    if schwab_equity_json:
        brokers_requested.append("SCHWAB")
        try:
            result, output = _capture_stdout(
                _ingest.write_schwab,
                equity_resp  = json.loads(schwab_equity_json),
                futures_resp = _j(schwab_futures_json),
                summary_resp = _j(schwab_summary_json),
                txn_resp     = _j(schwab_txn_json),
                margin_mode  = margin_mode,
            )
            summary["SCHWAB"] = result
            if output.strip():
                summary["SCHWAB"]["output"] = output.strip()
        except Exception as exc:
            error = str(exc)
            summary["SCHWAB"] = {"error": error}
            errors.append(f"SCHWAB: {error}")

    if tradier_positions_json:
        brokers_requested.append("TRADIER")
        try:
            result, output = _capture_stdout(
                _ingest.write_tradier,
                positions_resp = json.loads(tradier_positions_json),
                quotes_resp    = _j(tradier_quotes_json),
                history_resp   = _j(tradier_history_json),
            )
            summary["TRADIER"] = result
            if output.strip():
                summary["TRADIER"]["output"] = output.strip()
        except Exception as exc:
            error = str(exc)
            summary["TRADIER"] = {"error": error}
            errors.append(f"TRADIER: {error}")

    if ts_positions_json:
        brokers_requested.append("TS")
        try:
            result, output = _capture_stdout(
                _ingest.write_tradestation,
                positions_resp = json.loads(ts_positions_json),
                balances_resp  = _j(ts_balances_json),
                margin_mode    = margin_mode,
            )
            summary["TS"] = result
            if output.strip():
                summary["TS"]["output"] = output.strip()
        except Exception as exc:
            error = str(exc)
            summary["TS"] = {"error": error}
            errors.append(f"TS: {error}")

    if rh_positions_json:
        brokers_requested.append("RH-BV")
        try:
            result, output = _capture_stdout(
                _ingest.write_robinhood,
                positions_resp = json.loads(rh_positions_json),
                portfolio_resp = _j(rh_portfolio_json),
                margin_mode    = margin_mode,
            )
            summary["RH-BV"] = result
            if output.strip():
                summary["RH-BV"]["output"] = output.strip()
        except Exception as exc:
            error = str(exc)
            summary["RH-BV"] = {"error": error}
            errors.append(f"RH-BV: {error}")

    if coinbase_positions_json:
        brokers_requested.append("COINBASE")
        try:
            result, output = _capture_stdout(
                _ingest.write_coinbase,
                positions_resp = json.loads(coinbase_positions_json),
            )
            summary["COINBASE"] = result
            if output.strip():
                summary["COINBASE"]["output"] = output.strip()
        except Exception as exc:
            error = str(exc)
            summary["COINBASE"] = {"error": error}
            errors.append(f"COINBASE: {error}")

    if webull_account_list and webull_positions_json:
        brokers_requested.append("WEBULL")
        try:
            pos_by_id  = json.loads(webull_positions_json)
            bal_by_id  = json.loads(webull_balances_json) if webull_balances_json else None
            result, output = _capture_stdout(
                _ingest.write_webull,
                account_list_result = webull_account_list,
                positions_by_wb_id  = pos_by_id,
                balance_by_wb_id    = bal_by_id,
            )
            summary["WEBULL"] = result
            if output.strip():
                summary["WEBULL"]["output"] = output.strip()
        except Exception as exc:
            error = str(exc)
            summary["WEBULL"] = {"error": error}
            errors.append(f"WEBULL: {error}")

    # Enrich sector/industry for any new equity instruments
    try:
        from src.enrichment import enrich_sectors  # noqa: PLC0415
        enriched = enrich_sectors()
        summary["sector_enrichment"] = {"instruments_updated": enriched}
    except Exception as exc:
        warning = str(exc)
        summary["sector_enrichment"] = {"error": warning}
        warnings.append(f"sector_enrichment: {warning}")

    if not brokers_requested:
        warnings.append("No broker payloads were provided; no position rows were refreshed.")

    return _json_response(
        operation="positions.refresh",
        status="partial" if errors else "ok",
        data=summary,
        brokers_requested=brokers_requested,
        broker_count=len(brokers_requested),
        margin_mode=margin_mode,
        warnings=warnings,
        errors=errors,
    )


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
