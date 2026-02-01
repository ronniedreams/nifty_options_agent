import sqlite3
import pandas as pd
from config import STATE_DB_PATH

def get_connection():
    return sqlite3.connect(
        STATE_DB_PATH,
        check_same_thread=False
    )

def read_df(query, params=None):
    conn = get_connection()
    try:
        return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()
