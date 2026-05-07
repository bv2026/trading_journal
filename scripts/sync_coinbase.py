"""Sync Coinbase spot balances from coinbase-derivatives-mcp into journal.db.

This script reuses the Coinbase MCP server settings from Claude Desktop config,
captures a fresh Coinbase MCP snapshot, then imports the normalized Coinbase
portfolio state as COINBASE crypto/cash rows plus futures P&L rows in the
trading journal database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as _date
from pathlib import Path
from typing import Any

_project_root = str(Path(__file__).resolve().parents[1])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src import db


SERVER_NAME = "coinbase-derivatives-mcp"
DEFAULT_OUT_DIR = Path("data/tmp")


def _default_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; pass --config with your Claude config path.")
    return Path(appdata) / "Claude" / "claude_desktop_config.json"


def _load_server_env(config_path: Path) -> None:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers") or {}
    server = servers.get(SERVER_NAME)
    if not server:
        raise RuntimeError(f"{SERVER_NAME!r} was not found in {config_path}")

    os.environ.update(server.get("env") or {})
    for path in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if path and path not in sys.path:
            sys.path.insert(0, path)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _write_journal_snapshot() -> int:
    from src import ingest  # noqa: PLC0415

    snap_map = ingest._compute_snapshot_map()
    if not snap_map:
        return 0
    db.write_portfolio_snapshot(_date.today().isoformat(), snap_map)
    return len(snap_map)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Coinbase MCP balances into trading-journal.")
    parser.add_argument("--config", type=Path, default=None, help="Path to claude_desktop_config.json.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Where raw MCP responses are saved.")
    parser.add_argument("--label", default="trading-journal-sync", help="Coinbase MCP snapshot label.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and normalize but do not write journal.db.")
    parser.add_argument("--no-snapshot", action="store_true", help="Skip writing today's portfolio snapshot.")
    args = parser.parse_args()

    _load_server_env(args.config or _default_config_path())

    from coinbase_derivatives_mcp.server import (  # noqa: PLC0415
        capture_coinbase_portfolio_snapshot,
        query_current_balances,
        query_portfolio_state,
    )
    from src.mcp_ingest import write_coinbase  # noqa: PLC0415

    snapshot = capture_coinbase_portfolio_snapshot(label=args.label)
    balances = query_current_balances()
    state = query_portfolio_state()

    snapshot_path = args.out_dir / "coinbase_snapshot.json"
    balances_path = args.out_dir / "coinbase_current_balances_normalized.json"
    state_path = args.out_dir / "coinbase_portfolio_state.json"
    _write_json(snapshot_path, snapshot)
    _write_json(balances_path, balances)
    _write_json(state_path, state)

    result = write_coinbase(state, dry_run=args.dry_run)
    snapshot_accounts = 0
    if not args.dry_run and not args.no_snapshot:
        snapshot_accounts = _write_journal_snapshot()

    print(json.dumps({
        "coinbase_snapshot_status": snapshot.get("status"),
        "coinbase_snapshot_counts": snapshot.get("counts"),
        "coinbase_snapshot_errors": snapshot.get("errors"),
        "journal_import": result,
        "journal_snapshot_accounts": snapshot_accounts,
        "saved": [str(snapshot_path), str(balances_path), str(state_path)],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
