"""
Sector/industry enrichment via yfinance.

Populates NULL sector and industry in the instruments master table for equity
symbols, then copies those values back into the positions table.

Called automatically at the end of ingest.py runs so the dashboard always has
sector data for MCP-sourced positions (which arrive with sector=NULL).
"""
import logging
from typing import TYPE_CHECKING

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

if TYPE_CHECKING:
    pass


def _fetch_yfinance_meta(tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {sector, industry, name}} for each ticker that has data."""
    result: dict[str, dict] = {}
    if not tickers or not _YF_AVAILABLE:
        return result

    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            sector   = info.get("sector") or info.get("sectorDisp")
            industry = info.get("industry") or info.get("industryDisp")
            name     = info.get("longName") or info.get("shortName")
            if sector or industry or name:
                result[ticker] = {
                    "sector":   sector,
                    "industry": industry,
                    "name":     name,
                }
        except Exception as exc:
            logging.debug("yfinance meta fetch failed for %s: %s", ticker, exc)

    return result


def enrich_sectors(batch_size: int = 50) -> int:
    """
    Fill NULL sector/industry in instruments (equity only) using yfinance.
    Then propagate those values into the positions table.

    Returns the number of instruments updated.
    """
    from src.db import get_conn, DB_PATH  # noqa: PLC0415

    if not DB_PATH.exists():
        return 0

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT symbol FROM instruments "
            "WHERE asset_class = 'equity' "
            "AND (sector IS NULL OR industry IS NULL)",
        ).fetchall()

    symbols = [r[0] for r in rows]
    if not symbols:
        return 0

    updated = 0
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        meta  = _fetch_yfinance_meta(batch)
        if not meta:
            continue

        with get_conn() as conn:
            for sym, info in meta.items():
                conn.execute(
                    "UPDATE instruments SET sector=COALESCE(sector,?), "
                    "industry=COALESCE(industry,?), name=COALESCE(name,?) "
                    "WHERE symbol=? AND asset_class='equity'",
                    (info.get("sector"), info.get("industry"),
                     info.get("name"), sym),
                )
            conn.commit()
        updated += len(meta)

    # Propagate sector/industry back into equity positions rows that still have NULL.
    _propagate_to_positions()

    return updated


def _propagate_to_positions() -> None:
    """Copy sector/industry from instruments into positions where currently NULL."""
    from src.db import get_conn, DB_PATH  # noqa: PLC0415

    if not DB_PATH.exists():
        return

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE positions
            SET
                sector   = COALESCE(sector,   (SELECT i.sector   FROM instruments i
                                               WHERE i.symbol = positions.ticker
                                                 AND i.asset_class = 'equity')),
                industry = COALESCE(industry, (SELECT i.industry FROM instruments i
                                               WHERE i.symbol = positions.ticker
                                                 AND i.asset_class = 'equity')),
                name     = COALESCE(name,     (SELECT i.name     FROM instruments i
                                               WHERE i.symbol = positions.ticker
                                                 AND i.asset_class = 'equity'))
            WHERE (sector IS NULL OR industry IS NULL)
            """
        )
        conn.commit()
