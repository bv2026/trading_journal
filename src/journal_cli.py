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
from pathlib import Path
from datetime import date, datetime, timedelta

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


def _balance_row(market_value: float, margin: float, source: str, detail: str = "") -> dict:
    return {
        "market_value": float(market_value),
        "margin": float(margin),
        "net_equity": float(market_value) - float(margin),
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
                equity = _as_float(bal.get("equity"))
                rows[account_id] = _balance_row(equity + margin, margin, "Live MCP", f"profile {profile}")
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
        market_value = _as_float(bal.get("marketValue") or bal.get("market_value"))
        if market_value <= 0:
            market_value = equity + margin
        return _balance_row(market_value, margin, "Live API")

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
        market_value = _as_float(bal.get("marketValue") or bal.get("market_value"))
        if market_value <= 0:
            market_value = equity + margin
        return _balance_row(market_value, margin, "Live MCP")


def _schwab_cached_balance() -> dict | None:
    try:
        from src.cli import schwab as schwab_cli  # noqa: PLC0415
        from src.fetchers.schwab import normalize_balances  # noqa: PLC0415

        summary = schwab_cli.load_summary()
        if not summary:
            return None
        bal = normalize_balances(summary)
        return _balance_row(
            _as_float(bal.get("market_value")),
            _as_float(bal.get("margin")),
            "Claude JSON",
            f"summary {schwab_cli._file_age_str(schwab_cli.SUMMARY_FILE)}",
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
        return _balance_row(
            _as_float(bal.get("market_value")),
            _as_float(bal.get("margin")),
            "Claude JSON",
            f"balances {ts_cli._file_age_str(ts_cli.BALANCES_FILE)}",
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
        rows[account_id] = _balance_row(market_value, margin, "Claude JSON")
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
            if override.get("cost_basis") is not None:
                cost_value = _as_float(override.get("cost_basis"))
            balance_source = str(override.get("balance_source") or balance_source)
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
            "Net Equity": market_value - margin_value,
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


def _launch_dashboard() -> None:
    print("\nLaunching dashboard at http://localhost:8501 ...")
    args = [sys.executable, "-m", "streamlit", "run", "dashboard/app.py"]
    kwargs: dict = {
        "cwd": REPO_ROOT,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(args, **kwargs)
    print("Dashboard launch requested. Open http://localhost:8501 if it does not open automatically.")
    input("Press Enter to continue...")


def _launch_next_dashboard() -> None:
    print("\nLaunching Next.js dashboard (API :8000 + UI :3000) ...")
    base_kwargs: dict = {
        "cwd": REPO_ROOT,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        base_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"],
        **base_kwargs,
    )
    ui_dir = REPO_ROOT / "ui"
    subprocess.Popen(["npx", "next", "dev", "-p", "3000"], cwd=str(ui_dir), **base_kwargs)
    print("API: http://127.0.0.1:8000  UI: http://localhost:3000")
    input("Press Enter to continue...")


def _stop_dashboard() -> None:
    if sys.platform != "win32":
        print("Stop dashboard is currently implemented for Windows only.")
        input("Press Enter to continue...")
        return

    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match 'streamlit' -and $_.CommandLine -match 'dashboard[/\\\\]app\\.py' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    _run_command(
        "Stop dashboard",
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
    )


def housekeeping_menu() -> None:
    while True:
        print("\nHousekeeping")
        print("1. Run incremental ingest")
        print("2. Rebuild database from CSVs")
        print("3. Write snapshot only")
        print("4. Sync Coinbase")
        print("5. Dry-run Coinbase sync")
        print("6. Launch Streamlit dashboard")
        print("7. Launch Next.js dashboard")
        print("8. Stop dashboard")
        print("9. Run tests")
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
            _launch_dashboard()
        elif choice == "7":
            _launch_next_dashboard()
        elif choice == "8":
            _stop_dashboard()
        elif choice == "9":
            _run_command("Run tests", [sys.executable, "-m", "pytest", "tests/", "-q"])
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
        else:
            print("Choose 0-8.")


if __name__ == "__main__":
    raise SystemExit(main())
