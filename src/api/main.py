"""Read-only FastAPI backend for the next-generation Trading Journal UI."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from src.services import dashboard_capabilities
from src.services import portfolio


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
