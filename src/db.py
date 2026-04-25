import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "journal.db"
SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new columns to existing tables without losing data."""
    migrations = [
        "ALTER TABLE accounts ADD COLUMN account_group TEXT DEFAULT 'investment'",
        "ALTER TABLE accounts ADD COLUMN price_source  TEXT DEFAULT 'live'",
        "ALTER TABLE accounts ADD COLUMN active        INTEGER DEFAULT 1",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists


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
            "INSERT OR REPLACE INTO accounts "
            "(account_id, broker, account_type, account_group, holder, price_source, active) "
            "VALUES (:account_id, :broker, :account_type, :account_group, :holder, :price_source, :active)",
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
                "amount", "currency", "symbol", "description", "source_file"]
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
            " currency, symbol, description, source_file) "
            "VALUES (:id, :account_id, :date, :category, :subcategory, :amount, "
            "        :currency, :symbol, :description, :source_file)",
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
    cols = ["account_id", "ticker", "name", "shares", "cost_basis",
            "sector", "industry", "asset_type", "iv_rank", "perf_ytd",
            "atr_pct", "source_file"]
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
            "(account_id, ticker, name, shares, cost_basis, sector, industry, "
            " asset_type, iv_rank, perf_ytd, atr_pct, source_file) "
            "VALUES (:account_id, :ticker, :name, :shares, :cost_basis, :sector, "
            "        :industry, :asset_type, :iv_rank, :perf_ytd, :atr_pct, :source_file)",
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
            "SELECT account_id AS Account, ticker AS Ticker, name AS Name, "
            "shares AS Shares, cost_basis AS Cost_Basis, sector, industry, "
            "asset_type AS TYPE, iv_rank AS IV_Rank, perf_ytd AS PERF_YTD, "
            "atr_pct AS ATR_pct, source_file "
            "FROM positions",
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
            "call_put", "description", "qty", "price", "market_value", "source_file"]
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
            " description, qty, price, market_value, source_file) "
            "VALUES (:account_id, :symbol, :underlying, :expiry, :strike, :call_put, "
            "        :description, :qty, :price, :market_value, :source_file)",
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
                "description, qty, price, market_value, source_file "
                "FROM options_positions",
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
                "FROM futures_positions",
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
                "FROM crypto_positions",
                conn,
            )
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


def load_snapshot_periods(account_group: str = "investment") -> pd.DataFrame:
    """Query v_snapshot_periods filtered by account_group."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        with get_conn() as conn:
            return pd.read_sql_query(
                "SELECT p.* FROM v_snapshot_periods p "
                "JOIN accounts a ON p.account_id = a.account_id "
                "WHERE a.account_group = ?",
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
            "JOIN accounts a ON t.account_id = a.account_id",
            conn,
        )
    df["date"] = pd.to_datetime(df["date"])
    return df
