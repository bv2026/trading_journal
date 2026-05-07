"""Coinbase MCP response normalizer.

The Coinbase API/MCP shape can vary by endpoint. This normalizer accepts common
portfolio/account payloads and turns non-zero balances into journal rows. Spot
wallets and USD collateral are stored as ``crypto_positions`` because the
journal currently has no per-broker cash-position table; futures P&L is stored
as ``futures_positions``.
"""


def _as_list(resp) -> list:
    if isinstance(resp, list):
        return resp
    if not isinstance(resp, dict):
        return []
    for key in ("accounts", "balances", "portfolio", "data", "positions"):
        value = resp.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _as_list(value)
            if nested:
                return nested
    return []


def _nested(row: dict, *keys):
    cur = row
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _first(row: dict, *paths):
    for path in paths:
        value = _nested(row, *path) if isinstance(path, tuple) else row.get(path)
        if value not in (None, ""):
            return value
    return None


def _float(value) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = value.get("value") or value.get("amount")
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def _futures_summary(resp) -> dict:
    if not isinstance(resp, dict):
        return {}
    summary = resp.get("futures_balance_summary")
    return summary if isinstance(summary, dict) else {}


def normalize_positions(positions_resp: dict | list, account_id: str = "COINBASE") -> list[dict]:
    """Normalize Coinbase balances into crypto/cash-like position rows."""
    rows = _as_list(positions_resp)
    records: list[dict] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = _first(
            row,
            "symbol",
            ("currency", "code"),
            "asset",
            "asset_id",
        )
        symbol = str(symbol or "").strip().upper()
        if not symbol or symbol == "USDCASH":
            continue

        qty = _float(_first(
            row,
            "qty",
            "quantity",
            "balance",
            "amount",
            "total",
            "available",
            ("available_balance", "value"),
            ("balance", "amount"),
            ("holdings", "quantity"),
        ))
        if not qty:
            continue

        market_value = _float(_first(
            row,
            "market_value",
            "marketValue",
            "usd_value",
            "value",
            ("fiat_value", "amount"),
            ("native_balance", "amount"),
        ))
        price = _float(_first(
            row,
            "price",
            "price_usd",
            "last_price",
            "current_price",
            "spot_price",
            ("price", "amount"),
        ))
        if price is None and market_value is not None and qty:
            price = market_value / qty
        if market_value is None and price is not None:
            market_value = price * qty

        if market_value is None:
            continue

        name = _first(row, "name", "display_name", ("metadata", "name"), ("currency", "name")) or symbol
        cost_basis = _float(_first(row, "cost_basis", "costBasis", "cost"))
        unrealized_pnl = _float(_first(
            row,
            "unrealized_pnl",
            "unrealizedPnl",
            "unrealized_gain_loss",
            "gain_loss",
            "pnl",
        ))
        if cost_basis is None and unrealized_pnl is not None:
            cost_basis = market_value - unrealized_pnl
        if cost_basis is None and symbol in {"USD", "USDC"}:
            cost_basis = market_value

        records.append({
            "account_id":   account_id,
            "symbol":       symbol,
            "name":         str(name),
            "qty":          qty,
            "price":        price,
            "cost_basis":   cost_basis,
            "unrealized_pnl": unrealized_pnl,
            "market_value": market_value,
            "source_file":  None,
        })

    summary = _futures_summary(positions_resp)
    futures_usd = _float(summary.get("total_usd_balance"))
    has_usd = any(rec["symbol"] == "USD" for rec in records)
    if futures_usd and not has_usd:
        records.append({
            "account_id":   account_id,
            "symbol":       "USD",
            "name":         "Coinbase Derivatives USD",
            "qty":          futures_usd,
            "price":        1.0,
            "cost_basis":   futures_usd,
            "market_value": futures_usd,
            "source_file":  None,
        })

    return records


def normalize_futures(positions_resp: dict | list, account_id: str = "COINBASE") -> list[dict]:
    """Normalize Coinbase futures positions into P&L-valued futures rows."""
    if isinstance(positions_resp, dict):
        rows = positions_resp.get("positions")
    else:
        rows = positions_resp
    if not isinstance(rows, list):
        rows = []

    records: list[dict] = []
    position_pnl = 0.0
    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = str(_first(row, "product_id", "symbol") or "").strip().upper()
        if not symbol:
            continue

        qty = _float(_first(row, "contracts", "quantity", "qty")) or 0.0
        side = str(row.get("side") or "").upper()
        if side == "SHORT":
            qty = -abs(qty)

        pnl = _float(_first(row, "unrealized_pnl", "unrealizedPnl", "pnl")) or 0.0
        position_pnl += pnl
        price = _float(_first(row, "mark_price", "current_price", "price"))
        entry_price = _float(_first(row, "avg_entry_price", "entry_price", "average_entry_price"))
        notional = _float(_first(row, "notional_value", "notional"))
        if notional is None and entry_price is not None:
            notional = abs(qty) * entry_price
        underlying = symbol.split("-", 1)[0]

        records.append({
            "account_id":   account_id,
            "symbol":       symbol,
            "underlying":   underlying,
            "description":  f"Coinbase futures {side.lower() or 'position'}",
            "qty":          qty,
            "price":        price,
            "market_value": pnl,
            "cost_basis":   notional,
            "source_file":  None,
        })

    summary = _futures_summary(positions_resp)
    total_pnl = sum(
        value or 0.0
        for value in (
            _float(summary.get("unrealized_pnl")),
            _float(summary.get("daily_realized_pnl")),
            _float(summary.get("funding_pnl")),
        )
    )
    adjustment = total_pnl - position_pnl
    if abs(adjustment) >= 0.005:
        records.append({
            "account_id":   account_id,
            "symbol":       "COINBASE-FUTURES-PNL-ADJ",
            "underlying":   "COINBASE",
            "description":  "Coinbase futures realized/funding P&L adjustment",
            "qty":          1.0,
            "price":        adjustment,
            "market_value": adjustment,
            "cost_basis":   0.0,
            "source_file":  None,
        })

    return records


def normalize_instruments(crypto_records: list[dict], futures_records: list[dict] | None = None) -> list[dict]:
    records = []
    seen: set[tuple[str, str]] = set()
    for rec in crypto_records:
        symbol = rec["symbol"]
        key = (symbol, "crypto")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol": symbol,
            "asset_class": "crypto",
            "underlying": None,
            "name": rec.get("name"),
            "exchange": "Coinbase",
            "currency": "USD",
            "sector": None,
            "industry": None,
            "expiry": None,
            "strike": None,
            "call_put": None,
            "tick_size": None,
            "point_value": None,
            "tradable": None,
        })
    for rec in futures_records or []:
        symbol = rec["symbol"]
        key = (symbol, "future")
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "symbol": symbol,
            "asset_class": "future",
            "underlying": rec.get("underlying"),
            "name": rec.get("description") or symbol,
            "exchange": "Coinbase",
            "currency": "USD",
            "sector": None,
            "industry": None,
            "expiry": None,
            "strike": None,
            "call_put": None,
            "tick_size": None,
            "point_value": None,
            "tradable": None,
        })
    return records
