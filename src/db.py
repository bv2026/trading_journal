import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "journal.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new columns to existing tables without losing data."""
    migrations = [
        "ALTER TABLE accounts  ADD COLUMN account_group TEXT DEFAULT 'investment'",
        "ALTER TABLE accounts  ADD COLUMN price_source  TEXT DEFAULT 'live'",
        "ALTER TABLE accounts  ADD COLUMN active        INTEGER DEFAULT 1",
        "ALTER TABLE positions ADD COLUMN stored_price  REAL",
        "ALTER TABLE positions ADD COLUMN data_source   TEXT",
        "ALTER TABLE options_positions ADD COLUMN data_source TEXT",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists

    # Cash accounts table (idempotent)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cash_accounts (
            account_id TEXT PRIMARY KEY,
            name       TEXT,
            balance    REAL DEFAULT 0,
            updated_at TEXT
        )
    """)

    # Margin overrides table — persists manual margin entries across syncs
    conn.execute("""
        CREATE TABLE IF NOT EXISTS margin_overrides (
            account_id TEXT PRIMARY KEY,
            margin     REAL DEFAULT 0,
            updated_at TEXT
        )
    """)

    # Futures equity overrides — persists Schwab futures sub-account value across syncs
    conn.execute("""
        CREATE TABLE IF NOT EXISTS futures_equity_overrides (
            account_id    TEXT PRIMARY KEY,
            futures_equity REAL DEFAULT 0,
            updated_at    TEXT
        )
    """)

    # Latest account-level broker balance. This is separate from positions so
    # cash-only sub-accounts and broker-reported equity can be preserved.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_balances (
            account_id   TEXT PRIMARY KEY REFERENCES accounts(account_id),
            market_value REAL NOT NULL DEFAULT 0,
            cost_basis   REAL,
            margin       REAL NOT NULL DEFAULT 0,
            net_equity   REAL NOT NULL DEFAULT 0,
            source       TEXT,
            detail       TEXT,
            as_of        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("ALTER TABLE account_balances ADD COLUMN cost_basis REAL")
    except sqlite3.OperationalError:
        pass


def init_db():
    sql = SCHEMA_PATH.read_text()
    with get_conn() as conn:
        conn.executescript(sql)
        _migrate(conn)
        conn.commit()


def upsert_accounts(records: list[dict]):
    rows = [
        {
            "account_id":    r["account_id"],
            "broker":        r["broker"],
            "account_type":  r.get("account_type", "equity"),
            "account_group": r.get("account_group", "investment"),
            "holder":        r.get("holder"),
            "price_source":  r.get("price_source", "live"),
            "active":        r.get("active", 1),
        }
        for r in records
    ]
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO accounts
                (account_id, broker, account_type, account_group, holder, price_source, active)
            VALUES
                (:account_id, :broker, :account_type, :account_group, :holder, :price_source, :active)
            ON CONFLICT(account_id) DO UPDATE SET
                broker        = excluded.broker,
                account_type  = excluded.account_type,
                account_group = excluded.account_group,
                holder        = excluded.holder
            """,
            rows,
        )
        conn.commit()


def clear_transactions():
    with get_conn() as conn:
        conn.execute("DELETE FROM transactions")
        conn.commit()


def insert_transactions(records: list[dict]) -> int:
    """Insert records, silently skipping any whose id already exists.

    Returns the number of rows actually inserted.
    """
    if not records:
        return 0
    df = pd.DataFrame(records)
    required = ["id", "account_id", "date", "category", "subcategory",
                "amount", "currency", "symbol", "description", "data_source", "source_file"]
    for col in required:
        if col not in df.columns:
            df[col] = None
    rows = (
        df[required]
        .where(pd.notna(df[required]), None)
        .to_dict(orient="records")
    )
    with get_conn() as conn:
        cursor = conn.executemany(
            "INSERT OR IGNORE INTO transactions "
            "(id, account_id, date, category, subcategory, amount, "
            " currency, symbol, description, data_source, source_file) "
            "VALUES (:id, :account_id, :date, :category, :subcategory, :amount, "
            "        :currency, :symbol, :description, :data_source, :source_file)",
            rows,
        )
        conn.commit()
        return cursor.rowcount


def delete_by_account(account_id: str) -> None:
    """Delete all transactions for a given account (used for yearly-summary refresh)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM transactions WHERE account_id = ?", (account_id,))
        conn.commit()


def delete_positions_by_account(account_id: str) -> None:
    """Delete all equity positions for a given account (used before full re-insert)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
        conn.commit()


def insert_positions(records: list[dict]) -> int:
    """Insert or replace equity position records for an account.

    Returns number of rows written.
    """
    if not records:
        return 0
    df = pd.DataFrame(records)
    cols = ["account_id", "ticker", "name", "shares", "cost_basis", "stored_price",
            "sector", "industry", "asset_type", "iv_rank", "perf_ytd",
            "atr_pct", "data_source", "source_file"]
    for col in cols:
        if col not in df.columns:
            df[col] = None
    rows = (
        df[cols]
        .where(pd.notna(df[cols]), None)
        .to_dict(orient="records")
    )
    with get_conn() as conn:
        cursor = conn.executemany(
            "INSERT OR REPLACE INTO positions "
            "(account_id, ticker, name, shares, cost_basis, stored_price, sector, industry, "
            " asset_type, iv_rank, perf_ytd, atr_pct, data_source, source_file) "
            "VALUES (:account_id, :ticker, :name, :shares, :cost_basis, :stored_price, :sector, "
            "        :industry, :asset_type, :iv_rank, :perf_ytd, :atr_pct, :data_source, :source_file)",
            rows,
        )
        conn.commit()
        return cursor.rowcount


def load_positions_db() -> pd.DataFrame:
    """Load raw equity positions from DB (no price computation)."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT p.account_id AS Account, p.ticker AS Ticker, p.name AS Name, "
            "p.shares AS Shares, p.cost_basis AS Cost_Basis, p.stored_price AS Stored_Price, "
            "p.sector, p.industry, "
            "p.asset_type AS TYPE, p.iv_rank AS IV_Rank, p.perf_ytd AS PERF_YTD, "
            "p.atr_pct AS ATR_pct, p.source_file, "
            "a.price_source AS Price_Source, a.account_type AS Account_Type "
            "FROM positions p "
            "JOIN accounts a ON p.account_id = a.account_id "
            "WHERE COALESCE(a.active, 1) = 1",
            conn,
        )


# ── Options ──────────────────────────────────────────────────────────────────

def delete_options_by_account(account_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM options_positions WHERE account_id = ?", (account_id,))
        conn.commit()


def insert_options(records: list[dict]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    cols = ["account_id", "symbol", "underlying", "expiry", "strike",
            "call_put", "description", "qty", "price", "market_value",
            "data_source", "source_file"]
    for col in cols:
        if col not in df.columns:
            df[col] = None
    rows = (
        df[cols]
        .where(pd.notna(df[cols]), None)
        .to_dict(orient="records")
    )
    with get_conn() as conn:
        cursor = conn.executemany(
            "INSERT OR REPLACE INTO options_positions "
            "(account_id, symbol, underlying, expiry, strike, call_put, "
            " description, qty, price, market_value, data_source, source_file) "
            "VALUES (:account_id, :symbol, :underlying, :expiry, :strike, :call_put, "
            "        :description, :qty, :price, :market_value, :data_source, :source_file)",
            rows,
        )
        conn.commit()
        return cursor.rowcount


def load_options_db() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        with get_conn() as conn:
            return pd.read_sql_query(
                "SELECT account_id, symbol, underlying, expiry, strike, call_put, "
                "description, qty, price, market_value, data_source, source_file "
                "FROM options_positions o "
                "WHERE EXISTS ("
                "  SELECT 1 FROM accounts a "
                "  WHERE a.account_id = o.account_id AND COALESCE(a.active, 1) = 1"
                ")",
                conn,
            )
    except Exception:
        return pd.DataFrame()


# ── Futures ──────────────────────────────────────────────────────────────────

def delete_futures_by_account(account_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM futures_positions WHERE account_id = ?", (account_id,))
        conn.commit()


def insert_futures(records: list[dict]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    cols = ["account_id", "symbol", "underlying", "description",
            "qty", "price", "market_value", "source_file"]
    for col in cols:
        if col not in df.columns:
            df[col] = None
    rows = (
        df[cols]
        .where(pd.notna(df[cols]), None)
        .to_dict(orient="records")
    )
    with get_conn() as conn:
        cursor = conn.executemany(
            "INSERT OR REPLACE INTO futures_positions "
            "(account_id, symbol, underlying, description, qty, price, market_value, source_file) "
            "VALUES (:account_id, :symbol, :underlying, :description, "
            "        :qty, :price, :market_value, :source_file)",
            rows,
        )
        conn.commit()
        return cursor.rowcount


def load_futures_db() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        with get_conn() as conn:
            return pd.read_sql_query(
                "SELECT account_id, symbol, underlying, description, "
                "qty, price, market_value, source_file "
                "FROM futures_positions f "
                "WHERE EXISTS ("
                "  SELECT 1 FROM accounts a "
                "  WHERE a.account_id = f.account_id AND COALESCE(a.active, 1) = 1"
                ")",
                conn,
            )
    except Exception:
        return pd.DataFrame()


# ── Crypto ───────────────────────────────────────────────────────────────────

def delete_crypto_by_account(account_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM crypto_positions WHERE account_id = ?", (account_id,))
        conn.commit()


def insert_crypto(records: list[dict]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    cols = ["account_id", "symbol", "name", "qty", "price",
            "cost_basis", "market_value", "source_file"]
    for col in cols:
        if col not in df.columns:
            df[col] = None
    rows = (
        df[cols]
        .where(pd.notna(df[cols]), None)
        .to_dict(orient="records")
    )
    with get_conn() as conn:
        cursor = conn.executemany(
            "INSERT OR REPLACE INTO crypto_positions "
            "(account_id, symbol, name, qty, price, cost_basis, market_value, source_file) "
            "VALUES (:account_id, :symbol, :name, :qty, :price, "
            "        :cost_basis, :market_value, :source_file)",
            rows,
        )
        conn.commit()
        return cursor.rowcount


def load_crypto_db() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        with get_conn() as conn:
            return pd.read_sql_query(
                "SELECT account_id, symbol, name, qty, price, "
                "cost_basis, market_value, source_file "
                "FROM crypto_positions c "
                "WHERE EXISTS ("
                "  SELECT 1 FROM accounts a "
                "  WHERE a.account_id = c.account_id AND COALESCE(a.active, 1) = 1"
                ")",
                conn,
            )
    except Exception:
        return pd.DataFrame()


# ── Instruments ───────────────────────────────────────────────────────────────

def upsert_instruments(records: list[dict]) -> int:
    """Insert or replace instrument master records. Returns rows written."""
    if not records:
        return 0
    df = pd.DataFrame(records)
    cols = ["symbol", "asset_class", "underlying", "name", "exchange", "currency",
            "sector", "industry", "expiry", "strike", "call_put",
            "tick_size", "point_value", "tradable"]
    for col in cols:
        if col not in df.columns:
            df[col] = None
    rows = (
        df[cols]
        .where(pd.notna(df[cols]), None)
        .to_dict(orient="records")
    )
    with get_conn() as conn:
        cursor = conn.executemany(
            """
            INSERT INTO instruments
                (symbol, asset_class, underlying, name, exchange, currency,
                 sector, industry, expiry, strike, call_put,
                 tick_size, point_value, tradable)
            VALUES
                (:symbol, :asset_class, :underlying, :name, :exchange, :currency,
                 :sector, :industry, :expiry, :strike, :call_put,
                 :tick_size, :point_value, :tradable)
            ON CONFLICT(symbol, asset_class) DO UPDATE SET
                underlying  = COALESCE(excluded.underlying, instruments.underlying),
                name        = COALESCE(excluded.name, instruments.name),
                exchange    = COALESCE(excluded.exchange, instruments.exchange),
                currency    = COALESCE(excluded.currency, instruments.currency),
                sector      = COALESCE(excluded.sector, instruments.sector),
                industry    = COALESCE(excluded.industry, instruments.industry),
                expiry      = COALESCE(excluded.expiry, instruments.expiry),
                strike      = COALESCE(excluded.strike, instruments.strike),
                call_put    = COALESCE(excluded.call_put, instruments.call_put),
                tick_size   = COALESCE(excluded.tick_size, instruments.tick_size),
                point_value = COALESCE(excluded.point_value, instruments.point_value),
                tradable    = COALESCE(excluded.tradable, instruments.tradable),
                fetched_at  = CURRENT_TIMESTAMP
            """,
            rows,
        )
        conn.commit()
        return cursor.rowcount


def load_instruments(asset_class: str | None = None) -> pd.DataFrame:
    """Load instruments master table, optionally filtered by asset_class."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        with get_conn() as conn:
            if asset_class:
                return pd.read_sql_query(
                    "SELECT * FROM instruments WHERE asset_class = ?",
                    conn, params=(asset_class,)
                )
            return pd.read_sql_query("SELECT * FROM instruments", conn)
    except Exception:
        return pd.DataFrame()


# ── Snapshots ─────────────────────────────────────────────────────────────────

def write_portfolio_snapshot(snapshot_date: str, account_mv_map: dict[str, dict]) -> None:
    """Write or update per-account market value snapshots for a given date.

    account_mv_map: {account_id: {"market_value": float, "cost_basis": float, "margin": float}}
    """
    rows = [
        {
            "snapshot_date": snapshot_date,
            "account_id": acct,
            "market_value": vals.get("market_value", 0.0),
            "cost_basis": vals.get("cost_basis"),
            "margin": vals.get("margin", 0.0),
        }
        for acct, vals in account_mv_map.items()
    ]
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO portfolio_snapshots "
            "(snapshot_date, account_id, market_value, cost_basis, margin) "
            "VALUES (:snapshot_date, :account_id, :market_value, :cost_basis, :margin)",
            rows,
        )
        conn.commit()


def upsert_account_balances(records: list[dict]) -> int:
    """Persist latest per-account balances from live/cached broker sources."""
    if not records:
        return 0
    rows = []
    for rec in records:
        account_id = rec.get("account_id") or rec.get("Account")
        if not account_id or account_id == "TOTAL":
            continue
        market_value = float(rec.get("market_value", rec.get("Market Value", 0.0)) or 0.0)
        cost_basis_raw = rec.get("cost_basis", rec.get("Cost Basis"))
        cost_basis = None if cost_basis_raw in (None, "") else float(cost_basis_raw)
        margin = float(rec.get("margin", rec.get("Margin", 0.0)) or 0.0)
        net_equity = float(rec.get("net_equity", rec.get("Net Equity", market_value - margin)) or 0.0)
        rows.append({
            "account_id": str(account_id),
            "market_value": market_value,
            "cost_basis": cost_basis,
            "margin": margin,
            "net_equity": net_equity,
            "source": rec.get("source") or rec.get("Balance Source"),
            "detail": rec.get("detail") or rec.get("Live Status"),
        })
    if not rows:
        return 0
    with get_conn() as conn:
        cursor = conn.executemany(
            """
            INSERT INTO account_balances
                (account_id, market_value, cost_basis, margin, net_equity, source, detail, as_of)
            VALUES
                (:account_id, :market_value, :cost_basis, :margin, :net_equity, :source, :detail, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id) DO UPDATE SET
                market_value = excluded.market_value,
                cost_basis   = excluded.cost_basis,
                margin       = excluded.margin,
                net_equity   = excluded.net_equity,
                source       = excluded.source,
                detail       = excluded.detail,
                as_of        = excluded.as_of
            """,
            rows,
        )
        conn.commit()
        return cursor.rowcount


def load_account_balances() -> pd.DataFrame:
    """Load latest persisted account-level balances for active accounts."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        with get_conn() as conn:
            return pd.read_sql_query(
                """
                SELECT
                    b.account_id,
                    a.broker,
                    a.account_type,
                    b.market_value,
                    b.cost_basis,
                    b.margin,
                    b.net_equity,
                    b.source,
                    b.detail,
                    b.as_of
                FROM account_balances b
                LEFT JOIN accounts a ON a.account_id = b.account_id
                WHERE COALESCE(a.active, 1) = 1
                   OR a.account_id IS NULL
                ORDER BY b.account_id
                """,
                conn,
            )
    except Exception:
        return pd.DataFrame()


def load_snapshot_periods(account_group: str = "investment") -> pd.DataFrame:
    """Query v_snapshot_periods filtered by account_group."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        with get_conn() as conn:
            return pd.read_sql_query(
                "SELECT p.* FROM v_snapshot_periods p "
                "JOIN accounts a ON p.account_id = a.account_id "
                "WHERE a.account_group = ? AND COALESCE(a.active, 1) = 1",
                conn,
                params=(account_group,),
            )
    except Exception:
        return pd.DataFrame()


# ── Transactions ──────────────────────────────────────────────────────────────

def load_transactions() -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT t.*, a.broker FROM transactions t "
            "JOIN accounts a ON t.account_id = a.account_id "
            "WHERE COALESCE(a.active, 1) = 1",
            conn,
        )
    df["date"] = pd.to_datetime(df["date"])
    return df


# ── Cash accounts ─────────────────────────────────────────────────────────────

def upsert_cash_balance(balance: float,
                        account_id: str = "CASH",
                        name: str = "Cash & Savings") -> None:
    """Set (or update) the combined cash account balance."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO cash_accounts (account_id, name, balance, updated_at)
            VALUES (?, ?, ?, date('now'))
            ON CONFLICT(account_id) DO UPDATE SET
                balance    = excluded.balance,
                updated_at = excluded.updated_at
            """,
            (account_id, name, balance),
        )
        conn.commit()


def get_cash_balance(account_id: str = "CASH") -> float:
    """Return stored cash balance, or 0.0 if not set."""
    if not DB_PATH.exists():
        return 0.0
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT balance FROM cash_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


# ── Margin overrides ──────────────────────────────────────────────────────────

def set_account_price_source(account_id: str, price_source: str) -> None:
    """Set one account's price source without touching other settings."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET price_source=? WHERE account_id=?",
            (price_source, account_id),
        )
        conn.commit()


def upsert_margin_override(account_id: str, margin: float) -> None:
    """Set (or update) a persistent margin override for an account.

    When set, write_tradier / write_schwab will use this value instead of
    computing margin from API data, so the override survives re-syncs.
    Pass margin=0 to clear the override (computed margin will resume).
    """
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO margin_overrides (account_id, margin, updated_at)
            VALUES (?, ?, date('now'))
            ON CONFLICT(account_id) DO UPDATE SET
                margin     = excluded.margin,
                updated_at = excluded.updated_at
            """,
            (account_id, margin),
        )
        conn.commit()


def get_margin_override(account_id: str) -> float | None:
    """Return the stored margin override for an account, or None if not set.

    Returns None (not 0) when no override exists so callers can distinguish
    "not set" from "explicitly set to 0".
    """
    if not DB_PATH.exists():
        return None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT margin FROM margin_overrides WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        return float(row[0])
    except Exception:
        return None


def upsert_futures_equity_override(account_id: str, futures_equity: float) -> None:
    """Persist the futures sub-account equity for an account (e.g. Schwab)."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO futures_equity_overrides (account_id, futures_equity, updated_at)
            VALUES (?, ?, date('now'))
            ON CONFLICT(account_id) DO UPDATE SET
                futures_equity = excluded.futures_equity,
                updated_at     = excluded.updated_at
            """,
            (account_id, futures_equity),
        )
        conn.commit()


def get_futures_equity_override(account_id: str) -> float | None:
    """Return stored futures equity override, or None if not set."""
    if not DB_PATH.exists():
        return None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT futures_equity FROM futures_equity_overrides WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return float(row[0]) if row is not None else None
    except Exception:
        return None


def load_account_settings() -> pd.DataFrame:
    """Load all accounts with their current margin override and futures equity override."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        with get_conn() as conn:
            return pd.read_sql_query(
                """
                SELECT
                    a.account_id,
                    a.broker,
                    a.account_type,
                    a.account_group,
                    a.holder,
                    a.price_source,
                    a.active,
                    mo.margin          AS margin_override,
                    fe.futures_equity  AS futures_equity_override
                FROM accounts a
                LEFT JOIN margin_overrides       mo ON mo.account_id = a.account_id
                LEFT JOIN futures_equity_overrides fe ON fe.account_id = a.account_id
                ORDER BY a.account_id
                """,
                conn,
            )
    except Exception:
        return pd.DataFrame()


def save_account_settings(rows: list[dict]) -> None:
    """Bulk-save account active/price_source and margin/futures overrides.

    Each dict must have: account_id, active, price_source,
    margin_override (float|None), futures_equity_override (float|None).
    """
    with get_conn() as conn:
        for r in rows:
            conn.execute(
                "UPDATE accounts SET active=?, price_source=? WHERE account_id=?",
                (r["active"], r["price_source"], r["account_id"]),
            )
            if r.get("margin_override") is not None:
                conn.execute(
                    """INSERT INTO margin_overrides (account_id, margin, updated_at)
                       VALUES (?, ?, date('now'))
                       ON CONFLICT(account_id) DO UPDATE SET
                           margin=excluded.margin, updated_at=excluded.updated_at""",
                    (r["account_id"], r["margin_override"]),
                )
            else:
                conn.execute(
                    "DELETE FROM margin_overrides WHERE account_id=?",
                    (r["account_id"],),
                )
            if r.get("futures_equity_override") is not None:
                conn.execute(
                    """INSERT INTO futures_equity_overrides (account_id, futures_equity, updated_at)
                       VALUES (?, ?, date('now'))
                       ON CONFLICT(account_id) DO UPDATE SET
                           futures_equity=excluded.futures_equity,
                           updated_at=excluded.updated_at""",
                    (r["account_id"], r["futures_equity_override"]),
                )
            else:
                conn.execute(
                    "DELETE FROM futures_equity_overrides WHERE account_id=?",
                    (r["account_id"],),
                )
        conn.commit()


def get_accounts_by_type(account_type: str) -> list[str]:
    """Return account_ids with the given account_type (e.g. 'crypto', 'futures')."""
    if not DB_PATH.exists():
        return []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT account_id FROM accounts "
                "WHERE account_type = ? AND COALESCE(active, 1) = 1",
                (account_type,),
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
