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


def _positions(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("positions")
    return rows if isinstance(rows, list) else []


def _has_any_field(rows: list[dict], names: set[str]) -> bool:
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if key.lower() in names and value not in (None, ""):
                return True
    return False


def _cost_basis_probe(
    performance: dict[str, Any],
    current_spot: dict[str, Any],
    history_spot: dict[str, Any],
    current_all: dict[str, Any],
) -> dict[str, Any]:
    spot_rows = _positions(current_spot)
    spot_history = _positions(history_spot)
    all_rows = _positions(current_all)
    future_rows = [row for row in all_rows if row.get("asset_class") == "future"]

    avg_entry_names = {"avg_entry", "avg_entry_price", "average_entry", "average_entry_price"}
    cost_names = {"cost", "cost_basis", "costbasis", "basis"}
    pnl_names = {"pnl", "unrealized_pnl", "realized_pnl", "realized_pnl_day", "gain_loss"}

    return {
        "spot_current_count": len(spot_rows),
        "spot_history_count": len(spot_history),
        "spot_has_avg_entry": _has_any_field(spot_rows + spot_history, avg_entry_names),
        "spot_has_cost_basis": _has_any_field(spot_rows + spot_history, cost_names),
        "spot_has_realized_or_unrealized_pnl": _has_any_field(spot_rows + spot_history, pnl_names),
        "futures_current_count": len(future_rows),
        "futures_has_avg_entry": _has_any_field(future_rows, avg_entry_names),
        "futures_has_realized_or_unrealized_pnl": _has_any_field(future_rows, pnl_names),
        "performance_sections": sorted(performance.keys()) if isinstance(performance, dict) else [],
        "performance_notes": performance.get("notes") if isinstance(performance, dict) else None,
    }


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
        get_performance_report,
        get_position_history,
        query_current_balances,
        query_current_positions,
        query_portfolio_state,
    )
    from src.mcp_ingest import write_coinbase  # noqa: PLC0415

    snapshot = capture_coinbase_portfolio_snapshot(label=args.label)
    balances = query_current_balances()
    state = query_portfolio_state()
    performance = get_performance_report()
    current_spot = query_current_positions(asset_class="spot")
    current_all = query_current_positions(asset_class="all")
    history_spot = get_position_history(asset_class="spot", limit=1000)
    history_all = get_position_history(asset_class="all", limit=1000)

    snapshot_path = args.out_dir / "coinbase_snapshot.json"
    balances_path = args.out_dir / "coinbase_current_balances_normalized.json"
    state_path = args.out_dir / "coinbase_portfolio_state.json"
    performance_path = args.out_dir / "coinbase_performance_report.json"
    current_spot_path = args.out_dir / "coinbase_current_positions_spot.json"
    current_all_path = args.out_dir / "coinbase_current_positions_all.json"
    history_spot_path = args.out_dir / "coinbase_position_history_spot.json"
    history_all_path = args.out_dir / "coinbase_position_history_all.json"
    _write_json(snapshot_path, snapshot)
    _write_json(balances_path, balances)
    _write_json(state_path, state)
    _write_json(performance_path, performance)
    _write_json(current_spot_path, current_spot)
    _write_json(current_all_path, current_all)
    _write_json(history_spot_path, history_spot)
    _write_json(history_all_path, history_all)

    result = write_coinbase(state, dry_run=args.dry_run)
    cost_basis_probe = _cost_basis_probe(performance, current_spot, history_spot, current_all)
    snapshot_accounts = 0
    if not args.dry_run and not args.no_snapshot:
        snapshot_accounts = _write_journal_snapshot()

    print(json.dumps({
        "coinbase_snapshot_status": snapshot.get("status"),
        "coinbase_snapshot_counts": snapshot.get("counts"),
        "coinbase_snapshot_errors": snapshot.get("errors"),
        "coinbase_cost_basis_probe": cost_basis_probe,
        "journal_import": result,
        "journal_snapshot_accounts": snapshot_accounts,
        "saved": [
            str(snapshot_path),
            str(balances_path),
            str(state_path),
            str(performance_path),
            str(current_spot_path),
            str(current_all_path),
            str(history_spot_path),
            str(history_all_path),
        ],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
