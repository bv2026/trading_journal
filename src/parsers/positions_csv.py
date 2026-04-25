# -*- coding: utf-8 -*-
"""Parser for per-account positions CSV files.

Each broker exports a CSV whose columns match the TRADEPOSITIONS Excel structure.
We read the static fields (ticker, shares, cost_basis, sector …) and ignore the
price-derived columns (PRICE, COST, MARKET VALUE, totalReturn) because those are
computed at runtime from live prices.

Expected CSV naming: activity/positions-{suffix}.csv
  e.g. positions-scwb.csv     → account_id "SCHWAB"
       positions-trader.csv   → account_id "TRADIER"
       positions-rh-bv.csv    → account_id "RH-BV"
"""
import logging
import re
from pathlib import Path

import pandas as pd

from src.positions import COL_RENAME, SKIP_COL_PREFIXES

# Columns computed at runtime from live prices — always dropped from the CSV.
_RUNTIME_COLS = {"PRICE", "COST", "MARKET VALUE", "totalReturn"}

# After COL_RENAME, these are the numeric fields we coerce.
_NUMERIC_FIELDS = ("Shares", "Cost_Basis", "IV_Rank", "PERF_YTD", "ATR_pct")

# Matches $(1,234.56) or (1,234.56) — Excel's way of writing negative numbers.
_PAREN_NEG_RE = re.compile(r"^\$?\(([0-9,\.]+)\)$")


def _clean_value(v) -> str:
    """Normalise an Excel-exported cell to a bare numeric string."""
    if not isinstance(v, str):
        return str(v)
    v = v.strip()
    # Parenthetical negative:  $(1,234.56)  or  (1,234.56)
    m = _PAREN_NEG_RE.match(v)
    if m:
        return "-" + m.group(1).replace(",", "")
    # Strip currency symbol, commas, percent sign
    v = v.replace("$", "").replace(",", "").replace("%", "").strip()
    # Map non-numeric markers to NaN
    if v.upper() in ("N/A", "NA", "-", ""):
        return "nan"
    return v


def parse(filepath: str, account_id: str) -> list[dict]:
    """Parse a positions CSV and return a list of DB-ready dicts.

    Rows where Ticker is blank or 'MARGIN' are skipped.
    Columns in _RUNTIME_COLS are dropped — they will be recomputed from live prices.
    """
    path = Path(filepath)
    if not path.exists():
        logging.warning("positions_csv %s: file not found", filepath)
        return []

    try:
        df = pd.read_csv(filepath)
    except Exception as exc:
        logging.warning("positions_csv %s: read error — %s", filepath, exc)
        return []

    # Strip leading/trailing whitespace from column names (Excel export artefact)
    df.columns = df.columns.str.strip()

    # Drop Excel helper / unnamed columns
    keep = [c for c in df.columns
            if not any(str(c).startswith(p) for p in SKIP_COL_PREFIXES)]
    df = df[keep].copy()

    # Standardise column names (Sh/Contr → Shares, COST BASIS → Cost_Basis …)
    df.rename(columns=COL_RENAME, inplace=True)

    # Clean ticker first so we can identify MARGIN rows before dropping columns.
    # .fillna("") first because pandas 3+ astype(str) keeps NA as pd.NA, not "nan".
    df["Ticker"] = df["Ticker"].fillna("").astype(str).str.strip()
    df = df[~df["Ticker"].str.upper().isin(["NAN", ""])]

    # Coerce numeric fields now so columns are float dtype before any assignment.
    for col in _NUMERIC_FIELDS:
        if col in df.columns:
            df[col] = df[col].apply(_clean_value)
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Capture margin balance → Cost_Basis on the MARGIN row.
    # New format: dedicated MARGIN column (preferred).
    # Legacy fallback: MARKET VALUE column (dropped afterward as a runtime col).
    margin_mask = df["Ticker"].str.upper() == "MARGIN"
    if margin_mask.any():
        if "MARGIN" in df.columns:
            balance_col = df.loc[margin_mask, "MARGIN"]
        elif "MARKET VALUE" in df.columns:
            balance_col = df.loc[margin_mask, "MARKET VALUE"]
        else:
            balance_col = None

        if balance_col is not None:
            mv_vals = pd.to_numeric(
                balance_col.apply(_clean_value), errors="coerce"
            )
            if "Cost_Basis" not in df.columns:
                df["Cost_Basis"] = pd.array([float("nan")] * len(df), dtype="Float64")
            df.loc[margin_mask, "Cost_Basis"] = mv_vals.values

    # Capture CSV price as stored_price before dropping PRICE.
    if "PRICE" in df.columns:
        df["stored_price"] = pd.to_numeric(
            df["PRICE"].apply(_clean_value), errors="coerce"
        )
        # MARGIN row has no meaningful price — clear it
        df.loc[margin_mask, "stored_price"] = float("nan")

    # Drop runtime-computed columns and the MARGIN sentinel column
    _drop = _RUNTIME_COLS | {"MARGIN"}
    for col in _drop:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    if df.empty:
        return []

    # Also strip whitespace from text columns
    for col in ("Name", "sector", "industry", "TYPE"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace("nan", None)

    records: list[dict] = []
    for _, row in df.iterrows():
        def _val(col):
            v = row.get(col)
            if v is None:
                return None
            if isinstance(v, float) and pd.isna(v):
                return None
            if isinstance(v, str) and v.lower() in ("nan", "none", ""):
                return None
            return v

        records.append({
            "account_id":   account_id,
            "ticker":       row["Ticker"],
            "name":         _val("Name"),
            "shares":       _val("Shares"),
            "cost_basis":   _val("Cost_Basis"),
            "stored_price": _val("stored_price"),
            "sector":       _val("sector"),
            "industry":     _val("industry"),
            "asset_type":   _val("TYPE"),
            "iv_rank":      _val("IV_Rank"),
            "perf_ytd":     _val("PERF_YTD"),
            "atr_pct":      _val("ATR_pct"),
            "source_file":  str(path.name),
        })

    return records
