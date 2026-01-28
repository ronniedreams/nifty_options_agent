"""
State Manager for Crash Recovery

Persists strategy state to SQLite database for recovery after crashes/restarts.

Persisted State:
- Open positions
- Pending limit orders
- Active SL orders
- Cumulative R
- Daily exit status
- Trade log

This allows the strategy to resume from where it left off if it crashes.

🔴 PHASE 1 IMPROVEMENTS:
- WAL mode enabled for concurrent access
- Atomic transactions for critical multi-table writes
- Busy timeout for lock handling
"""

import logging
import sqlite3
import json
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from functools import wraps
import pytz

try:
    from .config import STATE_DB_PATH, TRADES_LOG_CSV, DAILY_SUMMARY_CSV
except ModuleNotFoundError:
    from config import STATE_DB_PATH, TRADES_LOG_CSV, DAILY_SUMMARY_CSV

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


def atomic_transaction(func):
    """
    Decorator for atomic database transactions
    
    Ensures all-or-nothing writes with automatic rollback on error.
    Critical for position+order saves where consistency is essential.
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            # Explicit BEGIN for immediate write lock
            self.conn.execute("BEGIN IMMEDIATE")
            
            result = func(self, *args, **kwargs)
            
            # Commit on success
            self.conn.commit()
            
            return result
            
        except sqlite3.OperationalError as e:
            # Database locked - retry once after brief wait
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
            # Rollback on any error
            self.conn.rollback()
            logger.error(f"Transaction failed in {func.__name__}: {e}", exc_info=True)
            raise
    
    return wrapper


class StateManager:
    """
    Manage persistent state in SQLite
    """
    
    def __init__(self, db_path: str = STATE_DB_PATH):
        self.db_path = db_path
        self.conn = None
        self._init_database()
        logger.info(f"StateManager initialized with DB: {db_path}")
    
    def _init_database(self):
        """Initialize database schema with WAL mode for production safety"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        # 🔴 PHASE 1: Enable WAL mode for concurrent reads/writes
        logger.info("Enabling SQLite WAL mode for production safety...")
        self.conn.execute("PRAGMA journal_mode=WAL;")
        
        # Increase busy timeout to 5 seconds (handle concurrent access)
        self.conn.execute("PRAGMA busy_timeout=5000;")
        
        # Set IMMEDIATE isolation for writes (acquire write lock immediately)
        self.conn.isolation_level = 'IMMEDIATE'
        
        logger.info("SQLite WAL mode enabled successfully")
        
        cursor = self.conn.cursor()
        
        # Positions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                strike INTEGER,
                option_type TEXT,
                entry_price REAL,
                sl_price REAL,
                quantity INTEGER,
                lots INTEGER,
                actual_R REAL,
                entry_time TEXT,
                current_price REAL,
                unrealized_pnl REAL,
                unrealized_R REAL,
                exit_price REAL,
                exit_time TEXT,
                exit_reason TEXT,
                realized_pnl REAL,
                realized_R REAL,
                is_closed INTEGER,
                trade_date TEXT
            )
        ''')
        
        # Pending orders table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_orders (
                order_id TEXT PRIMARY KEY,
                symbol TEXT,
                order_type TEXT,
                limit_price REAL,
                trigger_price REAL,
                quantity INTEGER,
                status TEXT,
                placed_at TEXT,
                candidate_info TEXT
            )
        ''')
        
        # Daily state table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_state (
                trade_date TEXT PRIMARY KEY,
                cumulative_R REAL,
                daily_exit_triggered INTEGER,
                daily_exit_reason TEXT,
                total_pnl REAL,
                total_positions INTEGER,
                expiry TEXT,
                updated_at TEXT
            )
        ''')
        
        # Trade log table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT,
                symbol TEXT,
                strike INTEGER,
                option_type TEXT,
                entry_time TEXT,
                entry_price REAL,
                sl_price REAL,
                quantity INTEGER,
                lots INTEGER,
                actual_R REAL,
                exit_time TEXT,
                exit_price REAL,
                exit_reason TEXT,
                realized_pnl REAL,
                realized_R REAL,
                duration_minutes REAL
            )
        ''')
        
        # Swing candidates table (for dashboard monitoring)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS swing_candidates (
                symbol TEXT PRIMARY KEY,
                swing_low REAL,
                vwap_at_swing REAL,
                timestamp TEXT,
                option_type TEXT,
                active INTEGER DEFAULT 1
            )
        ''')
        
        # Best strikes table (for dashboard monitoring)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS best_strikes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                option_type TEXT,
                symbol TEXT,
                entry_price REAL,
                sl_price REAL,
                sl_points REAL,
                vwap_premium_percent REAL,
                swing_timestamp TEXT,
                updated_at TEXT,
                is_current INTEGER DEFAULT 1
            )
        ''')
        
        # Order triggers table (for dashboard monitoring)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                option_type TEXT,
                action TEXT,
                symbol TEXT,
                current_price REAL,
                swing_low REAL,
                reason TEXT
            )
        ''')
        
        # Swing history table (for dashboard)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS swing_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                swing_low REAL,
                break_price REAL,
                break_time TEXT,
                vwap_premium REAL,
                sl_percent REAL,
                passed_filters INTEGER
            )
        ''')
        
        # ALL SWINGS LOG - for verification/analysis
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS all_swings_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                swing_type TEXT,
                swing_price REAL,
                swing_time TEXT,
                vwap REAL,
                bar_index INTEGER,
                detected_at TEXT,
                UNIQUE(symbol, swing_time, swing_type)
            )
        ''')
        
        # Bars table (for price data)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT,
                timestamp TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (symbol, timestamp)
            )
        ''')
        
        # Filter rejections table (for historical diagnostics)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS filter_rejections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                option_type TEXT,
                swing_low REAL,
                current_price REAL,
                vwap_at_swing REAL,
                vwap_premium_percent REAL,
                sl_percent REAL,
                rejection_reason TEXT
            )
        ''')
        
        self.conn.commit()
        logger.info("Database schema initialized")

        # Run migrations
        self._run_migrations()

    def _run_migrations(self):
        """Apply database migrations for schema changes"""
        cursor = self.conn.cursor()

        # Migration 1: Add unique constraint to all_swings_log
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='all_swings_log'")
        table_sql = cursor.fetchone()

        if table_sql and 'UNIQUE' not in table_sql[0]:
            logger.info("Migrating all_swings_log table to add unique constraint...")

            # Create new table with constraint
            cursor.execute('''
                CREATE TABLE all_swings_log_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    swing_type TEXT,
                    swing_price REAL,
                    swing_time TEXT,
                    vwap REAL,
                    bar_index INTEGER,
                    detected_at TEXT,
                    UNIQUE(symbol, swing_time, swing_type)
                )
            ''')

            # Copy unique data from old table (keep earliest detected_at for duplicates)
            cursor.execute('''
                INSERT INTO all_swings_log_new
                (symbol, swing_type, swing_price, swing_time, vwap, bar_index, detected_at)
                SELECT symbol, swing_type, swing_price, swing_time, vwap, bar_index, MIN(detected_at)
                FROM all_swings_log
                GROUP BY symbol, swing_time, swing_type
            ''')

            # Get count of original vs migrated rows
            cursor.execute("SELECT COUNT(*) FROM all_swings_log")
            old_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM all_swings_log_new")
            new_count = cursor.fetchone()[0]

            # Drop old table
            cursor.execute("DROP TABLE all_swings_log")

            # Rename new table
            cursor.execute("ALTER TABLE all_swings_log_new RENAME TO all_swings_log")

            self.conn.commit()

            logger.info(f"Migration complete: Removed {old_count - new_count} duplicate swings "
                       f"(kept {new_count} unique swings)")
        else:
            logger.debug("all_swings_log table already has unique constraint")

        # Migration 2: Add expiry column to daily_state
        cursor.execute("PRAGMA table_info(daily_state)")
        columns = {col[1]: col for col in cursor.fetchall()}

        if 'expiry' not in columns:
            logger.info("Migrating daily_state table to add expiry column...")
            cursor.execute("ALTER TABLE daily_state ADD COLUMN expiry TEXT")
            self.conn.commit()
            logger.info("Migration complete: Added expiry column to daily_state")
        else:
            logger.debug("daily_state table already has expiry column")

        # Migration 3: Add error_notifications_log table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='error_notifications_log'")
        if not cursor.fetchone():
            logger.info("Creating error_notifications_log table...")
            cursor.execute('''
                CREATE TABLE error_notifications_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_type TEXT NOT NULL,
                    error_message TEXT,
                    first_occurrence TIMESTAMP,
                    last_occurrence TIMESTAMP,
                    occurrence_count INTEGER DEFAULT 1,
                    last_notification_sent TIMESTAMP,
                    notification_count INTEGER DEFAULT 0,
                    is_resolved BOOLEAN DEFAULT 0,
                    resolved_at TIMESTAMP
                )
            ''')
            self.conn.commit()
            logger.info("Migration complete: Created error_notifications_log table")
        else:
            logger.debug("error_notifications_log table already exists")

        # Migration 4: Add operational_state table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='operational_state'")
        if not cursor.fetchone():
            logger.info("Creating operational_state table...")
            cursor.execute('''
                CREATE TABLE operational_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_state TEXT NOT NULL DEFAULT 'STARTING',
                    state_entered_at TIMESTAMP,
                    last_check_at TIMESTAMP,
                    error_reason TEXT,
                    updated_at TIMESTAMP
                )
            ''')

            # Initialize with STARTING state
            cursor.execute('''
                INSERT INTO operational_state (id, current_state, state_entered_at, updated_at)
                VALUES (1, 'STARTING', ?, ?)
            ''', (datetime.now(IST).isoformat(), datetime.now(IST).isoformat()))

            self.conn.commit()
            logger.info("Migration complete: Created operational_state table")
        else:
            logger.debug("operational_state table already exists")

    @atomic_transaction
    def save_positions(self, positions: List[Dict]):
        """Save all positions (open + closed) to database"""
        cursor = self.conn.cursor()
        
        for pos in positions:
            cursor.execute('''
                INSERT OR REPLACE INTO positions VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            ''', (
                pos['symbol'],
                pos['strike'],
                pos['option_type'],
                pos['entry_price'],
                pos['sl_price'],
                pos['quantity'],
                pos['lots'],
                pos['actual_R'],
                pos['entry_time'],
                pos['current_price'],
                pos['unrealized_pnl'],
                pos['unrealized_R'],
                pos['exit_price'],
                pos['exit_time'],
                pos['exit_reason'],
                pos['realized_pnl'],
                pos['realized_R'],
                1 if pos['is_closed'] else 0,
                datetime.now(IST).date().isoformat()
            ))
        
        # Commit handled by @atomic_transaction decorator
    
    def load_open_positions(self) -> List[Dict]:
        """Load open positions from database"""
        cursor = self.conn.cursor()
        
        cursor.execute('''
            SELECT * FROM positions
            WHERE is_closed = 0
            AND trade_date = ?
        ''', (datetime.now(IST).date().isoformat(),))
        
        rows = cursor.fetchall()
        
        positions = []
        for row in rows:
            positions.append(dict(row))
        
        logger.info(f"Loaded {len(positions)} open positions from DB")
        return positions
    
    @atomic_transaction
    def save_orders(self, pending_limit: Dict, active_sl: Dict):
        """Save pending orders to database (atomic: all-or-nothing)"""
        cursor = self.conn.cursor()
        
        # Clear existing orders
        cursor.execute('DELETE FROM pending_orders')
        
        # Save limit orders (now keyed by option_type, not symbol)
        for option_type, order_info in pending_limit.items():
            symbol = order_info.get('symbol')
            if not symbol:
                continue  # Skip if no symbol (shouldn't happen)

            # Convert candidate_info timestamps to ISO strings for JSON serialization
            candidate_info = order_info.get('candidate_info', {})
            if candidate_info:
                candidate_info_clean = {}
                for key, value in candidate_info.items():
                    if hasattr(value, 'isoformat'):
                        candidate_info_clean[key] = value.isoformat()
                    else:
                        candidate_info_clean[key] = value
            else:
                candidate_info_clean = {}

            cursor.execute('''
                INSERT INTO pending_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                order_info['order_id'],
                symbol,
                'LIMIT',
                order_info['limit_price'],
                None,
                order_info['quantity'],
                order_info['status'],
                order_info['placed_at'].isoformat(),
                json.dumps(candidate_info_clean)
            ))
        
        # Save SL orders
        for symbol, order_info in active_sl.items():
            cursor.execute('''
                INSERT INTO pending_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                order_info['order_id'],
                symbol,
                'SL',
                order_info['limit_price'],
                order_info['trigger_price'],
                order_info['quantity'],
                'active',
                order_info['placed_at'].isoformat(),
                None
            ))
        
        # Commit handled by @atomic_transaction decorator
    
    @atomic_transaction
    def save_daily_state(self, state: Dict):
        """Save daily state (cumulative R, exit status, etc.) with atomic transaction"""
        cursor = self.conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO daily_state VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now(IST).date().isoformat(),
            state.get('cumulative_R', 0),
            1 if state.get('daily_exit_triggered', False) else 0,
            state.get('daily_exit_reason'),
            state.get('total_pnl', 0),
            state.get('total_positions', 0),
            datetime.now(IST).isoformat(),
            state.get('expiry')
        ))

        # Commit handled by @atomic_transaction decorator
    
    def load_daily_state(self) -> Optional[Dict]:
        """Load daily state from database"""
        cursor = self.conn.cursor()
        
        cursor.execute('''
            SELECT * FROM daily_state
            WHERE trade_date = ?
        ''', (datetime.now(IST).date().isoformat(),))
        
        row = cursor.fetchone()
        
        if row:
            return dict(row)
        return None
    
    def log_trade(self, position_dict: Dict):
        """Log completed trade to database and CSV"""
        if not position_dict['is_closed']:
            return
        
        # Calculate duration
        entry_time = datetime.fromisoformat(position_dict['entry_time'])
        exit_time = datetime.fromisoformat(position_dict['exit_time'])
        duration_minutes = (exit_time - entry_time).total_seconds() / 60
        
        cursor = self.conn.cursor()
        
        cursor.execute('''
            INSERT INTO trade_log VALUES (
                NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        ''', (
            datetime.now(IST).date().isoformat(),
            position_dict['symbol'],
            position_dict['strike'],
            position_dict['option_type'],
            position_dict['entry_time'],
            position_dict['entry_price'],
            position_dict['sl_price'],
            position_dict['quantity'],
            position_dict['lots'],
            position_dict['actual_R'],
            position_dict['exit_time'],
            position_dict['exit_price'],
            position_dict['exit_reason'],
            position_dict['realized_pnl'],
            position_dict['realized_R'],
            duration_minutes
        ))
        
        self.conn.commit()
        
        # Also append to CSV
        self._append_trade_to_csv(position_dict, duration_minutes)
    
    def _append_trade_to_csv(self, position_dict: Dict, duration_minutes: float):
        """Append trade to CSV log file"""
        import csv
        import os
        
        os.makedirs(os.path.dirname(TRADES_LOG_CSV), exist_ok=True)
        
        file_exists = os.path.exists(TRADES_LOG_CSV)
        
        with open(TRADES_LOG_CSV, 'a', newline='') as f:
            writer = csv.writer(f)
            
            if not file_exists:
                # Write header
                writer.writerow([
                    'trade_date', 'symbol', 'strike', 'option_type',
                    'entry_time', 'entry_price', 'sl_price',
                    'quantity', 'lots', 'actual_R',
                    'exit_time', 'exit_price', 'exit_reason',
                    'realized_pnl', 'realized_R', 'duration_minutes'
                ])
            
            writer.writerow([
                datetime.now(IST).date().isoformat(),
                position_dict['symbol'],
                position_dict['strike'],
                position_dict['option_type'],
                position_dict['entry_time'],
                position_dict['entry_price'],
                position_dict['sl_price'],
                position_dict['quantity'],
                position_dict['lots'],
                position_dict['actual_R'],
                position_dict['exit_time'],
                position_dict['exit_price'],
                position_dict['exit_reason'],
                position_dict['realized_pnl'],
                position_dict['realized_R'],
                duration_minutes
            ])
    
    def save_daily_summary(self, summary: Dict):
        """Save daily summary to CSV"""
        import csv
        import os
        
        os.makedirs(os.path.dirname(DAILY_SUMMARY_CSV), exist_ok=True)
        
        file_exists = os.path.exists(DAILY_SUMMARY_CSV)
        
        with open(DAILY_SUMMARY_CSV, 'a', newline='') as f:
            writer = csv.writer(f)
            
            if not file_exists:
                writer.writerow([
                    'trade_date', 'cumulative_R', 'total_pnl',
                    'closed_positions', 'daily_exit_triggered',
                    'daily_exit_reason'
                ])
            
            writer.writerow([
                datetime.now(IST).date().isoformat(),
                summary.get('cumulative_R', 0),
                summary.get('total_pnl', 0),
                summary.get('closed_positions_today', 0),
                summary.get('daily_exit_triggered', False),
                summary.get('daily_exit_reason', '')
            ])
    
    def cleanup_old_data(self, days_to_keep: int = 30):
        """Delete data older than N days"""
        cutoff_date = (datetime.now(IST).date() - timedelta(days=days_to_keep)).isoformat()
        
        cursor = self.conn.cursor()
        
        cursor.execute('DELETE FROM positions WHERE trade_date < ?', (cutoff_date,))
        cursor.execute('DELETE FROM daily_state WHERE trade_date < ?', (cutoff_date,))
        cursor.execute('DELETE FROM trade_log WHERE trade_date < ?', (cutoff_date,))
        
        self.conn.commit()
        
        logger.info(f"Cleaned up data older than {days_to_keep} days")
    
    def save_swing_candidates(self, candidates: Dict):
        """Save current swing candidates (for dashboard)"""
        cursor = self.conn.cursor()
        
        # Clear old candidates
        cursor.execute('DELETE FROM swing_candidates')
        
        # Save current candidates
        for symbol, candidate in candidates.items():
            # Convert timestamp to ISO string if it's a pandas Timestamp
            timestamp = candidate['timestamp']
            if hasattr(timestamp, 'isoformat'):
                timestamp_str = timestamp.isoformat()
            else:
                timestamp_str = str(timestamp)
            
            cursor.execute('''
                INSERT INTO swing_candidates VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                candidate['price'],  # swing low price
                candidate['vwap'],   # vwap at swing
                timestamp_str,       # swing timestamp (converted to string)
                candidate['option_type'],
                1  # active
            ))
        
        self.conn.commit()
    
    def log_swing_detection(self, symbol: str, swing_type: str, swing_price: float,
                           swing_time: datetime, vwap: float, bar_index: int):
        """
        Log ALL swing detections for analysis/verification
        
        This logs every swing (HIGH and LOW) as it's detected, even if it gets
        replaced later. Used for verifying the watch-based detection logic.
        """
        cursor = self.conn.cursor()
        
        cursor.execute('''
            INSERT INTO all_swings_log 
            (symbol, swing_type, swing_price, swing_time, vwap, bar_index, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            symbol,
            swing_type,  # 'Low' or 'High'
            swing_price,
            swing_time.isoformat() if hasattr(swing_time, 'isoformat') else str(swing_time),
            vwap,
            bar_index,
            datetime.now(IST).isoformat()
        ))
        
        self.conn.commit()
        logger.debug(f"Logged swing detection: {symbol} {swing_type} @ {swing_price:.2f}")
    
    def save_best_strikes(self, best_ce: Optional[Dict], best_pe: Optional[Dict]):
        """Save best CE/PE strikes (for dashboard)"""
        cursor = self.conn.cursor()
        
        # Mark all previous as not current
        cursor.execute('UPDATE best_strikes SET is_current = 0')
        
        # Save best CE
        if best_ce:
            # Convert swing_time to ISO string if it's a datetime/Timestamp
            swing_timestamp = best_ce.get('swing_time')
            if hasattr(swing_timestamp, 'isoformat'):
                swing_timestamp_str = swing_timestamp.isoformat()
            else:
                swing_timestamp_str = str(swing_timestamp) if swing_timestamp else None

            cursor.execute('''
                INSERT INTO best_strikes
                (option_type, symbol, entry_price, sl_price, sl_points, vwap_premium_percent,
                 updated_at, is_current, swing_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'CE',
                best_ce['symbol'],
                best_ce['entry_price'],
                best_ce['sl_price'],
                best_ce['sl_points'],
                best_ce.get('vwap_premium', best_ce.get('vwap_premium_percent', 0)) * 100,  # Convert to %
                datetime.now(IST).isoformat(),
                1,  # is_current
                swing_timestamp_str
            ))

        # Save best PE
        if best_pe:
            # Convert swing_time to ISO string if it's a datetime/Timestamp
            swing_timestamp = best_pe.get('swing_time')
            if hasattr(swing_timestamp, 'isoformat'):
                swing_timestamp_str = swing_timestamp.isoformat()
            else:
                swing_timestamp_str = str(swing_timestamp) if swing_timestamp else None

            cursor.execute('''
                INSERT INTO best_strikes
                (option_type, symbol, entry_price, sl_price, sl_points, vwap_premium_percent,
                 updated_at, is_current, swing_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'PE',
                best_pe['symbol'],
                best_pe['entry_price'],
                best_pe['sl_price'],
                best_pe['sl_points'],
                best_pe.get('vwap_premium', best_pe.get('vwap_premium_percent', 0)) * 100,  # Convert to %
                datetime.now(IST).isoformat(),
                1,  # is_current
                swing_timestamp_str
            ))
        
        self.conn.commit()
    
    def log_order_trigger(self, option_type: str, action: str, symbol: str, 
                         current_price: float, swing_low: float, reason: str):
        """Log order trigger action (for dashboard)"""
        cursor = self.conn.cursor()
        
        cursor.execute('''
            INSERT INTO order_triggers VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now(IST).isoformat(),
            option_type,
            action,
            symbol,
            current_price,
            swing_low,
            reason
        ))
        
        self.conn.commit()
    
    def log_swing_break(self, symbol: str, swing_low: float, break_price: float,
                       vwap_premium: float, sl_percent: float, passed_filters: bool):
        """Log swing break event (for dashboard)"""
        cursor = self.conn.cursor()
        
        cursor.execute('''
            INSERT INTO swing_history VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            symbol,
            swing_low,
            break_price,
            datetime.now(IST).isoformat(),
            vwap_premium,
            sl_percent,
            1 if passed_filters else 0
        ))
        
        self.conn.commit()
    
    def save_latest_bars(self, bars_dict: Dict):
        """Save latest bar data for each symbol (keep all bars from today's session)"""
        cursor = self.conn.cursor()

        for symbol, bar in bars_dict.items():
            cursor.execute('''
                INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                bar['timestamp'],
                bar['open'],
                bar['high'],
                bar['low'],
                bar['close'],
                bar['volume']
            ))

            # Keep all bars from today only (auto-cleanup handled at market close)
            # Full session has ~375 bars (9:15 AM - 3:30 PM)
            # No need to prune during the day - dashboard needs full session data
            cursor.execute('''
                DELETE FROM bars
                WHERE symbol = ?
                AND DATE(timestamp) < DATE('now', 'localtime')
            ''', (symbol,))

        self.conn.commit()
    
    def save_filter_rejections(self, rejections: List[Dict]):
        """Save filter rejection details for historical analysis"""
        if not rejections:
            return
        
        cursor = self.conn.cursor()
        
        for rejection in rejections:
            cursor.execute('''
                INSERT INTO filter_rejections VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now(IST).isoformat(),
                rejection['symbol'],
                rejection['option_type'],
                rejection['swing_low'],
                rejection['current_price'],
                rejection['vwap_at_swing'],
                rejection['vwap_premium_percent'],
                rejection['sl_percent'],
                rejection['rejection_reason']
            ))
        
        self.conn.commit()
    
    def reset_daily_dashboard_data(self):
        """
        Reset dashboard-specific tables at start of new trading day
        
        Clears:
        - swing_candidates (active swings from previous day)
        - best_strikes (yesterday's best strikes - the bug fix!)
        - swing_history (swing break events)
        - filter_rejections (filter rejections log)
        - order_triggers (order trigger events)
        
        Does NOT clear:
        - positions (persist across days for audit)
        - trade_log (historical trades)
        - daily_state (historical daily summaries)
        """
        cursor = self.conn.cursor()
        today = datetime.now(IST).date().isoformat()
        
        logger.info(f"[DAILY-RESET] Resetting dashboard data for new trading day: {today}")
        
        # Clear swing candidates
        cursor.execute('DELETE FROM swing_candidates')
        swing_count = cursor.rowcount
        logger.info(f"[DAILY-RESET] Cleared {swing_count} swing candidates")
        
        # Clear best strikes (THIS IS THE FIX!)
        cursor.execute('DELETE FROM best_strikes')
        best_count = cursor.rowcount
        logger.info(f"[DAILY-RESET] Cleared {best_count} best strikes from previous session")
        
        # Clear swing history
        cursor.execute('DELETE FROM swing_history')
        history_count = cursor.rowcount
        logger.info(f"[DAILY-RESET] Cleared {history_count} swing break events")
        
        # Clear filter rejections
        cursor.execute('DELETE FROM filter_rejections')
        rejection_count = cursor.rowcount
        logger.info(f"[DAILY-RESET] Cleared {rejection_count} filter rejections")
        
        # Clear order triggers
        cursor.execute('DELETE FROM order_triggers')
        trigger_count = cursor.rowcount
        logger.info(f"[DAILY-RESET] Cleared {trigger_count} order triggers")
        
        # Clear all swings log (prevents UNIQUE constraint errors on re-detection)
        cursor.execute('DELETE FROM all_swings_log')
        swings_log_count = cursor.rowcount
        logger.info(f"[DAILY-RESET] Cleared {swings_log_count} swing detection logs")
        
        self.conn.commit()
        logger.info("[DAILY-RESET] Daily dashboard data reset complete")
    
    def get_current_state(self) -> str:
        """Get current operational state"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT current_state FROM operational_state WHERE id = 1')
        row = cursor.fetchone()
        return row[0] if row else 'STARTING'

    def transition_to(self, new_state: str, reason: str = ""):
        """
        Transition to new operational state

        Args:
            new_state: 'STARTING', 'ACTIVE', 'WAITING', 'ERROR', 'SHUTDOWN'
            reason: Reason for transition
        """
        now = datetime.now(IST)
        cursor = self.conn.cursor()

        old_state = self.get_current_state()

        cursor.execute('''
            UPDATE operational_state
            SET current_state = ?,
                state_entered_at = ?,
                error_reason = ?,
                updated_at = ?
            WHERE id = 1
        ''', (new_state, now.isoformat(), reason, now.isoformat()))

        self.conn.commit()

        logger.info(f"[STATE] Transitioned from {old_state} -> {new_state}: {reason}")

    def should_check_health(self) -> bool:
        """
        Check if health check should be performed (in WAITING mode)

        Returns:
            True if 5 minutes have elapsed since last check
        """
        from .config import WAITING_MODE_CHECK_INTERVAL

        cursor = self.conn.cursor()
        cursor.execute('SELECT last_check_at FROM operational_state WHERE id = 1')
        row = cursor.fetchone()

        if not row or not row[0]:
            return True

        last_check = datetime.fromisoformat(row[0])
        now = datetime.now(IST)
        elapsed = (now - last_check).total_seconds()

        return elapsed >= WAITING_MODE_CHECK_INTERVAL

    def update_last_check(self):
        """Update last health check timestamp"""
        now = datetime.now(IST)
        cursor = self.conn.cursor()

        cursor.execute('''
            UPDATE operational_state
            SET last_check_at = ?,
                updated_at = ?
            WHERE id = 1
        ''', (now.isoformat(), now.isoformat()))

        self.conn.commit()

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")


if __name__ == '__main__':
    # Test state manager
    logging.basicConfig(level=logging.INFO)
    
    from datetime import timedelta
    
    manager = StateManager()
    
    # Test saving position
    test_position = {
        'symbol': 'NIFTY26DEC2418000CE',
        'strike': 18000,
        'option_type': 'CE',
        'entry_price': 250,
        'sl_price': 260,
        'quantity': 650,
        'lots': 10,
        'actual_R': 6500,
        'entry_time': datetime.now(IST).isoformat(),
        'current_price': 245,
        'unrealized_pnl': 3250,
        'unrealized_R': 0.5,
        'exit_price': None,
        'exit_time': None,
        'exit_reason': None,
        'realized_pnl': 0,
        'realized_R': 0,
        'is_closed': False,
    }
    
    manager.save_positions([test_position])
    
    # Load positions
    loaded = manager.load_open_positions()
    print(f"Loaded positions: {loaded}")
    
    manager.close()
