"""Interactive menu-driven CLI for broker portfolio access.

Usage:
    python -m src.cli.menu          # launch interactive menu
    python src/cli/menu.py          # same thing
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path so `src.cli.*` imports work
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def print_header(title: str) -> None:
    width = 60
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}")


def prompt_choice(options: list[str], title: str = "Select", allow_back: bool = True) -> int | None:
    """Show numbered menu, return 0-based index or None for back/quit."""
    print()
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    if allow_back:
        print(f"  [0] Back")
    print()

    while True:
        try:
            raw = input(f"  {title}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if raw.lower() in ("q", "quit", "exit"):
            return None
        try:
            choice = int(raw)
        except ValueError:
            print("  Invalid input. Enter a number.")
            continue
        if allow_back and choice == 0:
            return None
        if 1 <= choice <= len(options):
            return choice - 1
        print(f"  Enter 1-{len(options)}" + (" or 0 to go back" if allow_back else ""))


# ---------------------------------------------------------------------------
# Broker registry — each broker module registers itself here
# ---------------------------------------------------------------------------

_BROKERS: list[dict[str, Any]] = []


def register_broker(name: str, handler: Any) -> None:
    _BROKERS.append({"name": name, "handler": handler})


def get_brokers() -> list[dict[str, Any]]:
    return _BROKERS


# ---------------------------------------------------------------------------
# Main menu loop
# ---------------------------------------------------------------------------

def main_menu() -> None:
    # Import broker modules to trigger registration into src.cli.menu._BROKERS
    from src.cli import robinhood as _rh  # noqa: F401
    from src.cli import tradestation as _ts  # noqa: F401
    from src.cli import tradier as _td  # noqa: F401
    from src.cli import webull as _wb  # noqa: F401
    from src.cli import schwab as _sc  # noqa: F401
    from src.cli import coinbase as _cb  # noqa: F401
    from src.cli import fidelity as _fi  # noqa: F401
    # When run as __main__, _BROKERS here is a different object than
    # src.cli.menu._BROKERS. Always read from the canonical module.
    import src.cli.menu as _canonical
    brokers = _canonical._BROKERS

    while True:
        print_header("Trading Journal CLI")

        if not brokers:
            print("  No brokers configured.")
            break

        broker_names = [b["name"] for b in brokers]
        broker_names.append("Exit")
        choice = prompt_choice(broker_names, title="Select", allow_back=False)
        if choice is None or choice == len(brokers):
            print("\n  Goodbye.\n")
            break

        brokers[choice]["handler"].broker_menu()


def main() -> None:
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\n  Goodbye.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
