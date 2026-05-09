"""Interactive terminal browser for trading-journal balances and positions.

Run:
    python -m src.journal_cli

The CLI reads the same SQLite database as the dashboard. It does not call broker
APIs directly; run the MCP sync workflow first when you want fresh positions.
"""
from __future__ import annotations

import sys
import subprocess
import asyncio
import json
import logging
import os
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import db
from src.positions import load_all_positions
from src.mcp_tools.health import check_mcp_health


MCP_POSITION_ACCOUNTS = {
    "RH-BV",
    "RH-KD",
    "WEBULL",
    "WEBULL-CASH",
    "WEBULL-EVENTS",
    "WEBULL-FUT",
    "TS",
    "SCHWAB",
    "TRADIER",
    "COINBASE",
}

ACCOUNT_HEALTH_BROKERS = {
    "COINBASE": "Coinbase",
    "RH-BV": "Robinhood",
    "RH-KD": "Robinhood",
    "TS": "TradeStation",
    "SCHWAB": "Schwab",
    "TRADIER": "Tradier",
    "WEBULL": "Webull",
    "WEBULL-CASH": "Webull",
    "WEBULL-EVENTS": "Webull",
    "WEBULL-FUT": "Webull",
}

MONEY_COLS = {"Market Value", "Cost Basis", "Margin", "Net Equity", "MARKET VALUE", "COST", "totalReturn"}
PRICE_COLS = {"PRICE", "price", "Cost_Basis"}
_HEALTH_CACHE: pd.DataFrame | None = None
_BALANCE_ERRORS: dict[str, str] = {}
REPO_ROOT = Path(__file__).resolve().parents[1]
HEALTH_OK_STATUSES = {"OK", "FALLBACK"}


def _money(v) -> str:
    try:
        if pd.isna(v):
            return ""
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _num(v, decimals: int = 4) -> str:
    try:
        if pd.isna(v):
            return ""
        return f"{float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _as_float(value, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return default


def _print_df(df: pd.DataFrame, *, max_rows: int = 200) -> None:
    if df.empty:
        print("No rows.")
        return
    out = df.head(max_rows).copy()
    for col in out.columns:
        if col in MONEY_COLS:
            out[col] = out[col].map(_money)
        elif col in PRICE_COLS:
            out[col] = out[col].map(lambda v: _num(v, 2))
        elif pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col].map(_num)
    print(out.to_string(index=False))
    if len(df) > max_rows:
        print(f"... {len(df) - max_rows:,} more rows omitted")


def show_mcp_health(*, force: bool = False, compact: bool = False) -> pd.DataFrame:
    global _HEALTH_CACHE

    print("\nMCP health")
    print("=" * 10)
    if _HEALTH_CACHE is None or force:
        print("Checking configured broker MCP servers...")
        rows = check_mcp_health()
        health = pd.DataFrame(rows)
        if "Tools" in health.columns:
            health["Tools"] = health["Tools"].astype(str)
        _HEALTH_CACHE = health
    else:
        print("Using cached MCP health for this CLI session.")
        health = _HEALTH_CACHE.copy()
    if "Tools" in health.columns:
        health["Tools"] = health["Tools"].astype(str)
    if compact:
        compact_cols = [col for col in ("Broker", "Accounts", "Status", "Tools") if col in health.columns]
        _print_df(health[compact_cols])
    else:
        _print_df(health)

    bad = health[~health["Status"].isin(HEALTH_OK_STATUSES)] if not health.empty else health
    if not bad.empty:
        print("\nAccount balances prefer live/cached broker sources; journal.db is used as fallback.")
    elif not health.empty and health["Status"].isin(["FALLBACK"]).any():
        print("\nSome broker MCPs are using configured cached sources. Account balances are available.")
    else:
        print("\nAll configured broker MCP servers responded. Account balances prefer live/cached broker sources.")
    return health


def _health_by_broker(health: pd.DataFrame | None = None) -> dict[str, dict]:
    if health is None:
        health = show_mcp_health()
    if health.empty or "Broker" not in health.columns:
        return {}
    return {str(row["Broker"]): row.to_dict() for _, row in health.iterrows()}


def _account_live_status(account_id: str, health_map: dict[str, dict]) -> str:
    if account_id in _BALANCE_ERRORS:
        return "Balance FAIL"
    broker = ACCOUNT_HEALTH_BROKERS.get(account_id)
    if not broker:
        return "N/A"
    status = str((health_map.get(broker) or {}).get("Status") or "UNKNOWN")
    if status == "OK":
        return "OK"
    if status == "FALLBACK":
        return "Fallback OK"
    if status == "WARN":
        return "WARN"
    return f"{status}/fallback"


def _report_live_fallbacks(health: pd.DataFrame | None = None) -> None:
    if health is None:
        health = _HEALTH_CACHE
    if health is None or health.empty:
        return
    bad = health[~health["Status"].isin(HEALTH_OK_STATUSES)]
    fallback = health[health["Status"].isin(["FALLBACK"])]
    if not fallback.empty:
        print("\nConfigured fallback sources in use:")
        for _, row in fallback.iterrows():
            print(f"  {row['Accounts']}: {row['Broker']} — {row['Detail']}")
    if bad.empty:
        return
    print("\nLIVE connection failures; using configured fallback source for these accounts:")
    for _, row in bad.iterrows():
        print(f"  {row['Accounts']}: {row['Broker']} {row['Status']} — {row['Detail']}")


def _load_accounts() -> pd.DataFrame:
    db.init_db()
    return db.load_account_settings()


def _report_balance_fallbacks() -> None:
    if not _BALANCE_ERRORS:
        return
    print("\nLIVE balance fetch failures; showing journal.db fallback for these accounts:")
    for account_id, detail in sorted(_BALANCE_ERRORS.items()):
        print(f"  {account_id}: {detail}")


def _load_positions() -> pd.DataFrame:
    return load_all_positions()


def _source_label(values: set[str]) -> str:
    if not values:
        return ""
    if values == {"MCP"}:
        return "MCP"
    if values == {"CSV"}:
        return "CSV"
    return "Mixed"


def _account_sources() -> dict[str, str]:
    """Return account_id -> MCP/CSV/Mixed based on stored position provenance."""
    sources: dict[str, set[str]] = {}

    def add(account_id, label: str) -> None:
        if not account_id or not label:
            return
        sources.setdefault(str(account_id), set()).add(label)

    with db.get_conn() as conn:
        for account_id, data_source, source_file in conn.execute(
            "SELECT account_id, data_source, source_file FROM positions"
        ):
            raw = str(data_source or "").strip().lower()
            if raw == "mcp":
                add(account_id, "MCP")
            elif raw == "csv" or source_file:
                add(account_id, "CSV")

        for account_id, data_source, source_file in conn.execute(
            "SELECT account_id, data_source, source_file FROM options_positions"
        ):
            raw = str(data_source or "").strip().lower()
            if raw == "mcp":
                add(account_id, "MCP")
            elif raw == "csv" or source_file:
                add(account_id, "CSV")

        for table in ("futures_positions", "crypto_positions"):
            for account_id, source_file in conn.execute(
                f"SELECT account_id, source_file FROM {table}"
            ):
                add(account_id, "CSV" if source_file else "MCP")

    return {account_id: _source_label(labels) for account_id, labels in sources.items()}


def _balance_row(
    market_value: float,
    margin: float,
    source: str,
    detail: str = "",
    *,
    net_equity: float | None = None,
) -> dict:
    return {
        "market_value": float(market_value),
        "margin": float(margin),
        "net_equity": float(net_equity) if net_equity is not None else (float(market_value) - float(margin)),
        "balance_source": source,
        "balance_detail": detail,
    }


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _coinbase_live_balance() -> dict | None:
    try:
        from scripts.sync_coinbase import _default_config_path, _load_server_env  # noqa: PLC0415
        from src.fetchers.coinbase import normalize_futures, normalize_positions  # noqa: PLC0415

        _load_server_env(_default_config_path())
        from coinbase_derivatives_mcp.server import (  # noqa: PLC0415
            capture_coinbase_portfolio_snapshot,
            query_portfolio_state,
        )

        capture_coinbase_portfolio_snapshot(label="journal-cli-balance")
        state = query_portfolio_state()
        crypto = normalize_positions(state, "COINBASE")
        futures = normalize_futures(state, "COINBASE")
        market_value = sum(_as_float(row.get("market_value")) for row in crypto + futures)
        if market_value <= 0:
            raise RuntimeError("Coinbase live balance returned no non-zero rows")
        row = _balance_row(market_value, 0.0, "Live MCP")
        row["cost_basis"] = sum(
            _as_float(rec.get("cost_basis"))
            for rec in crypto + futures
            if rec.get("cost_basis") is not None
        )
        return row
    except Exception as exc:  # noqa: BLE001 - fall back to DB
        return {"error": str(exc)}


def _robinhood_live_balances() -> dict[str, dict]:
    for name in ("mcp", "httpx", "httpcore", "rich"):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        from src.cli import robinhood as rh_cli  # noqa: PLC0415
        from src.fetchers.robinhood import account_map_from_list, normalize_portfolio  # noqa: PLC0415
    except Exception as exc:
        return {"_error": str(exc)}

    rows: dict[str, dict] = {}
    for profile in rh_cli.list_profiles():
        token = rh_cli.load_bearer_token(profile)
        if not token:
            continue
        try:
            accounts = asyncio.run(rh_cli.fetch_accounts(token))
        except Exception:
            continue
        account_map = account_map_from_list({"accounts": accounts})
        for account in accounts:
            account_number = str(account.get("account_number") or "")
            account_id = account_map.get(account_number)
            if not account_number or not account_id:
                continue
            try:
                portfolio = asyncio.run(rh_cli.fetch_portfolio(token, account_number))
                positions = asyncio.run(rh_cli.fetch_positions(token, account_number))
                bal = normalize_portfolio(portfolio)
                margin = _as_float(bal.get("margin"))
                equity = _as_float(bal.get("equity"))  # authoritative account value
                gross_mv = equity + margin
                rows[account_id] = _balance_row(gross_mv, margin, "Live MCP", f"profile {profile}", net_equity=equity)
                rows[account_id]["cost_basis"] = sum(
                    _as_float(pos.get("quantity")) * _as_float(pos.get("avg_cost"))
                    for pos in positions.get("positions", [])
                )
            except Exception:
                continue
    return rows


def _tradier_live_balance(db_margin: float) -> dict | None:
    def rest_balance() -> dict:
        from src.cli import tradier as tradier_cli  # noqa: PLC0415

        resp = tradier_cli.fetch_balances()
        bal = resp.get("balances", {}) or {}
        equity = _as_float(bal.get("totalEquity") or bal.get("total_equity"))
        cash = _as_float(bal.get("cash") or bal.get("total_cash"))
        margin = _as_float(bal.get("marginBalance") or bal.get("margin_balance"))
        if margin <= 0:
            margin = abs(cash) if cash < 0 else db_margin
        long_stock = _as_float(bal.get("longStockValue"))
        short_stock = _as_float(bal.get("shortStockValue"))
        long_option = _as_float(bal.get("longOptionValue"))
        short_option = _as_float(bal.get("shortOptionValue"))
        # Positions value shown by Tradier account details (gross holdings net shorts)
        market_value = long_stock + short_stock + long_option + short_option
        if market_value <= 0:
            market_value = _as_float(bal.get("marketValue") or bal.get("market_value"))
        if market_value <= 0:
            market_value = equity + margin
        return _balance_row(market_value, margin, "Live API", net_equity=equity if equity > 0 else None)

    async def mcp_balance() -> dict:
        from mcp.client.auth import OAuthClientProvider  # noqa: PLC0415
        from mcp.client.session import ClientSession  # noqa: PLC0415
        from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415
        from mcp.shared.auth import OAuthClientMetadata  # noqa: PLC0415
        from src.mcp_tools.auth import JsonTokenStorage, token_path  # noqa: PLC0415

        auth = OAuthClientProvider(
            server_url="https://mcp.tradier.com/mcp",
            client_metadata=OAuthClientMetadata(
                client_name="Trading Journal CLI",
                redirect_uris=["http://127.0.0.1/callback"],
                token_endpoint_auth_method="none",
            ),
            storage=JsonTokenStorage(token_path("tradier")),
            timeout=30,
        )
        async with streamablehttp_client(
            "https://mcp.tradier.com/mcp",
            auth=auth,
            timeout=30,
            sse_read_timeout=30,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "get_account_balances",
                    {"accountNumber": "6YB44166"},
                )
        text = result.content[0].text if result.content else "{}"
        payload = json.loads(text)
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return payload

    try:
        return rest_balance()
    except Exception as rest_exc:  # noqa: BLE001 - try MCP auth path next
        try:
            resp = asyncio.run(mcp_balance())
        except Exception as mcp_exc:  # noqa: BLE001 - fall back to DB
            return {"error": f"REST: {rest_exc}; MCP: {mcp_exc}"}
        bal = resp.get("balances", {}) or {}
        equity = _as_float(bal.get("totalEquity") or bal.get("total_equity"))
        cash = _as_float(bal.get("cash") or bal.get("total_cash"))
        margin = _as_float(bal.get("marginBalance") or bal.get("margin_balance"))
        if margin <= 0:
            margin = abs(cash) if cash < 0 else db_margin
        long_stock = _as_float(bal.get("longStockValue"))
        short_stock = _as_float(bal.get("shortStockValue"))
        long_option = _as_float(bal.get("longOptionValue"))
        short_option = _as_float(bal.get("shortOptionValue"))
        market_value = long_stock + short_stock + long_option + short_option
        if market_value <= 0:
            market_value = _as_float(bal.get("marketValue") or bal.get("market_value"))
        if market_value <= 0:
            market_value = equity + margin
        return _balance_row(market_value, margin, "Live MCP", net_equity=equity if equity > 0 else None)


def _schwab_cached_balance() -> dict | None:
    try:
        from src.cli import schwab as schwab_cli  # noqa: PLC0415
        from src.fetchers.schwab import normalize_balances  # noqa: PLC0415

        summary = schwab_cli.load_summary()
        if not summary:
            return None
        bal = normalize_balances(summary)
        futures_value = _as_float(db.get_futures_equity_override("SCHWAB") or 0.0)
        securities_mv = _as_float(summary.get("long_market_value") or bal.get("market_value"))
        equity = _as_float(summary.get("equity") or summary.get("liquidation_value") or bal.get("equity"))
        margin = _as_float(bal.get("margin"))
        market_value = securities_mv + futures_value
        if market_value <= 0:
            market_value = equity + margin
        net_equity = equity + futures_value if equity > 0 else None
        return _balance_row(
            market_value,
            margin,
            "Claude JSON",
            f"summary {schwab_cli._file_age_str(schwab_cli.SUMMARY_FILE)}",
            net_equity=net_equity,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _tradestation_cached_balance() -> dict | None:
    try:
        from src.cli import tradestation as ts_cli  # noqa: PLC0415
        from src.fetchers.tradestation import normalize_balances  # noqa: PLC0415

        balances = ts_cli.load_balances()
        if not balances:
            return None
        bal = normalize_balances(balances, "TS")
        # TS balances page semantics:
        # - market value/gross holdings = currentMarketValue
        # - account value/net equity = currentEquity
        ts_total_value = _as_float(bal.get("equity")) if _as_float(bal.get("equity")) > 0 else _as_float(bal.get("market_value"))
        ts_market_value = _as_float(bal.get("market_value")) if _as_float(bal.get("market_value")) > 0 else ts_total_value
        return _balance_row(
            ts_market_value,
            _as_float(bal.get("margin")),
            "Claude JSON",
            f"balances {ts_cli._file_age_str(ts_cli.BALANCES_FILE)}",
            net_equity=ts_total_value if ts_total_value > 0 else None,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _webull_cached_balances(db_market_values: pd.Series) -> dict[str, dict]:
    try:
        from src.cli import webull as wb_cli  # noqa: PLC0415
    except Exception as exc:
        return {"_error": str(exc)}

    rows: dict[str, dict] = {}
    for wb_account_id, info in wb_cli.ACCOUNTS.items():
        account_id = {
            "MARGIN": "WEBULL",
            "CASH": "WEBULL-CASH",
            "EVENTS": "WEBULL-EVENTS",
            "FUTURES": "WEBULL-FUT",
        }.get(info["short"])
        if not account_id:
            continue
        bal = wb_cli.load_balance(wb_account_id) if wb_account_id == wb_cli.MARGIN_ACCOUNT_ID else {}
        if not bal:
            bal = wb_cli.load_balance(wb_account_id)
        positions = wb_cli.load_positions(wb_account_id)
        market_value = _as_float(bal.get("total_market_value")) if bal else sum(
            _as_float(p.get("quantity")) * _as_float(p.get("last")) for p in positions
        )
        cash = _as_float(bal.get("total_cash")) if bal else 0.0
        net_liquidation = _as_float(bal.get("net_liquidation")) if bal else 0.0
        if not market_value and net_liquidation > 0:
            market_value = net_liquidation
        elif not market_value and cash > 0:
            market_value = cash
        if not market_value:
            market_value = float(db_market_values.get(account_id, 0.0))
        cost_basis = sum(_as_float(p.get("quantity")) * _as_float(p.get("avg_cost")) for p in positions)
        if account_id == "WEBULL-CASH" and cost_basis <= 0:
            cost_basis = market_value
        margin = abs(cash) if cash < 0 else 0.0
        net_equity = net_liquidation if net_liquidation > 0 else None
        rows[account_id] = _balance_row(market_value, margin, "Claude JSON", net_equity=net_equity)
        rows[account_id]["cost_basis"] = cost_basis
    return rows


def _fidelity_csv_balance() -> dict | None:
    try:
        from src.cli import fidelity as fidelity_cli  # noqa: PLC0415

        totals = fidelity_cli.load_account_totals()
        if not totals["market_value"]:
            return None
        row = _balance_row(totals["market_value"], totals["margin"], "CSV file", f"{fidelity_cli.CSV_PATH.name}")
        row["cost_basis"] = totals["cost_basis"]
        return row
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _broker_balance_overrides(db_mv: pd.Series, db_margin: pd.Series) -> dict[str, dict]:
    _BALANCE_ERRORS.clear()
    rows: dict[str, dict] = {}

    coinbase = _coinbase_live_balance()
    if coinbase and "error" not in coinbase:
        rows["COINBASE"] = coinbase
    elif coinbase:
        _BALANCE_ERRORS["COINBASE"] = str(coinbase["error"])

    for account_id, bal in _robinhood_live_balances().items():
        if not account_id.startswith("_"):
            rows[account_id] = bal

    tradier = _tradier_live_balance(float(db_margin.get("TRADIER", 0.0)))
    if tradier and "error" not in tradier:
        rows["TRADIER"] = tradier
    elif tradier:
        _BALANCE_ERRORS["TRADIER"] = str(tradier["error"])

    schwab = _schwab_cached_balance()
    if schwab and "error" not in schwab:
        rows["SCHWAB"] = schwab
    elif schwab:
        _BALANCE_ERRORS["SCHWAB"] = str(schwab["error"])

    ts = _tradestation_cached_balance()
    if ts and "error" not in ts:
        rows["TS"] = ts
    elif ts:
        _BALANCE_ERRORS["TS"] = str(ts["error"])

    for account_id, bal in _webull_cached_balances(db_mv).items():
        if not account_id.startswith("_"):
            rows[account_id] = bal

    fidelity = _fidelity_csv_balance()
    if fidelity and "error" not in fidelity:
        rows["FIDELITY"] = fidelity
    elif fidelity:
        _BALANCE_ERRORS["FIDELITY"] = str(fidelity["error"])

    return rows


def _account_summary(health: pd.DataFrame | None = None) -> pd.DataFrame:
    pos = _load_positions()
    accounts = _load_accounts()
    if accounts.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    account_sources = _account_sources()
    health_map = _health_by_broker(health) if health is not None else {}
    if not pos.empty:
        pos = pos.copy()
        pos["MARKET VALUE"] = pd.to_numeric(pos["MARKET VALUE"], errors="coerce").fillna(0)
        is_margin = pos["Ticker"].astype(str).str.upper().eq("MARGIN")
        mv = pos[~is_margin].groupby("Account")["MARKET VALUE"].sum()
        margin = pos[is_margin].groupby("Account")["MARKET VALUE"].sum().abs()
        cost_parts = pd.Series(0.0, index=pos.index)
        if "COST" in pos.columns:
            cost_parts = cost_parts + pd.to_numeric(pos["COST"], errors="coerce").fillna(0)
        if "cost_basis" in pos.columns:
            cost_parts = cost_parts + pd.to_numeric(pos["cost_basis"], errors="coerce").fillna(0)
        cost_basis = cost_parts[~is_margin].groupby(pos.loc[~is_margin, "Account"]).sum()
    else:
        mv = pd.Series(dtype=float)
        margin = pd.Series(dtype=float)
        cost_basis = pd.Series(dtype=float)
    balance_overrides = _broker_balance_overrides(mv, margin)

    for _, acct in accounts.sort_values("account_id").iterrows():
        if not bool(acct.get("active", 1)):
            continue
        account_id = str(acct["account_id"])
        market_value = float(mv.get(account_id, 0.0))
        cost_value = float(cost_basis.get(account_id, 0.0))
        margin_value = float(margin.get(account_id, 0.0))
        balance_source = "journal.db"
        override = balance_overrides.get(account_id)
        if override:
            market_value = _as_float(override.get("market_value"))
            margin_value = _as_float(override.get("margin"))
            net_equity_value = _as_float(override.get("net_equity"), market_value - margin_value)
            if override.get("cost_basis") is not None:
                cost_value = _as_float(override.get("cost_basis"))
            balance_source = str(override.get("balance_source") or balance_source)
        else:
            net_equity_value = market_value - margin_value
        source = account_sources.get(
            account_id,
            "MCP" if account_id in MCP_POSITION_ACCOUNTS else "CSV",
        )
        if source == "CSV" and account_id in MCP_POSITION_ACCOUNTS:
            source = "CSV->MCP"
        rows.append({
            "Account": account_id,
            "Broker": acct.get("broker") or "",
            "Type": acct.get("account_type") or "",
            "Source": source,
            "Balance Source": balance_source,
            "Live Status": _account_live_status(account_id, health_map) if health_map else "",
            "Market Value": market_value,
            "Cost Basis": cost_value,
            "Margin": margin_value,
            "Net Equity": net_equity_value,
        })

    cash = db.get_cash_balance()
    if cash > 0:
        rows.append({
            "Account": "CASH",
            "Broker": "Multi-Bank",
            "Type": "cash",
            "Source": "Manual",
            "Balance Source": "manual",
            "Live Status": "N/A",
            "Market Value": cash,
            "Cost Basis": cash,
            "Margin": 0.0,
            "Net Equity": cash,
        })

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    db.upsert_account_balances([
        {
            "account_id": row["Account"],
            "market_value": row["Market Value"],
            "cost_basis": row["Cost Basis"],
            "margin": row["Margin"],
            "net_equity": row["Net Equity"],
            "source": row["Balance Source"],
            "detail": row["Live Status"],
        }
        for row in rows
    ])
    total = {
        "Account": "TOTAL",
        "Broker": "",
        "Type": "",
        "Source": "",
        "Balance Source": "",
        "Live Status": "",
        "Market Value": summary["Market Value"].sum(),
        "Cost Basis": summary["Cost Basis"].sum(),
        "Margin": summary["Margin"].sum(),
        "Net Equity": summary["Net Equity"].sum(),
    }
    return pd.concat([summary, pd.DataFrame([total])], ignore_index=True)


def _account_ids() -> list[str]:
    accounts = _load_accounts()
    if accounts.empty:
        return []
    active = accounts[accounts["active"].fillna(1).astype(bool)]
    return [
        str(account_id)
        for account_id in active["account_id"].tolist()
        if str(account_id) != "CASH"
    ]


def show_overview() -> None:
    health = show_mcp_health(compact=True)
    summary = _account_summary(health)
    print("\nAccount balances")
    print("=" * 16)
    _print_df(summary)
    _report_live_fallbacks(health)
    _report_balance_fallbacks()

    total = summary[summary["Account"] == "TOTAL"]
    market_value = float(total["Market Value"].iloc[0]) if not total.empty else 0.0
    margin = float(total["Margin"].iloc[0]) if not total.empty else 0.0
    net_worth = float(total["Net Equity"].iloc[0]) if not total.empty else 0.0
    print()
    print(f"Net worth:    {_money(net_worth)}")
    print(f"Market value: {_money(market_value)}")
    print(f"Margin:       {_money(margin)}")


def show_account_menu() -> None:
    health = show_mcp_health()
    _report_live_fallbacks(health)
    accounts = _account_ids()
    if not accounts:
        print("No active accounts found.")
        return

    while True:
        print("\nAccounts")
        for i, account_id in enumerate(accounts, start=1):
            print(f"{i}. {account_id}")
        print("0. Back")

        choice = input("Select account: ").strip()
        if choice in {"0", "q", "Q"}:
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(accounts)):
            print("Choose a listed account number.")
            continue
        show_positions(accounts[int(choice) - 1])


def show_positions(account_id: str | None = None) -> None:
    health = show_mcp_health()
    _report_live_fallbacks(health)
    pos = _load_positions()
    if pos.empty:
        print("No positions found. Run an MCP sync or CSV ingest first.")
        return

    if account_id:
        pos = pos[pos["Account"].astype(str).str.upper() == account_id.upper()]
    pos = pos[pos["Ticker"].astype(str).str.upper() != "MARGIN"].copy()
    if pos.empty:
        print("No positions for that account.")
        return

    cols = [
        "Account", "asset_class", "Ticker", "Name", "TYPE", "sector",
        "Shares", "qty", "PRICE", "price", "MARKET VALUE", "COST", "totalReturn",
        "underlying", "expiry", "strike", "call_put",
    ]
    cols = [c for c in cols if c in pos.columns]
    pos = pos[cols].sort_values(["Account", "asset_class", "MARKET VALUE"], ascending=[True, True, False])
    title = f"Positions - {account_id}" if account_id else "All positions"
    print(f"\n{title}")
    print("=" * len(title))
    _print_df(pos)


def set_cash_balance() -> None:
    current = db.get_cash_balance()
    print(f"Current cash balance: {_money(current)}")
    raw = input("New cash balance, blank to cancel: ").strip().replace("$", "").replace(",", "")
    if not raw:
        return
    try:
        balance = float(raw)
    except ValueError:
        print("Enter a numeric balance.")
        return
    db.upsert_cash_balance(balance)
    print(f"Cash balance set to {_money(balance)}")


def _run_command(label: str, args: list[str], *, pause: bool = True) -> int:
    print(f"\n{label}")
    print("=" * len(label))
    print(" ".join(args))
    result = subprocess.run(args, cwd=REPO_ROOT)
    if result.returncode == 0:
        print(f"\n{label} completed.")
    else:
        print(f"\n{label} failed with exit code {result.returncode}.")
    if pause:
        input("Press Enter to continue...")
    return result.returncode


def _run_best_effort(label: str, args: list[str]) -> bool:
    code = _run_command(label, args, pause=False)
    return code == 0


def _prompt_optional_float(prompt: str) -> float | None:
    raw = input(prompt).strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        print("Invalid number; skipped.")
        return None


def _csv_files_changed() -> bool:
    tracked = [
        REPO_ROOT / "activity" / "WEBULL-inv.csv",
        REPO_ROOT / "activity" / "WEBULL-cash.csv",
        REPO_ROOT / "activity" / "schwab.csv",
        REPO_ROOT / "activity" / "robinhood-inv-bv.csv",
        REPO_ROOT / "activity" / "robinhood-inv-kd.csv",
        REPO_ROOT / "activity" / "coinbase-main.csv",
        REPO_ROOT / "activity" / "tradier.csv",
        REPO_ROOT / "activity" / "tdstation-cash.csv",
        REPO_ROOT / "activity" / "fidelity_Investment_income_balance.csv",
        REPO_ROOT / "activity" / "positions-fidelity.csv",
    ]
    state = db.load_csv_ingest_state()
    state_map = {
        str(Path(row["file_path"]).resolve()): row
        for _, row in state.iterrows()
        if row.get("file_path")
    } if not state.empty else {}
    for path in tracked:
        if not path.exists():
            continue
        key = str(path.resolve())
        row = state_map.get(key)
        stat = path.stat()
        if row is None:
            return True
        try:
            old_size = int(row.get("file_size_bytes")) if pd.notna(row.get("file_size_bytes")) else -1
        except Exception:
            old_size = -1
        old_mtime = str(row.get("file_mtime_utc") or "")
        cur_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        if old_size != stat.st_size or old_mtime != cur_mtime:
            return True
    return False


def _touch_csv_state_skipped() -> None:
    tracked = [
        (REPO_ROOT / "activity" / "WEBULL-inv.csv", "WEBULL", "transactions"),
        (REPO_ROOT / "activity" / "WEBULL-cash.csv", "WEBULL", "transactions"),
        (REPO_ROOT / "activity" / "schwab.csv", "SCHWAB", "transactions"),
        (REPO_ROOT / "activity" / "robinhood-inv-bv.csv", "RH-BV", "transactions"),
        (REPO_ROOT / "activity" / "robinhood-inv-kd.csv", "RH-KD", "transactions"),
        (REPO_ROOT / "activity" / "coinbase-main.csv", "COINBASE", "transactions"),
        (REPO_ROOT / "activity" / "tradier.csv", "TRADIER", "transactions"),
        (REPO_ROOT / "activity" / "tdstation-cash.csv", "TS", "transactions"),
        (REPO_ROOT / "activity" / "fidelity_Investment_income_balance.csv", "FIDELITY", "transactions"),
        (REPO_ROOT / "activity" / "positions-fidelity.csv", "FIDELITY", "positions"),
    ]
    for path, acct, role in tracked:
        if not path.exists():
            continue
        stat = path.stat()
        db.upsert_csv_ingest_state(
            file_path=str(path.resolve()),
            account_id=acct,
            file_role=role,
            file_mtime_utc=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            file_size_bytes=stat.st_size,
            rows_written=0,
            status="skipped_unchanged",
            detail="sync-all: unchanged; ingest skipped",
        )


async def _fetch_schwab_payloads_to_tmp() -> tuple[bool, str]:
    from mcp.client.session import ClientSession  # noqa: PLC0415
    from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: PLC0415
    from src.mcp_tools.health import load_mcp_servers  # noqa: PLC0415

    servers = load_mcp_servers()
    server = servers.get("schwab-smartspreads-file")
    if not server:
        return False, "schwab-smartspreads-file MCP server is not configured"
    command = server.get("command")
    if not command:
        return False, "schwab-smartspreads-file MCP server has no command configured"

    env = os.environ.copy()
    env.update(server.get("env") or {})
    env.setdefault("FASTMCP_LOG_LEVEL", "ERROR")
    env.setdefault("LOG_LEVEL", "ERROR")
    params = StdioServerParameters(
        command=str(command),
        args=[str(arg) for arg in (server.get("args") or [])],
        env=env,
        cwd=server.get("cwd"),
    )

    def _content_to_text(result) -> str:
        return "\n".join(
            getattr(item, "text", "")
            for item in result.content
            if getattr(item, "text", "")
        )

    def _extract_json_text(result) -> str:
        text = _content_to_text(result).strip()
        if not text:
            raise RuntimeError("empty MCP response")
        payload = json.loads(text)
        if isinstance(payload, dict) and isinstance(payload.get("result"), str):
            raw = payload["result"].strip()
            if raw:
                return raw
        return text

    tmp = REPO_ROOT / "data" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    out_files = {
        "equity": tmp / "schwab_equity.json",
        "futures": tmp / "schwab_futures.json",
        "summary": tmp / "schwab_summary.json",
    }

    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                eq = await session.call_tool("get_equity_positions", {})
                fut = await session.call_tool("get_futures_positions", {})
                summ = await session.call_tool("get_account_summary", {})

    out_files["equity"].write_text(_extract_json_text(eq), encoding="utf-8")
    out_files["futures"].write_text(_extract_json_text(fut), encoding="utf-8")
    out_files["summary"].write_text(_extract_json_text(summ), encoding="utf-8")
    return True, "wrote fresh schwab_equity/futures/summary JSON to data\\tmp"


def _fetch_schwab_payloads_to_tmp_sync() -> bool:
    print("\nSchwab MCP fetch (local -> data\\tmp)")
    print("=" * 33)
    try:
        ok, detail = asyncio.run(_fetch_schwab_payloads_to_tmp())
    except Exception as exc:  # noqa: BLE001
        print(f"Schwab MCP fetch failed: {exc}")
        return False
    print(detail)
    return ok


def _validate_ts_tmp_payloads(max_age_hours: int = 6) -> bool:
    print("\nTradeStation payload precheck")
    print("=" * 27)
    pos_path = REPO_ROOT / "data" / "tmp" / "ts_positions.json"
    bal_path = REPO_ROOT / "data" / "tmp" / "ts_balances.json"

    missing = [str(p) for p in (pos_path, bal_path) if not p.exists()]
    if missing:
        print("Missing required TS payload file(s):")
        for m in missing:
            print(f"  - {m}")
        print("Update both files before running TS ingest.")
        return False

    now = datetime.now(timezone.utc)
    ok = True
    for p in (pos_path, bal_path):
        try:
            text = p.read_text(encoding="utf-8-sig")
            obj = json.loads(text)
            if not isinstance(obj, dict):
                raise ValueError("root is not an object")
        except Exception as exc:  # noqa: BLE001
            print(f"Invalid JSON in {p.name}: {exc}")
            ok = False
            continue
        age_hours = (now - datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)).total_seconds() / 3600.0
        freshness = "OK" if age_hours <= max_age_hours else "STALE"
        print(f"{p.name}: {freshness} ({age_hours:.1f}h old)")
        if freshness != "OK":
            ok = False

    if not ok:
        print(
            "TS precheck failed. Refresh ts_positions.json and ts_balances.json "
            "with live MCP payloads, then rerun sync."
        )
    return ok


def sync_all_ingest_workflow() -> None:
    print("\nSync all (cached payloads + CSV + snapshot)")
    print("=" * 42)
    print("This runs broker ingests from data/tmp, optional cash/margin inputs, then snapshot.")

    ok = True
    ok &= _run_best_effort(
        "Webull ingest",
        [
            sys.executable, "-m", "src.mcp_ingest",
            "--broker", "webull",
            "--account-list", "data\\tmp\\wb_account_list.txt",
            "--positions-map", "data\\tmp\\wb_positions_map.rebuilt.json",
            "--balances-map", "data\\tmp\\wb_balances_map.json",
        ],
    )
    ok &= _fetch_schwab_payloads_to_tmp_sync()
    ok &= _run_best_effort(
        "Schwab ingest",
        [
            sys.executable, "-m", "src.mcp_ingest",
            "--broker", "schwab",
            "--equity", "data\\tmp\\schwab_equity.json",
            "--futures", "data\\tmp\\schwab_futures.json",
            "--summary", "data\\tmp\\schwab_summary.json",
        ],
    )
    ok &= _run_best_effort(
        "Tradier ingest",
        [
            sys.executable, "-m", "src.mcp_ingest",
            "--broker", "tradier",
            "--positions", "data\\tmp\\tradier_pos.json",
            "--quotes", "data\\tmp\\tradier_quotes.json",
            "--balances", "data\\tmp\\tradier_balances.json",
        ],
    )
    ok &= _validate_ts_tmp_payloads()
    ok &= _run_best_effort(
        "TradeStation ingest",
        [
            sys.executable, "-m", "src.mcp_ingest",
            "--broker", "ts",
            "--positions", "data\\tmp\\ts_positions.json",
            "--balances", "data\\tmp\\ts_balances.json",
        ],
    )
    ok &= _run_best_effort(
        "Robinhood RH-BV ingest",
        [
            sys.executable, "-m", "src.mcp_ingest",
            "--broker", "robinhood",
            "--account-id", "RH-BV",
            "--positions", "data\\tmp\\rh_pos.json",
            "--portfolio", "data\\tmp\\rh_port.json",
        ],
    )
    ok &= _run_best_effort(
        "Robinhood RH-KD ingest",
        [
            sys.executable, "-m", "src.mcp_ingest",
            "--broker", "robinhood",
            "--account-id", "RH-KD",
            "--positions", "data\\tmp\\rh_kd_pos.json",
            "--portfolio", "data\\tmp\\rh_kd_port.json",
        ],
    )
    ok &= _run_best_effort("Coinbase sync", [sys.executable, "scripts/sync_coinbase.py"])
    if _csv_files_changed():
        ok &= _run_best_effort("CSV ingest (changed files detected)", [sys.executable, "-m", "src.ingest"])
    else:
        print("\nCSV ingest skipped (no tracked CSV file changes detected).")
        _touch_csv_state_skipped()

    cash = _prompt_optional_float("CASH update (blank to skip): ")
    if cash is not None:
        ok &= _run_best_effort("Set CASH", [sys.executable, "-m", "src.cash", str(cash)])

    print("\nOptional fidelity margin override (blank = skip):")
    fidelity_margin = _prompt_optional_float("  FIDELITY margin amount: ")
    if fidelity_margin is not None:
        ok &= _run_best_effort(
            "Set margin FIDELITY",
            [sys.executable, "-m", "src.mcp_ingest", "--set-margin", "FIDELITY", str(fidelity_margin)],
        )

    ok &= _run_best_effort("Snapshot only", [sys.executable, "-m", "src.ingest", "--snapshot-only"])
    try:
        # Keep dashboard account summary aligned with latest sync by refreshing
        # persisted account_balances immediately after ingest/snapshot.
        health = show_mcp_health(force=True, compact=True)
        _account_summary(health)
        print("Account balances refreshed from latest sync inputs.")
    except Exception as exc:  # noqa: BLE001 - non-fatal post-sync refresh
        ok = False
        print(f"Account balance refresh failed: {exc}")
    print("\nSync-all completed." if ok else "\nSync-all completed with errors (see steps above).")
    input("Press Enter to continue...")


def _kill_next_dashboard() -> None:
    """Kill any existing API/Next.js processes before launching."""
    if sys.platform != "win32":
        return
    subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "($_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'src\\.api\\.main') -or "
            "($_.CommandLine -match 'next' -and $_.CommandLine -match 'dev.*-p.*3000') "
            "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            "$ports=3000,8000; foreach($p in $ports){ "
            "Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty OwningProcess -Unique | "
            "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }"
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_for_port(port: int, timeout: int = 15) -> bool:
    import socket, time
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(1)
    return False


def _launch_next_dashboard() -> None:
    import time
    print("\nLaunching Next.js dashboard (API :8000 + UI :3000) ...")
    print("  Stopping any existing instances ...")
    _kill_next_dashboard()
    time.sleep(2)

    api_port, ui_port = 8000, 3000
    log_dir = REPO_ROOT / "data" / "tmp"
    log_dir.mkdir(parents=True, exist_ok=True)

    api_cmd = f'"{sys.executable}" -m uvicorn src.api.main:app --host 127.0.0.1 --port {api_port}'
    subprocess.Popen(
        f'cmd /c start /b "" {api_cmd} 2>"{log_dir / "api.log"}"',
        cwd=REPO_ROOT, shell=True,
    )

    ui_dir = REPO_ROOT / "ui"
    next_bin = ui_dir / "node_modules" / "next" / "dist" / "bin" / "next"
    subprocess.Popen(
        f'cmd /c start /b "" node "{next_bin}" dev -p {ui_port} 2>"{log_dir / "ui.log"}"',
        cwd=str(ui_dir), shell=True,
    )

    print("  Waiting for services to start ...")
    api_ok = _wait_for_port(api_port, timeout=10)
    ui_ok = _wait_for_port(ui_port, timeout=15)

    if api_ok:
        print(f"  API ready at http://127.0.0.1:{api_port}")
    else:
        print(f"  API failed to start. Check {log_dir / 'api.log'}")
    if ui_ok:
        import webbrowser
        url = f"http://localhost:{ui_port}"
        print(f"  UI  ready at {url}")
        webbrowser.open(url)
    else:
        print(f"  UI  failed to start. Check {log_dir / 'ui.log'}")

    input("Press Enter to continue...")


def _stop_dashboard() -> None:
    if sys.platform != "win32":
        print("Stop dashboard is currently implemented for Windows only.")
        input("Press Enter to continue...")
        return

    script = REPO_ROOT / "scripts" / "kill_zombies.ps1"
    if not script.exists():
        print(f"Missing script: {script}")
        input("Press Enter to continue...")
        return
    _run_command(
        "Stop dashboard",
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-IncludePorts",
        ],
    )


def housekeeping_menu() -> None:
    while True:
        print("\nHousekeeping")
        print("1. Run incremental ingest")
        print("2. Rebuild database from CSVs")
        print("3. Write snapshot only")
        print("4. Sync Coinbase")
        print("5. Dry-run Coinbase sync")
        print("6. Launch Next.js dashboard")
        print("7. Stop dashboard")
        print("8. Run tests")
        print("9. Sync all brokers + CSV + snapshot")
        print("0. Back")

        choice = input("Select: ").strip()
        if choice in {"0", "q", "Q"}:
            return
        if choice == "1":
            _run_command("Incremental ingest", [sys.executable, "-m", "src.ingest"])
        elif choice == "2":
            confirm = input("This rebuilds journal.db from source files. Continue? [y/N]: ").strip().lower()
            if confirm == "y":
                _run_command("Reset ingest", [sys.executable, "-m", "src.ingest", "--reset"])
        elif choice == "3":
            _run_command("Snapshot only", [sys.executable, "-m", "src.ingest", "--snapshot-only"])
        elif choice == "4":
            _run_command("Sync Coinbase", [sys.executable, "scripts/sync_coinbase.py"])
        elif choice == "5":
            _run_command("Dry-run Coinbase sync", [sys.executable, "scripts/sync_coinbase.py", "--dry-run"])
        elif choice == "6":
            _launch_next_dashboard()
        elif choice == "7":
            _stop_dashboard()
        elif choice == "8":
            _run_command("Run tests", [sys.executable, "-m", "pytest", "tests/", "-q"])
        elif choice == "9":
            sync_all_ingest_workflow()
        else:
            print("Choose 0-9.")


def _prompt_date(label: str, default: date) -> str | None:
    while True:
        raw = input(f"{label} [{default.isoformat()}]: ").strip()
        if raw.lower() in {"q", "quit", "exit"}:
            return None
        if not raw:
            return default.isoformat()
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
        except ValueError:
            print("Use YYYY-MM-DD, for example 2026-05-01.")


def transaction_history_menu() -> None:
    from src.cli import webull as webull_cli

    while True:
        print("\nTransaction history")
        print("1. Webull")
        print("0. Back")

        broker_choice = input("Select broker: ").strip()
        if broker_choice in {"0", "q", "Q"}:
            return
        if broker_choice != "1":
            print("Choose 0-1.")
            continue

        account_items = list(webull_cli.ACCOUNTS.items())
        while True:
            print("\nWebull accounts")
            for i, (_account_id, info) in enumerate(account_items, start=1):
                print(f"{i}. {info['label']}")
            print("0. Back")

            account_choice = input("Select account: ").strip()
            if account_choice in {"0", "q", "Q"}:
                break
            if not account_choice.isdigit() or not (1 <= int(account_choice) <= len(account_items)):
                print(f"Choose 1-{len(account_items)} or 0.")
                continue

            account_id, info = account_items[int(account_choice) - 1]
            today = date.today()
            start = _prompt_date("From date", today - timedelta(days=7))
            if start is None:
                continue
            end = _prompt_date("To date", today)
            if end is None:
                continue
            print(f"\nFetching Webull {info['label']} transactions from {start} to {end}...")
            try:
                output_path = webull_cli.export_order_history_csv(account_id, start, end)
            except Exception as exc:  # noqa: BLE001 - interactive CLI should report and continue
                print(f"Transaction history export failed: {exc}")
                input("Press Enter to continue...")
                continue
            print(f"Exported transaction history to {output_path}")
            input("Press Enter to continue...")


def _broker_live_view() -> None:
    """Launch the standalone broker CLI menu (live API + cached data)."""
    from src.cli.menu import main_menu
    main_menu()


def main() -> int:
    db.init_db()
    while True:
        print("\nTrading Journal CLI")
        print("1. Account balances")
        print("2. Positions by account")
        print("3. All positions")
        print("4. Set cash balance")
        print("5. MCP health")
        print("6. Housekeeping")
        print("7. Broker Live View")
        print("8. Transaction history")
        print("9. Sync all brokers + CSV + snapshot")
        print("0. Exit")

        choice = input("Select: ").strip()
        if choice in {"0", "q", "Q"}:
            return 0
        if choice == "1":
            show_overview()
        elif choice == "2":
            show_account_menu()
        elif choice == "3":
            show_positions()
        elif choice == "4":
            set_cash_balance()
        elif choice == "5":
            show_mcp_health(force=True)
        elif choice == "6":
            housekeeping_menu()
        elif choice == "7":
            _broker_live_view()
        elif choice == "8":
            transaction_history_menu()
        elif choice == "9":
            sync_all_ingest_workflow()
        else:
            print("Choose 0-9.")


if __name__ == "__main__":
    raise SystemExit(main())
