import sqlite3
import os
from datetime import datetime
import pandas as pd
from config import STATE_DB_PATH

# Derive state directory from DB path for kill/pause switch files
_STATE_DIR = os.path.dirname(STATE_DB_PATH) if STATE_DB_PATH else '.'
KILL_SWITCH_FILE = os.path.join(_STATE_DIR, 'KILL_SWITCH')
PAUSE_SWITCH_FILE = os.path.join(_STATE_DIR, 'PAUSE_SWITCH')


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


def write_control_flag(flag_name: str, value: int):
    """
    Write a control flag to DB and create/remove corresponding switch file.

    Args:
        flag_name: 'pause_requested' or 'kill_requested'
        value: 1 to activate, 0 to deactivate
    """
    if flag_name not in ('pause_requested', 'kill_requested'):
        return

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE operational_state SET {flag_name} = ?, updated_at = ? WHERE id = 1",
            (value, datetime.now().isoformat())
        )
        conn.commit()
    finally:
        conn.close()

    # Create/remove corresponding file-based switch
    if flag_name == 'pause_requested':
        switch_file = PAUSE_SWITCH_FILE
    else:
        switch_file = KILL_SWITCH_FILE

    if value == 1:
        with open(switch_file, 'w') as f:
            f.write(f"triggered from dashboard at {datetime.now().isoformat()}")
    else:
        if os.path.exists(switch_file):
            os.remove(switch_file)


def get_control_flags() -> dict:
    """Get current control flags from operational_state."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT pause_requested, kill_requested FROM operational_state WHERE id = 1")
        row = cursor.fetchone()
        if row:
            return {'pause_requested': row[0] or 0, 'kill_requested': row[1] or 0}
        return {'pause_requested': 0, 'kill_requested': 0}
    except Exception:
        return {'pause_requested': 0, 'kill_requested': 0}
    finally:
        conn.close()
