import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "journal.db"
SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    sql = SCHEMA_PATH.read_text()
    with get_conn() as conn:
        conn.executescript(sql)


def upsert_accounts(records: list[dict]):
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO accounts(account_id, broker, account_type, holder) "
            "VALUES (:account_id, :broker, :account_type, :holder)",
            records,
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
    """Delete all positions for a given account (used before full re-insert)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
        conn.commit()


def insert_positions(records: list[dict]) -> int:
    """Insert or replace position records for an account.

    Uses INSERT OR REPLACE so that re-running on the same CSV is idempotent.
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
    """Load raw positions from DB (no price computation)."""
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


def load_transactions() -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT t.*, a.broker FROM transactions t "
            "JOIN accounts a ON t.account_id = a.account_id",
            conn,
        )
    df["date"] = pd.to_datetime(df["date"])
    return df
