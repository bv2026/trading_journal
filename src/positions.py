# -*- coding: utf-8 -*-
"""Position loading and net-worth helpers — no Streamlit dependency.

Extracted from dashboard/app.py so the logic can be unit-tested and reused
by both the dashboard and the MCP server.
"""
import logging
import re
from pathlib import Path

import pandas as pd

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

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

# Ticker-level sector overrides — highest priority, survive Excel replacements.
SECTOR_OVERRIDES: dict[str, str] = {
    # Income ETFs not caught by name-pattern (abbreviated/non-standard names)
    "CHPY": "Income ETF",
    "NVII": "Income ETF",
    "NVIT": "Income ETF",
    "RVI":  "Income ETF",
    "ULTI": "Income ETF",
    "SDTY": "Income ETF",
    "QDTY": "Income ETF",
    "RDTY": "Income ETF",
    "QLDY": "Income ETF",
    # Broad-market & bond ETFs miscategorized as Financial in Excel
    "BND": "Fixed Income",
    "VTI": "Broad Market",
    "VEA": "International",
    "VWO": "International",
    "DBC": "Commodities",
    # Sector ETFs miscategorized as Financial in Excel
    "XLE":  "Energy",
    "IYW":  "Technology",
    "SMH":  "Technology",
    "USD":  "Technology",
    "SHLD": "Industrials",
}

# Name-pattern overrides — catch entire fund families automatically so new
# products from these issuers are categorized correctly without code changes.
_INCOME_ETF_NAME_RE = re.compile(
    r"\b(yieldmax|roundhill|defiance|rex)\b",
    re.IGNORECASE,
)

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
            # .fillna("") first: pandas 3+ astype(str) keeps NA as pd.NA not "nan"
            df_["Ticker"] = df_["Ticker"].fillna("").astype(str).str.strip()
            df_ = df_[~df_["Ticker"].str.upper().isin(["NAN", ""])]
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

    # Apply sector overrides — ticker-level first, then fund-family name patterns.
    if "sector" in pos.columns:
        name_col = pos.get("Name", pd.Series("", index=pos.index)).fillna("")
        pos["sector"] = [
            _resolve_sector(t, n, s)
            for t, n, s in zip(pos["Ticker"], name_col, pos["sector"])
        ]

    # Coerce numeric columns
    for col in NUMERIC_COLS:
        if col in pos.columns:
            pos[col] = pd.to_numeric(pos[col], errors="coerce")

    return pos


def _fetch_live_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch last closing prices via yfinance for a list of tickers.

    Returns a dict {ticker: price}. Missing prices are omitted so callers
    can decide how to handle them (NaN MARKET VALUE).
    """
    if not tickers or not _YF_AVAILABLE:
        return {}
    try:
        data = yf.download(
            tickers, period="5d", progress=False, auto_adjust=True, threads=True
        )
        if data.empty:
            return {}
        closes = data["Close"] if "Close" in data.columns else data
        # yfinance returns a Series for a single ticker, DataFrame for multiple
        if isinstance(closes, pd.Series):
            last = closes.dropna()
            return {tickers[0]: float(last.iloc[-1])} if not last.empty else {}
        last_row = closes.ffill().iloc[-1]
        return {
            str(t): float(v)
            for t, v in last_row.items()
            if not pd.isna(v)
        }
    except Exception as exc:
        logging.warning("yfinance price fetch failed: %s", exc)
        return {}


def load_positions_from_db() -> pd.DataFrame:
    """Load positions from the DB, fetch live prices, and compute derived columns.

    Returns a DataFrame with the same columns as *load_positions* so callers
    (dashboard, MCP server) can use either source transparently.

    Derived columns computed at runtime:
      PRICE        — latest closing price from yfinance
      COST         — Shares × Cost_Basis
      MARKET VALUE — Shares × PRICE
      totalReturn  — MARKET VALUE − COST
    """
    # Import here to avoid circular dependency at module level.
    from src.db import load_positions_db  # noqa: PLC0415

    pos = load_positions_db()
    if pos.empty:
        return pd.DataFrame()

    # Fill text nullable columns
    for col in ("sector", "industry", "TYPE"):
        if col in pos.columns:
            pos[col] = pos[col].fillna("Unknown")

    # Apply sector overrides (same logic as load_positions)
    if "sector" in pos.columns:
        name_col = pos.get("Name", pd.Series("", index=pos.index)).fillna("")

        pos["sector"] = [
            _resolve_sector(t, n, s)
            for t, n, s in zip(pos["Ticker"], name_col, pos["sector"])
        ]

    # Coerce numeric columns from DB
    for col in ("Shares", "Cost_Basis", "Stored_Price", "IV_Rank", "PERF_YTD", "ATR_pct"):
        if col in pos.columns:
            pos[col] = pd.to_numeric(pos[col], errors="coerce")

    # Separate MARGIN rows — their "price" is meaningless; cost_basis holds the balance.
    is_margin = pos["Ticker"].str.upper() == "MARGIN"

    # Only fetch live prices for accounts that use yfinance (price_source = 'live').
    # Static accounts (e.g. COINBASE) use stored_price directly — skip yfinance to
    # avoid noisy 404 errors for crypto tickers that Yahoo Finance doesn't serve.
    is_static = pd.Series(False, index=pos.index)
    if "Price_Source" in pos.columns:
        is_static = pos["Price_Source"].str.lower().eq("static")
    if "Account_Type" in pos.columns:
        is_static = is_static | pos["Account_Type"].str.lower().eq("crypto")

    live_mask    = ~is_margin & ~is_static
    live_tickers = pos.loc[live_mask, "Ticker"].dropna().unique().tolist()
    prices       = _fetch_live_prices(live_tickers)
    pos["PRICE"] = pos["Ticker"].map(prices)

    # Static rows: always use stored_price (skip yfinance entirely).
    # Live rows:   fall back to stored_price only when yfinance returned nothing.
    if "Stored_Price" in pos.columns:
        use_stored = is_static | (pos["PRICE"].isna() & ~is_margin)
        pos.loc[use_stored, "PRICE"] = pos.loc[use_stored, "Stored_Price"]

    # Compute derived columns for real positions
    pos["COST"]         = pos["Shares"] * pos["Cost_Basis"]
    pos["MARKET VALUE"] = pos["Shares"] * pos["PRICE"]
    pos["totalReturn"]  = pos["MARKET VALUE"] - pos["COST"]

    # MARGIN rows: MARKET VALUE = cost_basis (the raw balance stored at ingest time)
    pos.loc[is_margin, "MARKET VALUE"] = pos.loc[is_margin, "Cost_Basis"]
    pos.loc[is_margin, "COST"]         = 0.0
    pos.loc[is_margin, "totalReturn"]  = 0.0

    return pos


def _resolve_sector(ticker: str, name: str, sector: str) -> str:
    """Return the corrected sector for a position row."""
    if ticker in SECTOR_OVERRIDES:
        return SECTOR_OVERRIDES[ticker]
    if _INCOME_ETF_NAME_RE.search(str(name)):
        return "Income ETF"
    return sector


def load_options_from_db() -> pd.DataFrame:
    """Load options positions from DB, normalised to the unified column layout.

    Returns a DataFrame with columns: Account, Ticker, MARKET VALUE, asset_class,
    plus options-specific columns (underlying, expiry, strike, call_put, qty, price).
    """
    from src.db import load_options_db  # noqa: PLC0415
    df = load_options_db()
    if df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        "account_id":   "Account",
        "symbol":       "Ticker",
        "market_value": "MARKET VALUE",
    })
    df["MARKET VALUE"] = pd.to_numeric(df["MARKET VALUE"], errors="coerce")
    df["asset_class"] = "options"
    return df


def load_futures_from_db() -> pd.DataFrame:
    """Load futures positions from DB, normalised to the unified column layout.

    Returns a DataFrame with columns: Account, Ticker, MARKET VALUE, asset_class,
    plus futures-specific columns (underlying, qty, price).
    """
    from src.db import load_futures_db  # noqa: PLC0415
    df = load_futures_db()
    if df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        "account_id":   "Account",
        "symbol":       "Ticker",
        "market_value": "MARKET VALUE",
    })
    df["MARKET VALUE"] = pd.to_numeric(df["MARKET VALUE"], errors="coerce")
    df["asset_class"] = "futures"
    return df


def load_crypto_from_db() -> pd.DataFrame:
    """Load crypto positions from DB, normalised to the unified column layout.

    Returns a DataFrame with columns: Account, Ticker, MARKET VALUE, asset_class,
    plus crypto-specific columns (name, qty, price, cost_basis).
    """
    from src.db import load_crypto_db  # noqa: PLC0415
    df = load_crypto_db()
    if df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        "account_id":   "Account",
        "symbol":       "Ticker",
        "market_value": "MARKET VALUE",
    })
    df["MARKET VALUE"] = pd.to_numeric(df["MARKET VALUE"], errors="coerce")
    df["asset_class"] = "crypto"
    return df


def load_all_positions() -> pd.DataFrame:
    """Load all position types and return a unified DataFrame.

    Columns guaranteed present: Account, Ticker, MARKET VALUE, asset_class.
    Asset-specific columns (sector, underlying, expiry, etc.) are included
    where available and NaN elsewhere.

    Used by the Performance tab and MCP get_positions tool.
    Existing tabs 1-5 continue to use load_positions_from_db() (equity only).
    """
    frames: list[pd.DataFrame] = []

    eq = load_positions_from_db()
    if not eq.empty:
        eq["asset_class"] = "equity"
        frames.append(eq)

    for loader in (load_options_from_db, load_futures_from_db, load_crypto_from_db):
        df = loader()
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def compute_net_worth(positions_df: pd.DataFrame) -> dict[str, float]:
    """Compute net worth from a positions DataFrame (equity or unified).

    Returns a dict with keys ``market_value``, ``margin``, and ``net_worth``.
    All values are 0.0 when *positions_df* is empty or lacks a MARKET VALUE column.

    MARGIN rows (equity-only sentinel) are separated out and treated as margin debt.
    All other rows — including options, futures, and crypto — contribute to market_value.
    """
    if positions_df.empty or "MARKET VALUE" not in positions_df.columns:
        return {"market_value": 0.0, "margin": 0.0, "net_worth": 0.0}

    mv_col = pd.to_numeric(positions_df["MARKET VALUE"], errors="coerce")
    is_margin = (
        positions_df.get("Ticker", pd.Series(dtype=str))
        .fillna("")
        .astype(str)
        .str.upper()
        .eq("MARGIN")
    )

    total_mv     = float(mv_col[~is_margin].sum())
    total_margin = abs(float(mv_col[is_margin].sum()))

    return {
        "market_value": total_mv,
        "margin":       total_margin,
        "net_worth":    total_mv - total_margin,
    }
