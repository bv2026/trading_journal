"""MCP server health checks for journal sync dependencies."""
from __future__ import annotations

import asyncio
import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from src.mcp_tools.auth import JsonTokenStorage, has_access_token, token_path


@dataclass(frozen=True)
class HealthTarget:
    broker: str
    accounts: str
    server_name: str
    required_tools: tuple[str, ...] = ()
    remote_url_env: str | None = None
    default_remote_url: str | None = None
    bearer_token_env: str | None = None


HEALTH_TARGETS: tuple[HealthTarget, ...] = (
    HealthTarget("Coinbase", "COINBASE", "coinbase-derivatives-mcp", ("query_current_balances",)),
    HealthTarget("Schwab", "SCHWAB", "schwab-smartspreads-file", ("get_positions", "get_account_summary")),
    HealthTarget("Webull", "WEBULL/WEBULL-CASH/WEBULL-EVENTS/WEBULL-FUT", "webull-openapi", ("get_account_positions",)),
    HealthTarget(
        "Tradier",
        "TRADIER",
        "tradier",
        ("get_positions",),
        "TRADIER_MCP_URL",
        "https://mcp.tradier.com/mcp",
        "TRADIER_MCP_BEARER_TOKEN",
    ),
    HealthTarget(
        "TradeStation",
        "TS",
        "tradestation",
        ("get-positions-details",),
        "TRADESTATION_MCP_URL",
        "https://mcp.tradestation.com/v2/mcp",
        "TRADESTATION_MCP_BEARER_TOKEN",
    ),
    HealthTarget(
        "Robinhood",
        "RH-BV",
        "robinhood",
        ("get_positions",),
        "ROBINHOOD_MCP_URL",
        "https://mcp.trayd.ai/mcp",
        "ROBINHOOD_MCP_BEARER_TOKEN",
    ),
)


def _claude_config_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Claude" / "claude_desktop_config.json"


def _codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _load_claude_servers() -> dict[str, dict[str, Any]]:
    path = _claude_config_path()
    if not path or not path.exists():
        return {}
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return cfg.get("mcpServers") or {}


def _load_codex_servers() -> dict[str, dict[str, Any]]:
    path = _codex_config_path()
    if not path.exists():
        return {}
    try:
        cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return cfg.get("mcp_servers") or {}


def load_mcp_servers() -> dict[str, dict[str, Any]]:
    merged = dict(_load_claude_servers())
    for name, server in _load_codex_servers().items():
        merged.setdefault(name, server)
    return merged


def _find_server(servers: dict[str, dict[str, Any]], target: HealthTarget) -> tuple[str | None, dict[str, Any] | None]:
    if target.server_name in servers:
        return target.server_name, servers[target.server_name]

    needle = target.server_name.lower()
    for name, server in servers.items():
        lowered = name.lower()
        if needle in lowered or lowered in needle:
            return name, server
    return None, None


def _exc_detail(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        details = [_exc_detail(inner) for inner in exc.exceptions[:3]]
        return "; ".join(detail for detail in details if detail) or type(exc).__name__
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _one_line(text: str, max_len: int = 140) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _broker_cli_bearer_token(server_key: str | None) -> str | None:
    if server_key != "robinhood":
        return None
    try:
        from src.cli.robinhood import list_profiles, load_bearer_token  # noqa: PLC0415
    except Exception:
        return None

    for profile in list_profiles():
        try:
            token = load_bearer_token(profile)
        except Exception:
            continue
        if token:
            return token
    return None


async def _check_server(name: str, server: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    command = server.get("command")
    if not command:
        return {"status": "FAIL", "tools": 0, "detail": "missing command"}

    env = os.environ.copy()
    env.update(server.get("env") or {})
    env.setdefault("FASTMCP_LOG_LEVEL", "ERROR")
    env.setdefault("LOG_LEVEL", "ERROR")
    env.setdefault("PYTHONWARNINGS", "ignore")
    params = StdioServerParameters(
        command=str(command),
        args=[str(arg) for arg in (server.get("args") or [])],
        env=env,
        cwd=server.get("cwd"),
    )

    async def run() -> dict[str, Any]:
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    tool_names = sorted(tool.name for tool in tools_result.tools)
                    return {
                        "status": "OK",
                        "tools": len(tool_names),
                        "tool_names": tool_names,
                        "detail": f"{len(tool_names)} tools",
                    }

    try:
        return await asyncio.wait_for(run(), timeout=timeout_seconds)
    except TimeoutError:
        return {"status": "FAIL", "tools": 0, "detail": f"timeout after {timeout_seconds:g}s"}
    except Exception as exc:  # noqa: BLE001 - health checks report, not raise
        return {"status": "FAIL", "tools": 0, "detail": _exc_detail(exc)}


async def _check_remote_server(
    url: str,
    timeout_seconds: float,
    bearer_token: str | None = None,
    server_key: str | None = None,
) -> dict[str, Any]:
    safe_url = url.split("?", 1)[0]
    token = bearer_token or _broker_cli_bearer_token(server_key)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    auth = None
    if not headers and server_key and has_access_token(server_key):
        from mcp.client.auth import OAuthClientProvider  # noqa: PLC0415
        from mcp.shared.auth import OAuthClientMetadata  # noqa: PLC0415

        auth = OAuthClientProvider(
            server_url=url,
            client_metadata=OAuthClientMetadata(
                client_name="Trading Journal CLI",
                redirect_uris=["http://127.0.0.1/callback"],
                token_endpoint_auth_method="none",
            ),
            storage=JsonTokenStorage(token_path(server_key)),
            timeout=timeout_seconds,
        )

    async def run() -> dict[str, Any]:
        async with streamablehttp_client(
            url,
            headers=headers,
            auth=auth,
            timeout=timeout_seconds,
            sse_read_timeout=timeout_seconds,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tool_names = sorted(tool.name for tool in tools_result.tools)
                return {
                    "status": "OK",
                    "tools": len(tool_names),
                    "tool_names": tool_names,
                    "detail": f"{len(tool_names)} tools at {safe_url}",
                }

    try:
        return await asyncio.wait_for(run(), timeout=timeout_seconds + 2)
    except TimeoutError:
        return {"status": "FAIL", "tools": 0, "detail": f"remote timeout after {timeout_seconds:g}s at {safe_url}"}
    except Exception as exc:  # noqa: BLE001 - health checks report, not raise
        return {"status": "FAIL", "tools": 0, "detail": f"{_one_line(_exc_detail(exc))} at {safe_url}"}


async def _check_all(timeout_seconds: float) -> list[dict[str, Any]]:
    servers = load_mcp_servers()
    rows: list[dict[str, Any]] = []
    tasks: list[asyncio.Task[dict[str, Any]]] = []
    task_targets: list[tuple[HealthTarget, str]] = []

    for target in HEALTH_TARGETS:
        server_name, server = _find_server(servers, target)
        if not server_name or not server:
            remote_url = os.environ.get(target.remote_url_env or "") or target.default_remote_url
            if remote_url:
                bearer_token = os.environ.get(target.bearer_token_env or "")
                task_targets.append((target, f"{target.server_name} remote"))
                tasks.append(asyncio.create_task(
                    _check_remote_server(remote_url, timeout_seconds, bearer_token, target.server_name)
                ))
                continue
            rows.append({
                "Broker": target.broker,
                "Accounts": target.accounts,
                "MCP Server": target.server_name,
                "Status": "MISSING",
                "Tools": 0,
                "Detail": "not configured locally and no remote URL configured",
            })
            continue
        task_targets.append((target, server_name))
        tasks.append(asyncio.create_task(_check_server(server_name, server, timeout_seconds)))

    if tasks:
        results = await asyncio.gather(*tasks)
        for (target, server_name), result in zip(task_targets, results, strict=True):
            tool_names = set(result.pop("tool_names", []))
            missing_tools = [tool for tool in target.required_tools if tool not in tool_names]
            status = result["status"]
            detail = result["detail"]
            if status == "OK" and missing_tools:
                status = "WARN"
                detail = f"missing expected tools: {', '.join(missing_tools)}"
            rows.append({
                "Broker": target.broker,
                "Accounts": target.accounts,
                "MCP Server": server_name,
                "Status": status,
                "Tools": result["tools"],
                "Detail": _one_line(detail),
            })

    order = {target.broker: idx for idx, target in enumerate(HEALTH_TARGETS)}
    return sorted(rows, key=lambda row: order.get(row["Broker"], 999))


def check_mcp_health(timeout_seconds: float = 30.0) -> list[dict[str, Any]]:
    return asyncio.run(_check_all(timeout_seconds))
