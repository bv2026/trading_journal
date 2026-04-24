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


def insert_transactions(records: list[dict]):
    if not records:
        return
    df = pd.DataFrame(records)
    required = ["id", "account_id", "date", "category", "subcategory",
                "amount", "currency", "symbol", "description", "source_file"]
    for col in required:
        if col not in df.columns:
            df[col] = None
    with get_conn() as conn:
        df[required].to_sql("transactions", conn, if_exists="append", index=False)


def load_transactions() -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT t.*, a.broker FROM transactions t "
            "JOIN accounts a ON t.account_id = a.account_id",
            conn,
        )
    df["date"] = pd.to_datetime(df["date"])
    return df
