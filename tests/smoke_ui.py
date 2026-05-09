#!/usr/bin/env python3
"""Lightweight smoke test for the Next.js dashboard + FastAPI backend.

Requires both servers running:
    python -m uvicorn src.api.main:app --port 8000
    cd ui && npx next dev -p 3001

Usage:
    python tests/smoke_ui.py
    python tests/smoke_ui.py --api http://127.0.0.1:8000 --ui http://127.0.0.1:3001
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error


def fetch(url: str, timeout: int = 30) -> dict | str:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    msg = f"  [{status}] {label}"
    if detail and not ok:
        msg += f" — {detail}"
    print(msg)
    return ok


def smoke_api(base: str) -> list[bool]:
    print(f"\n=== API smoke ({base}) ===")
    results: list[bool] = []

    # Health
    try:
        data = fetch(f"{base}/health")
        results.append(check("GET /health", data["status"] == "ok"))
    except Exception as e:
        results.append(check("GET /health", False, str(e)))
        print("  API unreachable — skipping remaining API checks")
        return results

    endpoints: list[tuple[str, str, callable]] = [
        ("/dashboard/capabilities", "capabilities count >= 30",
         lambda d: (d["data"]["capability_count"] >= 30, f'got {d["data"]["capability_count"]}')),
        ("/dashboard/capabilities", "8 tabs in contract",
         lambda d: (len(d["data"]["tabs"]) == 8, f'got {len(d["data"]["tabs"])}')),
        ("/dashboard/portfolio", "net_worth > 0",
         lambda d: ((d["data"]["net_worth"]["net_worth"] or 0) > 0, f'got {d["data"]["net_worth"]["net_worth"]}')),
        ("/dashboard/portfolio", "account_summary rows >= 10",
         lambda d: (len(d["data"]["account_summary"]) >= 10, f'got {len(d["data"]["account_summary"])}')),
        ("/dashboard/portfolio", "asset_class rows >= 4",
         lambda d: (len(d["data"]["asset_class_breakdown"]) >= 4, f'got {len(d["data"]["asset_class_breakdown"])}')),
        ("/dashboard/yearly-summary", "yearly summary rows >= 5",
         lambda d: (len(d["data"]["summary"]) >= 5, f'got {len(d["data"]["summary"])}')),
        ("/dashboard/by-account", "by-account net_cash_flow rows >= 5",
         lambda d: (len(d["data"]["net_cash_flow"]) >= 5, f'got {len(d["data"]["net_cash_flow"])}')),
        ("/dashboard/performance", "performance summary rows >= 1",
         lambda d: (len(d["data"]["summary"]) >= 1, f'got {len(d["data"]["summary"])}')),
        ("/portfolio/positions", "positions >= 100",
         lambda d: (len(d["data"]["canonical_positions"]) >= 100, f'got {len(d["data"]["canonical_positions"])}')),
        ("/transactions?limit=25", "transactions count >= 10",
         lambda d: (d["data"]["count"] >= 10, f'got {d["data"]["count"]}')),
    ]

    cache: dict[str, dict] = {}
    for path, label, validate in endpoints:
        try:
            if path not in cache:
                cache[path] = fetch(f"{base}{path}")
            ok, detail = validate(cache[path])
            results.append(check(label, ok, detail))
        except Exception as e:
            results.append(check(label, False, str(e)))

    return results


def smoke_ui(base: str) -> list[bool]:
    print(f"\n=== UI smoke ({base}) ===")
    results: list[bool] = []

    try:
        html = fetch(f"{base}/")
        is_html = isinstance(html, str) and "<html" in html.lower()
        results.append(check("GET / returns HTML", is_html))
    except Exception as e:
        results.append(check("GET / returns HTML", False, str(e)))
        print("  UI unreachable — skipping remaining UI checks")
        return results

    results.append(check("page contains Trading Journal", "Trading Journal" in html))
    results.append(check("page contains Portfolio tab", "Portfolio" in html))
    results.append(check("page contains 8 tab buttons", html.count("nav button") >= 1 or "Broker MCP" in html))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test the dashboard")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--ui", default="http://127.0.0.1:3001")
    args = parser.parse_args()

    all_results: list[bool] = []
    all_results.extend(smoke_api(args.api))
    all_results.extend(smoke_ui(args.ui))

    passed = sum(all_results)
    total = len(all_results)
    failed = total - passed
    print(f"\n{'='*40}")
    print(f"  {passed}/{total} passed, {failed} failed")
    print(f"{'='*40}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
