# -*- coding: utf-8 -*-
"""Position loading and net-worth helpers — no Streamlit dependency.

Extracted from dashboard/app.py so the logic can be unit-tested and reused
by both the dashboard and the MCP server.
"""
import logging
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Sheet → account-ID mapping (TLOG-RECONCILE is intentionally excluded)
# ---------------------------------------------------------------------------
SHEET_ACCOUNT: dict[str, str] = {
    "SCWB":     "SCHWAB",
    "TRDER":    "TRADIER",
    "TRDSTN":   "TS",
    "RH-KD":    "RH-KD",
    "RH-BV":    "RH-BV",
    "WBULL":    "WEBULL",
    "FIDELITY": "FIDELITY",
}

# Columns whose presence is mandatory — sheets missing any of these are skipped.
REQUIRED_COLS: frozenset[str] = frozenset({"Ticker", "COST", "MARKET VALUE", "totalReturn"})

# Column-name prefixes to drop (Excel helper columns).
SKIP_COL_PREFIXES: tuple[str, ...] = ("Unnamed", "MS FORM")

# Column renames applied after loading.
COL_RENAME: dict[str, str] = {
    "ATR %":      "ATR_pct",
    "IV RANK":    "IV_Rank",
    "PERF YTD":   "PERF_YTD",
    "Sh/Contr":   "Shares",
    "COST BASIS": "Cost_Basis",
}

# Sector overrides — applied after loading so they survive Excel file replacements.
SECTOR_OVERRIDES: dict[str, str] = {
    "CHPY": "Income ETF",
    "NVII": "Income ETF",
    "NVIT": "Income ETF",
    "RVI":  "Income ETF",
    "ULTI": "Income ETF",
    "SDTY": "Income ETF",
    "QDTY": "Income ETF",
    "RDTY": "Income ETF",
    "QLDY": "Income ETF",
}

# Columns to coerce to float after concatenation.
NUMERIC_COLS: tuple[str, ...] = (
    "PRICE", "Shares", "Cost_Basis", "COST", "MARKET VALUE",
    "totalReturn", "IV_Rank", "PERF_YTD", "ATR_pct",
)


def load_positions(filepath: Path) -> pd.DataFrame:
    """Load all position sheets from *filepath* into one DataFrame.

    Returns an empty DataFrame when:
    * The file does not exist.
    * No sheets load successfully.

    Sheet-level errors (missing required columns, unreadable data) are logged
    at WARNING level so they appear in the server log without crashing callers.
    """
    if not filepath.exists():
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for sheet, acct in SHEET_ACCOUNT.items():
        try:
            df_ = pd.read_excel(filepath, sheet_name=sheet)

            # Drop helper / unnamed columns
            keep = [c for c in df_.columns
                    if not any(str(c).startswith(p) for p in SKIP_COL_PREFIXES)]
            df_ = df_[keep].copy()

            # Standardise column names
            df_.rename(columns=COL_RENAME, inplace=True)

            # Validate required columns are present
            missing = REQUIRED_COLS - set(df_.columns)
            if missing:
                logging.warning(
                    "TRADEPOSITIONS %s: skipping — missing columns %s", sheet, missing
                )
                continue

            # Drop blank / NaN ticker rows
            df_["Ticker"] = df_["Ticker"].astype(str).str.strip()
            df_ = df_[df_["Ticker"].str.upper() != "NAN"]
            df_["Account"] = acct
            frames.append(df_)

        except Exception as exc:
            logging.warning("TRADEPOSITIONS %s: failed to load — %s", sheet, exc)

    if not frames:
        return pd.DataFrame()

    pos = pd.concat(frames, ignore_index=True)

    # Fill optional text columns
    for col in ("sector", "industry", "TYPE"):
        if col in pos.columns:
            pos[col] = pos[col].fillna("Unknown")

    # Apply hardcoded sector overrides (survives Excel file replacements)
    if "sector" in pos.columns:
        pos["sector"] = pos.apply(
            lambda r: SECTOR_OVERRIDES.get(r["Ticker"], r["sector"]), axis=1
        )

    # Coerce numeric columns
    for col in NUMERIC_COLS:
        if col in pos.columns:
            pos[col] = pd.to_numeric(pos[col], errors="coerce")

    return pos


def compute_net_worth(positions_df: pd.DataFrame) -> dict[str, float]:
    """Compute net worth from a loaded positions DataFrame.

    Returns a dict with keys ``market_value``, ``margin``, and ``net_worth``.
    All values are 0.0 when *positions_df* is empty or lacks a MARKET VALUE column.
    """
    if positions_df.empty or "MARKET VALUE" not in positions_df.columns:
        return {"market_value": 0.0, "margin": 0.0, "net_worth": 0.0}

    mv_col = pd.to_numeric(positions_df["MARKET VALUE"], errors="coerce")
    is_margin = positions_df["Ticker"] == "MARGIN"

    total_mv     = float(mv_col[~is_margin].sum())
    total_margin = abs(float(mv_col[is_margin].sum()))

    return {
        "market_value": total_mv,
        "margin":       total_margin,
        "net_worth":    total_mv - total_margin,
    }
