"""Authorize remote MCP servers and save OAuth tokens for CLI health checks."""
from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_project_root = str(Path(__file__).resolve().parents[1])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from mcp.client.auth import OAuthClientProvider
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientMetadata

from src.mcp_tools.auth import JsonTokenStorage, token_path
from src.mcp_tools.health import HEALTH_TARGETS


def _remote_targets() -> dict[str, tuple[str, str]]:
    targets: dict[str, tuple[str, str]] = {}
    for target in HEALTH_TARGETS:
        if target.default_remote_url:
            targets[target.broker.lower()] = (target.server_name, target.default_remote_url)
            targets[target.server_name.lower()] = (target.server_name, target.default_remote_url)
    return targets


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_callback(port: int) -> tuple[str, str | None]:
    server_holder: dict[str, asyncio.AbstractServer] = {}
    future: asyncio.Future[tuple[str, str | None]] = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        path = line.decode("utf-8", "replace").split(" ")[1]
        params = parse_qs(urlparse(path).query)
        code = params.get("code", [""])[0]
        state = params.get("state", [None])[0]
        body = "Authorization complete. You can close this tab."
        if code and not future.done():
            future.set_result((code, state))
        elif not future.done():
            future.set_exception(RuntimeError(f"OAuth callback did not include a code: {path}"))
            body = "Authorization failed. Return to the terminal for details."

        writer.write(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body.encode('utf-8'))}\r\n"
            "Connection: close\r\n\r\n"
            f"{body}".encode("utf-8")
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        server_holder["server"].close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    server_holder["server"] = server
    async with server:
        return await future


async def _manual_callback() -> tuple[str, str | None]:
    print()
    pasted = input("Paste the final callback URL, or just the authorization code: ").strip()
    if not pasted:
        raise RuntimeError("No callback URL/code was pasted.")
    if pasted.startswith("http://") or pasted.startswith("https://"):
        params = parse_qs(urlparse(pasted).query)
        code = params.get("code", [""])[0]
        state = params.get("state", [None])[0]
        if not code:
            raise RuntimeError("The pasted callback URL did not include a code parameter.")
        return code, state
    return pasted, None


async def _authorize(server_key: str, url: str, timeout: float, manual: bool, open_browser: bool) -> None:
    port = _free_port()
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    storage = JsonTokenStorage(token_path(server_key))
    metadata = OAuthClientMetadata(
        client_name="Trading Journal CLI",
        redirect_uris=[redirect_uri],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )

    async def redirect_handler(auth_url: str) -> None:
        print("\nOpen this URL to authorize:")
        print(auth_url, flush=True)
        if open_browser:
            webbrowser.open(auth_url)

    auth = OAuthClientProvider(
        server_url=url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=_manual_callback if manual else lambda: _wait_for_callback(port),
        timeout=timeout,
    )

    async with streamablehttp_client(url, auth=auth, timeout=timeout, sse_read_timeout=timeout) as (read, write, _sid):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"Authorized {server_key}: {len(tools.tools)} tools available.")
            print(f"Saved token: {token_path(server_key)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize a remote MCP server for trading-journal CLI health.")
    parser.add_argument("broker", choices=sorted(_remote_targets()), help="Remote MCP broker/server to authorize.")
    parser.add_argument("--url", help="Override the remote MCP URL.")
    parser.add_argument("--timeout", type=float, default=300.0, help="OAuth timeout in seconds.")
    parser.add_argument("--manual", action="store_true", help="Paste the final callback URL/code instead of using localhost.")
    parser.add_argument("--no-browser", action="store_true", help="Print the auth URL without opening a browser.")
    parser.add_argument("--reset", action="store_true", help="Delete saved auth for this broker before starting.")
    args = parser.parse_args()

    server_key, default_url = _remote_targets()[args.broker.lower()]
    if args.reset:
        path = token_path(server_key)
        if path.exists():
            path.unlink()
            print(f"Deleted saved auth: {path}")
    asyncio.run(_authorize(server_key, args.url or default_url, args.timeout, args.manual, not args.no_browser))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
