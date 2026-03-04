"""
State Manager for V3 Live Trading — SQLite Persistence

Persists V3 strategy state for crash recovery:
- Pyramid positions (CE/PE sequences with shared SL)
- Daily state (cumulative R, trades count, bar index)
- Trade log (entry/exit records with R-multiples)
- Order log (market orders, SL orders)

Uses WAL mode for concurrent access. Same patterns as baseline state_manager.py.
"""

import json
import logging
import sqlite3
from datetime import datetime, date
from functools import wraps
from typing import Dict, List, Optional

import pytz

from .config import V3_STATE_DB_PATH

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


def atomic_transaction(func):
    """Decorator for atomic database transactions."""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            result = func(self, *args, **kwargs)
            self.conn.commit()
            return result
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                logger.warning(f"Database locked in {func.__name__}, retrying once...")
                import time
                time.sleep(0.1)
                try:
                    self.conn.rollback()
                    self.conn.execute("BEGIN IMMEDIATE")
                    result = func(self, *args, **kwargs)
                    self.conn.commit()
                    return result
                except Exception as retry_error:
                    self.conn.rollback()
                    logger.error(f"Retry failed in {func.__name__}: {retry_error}")
                    raise
            else:
                self.conn.rollback()
                logger.error(f"Database error in {func.__name__}: {e}")
                raise
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Transaction failed in {func.__name__}: {e}", exc_info=True)
            raise
    return wrapper


class StateManagerV3:
    """SQLite state manager for V3 RL agent."""

    def __init__(self, db_path: str = V3_STATE_DB_PATH):
        self.db_path = db_path
        self.conn = None
        self._init_database()
        logger.info(f"[V3-STATE] Initialized DB: {db_path}")

    def _init_database(self):
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")

        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS v3_daily_state (
                date TEXT PRIMARY KEY,
                cumulative_R REAL DEFAULT 0.0,
                trades_today INTEGER DEFAULT 0,
                bar_idx INTEGER DEFAULT 0,
                target_R REAL DEFAULT 5.0,
                stop_R REAL DEFAULT -5.0,
                session_stopped INTEGER DEFAULT 0,
                pyramid_state TEXT DEFAULT '{}',
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS v3_trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                option_type TEXT NOT NULL,
                action TEXT NOT NULL,
                entry_price REAL,
                exit_price REAL,
                quantity INTEGER,
                lots INTEGER,
                sl_points REAL,
                realized_R REAL,
                pnl REAL,
                entry_time TEXT,
                exit_time TEXT,
                exit_reason TEXT,
                sequence_position INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS v3_order_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                order_id TEXT,
                symbol TEXT NOT NULL,
                order_type TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL,
                trigger_price REAL,
                quantity INTEGER,
                status TEXT DEFAULT 'PENDING',
                broker_status TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS v3_daily_summary (
                date TEXT PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                cumulative_R REAL DEFAULT 0.0,
                max_drawdown_R REAL DEFAULT 0.0,
                pnl REAL DEFAULT 0.0,
                session_stopped INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_v3_trade_log_date ON v3_trade_log(date);
            CREATE INDEX IF NOT EXISTS idx_v3_order_log_date ON v3_order_log(date);
        """)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Daily state
    # ------------------------------------------------------------------

    @atomic_transaction
    def save_daily_state(self, cumulative_R: float, trades_today: int,
                         bar_idx: int, target_R: float, stop_R: float,
                         session_stopped: bool, pyramid_state: dict):
        today = datetime.now(IST).date().isoformat()
        now_str = datetime.now(IST).isoformat()
        pyramid_json = json.dumps(pyramid_state)

        self.conn.execute("""
            INSERT INTO v3_daily_state (date, cumulative_R, trades_today, bar_idx,
                                        target_R, stop_R, session_stopped,
                                        pyramid_state, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                cumulative_R=excluded.cumulative_R,
                trades_today=excluded.trades_today,
                bar_idx=excluded.bar_idx,
                target_R=excluded.target_R,
                stop_R=excluded.stop_R,
                session_stopped=excluded.session_stopped,
                pyramid_state=excluded.pyramid_state,
                updated_at=excluded.updated_at
        """, (today, cumulative_R, trades_today, bar_idx,
              target_R, stop_R, int(session_stopped), pyramid_json, now_str))

    def load_daily_state(self) -> Optional[dict]:
        """Load today's state for crash recovery."""
        today = datetime.now(IST).date().isoformat()
        cursor = self.conn.execute(
            "SELECT * FROM v3_daily_state WHERE date = ?", (today,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in cursor.description]
        state = dict(zip(cols, row))
        state['pyramid_state'] = json.loads(state.get('pyramid_state', '{}'))
        return state

    # ------------------------------------------------------------------
    # Trade log
    # ------------------------------------------------------------------

    @atomic_transaction
    def log_trade(self, symbol: str, option_type: str, action: str,
                  entry_price: float, exit_price: float, quantity: int,
                  lots: int, sl_points: float, realized_R: float,
                  pnl: float, entry_time: str, exit_time: str,
                  exit_reason: str, sequence_position: int = 0):
        today = datetime.now(IST).date().isoformat()
        self.conn.execute("""
            INSERT INTO v3_trade_log (date, symbol, option_type, action,
                entry_price, exit_price, quantity, lots, sl_points,
                realized_R, pnl, entry_time, exit_time, exit_reason,
                sequence_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (today, symbol, option_type, action, entry_price, exit_price,
              quantity, lots, sl_points, realized_R, pnl, entry_time,
              exit_time, exit_reason, sequence_position))

    def get_trades_today(self) -> List[dict]:
        today = datetime.now(IST).date().isoformat()
        cursor = self.conn.execute(
            "SELECT * FROM v3_trade_log WHERE date = ? ORDER BY id", (today,)
        )
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Order log
    # ------------------------------------------------------------------

    @atomic_transaction
    def log_order(self, order_id: str, symbol: str, order_type: str,
                  action: str, price: float, trigger_price: float,
                  quantity: int, status: str = 'PENDING'):
        today = datetime.now(IST).date().isoformat()
        now_str = datetime.now(IST).isoformat()
        self.conn.execute("""
            INSERT INTO v3_order_log (date, order_id, symbol, order_type,
                action, price, trigger_price, quantity, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (today, order_id, symbol, order_type, action, price,
              trigger_price, quantity, status, now_str))

    @atomic_transaction
    def update_order_status(self, order_id: str, status: str,
                            broker_status: str = None):
        now_str = datetime.now(IST).isoformat()
        self.conn.execute("""
            UPDATE v3_order_log SET status = ?, broker_status = ?, updated_at = ?
            WHERE order_id = ?
        """, (status, broker_status, now_str, order_id))

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    @atomic_transaction
    def save_daily_summary(self, total_trades: int, winning_trades: int,
                           losing_trades: int, cumulative_R: float,
                           max_drawdown_R: float, pnl: float,
                           session_stopped: bool):
        today = datetime.now(IST).date().isoformat()
        self.conn.execute("""
            INSERT INTO v3_daily_summary (date, total_trades, winning_trades,
                losing_trades, cumulative_R, max_drawdown_R, pnl, session_stopped)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_trades=excluded.total_trades,
                winning_trades=excluded.winning_trades,
                losing_trades=excluded.losing_trades,
                cumulative_R=excluded.cumulative_R,
                max_drawdown_R=excluded.max_drawdown_R,
                pnl=excluded.pnl,
                session_stopped=excluded.session_stopped
        """, (today, total_trades, winning_trades, losing_trades,
              cumulative_R, max_drawdown_R, pnl, int(session_stopped)))

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("[V3-STATE] Database connection closed")
