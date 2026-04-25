"""Parser for options, futures, and crypto position CSV files (Tradier-style export).

Expected columns:
    Symbol, Expiry, Strike, Call/Put, Description, Qty, Price, Market Value,
    Underlying Symbol

Optional / ignored: Account Type, Day Change

Row routing (determined by cell content, not account_type arg):
  - non-empty Expiry AND non-empty Call/Put  → options row
  - non-empty Underlying Symbol, empty Call/Put → futures row
  - empty Expiry, Strike, Call/Put            → crypto row
  - anything else (ambiguous equity)          → skipped

The `account_type` argument selects which rows are returned and shapes the dicts:
  - 'options'  → options rows  → dicts for options_positions table
  - 'futures'  → futures rows  → dicts for futures_positions table
  - 'crypto'   → crypto rows   → dicts for crypto_positions table

Missing file → empty list (same contract as positions_csv.py).
"""
import logging
import re
from pathlib import Path

import pandas as pd

_PAREN_NEG_RE = re.compile(r"^\$?\(([0-9,\.]+)\)$")

_NUMERIC = ("Qty", "Price", "Market Value", "Strike")


def _clean_num(v) -> str:
    """Normalise an exported cell to a bare numeric string."""
    if not isinstance(v, str):
        return str(v)
    v = v.strip()
    m = _PAREN_NEG_RE.match(v)
    if m:
        return "-" + m.group(1).replace(",", "")
    v = v.replace("$", "").replace(",", "").replace("%", "").strip()
    if v.upper() in ("N/A", "NA", "-", ""):
        return "nan"
    return v


def _to_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.apply(_clean_num), errors="coerce")


def _str_col(df: pd.DataFrame, col: str) -> pd.Series:
    """Return column as stripped strings; missing column → empty strings."""
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    return df[col].fillna("").astype(str).str.strip()


def _val(v):
    """None-ify NaN / empty string values."""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, str) and v.lower() in ("nan", "none", ""):
        return None
    return v


def parse(filepath: str, account_id: str, account_type: str) -> list[dict]:
    """Parse a Tradier-style static positions CSV.

    Returns a list of dicts shaped for the target table indicated by account_type.
    """
    path = Path(filepath)
    if not path.exists():
        logging.warning("static_positions_csv %s: file not found", filepath)
        return []

    try:
        df = pd.read_csv(filepath)
    except Exception as exc:
        logging.warning("static_positions_csv %s: read error — %s", filepath, exc)
        return []

    df.columns = df.columns.str.strip()

    # Coerce numeric fields
    for col in _NUMERIC:
        if col in df.columns:
            df[col] = _to_float(df[col])

    # Build string sentinel columns for routing
    expiry     = _str_col(df, "Expiry")
    call_put   = _str_col(df, "Call/Put")
    underlying = _str_col(df, "Underlying Symbol")
    strike_raw = _str_col(df, "Strike")

    has_expiry     = expiry.str.len() > 0
    has_call_put   = call_put.str.len() > 0
    has_underlying = underlying.str.len() > 0
    has_strike     = strike_raw.str.len() > 0

    source = path.name

    if account_type == "options":
        mask = has_expiry & has_call_put
        return _build_options(df[mask], account_id, expiry[mask], call_put[mask],
                              underlying[mask], source)

    if account_type == "futures":
        mask = has_underlying & ~has_call_put
        return _build_futures(df[mask], account_id, underlying[mask], source)

    if account_type == "crypto":
        mask = ~has_expiry & ~has_strike & ~has_call_put & ~has_underlying
        return _build_crypto(df[mask], account_id, source)

    logging.warning("static_positions_csv: unknown account_type=%r", account_type)
    return []


def _build_options(df: pd.DataFrame, account_id: str,
                   expiry: pd.Series, call_put: pd.Series,
                   underlying: pd.Series, source: str) -> list[dict]:
    records = []
    for i, row in df.iterrows():
        records.append({
            "account_id":   account_id,
            "symbol":       _val(str(row.get("Symbol", "")).strip()) or None,
            "underlying":   _val(underlying[i]) or None,
            "expiry":       _val(expiry[i]) or None,
            "strike":       _val(row.get("Strike")),
            "call_put":     _val(call_put[i]) or None,
            "description":  _val(str(row.get("Description", "")).strip()) or None,
            "qty":          _val(row.get("Qty")),
            "price":        _val(row.get("Price")),
            "market_value": _val(row.get("Market Value")),
            "source_file":  source,
        })
    return records


def _build_futures(df: pd.DataFrame, account_id: str,
                   underlying: pd.Series, source: str) -> list[dict]:
    records = []
    for i, row in df.iterrows():
        records.append({
            "account_id":   account_id,
            "symbol":       _val(str(row.get("Symbol", "")).strip()) or None,
            "underlying":   _val(underlying[i]) or None,
            "description":  _val(str(row.get("Description", "")).strip()) or None,
            "qty":          _val(row.get("Qty")),
            "price":        _val(row.get("Price")),
            "market_value": _val(row.get("Market Value")),
            "source_file":  source,
        })
    return records


def _build_crypto(df: pd.DataFrame, account_id: str, source: str) -> list[dict]:
    records = []
    for _, row in df.iterrows():
        sym = _val(str(row.get("Symbol", "")).strip()) or None
        if sym is None:
            continue
        records.append({
            "account_id":   account_id,
            "symbol":       sym,
            "name":         _val(str(row.get("Description", "")).strip()) or None,
            "qty":          _val(row.get("Qty")),
            "price":        _val(row.get("Price")),
            "cost_basis":   None,
            "market_value": _val(row.get("Market Value")),
            "source_file":  source,
        })
    return records
