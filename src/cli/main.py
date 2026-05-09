"""Unified Trading Journal command surface.

This is the non-interactive CLI intended for automation, MCP wrappers, and
repeatable local workflows. Existing legacy module CLIs remain available while
commands migrate here.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import db
from src import ingest as ingest_module
from src import mcp_ingest
from src.mcp_tools.health import check_mcp_health
from src.services import dashboard_capabilities
from src.services import portfolio


def _print_json(payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _receipt(
    *,
    operation: str,
    status: str = "ok",
    message: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "operation": operation,
    }
    if message:
        payload["message"] = message
    payload.update(fields)
    return payload


def _run_with_captured_stdout(fn, *args, **kwargs) -> tuple[Any, str]:
    buffer = StringIO()
    with redirect_stdout(buffer):
        result = fn(*args, **kwargs)
    return result, buffer.getvalue()


def _cmd_portfolio_summary(args: argparse.Namespace) -> int:
    result = portfolio.get_portfolio_summary(
        year=args.year,
        account_id=args.account,
        include_live_net_worth=not args.no_live,
    )
    if result is None:
        _print_json(_receipt(
            operation="portfolio.summary",
            status="empty",
            message="No data found.",
        ))
        return 1
    _print_json(result)
    return 0


def _cmd_portfolio_positions(args: argparse.Namespace) -> int:
    result = portfolio.get_positions_report(
        account_id=args.account,
        asset_class=args.asset_class,
        sector=args.sector,
        position_type=args.position_type,
    )
    if result is None:
        _print_json(_receipt(
            operation="portfolio.positions",
            status="empty",
            message="No positions in database.",
        ))
        return 1
    _print_json(result)
    return 0


def _cmd_portfolio_performance(args: argparse.Namespace) -> int:
    rows = portfolio.get_performance_report(account_id=args.account)
    if rows is None:
        _print_json(_receipt(
            operation="portfolio.performance",
            status="empty",
            message="No snapshot data found.",
        ))
        return 1
    _print_json(rows)
    return 0


def _cmd_transactions(args: argparse.Namespace) -> int:
    result = portfolio.query_transactions(
        category=args.category,
        account_id=args.account,
        year=args.year,
        search=args.search,
        limit=args.limit,
    )
    if result is None:
        _print_json(_receipt(
            operation="transactions.query",
            status="empty",
            message="No transactions found.",
        ))
        return 1
    _print_json(result)
    return 0


def _cmd_ingest_csv(args: argparse.Namespace) -> int:
    _, output = _run_with_captured_stdout(
        ingest_module.run,
        reset=args.reset,
        include_mcp_position_csv=args.include_mcp_position_csv,
    )
    _print_json(_receipt(
        operation="ingest.csv",
        reset=args.reset,
        include_mcp_position_csv=args.include_mcp_position_csv,
        output=output.strip(),
    ))
    return 0


def _cmd_ingest_snapshot(args: argparse.Namespace) -> int:
    db.init_db()
    snapshot_date = args.as_of or date.today().isoformat()
    snap_map = ingest_module._compute_snapshot_map()
    if not snap_map:
        _print_json(_receipt(
            operation="ingest.snapshot",
            status="empty",
            snapshot_date=snapshot_date,
            account_count=0,
            message="No positions found; snapshot skipped.",
        ))
        return 1
    db.write_portfolio_snapshot(snapshot_date, snap_map)
    _print_json(_receipt(
        operation="ingest.snapshot",
        snapshot_date=snapshot_date,
        account_count=len(snap_map),
        accounts=sorted(snap_map),
    ))
    return 0


def _cmd_account_cash_get(args: argparse.Namespace) -> int:
    db.init_db()
    balance = db.get_cash_balance(args.account)
    _print_json(_receipt(
        operation="account.cash.get",
        account_id=args.account,
        balance=round(balance, 2),
    ))
    return 0


def _cmd_account_cash_set(args: argparse.Namespace) -> int:
    db.init_db()
    db.upsert_cash_balance(args.amount, account_id=args.account)
    _print_json(_receipt(
        operation="account.cash.set",
        account_id=args.account,
        balance=round(args.amount, 2),
    ))
    return 0


def _cmd_account_margin_get(args: argparse.Namespace) -> int:
    db.init_db()
    amount = db.get_margin_override(args.account)
    _print_json(_receipt(
        operation="account.margin.get",
        account_id=args.account,
        margin=round(amount, 2) if amount is not None else None,
    ))
    return 0


def _cmd_account_margin_set(args: argparse.Namespace) -> int:
    result, _ = _run_with_captured_stdout(mcp_ingest.set_margin, args.account, args.amount)
    _print_json(_receipt(
        operation="account.margin.set",
        **result,
    ))
    return 0


def _cmd_health(args: argparse.Namespace) -> int:
    rows = check_mcp_health()
    _print_json(_receipt(
        operation="health",
        row_count=len(rows),
        checks=rows,
    ))
    return 0


def _cmd_dashboard_launch(args: argparse.Namespace) -> int:
    app = ROOT / "dashboard" / "app.py"
    if not app.exists():
        _print_json(_receipt(
            operation="dashboard.launch",
            status="error",
            message="dashboard/app.py not found.",
        ))
        return 1

    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.headless", "true"]
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(cmd, **kwargs)
    _print_json(_receipt(
        operation="dashboard.launch",
        url=args.url,
    ))
    return 0


def _cmd_dashboard_next(args: argparse.Namespace) -> int:
    ui_dir = ROOT / "ui"
    if not (ui_dir / "package.json").exists():
        _print_json(_receipt(
            operation="dashboard.next",
            status="error",
            message="ui/package.json not found.",
        ))
        return 1

    api_cmd = [
        sys.executable, "-m", "uvicorn", "src.api.main:app",
        "--host", args.api_host, "--port", str(args.api_port),
    ]
    if args.reload:
        api_cmd.append("--reload")

    base_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        base_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(api_cmd, cwd=str(ROOT), **base_kwargs)

    ui_cmd = ["npx", "next", "dev", "-p", str(args.ui_port)]
    subprocess.Popen(ui_cmd, cwd=str(ui_dir), **base_kwargs)

    _print_json(_receipt(
        operation="dashboard.next",
        api_url=f"http://{args.api_host}:{args.api_port}",
        ui_url=f"http://localhost:{args.ui_port}",
    ))
    return 0


def _cmd_dashboard_capabilities(args: argparse.Namespace) -> int:
    capabilities = dashboard_capabilities.list_dashboard_capabilities()
    payload = _receipt(
        operation="dashboard.capabilities",
        tabs=list(dashboard_capabilities.DASHBOARD_TABS),
        tab_count=len(dashboard_capabilities.DASHBOARD_TABS),
        capability_count=len(capabilities),
        capability_counts=dashboard_capabilities.tab_capability_counts(),
        capabilities=capabilities,
    )
    _print_json(payload)
    return 0


def _cmd_api_launch(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.api.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.reload:
        cmd.append("--reload")
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(cmd, **kwargs)
    _print_json(_receipt(
        operation="api.launch",
        host=args.host,
        port=args.port,
        url=f"http://{args.host}:{args.port}",
        reload=args.reload,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tj", description="Trading Journal CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    portfolio_parser = sub.add_parser("portfolio", help="Portfolio reports")
    portfolio_sub = portfolio_parser.add_subparsers(dest="portfolio_command", required=True)

    summary = portfolio_sub.add_parser("summary", help="Portfolio KPI summary")
    summary.add_argument("--year", type=int)
    summary.add_argument("--account", dest="account")
    summary.add_argument("--no-live", action="store_true", help="Skip live net worth calculation")
    summary.set_defaults(func=_cmd_portfolio_summary)

    positions = portfolio_sub.add_parser("positions", help="Current positions report")
    positions.add_argument("--account", dest="account")
    positions.add_argument("--asset-class", choices=["equity", "options", "futures", "crypto"])
    positions.add_argument("--sector")
    positions.add_argument("--position-type")
    positions.set_defaults(func=_cmd_portfolio_positions)

    performance = portfolio_sub.add_parser("performance", help="Snapshot performance report")
    performance.add_argument("--account", dest="account")
    performance.set_defaults(func=_cmd_portfolio_performance)

    txns = sub.add_parser("transactions", help="Query transactions")
    txns.add_argument("--category")
    txns.add_argument("--account", dest="account")
    txns.add_argument("--year", type=int)
    txns.add_argument("--search")
    txns.add_argument("--limit", type=int, default=50)
    txns.set_defaults(func=_cmd_transactions)

    ingest = sub.add_parser("ingest", help="Ingest workflows")
    ingest_sub = ingest.add_subparsers(dest="ingest_command", required=True)

    csv = ingest_sub.add_parser("csv", help="Run CSV ingest")
    csv.add_argument("--reset", action="store_true")
    csv.add_argument("--include-mcp-position-csv", action="store_true")
    csv.set_defaults(func=_cmd_ingest_csv)

    snapshot = ingest_sub.add_parser("snapshot", help="Write portfolio snapshot only")
    snapshot.add_argument("--as-of", help="Snapshot date in YYYY-MM-DD format")
    snapshot.set_defaults(func=_cmd_ingest_snapshot)

    account = sub.add_parser("account", help="Account settings")
    account_sub = account.add_subparsers(dest="account_command", required=True)

    cash = account_sub.add_parser("cash", help="Cash balance")
    cash_sub = cash.add_subparsers(dest="cash_command", required=True)
    cash_get = cash_sub.add_parser("get", help="Get cash balance")
    cash_get.add_argument("--account", default="CASH")
    cash_get.set_defaults(func=_cmd_account_cash_get)
    cash_set = cash_sub.add_parser("set", help="Set cash balance")
    cash_set.add_argument("amount", type=float)
    cash_set.add_argument("--account", default="CASH")
    cash_set.set_defaults(func=_cmd_account_cash_set)

    margin = account_sub.add_parser("margin", help="Margin override")
    margin_sub = margin.add_subparsers(dest="margin_command", required=True)
    margin_get = margin_sub.add_parser("get", help="Get margin override")
    margin_get.add_argument("account")
    margin_get.set_defaults(func=_cmd_account_margin_get)
    margin_set = margin_sub.add_parser("set", help="Set margin override")
    margin_set.add_argument("account")
    margin_set.add_argument("amount", type=float)
    margin_set.set_defaults(func=_cmd_account_margin_set)

    health = sub.add_parser("health", help="MCP health check")
    health.set_defaults(func=_cmd_health)

    api = sub.add_parser("api", help="FastAPI backend commands")
    api_sub = api.add_subparsers(dest="api_command", required=True)
    api_launch = api_sub.add_parser("launch", help="Launch read-only FastAPI backend")
    api_launch.add_argument("--host", default="127.0.0.1")
    api_launch.add_argument("--port", type=int, default=8000)
    api_launch.add_argument("--reload", action="store_true")
    api_launch.set_defaults(func=_cmd_api_launch)

    dashboard = sub.add_parser("dashboard", help="Dashboard commands")
    dashboard_sub = dashboard.add_subparsers(dest="dashboard_command", required=True)
    dashboard_launch = dashboard_sub.add_parser("launch", help="Launch Streamlit dashboard")
    dashboard_launch.add_argument("--url", default="http://localhost:8501")
    dashboard_launch.set_defaults(func=_cmd_dashboard_launch)
    dashboard_next = dashboard_sub.add_parser("next", help="Launch Next.js dashboard (API + UI)")
    dashboard_next.add_argument("--api-host", default="127.0.0.1")
    dashboard_next.add_argument("--api-port", type=int, default=8000)
    dashboard_next.add_argument("--ui-port", type=int, default=3000)
    dashboard_next.add_argument("--reload", action="store_true")
    dashboard_next.set_defaults(func=_cmd_dashboard_next)
    dashboard_caps = dashboard_sub.add_parser("capabilities", help="List required dashboard capabilities")
    dashboard_caps.set_defaults(func=_cmd_dashboard_capabilities)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
