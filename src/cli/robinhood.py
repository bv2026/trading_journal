"""Robinhood broker module for the Trading Journal CLI.

Standalone usage:
    python src/cli/robinhood.py --login --profile bv
    python src/cli/robinhood.py --profile bv
    python src/cli/robinhood.py

Interactive (via menu.py):
    python src/cli/menu.py  →  Robinhood  →  profile  →  account  →  action

Token storage: ~/.trayd/<profile>.json
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_project_root = str(_Path(__file__).resolve().parents[2])
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
import base64
import getpass
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode, urlparse, parse_qs
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "https://mcp.trayd.ai/mcp"
OAUTH_METADATA_URL = "https://mcp.trayd.ai/.well-known/oauth-authorization-server"
TOKEN_DIR = Path.home() / ".trayd"
CLAUDE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
TRAYD_KEY = "trayd|4bc904c3febee777"
CALLBACK_PORT = 8919
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}/callback"


def _token_path(profile: str) -> Path:
    return TOKEN_DIR / f"{profile}.json"


def list_profiles() -> list[str]:
    if not TOKEN_DIR.exists():
        return []
    return sorted(p.stem for p in TOKEN_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def _load_oauth_metadata() -> dict[str, Any]:
    resp = httpx.get(OAUTH_METADATA_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _get_or_register_client(profile: str, metadata: dict[str, Any]) -> tuple[str, str]:
    path = _token_path(profile)
    if path.exists():
        data = json.loads(path.read_text("utf-8"))
        cid = data.get("client_id")
        if cid:
            return cid, data.get("client_secret", "")

    if CLAUDE_CREDENTIALS.exists():
        creds = json.loads(CLAUDE_CREDENTIALS.read_text("utf-8"))
        entry = (creds.get("mcpOAuth") or {}).get(TRAYD_KEY, {})
        cid = entry.get("clientId")
        if cid:
            return cid, entry.get("clientSecret", "")

    reg_url = metadata.get("registration_endpoint")
    if not reg_url:
        print("ERROR: no registration endpoint and no stored client_id", file=sys.stderr)
        sys.exit(1)

    resp = httpx.post(reg_url, json={
        "client_name": f"Trading Journal CLI ({profile})",
        "redirect_uris": [REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }, timeout=10)
    resp.raise_for_status()
    reg = resp.json()
    return reg["client_id"], reg.get("client_secret", "")


_auth_code: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        global _auth_code
        qs = parse_qs(urlparse(self.path).query)
        code = (qs.get("code") or [None])[0]
        if code:
            _auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Login successful! You can close this tab.</h2>")
        else:
            err = (qs.get("error") or ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h2>Error: {err}</h2>".encode())

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


def do_login(profile: str) -> None:
    global _auth_code
    _auth_code = None

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)

    metadata = _load_oauth_metadata()
    client_id, client_secret = _get_or_register_client(profile, metadata)

    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)

    auth_url = metadata["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "mcp:tools",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })

    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()

    import webbrowser
    print(f"Opening browser for trayd login (profile: {profile})...")
    print(f"If it doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    thread.join(timeout=120)
    server.server_close()

    if not _auth_code:
        print("ERROR: did not receive authorization code (timeout or denied)", file=sys.stderr)
        sys.exit(1)

    resp = httpx.post(metadata["token_endpoint"], data={
        "grant_type": "authorization_code",
        "code": _auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": code_verifier,
    }, timeout=10)
    resp.raise_for_status()
    tokens = resp.json()

    expires_in = tokens.get("expires_in", 86400)
    save_data = {
        "profile": profile,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": time.time() + expires_in,
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": tokens.get("scope", "mcp:tools"),
    }
    _token_path(profile).write_text(json.dumps(save_data, indent=2), encoding="utf-8")
    print(f"Token saved to {_token_path(profile)} (expires in {expires_in // 3600}h)")


def _try_refresh(profile: str) -> str | None:
    path = _token_path(profile)
    if not path.exists():
        return None
    data = json.loads(path.read_text("utf-8"))
    rt = data.get("refresh_token")
    cid = data.get("client_id")
    if not rt or not cid:
        return None

    try:
        metadata = _load_oauth_metadata()
        resp = httpx.post(metadata["token_endpoint"], data={
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": cid,
            "client_secret": data.get("client_secret", ""),
        }, timeout=10)
        resp.raise_for_status()
        tokens = resp.json()
        expires_in = tokens.get("expires_in", 86400)
        data["access_token"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            data["refresh_token"] = tokens["refresh_token"]
        data["expires_at"] = time.time() + expires_in
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"  [{profile}] Token refreshed (expires in {expires_in // 3600}h)")
        return tokens["access_token"]
    except Exception as exc:
        print(f"  [{profile}] Token refresh failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Token loading
# ---------------------------------------------------------------------------

def load_bearer_token(profile: str, explicit_token: str | None = None) -> str | None:
    """Return bearer token or None if unavailable."""
    if explicit_token:
        return explicit_token

    env_token = os.environ.get("TRAYD_BEARER_TOKEN")
    if env_token:
        return env_token

    path = _token_path(profile)
    if path.exists():
        data = json.loads(path.read_text("utf-8"))
        token = data.get("access_token", "")
        expires_at = data.get("expires_at", 0)
        if token and expires_at > time.time():
            return token
        if token:
            refreshed = _try_refresh(profile)
            if refreshed:
                return refreshed

    if CLAUDE_CREDENTIALS.exists():
        creds = json.loads(CLAUDE_CREDENTIALS.read_text("utf-8"))
        oauth = (creds.get("mcpOAuth") or {}).get(TRAYD_KEY)
        if oauth:
            token = oauth.get("accessToken", "")
            expires_at = oauth.get("expiresAt", 0)
            if token and (not expires_at or expires_at > time.time() * 1000):
                return token

    return None


# ---------------------------------------------------------------------------
# MCP session helpers
# ---------------------------------------------------------------------------

async def _call_tool(session: ClientSession, tool_name: str, args: dict | None = None) -> dict:
    result = await session.call_tool(tool_name, args or {})
    for block in result.content:
        if hasattr(block, "text"):
            return json.loads(block.text)
    return {}


async def _open_session(token: str):
    """Return an async context manager for the MCP session."""
    headers = {"Authorization": f"Bearer {token}"}
    return streamablehttp_client(
        MCP_URL, headers=headers, timeout=30, sse_read_timeout=30,
    )


async def fetch_accounts(token: str) -> list[dict]:
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"},
        timeout=30, sse_read_timeout=30,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            data = await _call_tool(session, "list_accounts")
            return data.get("accounts", [])


async def check_login_status(token: str) -> dict:
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"},
        timeout=30, sse_read_timeout=30,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await _call_tool(session, "check_login_status")


async def logout_robinhood(token: str) -> dict:
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"},
        timeout=30, sse_read_timeout=30,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await _call_tool(session, "logout")


async def start_robinhood_link(token: str, email: str, password: str) -> dict:
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"},
        timeout=30, sse_read_timeout=30,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await _call_tool(session, "link_robinhood", {
                "email": email,
                "password": password,
            })


async def complete_robinhood_link(token: str, email: str, password: str) -> dict:
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"},
        timeout=30, sse_read_timeout=30,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await _call_tool(session, "complete_robinhood_link", {
                "email": email,
                "password": password,
            })


async def submit_sms_code(token: str, email: str, password: str, sms_code: str) -> dict:
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"},
        timeout=30, sse_read_timeout=30,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await _call_tool(session, "submit_sms_code", {
                "email": email,
                "password": password,
                "sms_code": sms_code,
            })


async def fetch_portfolio(token: str, account_number: str) -> dict:
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"},
        timeout=30, sse_read_timeout=30,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await _call_tool(session, "get_portfolio", {"account_number": account_number})


async def fetch_positions(token: str, account_number: str) -> dict:
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"},
        timeout=30, sse_read_timeout=30,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await _call_tool(session, "get_positions", {"account_number": account_number})


async def fetch_all_for_account(token: str, account_number: str) -> dict:
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"},
        timeout=30, sse_read_timeout=30,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            portfolio = await _call_tool(session, "get_portfolio", {"account_number": account_number})
            positions = await _call_tool(session, "get_positions", {"account_number": account_number})
            return {"portfolio": portfolio, "positions": positions}


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def display_portfolio(data: dict, header: str = "") -> None:
    equity = float(data.get("equity", 0))
    cash = float(data.get("cash", 0))
    num = data.get("num_positions", 0)
    margin = abs(cash) if cash < 0 else 0

    if header:
        print(f"\n{'='*60}")
        print(f"  {header}  |  {num} positions")
        print(f"{'='*60}")
    print(f"  Equity:         ${equity:>12,.2f}")
    if margin:
        print(f"  Margin Used:    ${margin:>12,.2f}")
        print(f"  Net Equity:     ${equity - margin:>12,.2f}")
    else:
        print(f"  Cash:           ${cash:>12,.2f}")


def display_positions(data: dict) -> None:
    positions = data.get("positions", [])
    if not positions:
        print("\n  No positions found.")
        return

    positions.sort(key=lambda p: float(p.get("market_value", 0)), reverse=True)

    total_mv = sum(float(p.get("market_value", 0)) for p in positions)
    total_gl = sum(float(p.get("gain_loss", 0)) for p in positions)

    print(f"\n  {'Symbol':<8} {'Qty':>10} {'Avg Cost':>10} {'Price':>10} "
          f"{'Mkt Value':>12} {'P/L':>12} {'P/L%':>8}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*12} {'-'*12} {'-'*8}")

    for p in positions:
        sym = p.get("symbol", "???")
        qty = float(p.get("quantity", 0))
        avg = float(p.get("avg_cost", 0))
        price = float(p.get("current_price", 0))
        mv = float(p.get("market_value", 0))
        gl = float(p.get("gain_loss", 0))
        pct = (gl / (avg * qty) * 100) if avg * qty else 0
        sign = "+" if gl >= 0 else ""

        print(f"  {sym:<8} {qty:>10.2f} {avg:>10.2f} {price:>10.2f} "
              f"${mv:>11,.2f} {sign}${gl:>10,.2f} {sign}{pct:>6.1f}%")

    print(f"  {'-'*8} {' '*10} {' '*10} {' '*10} {'-'*12} {'-'*12}")
    sign = "+" if total_gl >= 0 else ""
    print(f"  {'TOTAL':<8} {' '*10} {' '*10} {' '*10} "
          f"${total_mv:>11,.2f} {sign}${total_gl:>10,.2f}")


# ---------------------------------------------------------------------------
# Interactive menu (called from menu.py)
# ---------------------------------------------------------------------------

def broker_menu() -> None:
    from src.cli.menu import print_header, prompt_choice

    while True:
        profiles = list_profiles()
        print_header("Robinhood")

        options = [f"Profile: {p}" for p in profiles]
        options.append("+ Login new profile")
        options.append("Link Robinhood to profile")
        options.append("Robinhood link status")
        options.append("Logout Robinhood link")

        choice = prompt_choice(options, title="Select")
        if choice is None:
            return

        if choice == len(profiles):
            # New login
            try:
                name = input("  Profile name (e.g. bv, kd): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                continue
            if not name:
                continue
            do_login(name)
            continue

        if choice >= len(profiles):
            try:
                name = input("  Profile name: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                continue
            if not name:
                continue
            action = choice - len(profiles)
            if action == 1:
                link_robinhood_interactive(name)
            elif action == 2:
                print_login_status(name)
            elif action == 3:
                logout_robinhood_interactive(name)
            continue

        profile = profiles[choice]
        _profile_menu(profile)


def _profile_menu(profile: str) -> None:
    from src.cli.menu import print_header, prompt_choice

    token = load_bearer_token(profile)
    if not token:
        print(f"\n  No valid token for '{profile}'. Login required.")
        try:
            ans = input("  Login now? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if ans in ("y", "yes"):
            do_login(profile)
            token = load_bearer_token(profile)
        if not token:
            return

    print(f"\n  Loading accounts for [{profile}]...")
    try:
        accounts = asyncio.run(fetch_accounts(token))
    except Exception as exc:
        if "401" in str(exc):
            print(f"  ERROR: Token expired. Run login again.")
            return
        print(f"  ERROR: {exc}")
        return

    if not accounts:
        print("  No accounts found.")
        return

    if len(accounts) == 1:
        _account_menu(profile, token, accounts[0])
        return

    while True:
        print_header(f"Robinhood — {profile.upper()}")
        options = []
        for acct in accounts:
            nickname = acct.get("nickname") or acct.get("type") or ""
            bp = float(acct.get("buying_power", 0))
            default = " *" if acct.get("is_default") else ""
            options.append(f"{acct['account_number']}  {nickname:<20} BP: ${bp:>12,.2f}{default}")

        choice = prompt_choice(options, title="Account")
        if choice is None:
            return

        _account_menu(profile, token, accounts[choice])


def _account_menu(profile: str, token: str, account: dict) -> None:
    from src.cli.menu import print_header, prompt_choice

    acct_num = account["account_number"]
    nickname = account.get("nickname") or account.get("type") or acct_num
    header = f"Robinhood — {profile.upper()} — {nickname} ({acct_num})"

    while True:
        print_header(header)

        options = [
            "Portfolio + Positions (full view)",
            "Portfolio summary",
            "Positions",
        ]
        choice = prompt_choice(options, title="Action")
        if choice is None:
            return

        print(f"\n  Fetching...")
        try:
            if choice == 0:
                data = asyncio.run(fetch_all_for_account(token, acct_num))
                display_portfolio(data["portfolio"], header=header)
                display_positions(data["positions"])
            elif choice == 1:
                data = asyncio.run(fetch_portfolio(token, acct_num))
                display_portfolio(data, header=header)
            elif choice == 2:
                data = asyncio.run(fetch_positions(token, acct_num))
                display_positions(data)
        except Exception as exc:
            if "401" in str(exc):
                print(f"  ERROR: Token expired. Re-login with --login --profile {profile}")
                return
            print(f"  ERROR: {exc}")

        print()
        try:
            input("  Press Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            print()
            return


def link_robinhood_interactive(profile: str, explicit_token: str | None = None) -> None:
    """Prompt for Robinhood credentials and run Trayd's link flow.

    Credentials are held only in memory long enough to call Trayd. They are not
    written to ~/.trayd or any journal file.
    """
    token = load_bearer_token(profile, explicit_token)
    if not token:
        print(f"ERROR: no valid Trayd OAuth token for '{profile}'", file=sys.stderr)
        print(f"Fix: python src/cli/robinhood.py --login --profile {profile}", file=sys.stderr)
        sys.exit(1)

    email = input("Robinhood email: ").strip()
    password = getpass.getpass("Robinhood password: ")
    if not email or not password:
        print("ERROR: Robinhood email and password are required", file=sys.stderr)
        sys.exit(1)

    print("\nStarting Robinhood link through Trayd...")
    result = asyncio.run(start_robinhood_link(token, email, password))
    print(json.dumps(result, indent=2))
    print(
        "\nPlease check your phone and approve the Robinhood notification. "
        "If it says the login is from Ashburn, VA, that is expected for Trayd's AWS server."
    )

    while True:
        input("\nPress Enter after approving in the Robinhood app...")
        result = asyncio.run(complete_robinhood_link(token, email, password))
        print(json.dumps(result, indent=2))
        status = str(result.get("status") or result.get("result") or "").lower()
        if status in {"logged_in", "success", "linked"} or result.get("logged_in") is True:
            print("Robinhood linked successfully.")
            return
        if status == "sms_required" or result.get("sms_required"):
            sms_code = input("SMS code: ").strip()
            result = asyncio.run(submit_sms_code(token, email, password, sms_code))
            print(json.dumps(result, indent=2))
            status = str(result.get("status") or result.get("result") or "").lower()
            if status in {"logged_in", "success", "linked"} or result.get("logged_in") is True:
                print("Robinhood linked successfully.")
                return
        if status != "pending":
            print("Link flow did not complete. Re-run with --link-robinhood to try again.")
            return


def print_login_status(profile: str, explicit_token: str | None = None) -> None:
    token = load_bearer_token(profile, explicit_token)
    if not token:
        print(f"ERROR: no valid Trayd OAuth token for '{profile}'", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(asyncio.run(check_login_status(token)), indent=2))


def logout_robinhood_interactive(profile: str, explicit_token: str | None = None) -> None:
    token = load_bearer_token(profile, explicit_token)
    if not token:
        print(f"ERROR: no valid Trayd OAuth token for '{profile}'", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(asyncio.run(logout_robinhood(token)), indent=2))
    print("Robinhood connection removed from Trayd server memory.")


# ---------------------------------------------------------------------------
# Register with menu system
# ---------------------------------------------------------------------------

def _register() -> None:
    try:
        from src.cli.menu import register_broker
        register_broker("Robinhood", sys.modules[__name__])
    except ImportError:
        pass

_register()


# ---------------------------------------------------------------------------
# Standalone CLI (non-interactive)
# ---------------------------------------------------------------------------

async def _cli_fetch_all(
    *,
    profiles: list[str],
    account_filter: str | None = None,
    show_positions: bool = True,
    show_portfolio: bool = True,
    show_accounts_only: bool = False,
    raw_json: bool = False,
    explicit_token: str | None = None,
) -> None:
    all_results: list[dict[str, Any]] = []

    for profile in profiles:
        token = load_bearer_token(profile, explicit_token)
        if not token:
            print(f"ERROR: no valid token for '{profile}'", file=sys.stderr)
            print(f"Fix: python src/cli/robinhood.py --login --profile {profile}", file=sys.stderr)
            continue

        try:
            async with streamablehttp_client(
                MCP_URL, headers={"Authorization": f"Bearer {token}"},
                timeout=30, sse_read_timeout=30,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    accounts_data = await _call_tool(session, "list_accounts")
                    accounts = accounts_data.get("accounts", [])

                    if account_filter:
                        accounts = [a for a in accounts if a["account_number"] == account_filter]

                    if show_accounts_only:
                        all_results.append({"profile": profile, "accounts": accounts})
                        continue

                    profile_result: dict[str, Any] = {"profile": profile, "accounts": []}
                    for acct in accounts:
                        acct_num = acct["account_number"]
                        acct_result: dict[str, Any] = {
                            "account_number": acct_num,
                            "nickname": acct.get("nickname", ""),
                            "type": acct.get("type", ""),
                            "buying_power": acct.get("buying_power", "0"),
                        }
                        if show_portfolio:
                            acct_result["portfolio"] = await _call_tool(
                                session, "get_portfolio", {"account_number": acct_num})
                        if show_positions:
                            acct_result["positions"] = await _call_tool(
                                session, "get_positions", {"account_number": acct_num})
                        profile_result["accounts"].append(acct_result)
                    all_results.append(profile_result)
        except Exception as exc:
            if "401" in str(exc):
                print(f"ERROR: [{profile}] 401 Unauthorized", file=sys.stderr)
                print(f"Fix: python src/cli/robinhood.py --login --profile {profile}", file=sys.stderr)
            else:
                print(f"ERROR: [{profile}] {exc}", file=sys.stderr)

    if not all_results:
        sys.exit(1)

    if raw_json:
        print(json.dumps(all_results, indent=2))
        return

    if show_accounts_only:
        for r in all_results:
            accts = r["accounts"]
            print(f"\n{'='*60}")
            print(f"  [{r['profile'].upper()}] Robinhood Accounts ({len(accts)})")
            print(f"{'='*60}")
            for acct in accts:
                default = " (default)" if acct.get("is_default") else ""
                print(f"  {acct['account_number']}  {acct.get('type', ''):>10}  "
                      f"BP: ${float(acct.get('buying_power', 0)):>12,.2f}{default}")
        return

    for r in all_results:
        for acct in r["accounts"]:
            label = acct.get("nickname") or acct.get("type") or acct["account_number"]
            header = f"{r['profile'].upper()} — {label} ({acct['account_number']})"
            if "portfolio" in acct:
                display_portfolio(acct["portfolio"], header=header)
            if "positions" in acct:
                display_positions(acct["positions"])


def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="Robinhood portfolio CLI via trayd MCP",
        epilog="Each --profile is a separate Robinhood login. "
               "Each login can have multiple accounts (Individual, Roth IRA, etc.).",
    )
    parser.add_argument("--login", action="store_true", help="OAuth login (opens browser)")
    parser.add_argument("--link-robinhood", action="store_true",
                        help="Link Robinhood credentials inside the selected Trayd profile")
    parser.add_argument("--status", action="store_true", help="Show Robinhood link status")
    parser.add_argument("--logout-robinhood", action="store_true",
                        help="Remove Robinhood link from Trayd server memory")
    parser.add_argument("--profile", default=None, help="Profile name (e.g. bv, kd). Omit for all.")
    parser.add_argument("--account", default=None, help="Filter to a single account number")
    parser.add_argument("--positions", action="store_true", help="Show positions only")
    parser.add_argument("--portfolio", action="store_true", help="Show portfolio summary only")
    parser.add_argument("--accounts", action="store_true", help="List accounts per profile")
    parser.add_argument("--json", action="store_true", help="Raw JSON output")
    parser.add_argument("--token", default=None, help="Bearer token override")
    args = parser.parse_args()

    if args.login:
        if not args.profile:
            print("ERROR: --login requires --profile <name>", file=sys.stderr)
            sys.exit(1)
        do_login(args.profile)
        return

    if args.link_robinhood or args.status or args.logout_robinhood:
        if not args.profile:
            print("ERROR: this action requires --profile <name>", file=sys.stderr)
            sys.exit(1)
        if args.link_robinhood:
            link_robinhood_interactive(args.profile, args.token)
        elif args.status:
            print_login_status(args.profile, args.token)
        else:
            logout_robinhood_interactive(args.profile, args.token)
        return

    if args.profile:
        profiles = [args.profile]
    else:
        profiles = list_profiles()
        if not profiles:
            print("No profiles found. Login first:", file=sys.stderr)
            print("  python src/cli/robinhood.py --login --profile bv", file=sys.stderr)
            sys.exit(1)

    show_pos = True
    show_port = True
    if args.positions and not args.portfolio:
        show_port = False
    elif args.portfolio and not args.positions:
        show_pos = False

    asyncio.run(_cli_fetch_all(
        profiles=profiles,
        account_filter=args.account,
        show_positions=show_pos,
        show_portfolio=show_port,
        show_accounts_only=args.accounts,
        raw_json=args.json,
        explicit_token=args.token,
    ))


if __name__ == "__main__":
    _cli_main()
