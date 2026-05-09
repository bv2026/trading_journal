"""Read-only FastAPI backend for the next-generation Trading Journal UI."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from src import db
from src.positions import (
    load_all_positions,
    load_crypto_from_db,
    load_futures_from_db,
    load_options_from_db,
    load_positions_from_db,
)
from src.services import dashboard_performance
from src.services import dashboard_portfolio
from src.services import dashboard_transactions
from src.services import dashboard_capabilities
from src.services import portfolio
from src.mcp_tools.health import check_mcp_health


def create_app() -> FastAPI:
    app = FastAPI(
        title="Trading Journal API",
        version="0.1.0",
        description="Read-only API over the Trading Journal service layer.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    register_routes(app)
    return app


def receipt(
    *,
    operation: str,
    status: str = "ok",
    data: Any = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "operation": operation,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings or [],
        "errors": errors or [],
    }
    if data is not None:
        payload["data"] = data
    payload.update(fields)
    return payload


def _clean(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, float):
        if math.isinf(value) or math.isnan(value):
            return None
        return round(value, 4)
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clean = frame.where(pd.notna(frame), None)
    return [
        {str(key): _clean(value) for key, value in row.items()}
        for row in clean.to_dict(orient="records")
    ]


def _all_accounts(transactions: pd.DataFrame) -> list[str]:
    if transactions.empty or "account_id" not in transactions.columns:
        return []
    return sorted(str(account) for account in transactions["account_id"].dropna().unique())


def register_routes(app: FastAPI) -> None:
    @app.get("/")
    def root() -> dict[str, Any]:
        return receipt(
            operation="api.root",
            data={
                "name": "Trading Journal API",
                "version": app.version,
                "docs": "/docs",
                "health": "/health",
            },
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return receipt(
            operation="api.health",
            data={"service": "trading-journal-api", "ready": True},
        )

    @app.get("/dashboard/capabilities")
    def dashboard_capability_contract() -> dict[str, Any]:
        capabilities = dashboard_capabilities.list_dashboard_capabilities()
        return receipt(
            operation="dashboard.capabilities",
            data={
                "tabs": list(dashboard_capabilities.DASHBOARD_TABS),
                "tab_count": len(dashboard_capabilities.DASHBOARD_TABS),
                "capability_count": len(capabilities),
                "capability_counts": dashboard_capabilities.tab_capability_counts(),
                "capabilities": capabilities,
            },
        )

    @app.get("/operations/status")
    def operations_status() -> dict[str, Any]:
        ops = db.load_account_operations_status()
        if ops.empty:
            return receipt(operation="operations.status", data={"accounts": [], "health": []})

        def _source_signal(raw: Any) -> str:
            if raw in (None, ""):
                return "UNKNOWN"
            parts = sorted({str(p).strip().upper() for p in str(raw).split(",") if str(p).strip()})
            if not parts:
                return "UNKNOWN"
            if len(parts) == 1:
                return parts[0]
            return "MIXED"

        for col in [
            "eq_last_ingested_at", "opt_last_ingested_at", "fut_last_ingested_at",
            "cry_last_ingested_at", "txn_last_created_at",
        ]:
            ops[col] = pd.to_datetime(ops[col], errors="coerce", utc=True)
        ops["last_synced_ts"] = ops[
            ["eq_last_ingested_at", "opt_last_ingested_at", "fut_last_ingested_at", "cry_last_ingested_at", "txn_last_created_at"]
        ].max(axis=1)
        with db.get_conn() as conn:
            cash_upd = conn.execute("SELECT updated_at FROM cash_accounts WHERE account_id='CASH'").fetchone()
            fid_margin_upd = conn.execute("SELECT updated_at FROM margin_overrides WHERE account_id='FIDELITY'").fetchone()
        cash_ts = pd.to_datetime(cash_upd[0], errors="coerce", utc=True) if cash_upd and cash_upd[0] else pd.NaT
        fid_margin_ts = pd.to_datetime(fid_margin_upd[0], errors="coerce", utc=True) if fid_margin_upd and fid_margin_upd[0] else pd.NaT
        ops.loc[ops["account_id"] == "CASH", "last_synced_ts"] = cash_ts
        fidelity_mask = ops["account_id"] == "FIDELITY"
        if fidelity_mask.any() and pd.notna(fid_margin_ts):
            existing = ops.loc[fidelity_mask, "last_synced_ts"]
            fill = pd.Series([fid_margin_ts] * len(existing), index=existing.index)
            ops.loc[fidelity_mask, "last_synced_ts"] = existing.combine_first(fill)
        now_utc = pd.Timestamp.now(tz="UTC")
        ops["age_hours"] = (now_utc - ops["last_synced_ts"]).dt.total_seconds() / 3600.0
        ops["source_signal"] = ops["raw_sources"].map(_source_signal)
        ops["status_label"] = ops.apply(
            lambda r: (
                "STALE" if int(r.get("active") or 0) == 1 and (pd.isna(r["last_synced_ts"]) or (pd.notna(r["age_hours"]) and r["age_hours"] > 36))
                else "OK"
            ),
            axis=1,
        )
        account_rows = ops[[
            "account_id", "broker", "account_type", "active", "source_signal",
            "last_synced_ts", "last_snapshot_date", "age_hours", "status_label",
            "eq_last_ingested_at", "opt_last_ingested_at", "fut_last_ingested_at",
            "cry_last_ingested_at", "txn_last_created_at",
        ]]
        activity = Path(__file__).resolve().parents[2] / "activity"
        fid_income = activity / "fidelity_Investment_income_balance.csv"
        fid_positions = activity / "positions-fidelity.csv"
        fidelity_upload_ts = None
        if fid_income.exists() and fid_positions.exists():
            fidelity_upload_ts = max(
                datetime.fromtimestamp(fid_income.stat().st_mtime, tz=timezone.utc),
                datetime.fromtimestamp(fid_positions.stat().st_mtime, tz=timezone.utc),
            ).isoformat()
        elif fid_income.exists():
            fidelity_upload_ts = datetime.fromtimestamp(fid_income.stat().st_mtime, tz=timezone.utc).isoformat()
        elif fid_positions.exists():
            fidelity_upload_ts = datetime.fromtimestamp(fid_positions.stat().st_mtime, tz=timezone.utc).isoformat()

        return receipt(
            operation="operations.status",
            data={
                "accounts": _records(account_rows),
                "health": check_mcp_health(),
                "csv_ingest_state": _records(db.load_csv_ingest_state()),
                "csv_uploads": {
                    "fidelity_income_csv": datetime.fromtimestamp(fid_income.stat().st_mtime, tz=timezone.utc).isoformat() if fid_income.exists() else None,
                    "fidelity_positions_csv": datetime.fromtimestamp(fid_positions.stat().st_mtime, tz=timezone.utc).isoformat() if fid_positions.exists() else None,
                    "fidelity_last_upload_ts": fidelity_upload_ts,
                },
            },
        )

    @app.get("/dashboard/portfolio")
    def dashboard_portfolio_payload() -> dict[str, Any]:
        transactions = portfolio.load_transactions_filtered()
        all_positions = load_all_positions()
        equity_positions = load_positions_from_db()
        options = load_options_from_db()
        futures = load_futures_from_db()
        crypto = load_crypto_from_db()
        account_balances = db.load_account_balances()
        cash_balance = db.get_cash_balance()
        accounts = _all_accounts(transactions)

        equity, margin = dashboard_portfolio.split_equity_margin(equity_positions)
        sector_labels = dashboard_portfolio.collapsed_sector_labels(equity) if not equity.empty else None
        kpis = (
            dashboard_portfolio.portfolio_kpi_row(portfolio.compute_metrics(transactions))
            if not transactions.empty
            else {}
        )
        account_summary = dashboard_portfolio.account_summary(
            pos=equity,
            margin_df=margin,
            opts_all=options,
            futs_all=futures,
            cry_all=crypto,
            account_balances=account_balances,
            transactions=transactions,
            all_accounts=accounts,
            selected_accounts=accounts,
            cash_balance=cash_balance,
        )
        asset_class = dashboard_portfolio.asset_class_breakdown(
            pos=equity,
            opts_all=options,
            futs_all=futures,
            cry_all=crypto,
            cash_balance=cash_balance,
            crypto_accounts=set(db.get_accounts_by_type("crypto")),
        )
        sector_allocation = dashboard_portfolio.sector_allocation(equity, sector_labels) if not equity.empty else pd.DataFrame()
        sector_summary = dashboard_portfolio.sector_summary(
            pos=equity,
            transactions=transactions,
            sector_labels=sector_labels,
        ) if not equity.empty else pd.DataFrame()
        futures_by_commodity = dashboard_portfolio.futures_by_commodity(futures) if not futures.empty else pd.DataFrame()

        return receipt(
            operation="dashboard.portfolio",
            data={
                "net_worth": dashboard_portfolio.net_worth_banner(
                    account_balances=account_balances,
                    all_positions=all_positions,
                    cash_balance=cash_balance,
                ),
                "transaction_kpis": {key: _clean(value) for key, value in kpis.items()},
                "account_summary": _records(account_summary),
                "asset_class_breakdown": _records(asset_class),
                "futures_by_commodity": _records(futures_by_commodity),
                "sector_allocation": _records(sector_allocation),
                "positions_by_account": _records(equity),
                "sector_summary": _records(sector_summary),
            },
        )

    @app.get("/dashboard/performance")
    def dashboard_performance_payload() -> dict[str, Any]:
        result = dashboard_performance.performance_tables(
            all_positions=load_all_positions(),
            snapshot_periods=db.load_snapshot_periods(),
            cash_balance=db.get_cash_balance(),
        )
        return receipt(
            operation="dashboard.performance",
            data={
                "summary": _records(result["summary"]),
                "returns": _records(result["returns"]),
                "has_snapshots": result["has_snapshots"],
            },
        )

    @app.get("/dashboard/yearly-summary")
    def dashboard_yearly_summary_payload() -> dict[str, Any]:
        transactions = portfolio.load_transactions_filtered()
        summary = dashboard_transactions.yearly_summary_table(transactions)
        income = dashboard_transactions.income_breakdown_by_type(transactions)
        return receipt(
            operation="dashboard.yearly_summary",
            data={
                "summary": _records(summary),
                "income_breakdown": _records(income),
            },
        )

    @app.get("/dashboard/by-account")
    def dashboard_by_account_payload() -> dict[str, Any]:
        transactions = portfolio.load_transactions_filtered()
        accounts = _all_accounts(transactions)
        pivots = dashboard_transactions.by_account_pivots(
            transactions,
            all_accounts=accounts,
            selected_accounts=accounts,
        )
        crypto_flow = dashboard_transactions.crypto_flow_summary(transactions)
        return receipt(
            operation="dashboard.by_account",
            data={
                "net_cash_flow": _records(pivots["net_cash_flow"]),
                "div_rewards": _records(pivots["div_rewards"]),
                "margin_fees": _records(pivots["margin_fees"]),
                "crypto_flow": {
                    "has_crypto_flow": crypto_flow["has_crypto_flow"],
                    "total_in": _clean(crypto_flow["total_in"]),
                    "total_out": _clean(crypto_flow["total_out"]),
                    "net": _clean(crypto_flow["net"]),
                    "inflows": _records(crypto_flow["inflows"]),
                    "outflows": _records(crypto_flow["outflows"]),
                },
            },
        )

    @app.get("/portfolio/summary")
    def portfolio_summary(
        year: int | None = None,
        account_id: str | None = Query(default=None, alias="account"),
        include_live_net_worth: bool = True,
    ) -> dict[str, Any]:
        result = portfolio.get_portfolio_summary(
            year=year,
            account_id=account_id,
            include_live_net_worth=include_live_net_worth,
        )
        if result is None:
            return receipt(
                operation="portfolio.summary",
                status="empty",
                data=None,
                warnings=["No matching transactions found."],
            )
        return receipt(operation="portfolio.summary", data=result)

    @app.get("/portfolio/yearly-summary")
    def portfolio_yearly_summary(
        account_id: str | None = Query(default=None, alias="account"),
    ) -> dict[str, Any]:
        result = portfolio.get_yearly_summary(account_id=account_id)
        if result is None:
            return receipt(
                operation="portfolio.yearly_summary",
                status="empty",
                data=[],
                warnings=["No matching transactions found."],
                count=0,
            )
        return receipt(operation="portfolio.yearly_summary", data=result, count=len(result))

    @app.get("/portfolio/account-summary")
    def portfolio_account_summary(year: int | None = None) -> dict[str, Any]:
        result = portfolio.get_account_summary(year=year)
        if result is None:
            return receipt(
                operation="portfolio.account_summary",
                status="empty",
                data=[],
                warnings=["No matching transactions found."],
                count=0,
            )
        return receipt(operation="portfolio.account_summary", data=result, count=len(result))

    @app.get("/portfolio/positions")
    def portfolio_positions(
        account_id: str | None = Query(default=None, alias="account"),
        asset_class: str | None = None,
        sector: str | None = None,
        position_type: str | None = None,
    ) -> dict[str, Any]:
        result = portfolio.get_positions_report(
            account_id=account_id,
            asset_class=asset_class,
            sector=sector,
            position_type=position_type,
        )
        if result is None:
            return receipt(
                operation="portfolio.positions",
                status="empty",
                data=None,
                warnings=["No positions found."],
            )
        return receipt(operation="portfolio.positions", data=result)

    @app.get("/portfolio/performance")
    def portfolio_performance(
        account_id: str | None = Query(default=None, alias="account"),
    ) -> dict[str, Any]:
        result = portfolio.get_performance_report(account_id=account_id)
        if result is None:
            return receipt(
                operation="portfolio.performance",
                status="empty",
                data=[],
                warnings=["No snapshot data found."],
            )
        return receipt(operation="portfolio.performance", data=result, count=len(result))

    @app.get("/transactions")
    def transactions(
        category: str | None = None,
        account_id: str | None = Query(default=None, alias="account"),
        year: int | None = None,
        search: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        result = portfolio.query_transactions(
            category=category,
            account_id=account_id,
            year=year,
            search=search,
            limit=limit,
        )
        if result is None:
            return receipt(
                operation="transactions.query",
                status="empty",
                data={"count": 0, "transactions": []},
                warnings=["No transactions found."],
            )
        return receipt(operation="transactions.query", data=result, count=result["count"])


app = create_app()
