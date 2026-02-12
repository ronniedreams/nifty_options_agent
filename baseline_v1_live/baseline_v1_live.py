"""
Baseline V1 Live Trading Orchestrator

Main script for running the baseline_v1 swing-break options shorting strategy
in live markets via OpenAlgo.

Trading Logic:
1. Monitor 82 options (±20 strikes from ATM, CE + PE) for swing low breaks
2. Apply entry filters (100-300 price, >4% VWAP premium, 2-10% SL)
3. Select best strike (SL closest to 10 points, then highest price)
4. Place proactive limit orders BEFORE break (swing_low - 1 tick)
5. On fill, immediately place SL order (trigger at SL price, limit +3 Rs)
6. Track cumulative R, exit all at ±5R or 3:15 PM EOD

Position Limits:
- Max 5 total positions
- Max 3 CE, Max 3 PE
- Max 10 lots per position
- R_VALUE = ₹6,500 per position

Usage:
    python baseline_v1_live.py --expiry 26DEC24 --atm 18000

For OpenAlgo Python Strategy Manager, set environment variables:
    OPENALGO_API_KEY=your_api_key
    OPENALGO_HOST=http://127.0.0.1:5000
    PAPER_TRADING=true  # For Analyzer Mode testing
"""

import logging
import argparse
import sys
import time
import asyncio
import signal
from datetime import datetime, time as dt_time
from typing import Dict, Optional
import pytz
import os

# Imports from current package
from .config import (
    MARKET_START_TIME,
    MARKET_END_TIME,
    FORCE_EXIT_TIME,
    ORDER_FILL_CHECK_INTERVAL,
    LOG_DIR,
    LOG_LEVEL,
    PAPER_TRADING,
    SHUTDOWN_TIMEOUT,
    WAITING_MODE_CHECK_INTERVAL,
    WAITING_MODE_SEND_HOURLY_STATUS,
)
from .data_pipeline import DataPipeline
from .swing_detector import MultiSwingDetector
from .strike_filter import StrikeFilter
from .continuous_filter import ContinuousFilterEngine
from .order_manager import OrderManager
from .position_tracker import PositionTracker
from .state_manager import StateManager
from .telegram_notifier import get_notifier
from .notification_manager import NotificationManager
from .startup_health_check import StartupHealthCheck

# Setup logging
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, f'baseline_v1_live_{datetime.now().strftime("%Y%m%d")}.log')),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class BaselineV1Live:
    """
    Main orchestrator for baseline_v1 live trading (async version)

    Uses asyncio for non-blocking main loop to enable future async order management.
    """
    
    def __init__(self, expiry_date: str, atm_strike: int):
        """
        Initialize live trading components
        
        Args:
            expiry_date: Expiry date string (e.g., '26DEC24')
            atm_strike: ATM strike price (e.g., 18000)
        """
        self.expiry_date = expiry_date
        self.atm_strike = atm_strike
        
        logger.info("="*80)
        logger.info("Baseline V1 Live Trading - Initialization")
        logger.info("="*80)

        # Instance identification for dual-instance setup (local vs EC2)
        instance_name = os.getenv("INSTANCE_NAME", "UNKNOWN")
        db_path = os.getenv("STATE_DB_PATH", "UNKNOWN")

        logger.info(f"Instance: {instance_name}")
        logger.info(f"Database: {db_path}")
        logger.info(f"Expiry: {expiry_date}, ATM Strike: {atm_strike}")
        logger.info(f"Paper Trading: {PAPER_TRADING}")
        logger.info(f"Market Hours: {MARKET_START_TIME} - {MARKET_END_TIME}")
        
        # Initialize components
        self.data_pipeline = DataPipeline()
        
        # State manager must be initialized first
        self.state_manager = StateManager()
        
        # FIX: Only reset dashboard data if it's a new day
        last_state = self.state_manager.load_daily_state()
        today = datetime.now(IST).date().isoformat()
        
        if last_state is None or last_state.get('trade_date') != today:
            logger.info(f"[NEW-DAY] Resetting dashboard data for new trading day: {today}")
            self.state_manager.reset_daily_dashboard_data()
        else:
            logger.info(f"[RESTART] Same day restart detected ({today}), keeping dashboard data")
        
        # Swing detector with callback for new swings
        self.swing_detector = MultiSwingDetector(
            on_swing_detected=self._on_swing_detected,
            state_manager=self.state_manager  # Pass for logging all swings
        )
        
        self.strike_filter = StrikeFilter()  # Kept for compatibility
        self.continuous_filter = ContinuousFilterEngine(state_manager=self.state_manager)  # Pass state_manager for DB logging
        
        # Clear in-memory swing data for new trading day
        self.continuous_filter.reset_daily_data()
        
        self.order_manager = OrderManager()
        self.position_tracker = PositionTracker(order_manager=self.order_manager)
        self.telegram = get_notifier()
        
        # Initialize failure handling components
        self.notification_manager = NotificationManager(self.telegram, self.state_manager)
        self.startup_checker = StartupHealthCheck(self.notification_manager)
        self.shutdown_requested = False

        # Track historical data loading state (to prevent notification spam)
        self.loading_historical_data = False

        # Register initial state
        self.state_manager.update_operational_state('STARTING')

        # Generate option symbols to monitor
        self.symbols = self.data_pipeline.generate_option_symbols(atm_strike, expiry_date)
        logger.info(f"Monitoring {len(self.symbols)} option symbols")
        
        # Add symbols to swing detector
        self.swing_detector.add_symbols(self.symbols)
        
        # Remove old tracking variables
        # self.current_candidate = None  # No longer needed

        # Track previous best strikes for telegram notifications (avoid spam)
        self.previous_best_strikes = {
            'CE': None,  # Stores previous best CE symbol
            'PE': None   # Stores previous best PE symbol
        }

        # Last bar update time
        self.last_bar_update = None

        # Track the last bar timestamp sent to swing_detector per symbol.
        # Prevents feeding the same or older bar repeatedly (e.g., during the
        # startup window when fill_initial_gap and live-stream bars overlap).
        self._last_sent_bar_ts = {}  # {symbol: datetime}

        # Guard flags to ensure one-shot exit handlers (prevent Telegram spam)
        self._eod_exit_done = False  # Set True after handle_eod_exit() runs once

        # Guard against duplicate fill processing (same fill from multiple paths)
        self._processed_fill_ids = set()

        # FIX: Load previous state from database (CRITICAL for crash recovery)
        self.load_state()

        logger.info("Initialization complete")
    
    def load_state(self):
        """Restore strategy state from database after crash/restart"""
        logger.info("[RECOVERY] Restoring state from database...")

        try:
            # 1. Load daily state (cumulative R, exit status)
            daily_state = self.state_manager.load_daily_state()

            # 2. Load open positions
            open_positions_data = self.state_manager.load_open_positions()

            # 3. Restore to PositionTracker
            self.position_tracker.restore_state(open_positions_data, daily_state)

            # 4. Load pending and active orders
            pending_limit, active_sl = self.state_manager.load_orders()

            # 5. Restore to OrderManager
            self.order_manager.restore_state(pending_limit, active_sl)

            logger.info(
                f"[RECOVERY] State recovery complete. Restored {len(open_positions_data)} positions "
                f"and {len(pending_limit) + len(active_sl)} orders."
            )
            # NOTE: Order reconciliation against broker (step 6) is deferred to
            # start(), after data_pipeline is connected and bars are loaded.
            # get_all_latest_bars() returns empty before that point.

        except Exception as e:
            logger.error(f"[RECOVERY] Failed to restore state: {e}", exc_info=True)
            # Strategy will continue with fresh state if recovery fails

    def _reconcile_restored_orders(self):
        """Reconcile orders that were restored from DB against the live broker.

        Must be called AFTER data_pipeline is connected and historical bars
        are loaded -- get_all_latest_bars() is empty before that point.

        Handles three scenarios that can occur during a crash:
        - Entry order filled at broker but not recorded locally -> creates
          position and places exit SL immediately.
        - Entry order rejected/cancelled by broker -> already cleaned up by
          reconcile_orders_with_broker.
        - Exit SL order missing for an open position -> fires critical alert.
        """
        if not self.order_manager.pending_limit_orders and not self.order_manager.active_sl_orders:
            return

        logger.info("[RECOVERY] Reconciling restored orders with broker...")
        try:
            open_positions = self.position_tracker.open_positions
            reconcile_results = self.order_manager.reconcile_orders_with_broker(
                open_positions
            )

            # Process any fills discovered during the crash window
            if reconcile_results['limit_orders_filled']:
                logger.warning(
                    f"[RECOVERY] Found {len(reconcile_results['limit_orders_filled'])} "
                    f"orders filled during crash window"
                )

                latest_bars = self.data_pipeline.get_all_latest_bars()
                current_prices = {symbol: bar.close for symbol, bar in latest_bars.items()}

                for fill_info in reconcile_results['limit_orders_filled']:
                    logger.warning(
                        f"[RECOVERY] Processing fill from crash window: "
                        f"{fill_info['symbol']} @ {fill_info['fill_price']:.2f}"
                    )
                    self.handle_order_fill(fill_info, current_prices)

            # Auto re-place missing SL orders (positions without stop-loss)
            if reconcile_results['sl_orders_missing']:
                for missing_symbol in reconcile_results['sl_orders_missing']:
                    if missing_symbol in self.position_tracker.open_positions:
                        pos = self.position_tracker.open_positions[missing_symbol]
                        logger.warning(
                            f"[RECOVERY] Attempting to re-place missing SL for {missing_symbol} "
                            f"@ {pos.sl_price:.2f}"
                        )
                        sl_id = self.order_manager.place_sl_order(
                            symbol=missing_symbol,
                            trigger_price=pos.sl_price,
                            quantity=pos.quantity
                        )
                        if sl_id:
                            logger.warning(f"[RECOVERY] Re-placed SL for {missing_symbol}: {sl_id}")
                            self.telegram.send_message(
                                f"[RECOVERY] Re-placed missing SL\n"
                                f"Symbol: {missing_symbol}\n"
                                f"Trigger: {pos.sl_price:.2f}\n"
                                f"Order: {sl_id}"
                            )
                        else:
                            logger.critical(
                                f"[RECOVERY] FAILED to re-place SL for {missing_symbol}"
                            )
                            self.telegram.send_message(
                                f"[CRITICAL] FAILED to re-place SL for {missing_symbol}\n"
                                f"MANUAL BROKER CHECK REQUIRED"
                            )

        except Exception as e:
            logger.error(f"[RECOVERY] Order reconciliation failed: {e}", exc_info=True)
            # Non-fatal: system continues but operator should verify broker state

    def start(self):
        """Start live trading"""
        logger.info("="*80)
        logger.info("Starting Baseline V1 Live Trading")
        logger.info("="*80)
        
        # 1. Run Startup Health Checks
        logger.info("Running pre-flight health checks...")
        success, error_type, error_msg = self.startup_checker.run_all_checks()

        if not success:
            logger.error(f"Startup failed: {error_type} - {error_msg}")
            self.notification_manager.send_error_notification(
                'STARTUP_FAILURE', 
                f"Type: {error_type}\nError: {error_msg}",
                is_critical=True
            )

            if error_type == 'PERMANENT':
                logger.critical("Permanent error detected. Exiting.")
                self.state_manager.update_operational_state('ERROR', error_msg)
                sys.exit(1)
            else:
                # Transient error - enter waiting mode
                logger.warning("Transient error detected. Entering waiting mode...")
                self.enter_waiting_mode(error_type, error_msg)
        
        # 2. Update state to ACTIVE
        self.state_manager.update_operational_state('ACTIVE')
        self.notification_manager.send_error_notification(
            'SYSTEM_STARTED', 
            f"Trading Agent Started\nMode: {'PAPER' if PAPER_TRADING else 'LIVE'}\nExpiry: {self.expiry_date}",
            is_critical=False
        )
        
        # Connect to data pipeline (Zerodha primary)
        self.data_pipeline.connect()

        # Connect Angel One backup feed (always-on, silent standby)
        self.data_pipeline.connect_angelone_backup()

        # Subscribe to options (Zerodha primary)
        self.data_pipeline.subscribe_options(self.symbols)

        # Subscribe Angel One to same symbols (backup, ticks ignored until failover)
        self.data_pipeline.subscribe_angelone_backup(self.symbols)
        
        # Load today's historical data BEFORE starting live loop
        # This ensures swings are detected correctly even when starting mid-day
        logger.info("="*80)
        logger.info("[HIST] LOADING HISTORICAL DATA (9:15 AM - Current Time)")
        logger.info("="*80)

        # Set flag to suppress swing notifications during historical loading
        self.loading_historical_data = True

        self.data_pipeline.load_historical_data(symbols=self.symbols)
        
        # 🔧 FIX: Fill any gap between last historical bar and current time
        # This handles mid-session starts where the current minute bar is incomplete
        logger.info("[GAP-FILL] Checking for missing bars...")
        self.data_pipeline.fill_initial_gap()
        logger.info("[GAP-FILL] Gap fill complete")
        
        # Reset swing detectors before historical replay.
        # On same-day restart self.bars retains the previous session's last bar,
        # so historical replay (9:15 onward) is rejected as OUT-OF-ORDER.
        # Setting current_date=None forces reset_for_new_day() on the first bar,
        # clearing all stale bar/swing state before replay begins.
        for symbol in self.symbols:
            detector = self.swing_detector.get_detector(symbol)
            if detector:
                detector.current_date = None
        logger.info("[HIST] Swing detectors reset - historical replay will start fresh")

        # Initialize swing detector with historical bars
        logger.info("[SWING] Processing historical bars for swing detection...")
        
        for symbol in self.symbols:
            bars = self.data_pipeline.get_bars_for_symbol(symbol)
            if bars:
                # Process each historical bar
                for bar in bars:
                    bar_dict = {
                        'timestamp': bar.timestamp,
                        'open': bar.open,
                        'high': bar.high,
                        'low': bar.low,
                        'close': bar.close,
                        'volume': bar.volume,
                        'vwap': bar.vwap
                    }
                    self.swing_detector.update(symbol, bar_dict)
                
                logger.debug(f"{symbol}: {len(bars)} historical bars processed")

        logger.info("[HIST] Historical data processing complete")

        # Seed last-sent timestamps from what the swing detectors now have.
        # This ensures the live-mode dedup filter starts from the right baseline.
        for symbol in self.symbols:
            detector = self.swing_detector.get_detector(symbol)
            if detector and detector.bars:
                self._last_sent_bar_ts[symbol] = detector.bars[-1]['timestamp']

        # Clear flag - now in real-time mode, send notifications normally
        self.loading_historical_data = False

        # CRITICAL: Backfill all historical swings to database
        # These were detected but not logged because is_historical_processing = True
        logger.info("[HIST] Backfilling historical swings to database...")
        historical_swings_logged = 0
        duplicates_skipped = 0

        for symbol in self.symbols:
            detector = self.swing_detector.get_detector(symbol)
            if detector and detector.swings:
                for swing in detector.swings:
                    try:
                        # Check if already logged to prevent duplicates
                        swing_time_iso = swing['timestamp'].isoformat() if hasattr(swing['timestamp'], 'isoformat') else str(swing['timestamp'])

                        cursor = self.state_manager.conn.cursor()
                        cursor.execute('''
                            SELECT COUNT(*) FROM all_swings_log
                            WHERE symbol = ? AND swing_time = ? AND swing_type = ?
                        ''', (symbol, swing_time_iso, swing['type']))

                        exists = cursor.fetchone()[0] > 0

                        if not exists:
                            self.state_manager.log_swing_detection(
                                symbol=symbol,
                                swing_type=swing['type'],
                                swing_price=swing['price'],
                                swing_time=swing['timestamp'],
                                vwap=swing['vwap'],
                                bar_index=swing['index']
                            )
                            historical_swings_logged += 1
                        else:
                            duplicates_skipped += 1

                    except Exception as e:
                        logger.error(f"Error logging historical swing for {symbol}: {e}")

        logger.info(f"[HIST] Backfilled {historical_swings_logged} historical swings to database ({duplicates_skipped} duplicates skipped)")

        # Save all historical bars to database for dashboard visibility
        logger.info("[HIST] Saving historical bars to database...")
        historical_bars_saved = 0
        try:
            for symbol in self.symbols:
                bars = self.data_pipeline.get_bars_for_symbol(symbol)
                if bars:
                    # Save ALL historical bars to database (not just the last one)
                    # This ensures dashboard shows complete bar history from 9:15 AM
                    for bar in bars:
                        bars_for_db = {
                            symbol: {
                                'timestamp': bar.timestamp.isoformat(),
                                'open': bar.open,
                                'high': bar.high,
                                'low': bar.low,
                                'close': bar.close,
                                'volume': bar.volume
                            }
                        }
                        self.state_manager.save_latest_bars(bars_for_db)
                        historical_bars_saved += 1

            logger.info(f"[HIST] Saved {historical_bars_saved} historical bars to database")
        except Exception as e:
            logger.error(f"[HIST] Error saving historical bars to database: {e}")

        # STARTUP PROTECTION: Mark swings that already broke in historical data
        # These swings will NOT trigger order placement (opportunity already missed)
        logger.info("[STARTUP-PROTECTION] Checking for swings that broke before startup...")
        broken_count = self.continuous_filter.mark_historical_breaks(self.swing_detector)
        if broken_count > 0:
            logger.warning(f"[STARTUP-PROTECTION] {broken_count} swings marked as already broken - will NOT place orders for these")

        # Mark all detectors as finished with historical processing
        # From now on, swings will be logged to database automatically
        self.swing_detector.enable_live_mode()
        logger.info("[HIST] Live mode enabled - new swings will auto-log to database")

        # CRITICAL: Reconcile any orders restored from DB against broker.
        # Deferred from load_state() because get_all_latest_bars() requires
        # the data pipeline to be connected and bars to be loaded.
        # Orders may have filled, been rejected, or triggered during the crash.
        # An unreconciled fill leaves a position at the broker with no exit SL.
        self._reconcile_restored_orders()

        logger.info("="*80)

        # Wait for live data stream to stabilize
        logger.info("Waiting for live WebSocket stream to stabilize...")
        time.sleep(10)
        logger.info("Live stream ready")
        logger.info("="*80)
        
        # Check data health
        try:
            health = self.data_pipeline.get_health_status()
            logger.info(f"Data Pipeline Health: {health}")
            
            if health['data_coverage'] < 0.5:
                logger.warning(f"Data coverage {health['data_coverage']:.1%} < 50%, consider waiting longer")
            else:
                logger.info(f"Data coverage: {health['data_coverage']:.1%} - Good!")
        except Exception as e:
            logger.error(f"Error getting health status: {e}", exc_info=True)
            raise
        
        # Main trading loop (async)
        logger.info("Starting main trading loop (async)...")
        asyncio.run(self.run_trading_loop())

    def enter_waiting_mode(self, error_type: str, error_msg: str):
        """
        Enter waiting mode until system recovers

        Args:
            error_type: Type of error causing waiting mode
            error_msg: Error description
        """
        logger.warning(f"ENTERING WAITING MODE: {error_type}")
        self.state_manager.update_operational_state('WAITING', error_msg)
        
        # Send notification
        self.notification_manager.send_error_notification(
            error_type,
            f"System entering WAITING mode.\nError: {error_msg}\nWill retry every {WAITING_MODE_CHECK_INTERVAL}s."
        )

        last_hourly_status = time.time()

        while not self.shutdown_requested:
            try:
                logger.info(f"[WAITING] Checking system health (Next check in {WAITING_MODE_CHECK_INTERVAL}s)...")
                
                # Run health checks
                success, new_error_type, new_error_msg = self.startup_checker.run_all_checks()

                if success:
                    logger.info("[WAITING] System recovered! Resuming normal operation.")
                    self.notification_manager.send_error_notification(
                        'SYSTEM_RECOVERED',
                        "System health checks passed. Resuming trading."
                    )
                    self.notification_manager.mark_resolved(error_type)
                    self.state_manager.update_operational_state('ACTIVE')
                    return  # Exit waiting mode
                
                # Still failing
                logger.warning(f"[WAITING] Check failed: {new_error_type} - {new_error_msg}")
                
                # Send hourly status update if configured
                if WAITING_MODE_SEND_HOURLY_STATUS and (time.time() - last_hourly_status > 3600):
                    self.notification_manager.send_error_notification(
                        'WAITING_STATUS',
                        f"System still in WAITING mode.\nLast Error: {new_error_msg}",
                        is_critical=False
                    )
                    last_hourly_status = time.time()

                time.sleep(WAITING_MODE_CHECK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("[WAITING] Interrupted by user")
                self.shutdown_requested = True
                break
            except Exception as e:
                logger.error(f"[WAITING] Error in waiting loop: {e}")
                time.sleep(60)

    async def run_trading_loop(self):
        """Main trading loop - runs continuously during market hours (async version)"""
        logger.info("Entering main trading loop (async)...")
        
        tick_count = 0
        last_heartbeat = time.time()
        last_watchdog_check = time.time()
        
        while not self.shutdown_requested:
            try:
                tick_count += 1
                
                # [CRITICAL] WATCHDOG: Check data freshness every 30 seconds
                if time.time() - last_watchdog_check > 30:

                    is_fresh, stale_reason = self.data_pipeline.check_data_freshness()

                    if not is_fresh:
                        health = self.data_pipeline.get_health_status()

                        logger.warning(
                            f"[WATCHDOG] TRIGGERED: {stale_reason} - "
                            f"Data is not fresh, attempting reconnection..."
                        )

                        # Send Telegram alert about reconnection attempt
                        self.telegram.send_message(
                            f"[WARNING]️ [WATCHDOG ALERT] STALE DATA\n\n"
                            f"Reason: {stale_reason}\n"
                            f"Data coverage: {health['data_coverage']:.1%}\n"
                            f"Fresh symbols: {health['symbols_with_data']}/{health['subscribed_symbols']}\n"
                            f"Stale symbols: {health['stale_symbols']}\n\n"
                            f"🔄 Attempting automatic reconnection..."
                        )

                        # Attempt automatic reconnection
                        # If pipeline already reconnecting (self-healed), skip and let it finish
                        if self.data_pipeline.is_reconnecting:
                            logger.info(
                                "[WATCHDOG] Pipeline reconnection already in progress - "
                                "skipping watchdog reconnect, resetting timer"
                            )
                            last_watchdog_check = time.time()
                            continue

                        logger.warning("[WATCHDOG] Attempting to reconnect WebSocket...")
                        reconnect_success = self.data_pipeline.reconnect()

                        if reconnect_success:
                            logger.info("[WATCHDOG] Reconnection successful, reconciling orders...")

                            # 🔧 CRITICAL: Reconcile orders with broker after reconnection
                            # This ensures local state matches broker reality
                            try:
                                # Get current open positions for reconciliation
                                # open_positions is a dict: {symbol: Position object}
                                open_positions = self.position_tracker.open_positions

                                # Reconcile orders
                                reconcile_results = self.order_manager.reconcile_orders_with_broker(
                                    open_positions
                                )

                                # Handle filled orders discovered during reconnection
                                if reconcile_results['limit_orders_filled']:
                                    logger.warning(
                                        f"[WATCHDOG] Found {len(reconcile_results['limit_orders_filled'])} "
                                        f"orders filled during disconnect"
                                    )

                                    # Get current prices
                                    latest_bars = self.data_pipeline.get_all_latest_bars()
                                    current_prices = {symbol: bar.close for symbol, bar in latest_bars.items()}

                                    # Process each fill
                                    for fill_info in reconcile_results['limit_orders_filled']:
                                        logger.warning(
                                            f"[WATCHDOG] Processing fill from reconnect: "
                                            f"{fill_info['symbol']} @ {fill_info['fill_price']:.2f}"
                                        )
                                        self.handle_order_fill(fill_info, current_prices)

                                # Handle missing SL orders (CRITICAL!)
                                if reconcile_results['sl_orders_missing']:
                                    missing_symbols = ', '.join(reconcile_results['sl_orders_missing'])
                                    logger.critical(
                                        f"[CRITICAL] MISSING SL ORDERS - "
                                        f"Positions without SL: {missing_symbols} - "
                                        f"MANUAL BROKER CHECK REQUIRED"
                                    )
                                    self.telegram.send_message(
                                        f"[CRITICAL] MISSING SL ORDERS\n\n"
                                        f"Positions without SL protection:\n"
                                        f"{missing_symbols}\n\n"
                                        f"MANUAL BROKER CHECK REQUIRED!"
                                    )

                                    # Consider triggering emergency shutdown if missing SLs
                                    logger.critical(
                                        "[WATCHDOG] Positions without SL detected - "
                                        "initiating emergency shutdown for safety"
                                    )

                                    self.telegram.send_message(
                                        f"❌ [EMERGENCY] Shutting down due to missing SL orders\n"
                                        f"Check broker manually for positions:\n"
                                        f"{', '.join(reconcile_results['sl_orders_missing'])}"
                                    )

                                    self.handle_emergency_shutdown()
                                    raise SystemExit("Missing SL orders after reconnect")

                                logger.info("[WATCHDOG] Order reconciliation complete")

                            except SystemExit:
                                raise  # Re-raise SystemExit
                            except Exception as e:
                                logger.error(f"[WATCHDOG] Error during order reconciliation: {e}", exc_info=True)

                                # Send error notification but continue
                                self.telegram.send_message(
                                    f"[WARNING]️ [WARNING] Order reconciliation failed\n\n"
                                    f"Error: {str(e)}\n\n"
                                    f"Check positions manually at broker!"
                                )

                            # Send success notification
                            self.telegram.send_message(
                                f"✅ [WATCHDOG] RECONNECTION SUCCESSFUL\n\n"
                                f"WebSocket reconnected and operational.\n"
                                f"Orders reconciled with broker.\n"
                                f"Trading system continuing normally."
                            )

                            # Reset watchdog timer and continue
                            last_watchdog_check = time.time()
                            continue
                        else:
                            # Reconnection failed - trigger emergency shutdown
                            logger.critical(
                                f"[WATCHDOG] Reconnection failed after multiple attempts - "
                                f"initiating emergency shutdown"
                            )

                            # Send critical Telegram alert
                            self.telegram.send_message(
                                f"❌ [WATCHDOG CRITICAL] RECONNECTION FAILED\n\n"
                                f"Reason: {stale_reason}\n"
                                f"Reconnection attempts: All failed\n\n"
                                f"🚨 Emergency shutdown initiated...\n"
                                f"All positions will be closed at market."
                            )

                            self.handle_emergency_shutdown()
                            raise SystemExit(f"Watchdog triggered: {stale_reason} - reconnection failed")

                    last_watchdog_check = time.time()
                
                # Check if market is open
                if not self.is_market_open():
                    logger.debug("Market closed, waiting...")
                    await asyncio.sleep(60)
                    continue

                # Check if force exit time reached (3:15 PM)
                if self.is_force_exit_time():
                    if not self._eod_exit_done:
                        logger.warning("Force exit time (3:15 PM) reached - initiating EOD exit")
                        self._eod_exit_done = True
                        self.handle_eod_exit()
                        logger.info("EOD exit complete - system will monitor until market close")
                    # Continue running (monitor mode) until market closes at 3:30 PM
                    await asyncio.sleep(60)
                    continue

                # Main logic (swing detection continues until market close)
                self.process_tick()
                
                # Heartbeat every 60 seconds
                if time.time() - last_heartbeat > 60:
                    health = self.data_pipeline.get_health_status()
                    logger.info(
                        f"[HEARTBEAT] Positions: {len(self.position_tracker.open_positions)} | "
                        f"Data: {health['symbols_with_data']}/{health['subscribed_symbols']} | "
                        f"Coverage: {health['data_coverage']:.1%} | "
                        f"Stale: {health['stale_symbols']}"
                    )

                    last_heartbeat = time.time()
                
                # Sleep until next check
                logger.debug(f"Sleeping {ORDER_FILL_CHECK_INTERVAL} seconds...")
                await asyncio.sleep(ORDER_FILL_CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received, shutting down...")
                self.shutdown_requested = True
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                
                # Use failure handling logic
                self.notification_manager.queue_error_for_aggregation('RUNTIME_ERROR', str(e))
                
                # If critical connectivity error, enter waiting mode
                if "Connection" in str(e) or "WebSocket" in str(e):
                    self.enter_waiting_mode('CONNECTION_LOST', str(e))
                else:
                    await asyncio.sleep(10)

        self.handle_graceful_shutdown()
    
    def _on_swing_detected(self, symbol: str, swing_info: Dict):
        """
        Callback when new swing low is detected

        Add to continuous filter's swing candidates
        """
        logger.info(
            f"[SWING-CALLBACK] {symbol}: Swing @ {swing_info['price']:.2f} "
            f"(VWAP: {swing_info['vwap']:.2f})"
        )
        self.continuous_filter.add_swing_candidate(symbol, swing_info)

        # Swing notifications disabled - too noisy (only notify on trades, not swings)
        # if self.telegram and not self.loading_historical_data:
        #     self.telegram.notify_swing_detected(symbol, swing_info)
    
    def process_tick(self):
        """
        NEW: Continuous evaluation approach
        
        Every tick:
        1. Evaluate all swing candidates with latest bars
        2. Get best CE and PE strikes
        3. Manage proactive limit orders based on price proximity
        4. Check for fills
        5. Update positions and check exits
        """
        
        # 1. Get latest bars for all symbols
        latest_bars = self.data_pipeline.get_all_latest_bars()  # Completed bars
        current_bars = self.data_pipeline.get_all_current_bars()  # Real-time incomplete bars

        if not latest_bars:
            return
        
        # 2. Update swing detectors with latest bars
        # (This will trigger _on_swing_detected callback for new swings)
        # Only feed bars that are strictly newer than what was last sent per symbol.
        # This prevents the startup overlap window (fill_initial_gap vs live stream)
        # from causing repeated OUT-OF-ORDER/DUPLICATE errors in swing_detector.
        new_bars_dict = {}
        for symbol, bar in latest_bars.items():
            last_ts = self._last_sent_bar_ts.get(symbol)
            if last_ts is None or bar.timestamp > last_ts:
                new_bars_dict[symbol] = bar.to_dict()
                self._last_sent_bar_ts[symbol] = bar.timestamp
        if new_bars_dict:
            self.swing_detector.update_all(new_bars_dict)
        
        # 3. Evaluate ALL swing candidates with latest data (including current incomplete bars for real-time SL% calculation)
        best_strikes = self.continuous_filter.evaluate_all_candidates(
            latest_bars,
            self.swing_detector,
            current_bars,  # Include current bars for accurate highest_high tracking
            open_position_symbols=set(self.position_tracker.open_positions.keys())
        )
        
        # Log current best strikes and candidates (INFO level for visibility)
        summary = self.continuous_filter.get_summary()
        if summary['total_candidates'] > 0:
            logger.info(
                f"[CANDIDATES] Total: {summary['total_candidates']} "
                f"(CE: {summary['ce_candidates']}, PE: {summary['pe_candidates']}) | "
                f"Best: CE={summary['best_ce']}, PE={summary['best_pe']}"
            )
        
        # Persist swing candidates to DB for dashboard (even if 0 candidates to clear table)
        try:
            self.state_manager.save_swing_candidates(self.continuous_filter.swing_candidates)
        except Exception as e:
            logger.warning(f"Failed to save swing candidates: {e}")
        
        # Check for best strike changes and send telegram notifications (only when changed)
        best_ce = best_strikes.get('CE')
        best_pe = best_strikes.get('PE')

        for option_type in ['CE', 'PE']:
            current_best = best_strikes.get(option_type)
            previous_best = self.previous_best_strikes[option_type]

            # Check if best strike changed
            if current_best:
                current_symbol = current_best['symbol']

                if previous_best is None:
                    # First time a best strike is selected
                    logger.info(f"[TELEGRAM] First best {option_type} selected: {current_symbol}")
                    try:
                        self.telegram.notify_best_strike_change(option_type, current_best, is_new=True)
                    except Exception as e:
                        logger.error(f"Failed to send telegram notification: {e}")
                    self.previous_best_strikes[option_type] = current_symbol

                elif previous_best != current_symbol:
                    # Best strike changed to a different symbol
                    logger.info(f"[TELEGRAM] Best {option_type} changed: {previous_best} -> {current_symbol}")
                    try:
                        self.telegram.notify_best_strike_change(option_type, current_best, is_new=False)
                    except Exception as e:
                        logger.error(f"Failed to send telegram notification: {e}")
                    self.previous_best_strikes[option_type] = current_symbol

                # else: Same symbol still best - no notification

            elif previous_best is not None:
                # Best strike went from something to None (candidate disqualified)
                logger.info(f"[TELEGRAM] Best {option_type} cleared: {previous_best} no longer qualifies")
                self.previous_best_strikes[option_type] = None

        # Persist best strikes to DB for dashboard
        # 🔧 CRITICAL: Always call save_best_strikes(), even when both are None
        # This ensures stale records get cleared when swings are replaced by unqualified ones
        try:
            self.state_manager.save_best_strikes(best_ce, best_pe)
        except Exception as e:
            logger.warning(f"Failed to save best strikes: {e}")
        
        # Persist latest bars to DB for dashboard
        try:
            bars_for_db = {}
            for symbol, bar in latest_bars.items():
                bars_for_db[symbol] = {
                    'timestamp': bar.timestamp.isoformat(),
                    'open': bar.open,
                    'high': bar.high,
                    'low': bar.low,
                    'close': bar.close,
                    'volume': bar.volume
                }
            if bars_for_db:
                self.state_manager.save_latest_bars(bars_for_db)
        except Exception as e:
            logger.warning(f"Failed to save latest bars: {e}")
        
        # 4. Get order triggers based on price proximity and existing orders
        # Use CURRENT bars for real-time price checking, LATEST bars for metrics
        pending_orders = self.order_manager.get_pending_orders_by_type()
        triggers = self.continuous_filter.get_order_triggers(latest_bars, current_bars, pending_orders)

        # Log pending orders and trigger decisions for debugging
        logger.info(f"[PENDING-ORDERS] {self.order_manager.debug_pending_orders()}")
        logger.info(f"[TRIGGER-CE] Action={triggers['CE']['action']}, Reason={triggers['CE'].get('reason', 'N/A')}")
        logger.info(f"[TRIGGER-PE] Action={triggers['PE']['action']}, Reason={triggers['PE'].get('reason', 'N/A')}")

        # 5. Manage limit orders for CE and PE
        for option_type in ['CE', 'PE']:
            trigger = triggers[option_type]
            action = trigger['action']
            candidate = trigger.get('candidate')
            
            # Log order trigger to DB for dashboard
            try:
                if action in ['place', 'wait', 'modify', 'cancel']:
                    symbol = candidate['symbol'] if candidate else 'N/A'
                    current_price = candidate.get('entry_price', 0) if candidate else 0
                    swing_low = candidate.get('swing_low', 0) if candidate else 0
                    reason = trigger.get('reason', '')
                    self.state_manager.log_order_trigger(
                        option_type, action, symbol, current_price, swing_low, reason
                    )
            except Exception as e:
                logger.warning(f"Failed to log order trigger: {e}")
            
            if action == 'place':
                # Price within 1 Rs of swing - place/update order
                limit_price = trigger['limit_price']

                # Check available margin before attempting order (CRITICAL FIX #5)
                try:
                    # Query broker for account info
                    account_info = self.data_pipeline.client.get_account_details()

                    if account_info and account_info.get('status') == 'success':
                        available_margin = float(account_info.get('data', {}).get('availablecash', 0))

                        # Rough margin estimate: entry_price × quantity (conservative - actual may be lower)
                        estimated_margin_required = candidate['entry_price'] * candidate['quantity']

                        if available_margin < estimated_margin_required:
                            logger.warning(
                                f"[MARGIN-CHECK-{option_type}] INSUFFICIENT MARGIN "
                                f"Available: ₹{available_margin:,.0f} < Required: ₹{estimated_margin_required:,.0f} "
                                f"(Symbol: {candidate['symbol']}, Qty: {candidate['quantity']})"
                            )
                            # Skip order placement - insufficient margin
                            self.order_manager.manage_limit_order_for_type(option_type, None, None)
                            continue
                        else:
                            logger.debug(
                                f"[MARGIN-CHECK-{option_type}] OK "
                                f"Available: ₹{available_margin:,.0f} >= Required: ₹{estimated_margin_required:,.0f}"
                            )
                    else:
                        # If margin check fails, log warning but proceed (don't block on API failure)
                        logger.warning(
                            f"[MARGIN-CHECK-{option_type}] API call failed: {account_info}. "
                            f"Proceeding with order (margin not verified)"
                        )
                except Exception as e:
                    # If margin check throws exception, log but proceed
                    logger.warning(
                        f"[MARGIN-CHECK-{option_type}] Exception during margin check: {e}. "
                        f"Proceeding with order (margin not verified)"
                    )

                # Check if we can open position for this type (include pending orders)
                pending_ce = 1 if self.order_manager.pending_limit_orders.get('CE') else 0
                pending_pe = 1 if self.order_manager.pending_limit_orders.get('PE') else 0

                can_open, reason = self.position_tracker.can_open_position(
                    candidate['symbol'],
                    option_type,
                    pending_ce_orders=pending_ce,
                    pending_pe_orders=pending_pe
                )

                logger.info(
                    f"[ATTEMPTING-{option_type}] Symbol={candidate['symbol']}, "
                    f"Limit={limit_price:.2f}, Can_Open={can_open}, Reason={reason}"
                )

                if can_open:
                    result = self.order_manager.manage_limit_order_for_type(
                        option_type,
                        candidate,
                        limit_price
                    )
                    logger.info(f"[ORDER-RESULT-{option_type}] {result}: {candidate['symbol']} @ {limit_price:.2f}")
                else:
                    # Can't open - cancel any existing order
                    self.order_manager.manage_limit_order_for_type(option_type, None, None)
                    logger.warning(f"[BLOCKED-{option_type}] {reason}")
            
            elif action == 'cancel':
                # Price too far - cancel order
                self.order_manager.manage_limit_order_for_type(option_type, None, None)
                logger.debug(f"[ORDER-{option_type}] Cancelled: {trigger.get('reason')}")
            
            elif action == 'check_fill':
                # Price broke - order should have filled
                logger.debug(f"[ORDER-{option_type}] Price broke: {trigger.get('reason')}")
            
            # action == 'wait': do nothing
        
        # 6. Check for order fills
        fills = self.order_manager.check_fills_by_type()
        
        current_prices = {symbol: bar.close for symbol, bar in latest_bars.items()}
        
        for option_type in ['CE', 'PE']:
            if fills[option_type]:
                self.handle_order_fill(fills[option_type], current_prices)
        
        # 7. Update position prices
        self.position_tracker.update_prices(current_prices)
        
        # 8. Check for daily ±5R exit
        # Capture flag BEFORE calling check_daily_exit() so we only call
        # handle_daily_exit() once — on the tick that first triggers the exit.
        # check_daily_exit() returns the reason on every subsequent call too,
        # which would cause repeated Telegram notifications without this guard.
        was_already_triggered = self.position_tracker.daily_exit_triggered
        exit_reason = self.position_tracker.check_daily_exit()

        if exit_reason and not was_already_triggered:
            self.handle_daily_exit(exit_reason, current_prices)
        
        # 7. Reconcile positions with broker (every 60 seconds)
        if self.last_bar_update is None or \
           (datetime.now(IST) - self.last_bar_update).total_seconds() > 60:
            phantom_closed = self.position_tracker.reconcile_with_broker()
            if phantom_closed:
                for sym in phantom_closed:
                    self.continuous_filter.remove_swing_candidate(sym)
                    logger.info(f"[PHANTOM-CLEANUP] {sym} removed from filter after SL hit")
            self.last_bar_update = datetime.now(IST)
        
        # 8. Save state
        self.save_state()
    
    def _compute_live_sl_price(self, symbol: str, candidate_info: Dict) -> float:
        """Compute fresh SL price using live highest_high at fill time.

        Falls back to candidate_info['sl_price'] if live data unavailable.
        """
        try:
            detector = self.swing_detector.detectors.get(symbol)
            if not detector or not detector.bars:
                return candidate_info['sl_price']

            swing_time = candidate_info.get('swing_time')
            if not swing_time:
                return candidate_info['sl_price']

            # Find highest high from all bars at/after swing time
            highest_high = 0.0
            found_swing = False
            for bar in detector.bars:
                if bar['timestamp'] >= swing_time:
                    found_swing = True
                    highest_high = max(highest_high, bar.get('high', 0.0))

            if not found_swing:
                return candidate_info['sl_price']

            # Include current incomplete bar
            current_bars = self.data_pipeline.get_all_current_bars()
            current_bar = current_bars.get(symbol)
            if current_bar and current_bar.high is not None:
                highest_high = max(highest_high, current_bar.high)

            live_sl = highest_high + 1  # +1 Rs buffer (per architecture)

            # Only use live SL if it's HIGHER than stale (safer for short positions)
            stale_sl = candidate_info['sl_price']
            if live_sl > stale_sl:
                logger.info(
                    f"[SL-RECOMPUTE] {symbol}: stale SL={stale_sl:.2f} -> "
                    f"live SL={live_sl:.2f} (highest_high={highest_high:.2f})"
                )
                return live_sl
            return stale_sl

        except Exception as e:
            logger.error(f"[SL-RECOMPUTE] Error for {symbol}: {e}, using stale SL")
            return candidate_info['sl_price']

    def handle_order_fill(self, fill: Dict, current_prices: Dict):
        """Handle filled limit order"""
        symbol = fill['symbol']
        fill_price = fill['fill_price']
        quantity = fill['quantity']
        candidate_info = fill['candidate_info']
        option_type = fill['option_type']

        # Dedup guard: prevent processing the same fill multiple times
        fill_key = f"{symbol}_{fill.get('order_id', '')}_{fill_price}"
        if fill_key in self._processed_fill_ids:
            logger.warning(f"[FILL-DEDUP] {symbol} fill already processed (key={fill_key}), skipping")
            return
        self._processed_fill_ids.add(fill_key)

        logger.info(f"[FILL-{option_type}] {symbol} @ {fill_price:.2f}, Qty={quantity}")

        # Recompute SL price using live highest_high (not stale candidate_info)
        live_sl_price = self._compute_live_sl_price(symbol, candidate_info)

        # Add position (use live SL for position record too)
        position = self.position_tracker.add_position(
            symbol=symbol,
            entry_price=fill_price,
            sl_price=live_sl_price,
            quantity=quantity,
            actual_R=candidate_info['actual_R'],
            candidate_info=candidate_info
        )

        # CRITICAL: Remove filled symbol from filter pool to prevent re-ordering
        self.continuous_filter.remove_swing_candidate(symbol)
        logger.info(f"[FILL-CLEANUP] {symbol} removed from filter pool after fill")

        # Place SL order immediately with live price
        sl_order_id = self.order_manager.place_sl_order(
            symbol=symbol,
            trigger_price=live_sl_price,
            quantity=quantity
        )
        
        if sl_order_id:
            logger.info(f"[SL-ORDER] {symbol} @ {live_sl_price:.2f} | Order: {sl_order_id}")
        else:
            # 🚨 CRITICAL: SL placement failed - position has unlimited risk
            logger.critical(
                f"[CRITICAL] SL PLACEMENT FAILED for {symbol} - Initiating emergency market exit"
            )
            
            # Send immediate Telegram alert
            self.telegram.send_message(
                f"🚨 CRITICAL: SL PLACEMENT FAILED\n\n"
                f"Symbol: {symbol}\n"
                f"Entry: ₹{fill_price:.2f}\n"
                f"Qty: {quantity}\n"
                f"Expected SL: ₹{live_sl_price:.2f}\n\n"
                f"[WARNING]️ Initiating emergency MARKET exit..."
            )
            
            # Attempt emergency market exit
            emergency_order_id = self.order_manager.emergency_market_exit(
                symbol=symbol,
                quantity=quantity,
                reason="SL_PLACEMENT_FAILED"
            )
            
            if emergency_order_id:
                logger.warning(
                    f"Emergency exit placed: {emergency_order_id} - "
                    f"Position will be force-closed at market"
                )
                
                # Send success confirmation
                self.telegram.send_message(
                    f"✅ Emergency exit order placed\n\n"
                    f"Symbol: {symbol}\n"
                    f"Order ID: {emergency_order_id}\n"
                    f"Type: MARKET (force close)\n\n"
                    f"Position will be closed at market price."
                )
                
                # Remove position from tracker (will be closed)
                self.position_tracker.close_position(
                    symbol=symbol,
                    exit_price=fill_price,  # Use entry price as approximation
                    exit_reason="EMERGENCY_EXIT_SL_FAILED"
                )
            else:
                logger.critical(
                    f"[ERROR] EMERGENCY EXIT FAILED for {symbol} - MANUAL INTERVENTION REQUIRED!"
                )
                
                # Send critical failure alert
                self.telegram.send_message(
                    f"❌ EMERGENCY EXIT FAILED\n\n"
                    f"Symbol: {symbol}\n"
                    f"Qty: {quantity}\n\n"
                    f"🚨 MANUAL BROKER INTERVENTION REQUIRED!\n"
                    f"Position has NO STOP LOSS - close immediately in broker!"
                )
            
            # Check if we should halt trading
            if self.order_manager.should_halt_trading():
                logger.critical("[HALT] HALTING TRADING DUE TO REPEATED SL FAILURES")
                
                # Send halt notification
                self.telegram.send_message(
                    f"🛑 TRADING HALTED\n\n"
                    f"Reason: {self.order_manager.consecutive_sl_failures} consecutive SL failures\n"
                    f"Threshold: {self.order_manager.consecutive_sl_failures}/3\n\n"
                    f"System initiating emergency shutdown..."
                )
                
                self.handle_emergency_shutdown()
                raise SystemExit("Trading halted due to SL placement failures")
        
        # Send Telegram notification
        self.telegram.notify_trade_entry(fill)
    
    def handle_daily_exit(self, exit_reason: str, current_prices: Dict):
        """Handle ±5R daily exit"""
        logger.warning(f"DAILY EXIT TRIGGERED: {exit_reason}")

        try:
            # Cancel all orders FIRST (prevent new fills during exit)
            self.order_manager.cancel_all_orders()

            # Clear filter pools to prevent re-nomination after exit
            self.continuous_filter.reset_daily_data()
            logger.info("[DAILY-EXIT] Filter pools cleared to prevent re-nomination")

            # Close all positions
            self.position_tracker.close_all_positions(exit_reason, current_prices)

            # Save final state
            self.save_state()

            # Save daily summary and notify
            summary = self.position_tracker.get_position_summary()
            self.state_manager.save_daily_summary(summary)
            self.telegram.notify_daily_target(summary)

            logger.info(f"Daily Summary: {summary}")
            logger.info("Trading stopped for the day")

        except Exception as e:
            logger.critical(
                f"[DAILY-EXIT-ERROR] Failed during daily exit: {e}. "
                f"Some positions may still be open!",
                exc_info=True
            )
            # Send critical alert
            self.telegram.send_message(
                f"[CRITICAL] Daily exit FAILED: {e}\n"
                f"MANUAL BROKER CHECK REQUIRED"
            )
    
    def handle_eod_exit(self):
        """Handle end-of-day forced exit at 3:15 PM"""
        logger.warning("End-of-Day Exit (3:15 PM)")

        # Get current prices
        latest_bars = self.data_pipeline.get_all_latest_bars()
        current_prices = {symbol: bar.close for symbol, bar in latest_bars.items()}

        # Cancel all orders
        self.order_manager.cancel_all_orders()

        # Clear filter pools to prevent re-nomination after exit
        self.continuous_filter.reset_daily_data()
        logger.info("[EOD-EXIT] Filter pools cleared to prevent re-nomination")

        # Close all positions
        self.position_tracker.close_all_positions('EOD_EXIT', current_prices)

        # Save final state
        self.save_state()

        # Save daily summary
        summary = self.position_tracker.get_position_summary()
        self.state_manager.save_daily_summary(summary)

        # Send Telegram notification
        self.telegram.notify_daily_summary(summary)

        logger.info(f"EOD Summary: {summary}")
    
    def save_state(self):
        """Save current state to database"""
        # Get all positions
        positions = self.position_tracker.get_all_positions()
        self.state_manager.save_positions(positions)
        
        # Save orders
        self.state_manager.save_orders(
            self.order_manager.pending_limit_orders,
            self.order_manager.active_sl_orders
        )
        
        # Save daily state
        summary = self.position_tracker.get_position_summary()
        summary['expiry'] = self.expiry_date  # Add expiry for dashboard
        self.state_manager.save_daily_state(summary)
        
        # Log completed trades
        for pos in positions:
            if pos['is_closed']:
                self.state_manager.log_trade(pos)
    
    def is_market_open(self) -> bool:
        """Check if market is currently open"""
        now = datetime.now(IST).time()
        return MARKET_START_TIME <= now < MARKET_END_TIME
    
    def is_force_exit_time(self) -> bool:
        """Check if it's force exit time"""
        now = datetime.now(IST).time()
        return now >= FORCE_EXIT_TIME
    
    def handle_graceful_shutdown(self):
        """Graceful shutdown with timeout"""
        logger.info(f"Initiating graceful shutdown (timeout: {SHUTDOWN_TIMEOUT}s)...")
        self.state_manager.update_operational_state('SHUTDOWN')
        
        start_time = time.time()
        
        try:
            # 1. Cancel pending orders (if any)
            if self.order_manager:
                logger.info("Cancelling pending orders...")
                self.order_manager.cancel_all_orders()
            
            # 2. Close state manager (saves everything)
            if self.state_manager:
                logger.info("Saving state and closing database...")
                self.save_state()
                self.state_manager.close()
            
            # 3. Disconnect data pipeline
            if self.data_pipeline:
                logger.info("Disconnecting data pipeline...")
                self.data_pipeline.disconnect()
                
            elapsed = time.time() - start_time
            logger.info(f"Shutdown complete in {elapsed:.2f}s")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            # Force exit if stuck
            sys.exit(1)
    
    def handle_emergency_shutdown(self):
        """
        EMERGENCY SHUTDOWN: Cancel all orders, exit all positions, save state
        
        Called when critical failures occur (e.g., repeated SL placement failures)
        """
        logger.critical("[EMERGENCY] INITIATING EMERGENCY SHUTDOWN")
        
        try:
            # 1. Cancel ALL pending orders (limit + SL)
            logger.warning("Cancelling all pending orders...")
            self.order_manager.cancel_all_orders()
            
            # 2. Close ALL open positions at market
            logger.warning("Force-closing all open positions...")
            all_positions = self.position_tracker.get_all_positions()
            open_positions = [pos for pos in all_positions if not pos['is_closed']]
            
            for position in open_positions:
                symbol = position['symbol']
                quantity = position['quantity']
                
                logger.warning(f"Emergency exit: {symbol} qty={quantity}")
                
                emergency_order_id = self.order_manager.emergency_market_exit(
                    symbol=symbol,
                    quantity=quantity,
                    reason="EMERGENCY_SHUTDOWN"
                )
                
                if emergency_order_id:
                    # Mark position as closed (will be filled at market)
                    self.position_tracker.close_position(
                        symbol=symbol,
                        exit_price=position['current_price'],
                        exit_reason="EMERGENCY_SHUTDOWN"
                    )
                else:
                    logger.critical(
                        f"[FAIL] Failed to emergency exit {symbol} - "
                        f"MANUAL BROKER INTERVENTION REQUIRED!"
                    )
            
            # 3. Save final state
            logger.warning("Saving final state...")
            self.save_state()
            
            # 4. Send Telegram alert
            summary = self.position_tracker.get_position_summary()
            self.telegram.send_message(
                f"🚨 EMERGENCY SHUTDOWN\n\n"
                f"Reason: Repeated SL placement failures\n"
                f"Cumulative R: {summary['cumulative_R']:.2f}R\n"
                f"Closed positions: {summary['total_positions']}\n\n"
                f"[WARNING]️ Check broker positions manually!"
            )
            
            logger.critical("Emergency shutdown complete - check broker positions manually")
            
        except Exception as e:
            logger.critical(
                f"Exception during emergency shutdown: {e}",
                exc_info=True
            )
            raise


# Global reference for signal handler
strategy_instance = None

def signal_handler(signum, frame):
    """Handle system signals for graceful shutdown"""
    global strategy_instance
    print(f"\n[SHUTDOWN] Signal {signum} received. Requesting shutdown...")
    if strategy_instance:
        strategy_instance.shutdown_requested = True
    else:
        sys.exit(0)

def main():
    """Main entry point"""
    global strategy_instance

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(
        description='Baseline V1 Live Trading - Options Swing Break Strategy'
    )

    # Add --auto flag
    parser.add_argument(
        '--auto',
        action='store_true',
        help='Auto-detect ATM and expiry (waits until 9:16 AM, fetches NIFTY spot, selects nearest expiry)'
    )

    # Make --expiry and --atm optional when --auto is used
    parser.add_argument(
        '--expiry',
        required=False,
        help='Expiry date (e.g., 26DEC24) - Required if --auto not used'
    )
    parser.add_argument(
        '--atm',
        type=int,
        required=False,
        help='ATM strike price (e.g., 18000) - Required if --auto not used'
    )

    args = parser.parse_args()

    # Determine ATM and expiry based on mode
    if args.auto:
        # Auto mode - detect ATM and expiry using WebSocket
        logger.info("[AUTO] Auto-detection mode enabled (WebSocket + API fallback)")

        from .auto_detector import AutoDetector
        from .config import OPENALGO_API_KEY, OPENALGO_HOST
        from .data_pipeline import DataPipeline

        # Try WebSocket for spot price, fall back to API if it fails
        temp_pipeline = None
        spot_symbol = "Nifty 50"

        try:
            temp_pipeline = DataPipeline()
            logger.info("[AUTO] Connecting to WebSocket for spot price...")
            temp_pipeline.connect()

            logger.info(f"[AUTO] Subscribing to NIFTY spot: {spot_symbol}")
            temp_pipeline.subscribe_options([], spot_symbol=spot_symbol)

            # Give WebSocket time to receive first tick
            time.sleep(3)
        except Exception as e:
            logger.warning(f"[AUTO] WebSocket connection failed: {e}")
            logger.info("[AUTO] Will use API fallback for spot price")
            temp_pipeline = None  # Signal AutoDetector to skip WebSocket

        # Run auto-detection (WebSocket if connected, else API fallback)
        detector = AutoDetector(
            api_key=OPENALGO_API_KEY,
            host=OPENALGO_HOST,
            data_pipeline=temp_pipeline,
            spot_symbol=spot_symbol
        )
        atm_strike, expiry_date = detector.auto_detect()

        # Clean up if WebSocket was used
        if temp_pipeline:
            logger.info("[AUTO] Cleaning up temporary WebSocket connection...")
            temp_pipeline.disconnect()

        logger.info(f"[AUTO] Detected ATM: {atm_strike}, Expiry: {expiry_date}")
    else:
        # Manual mode - require --expiry and --atm
        if not args.expiry or not args.atm:
            parser.error("--expiry and --atm are required when --auto is not used")

        atm_strike = args.atm
        expiry_date = args.expiry
        logger.info(f"[MANUAL] Using provided ATM: {atm_strike}, Expiry: {expiry_date}")

    # Create and start strategy
    strategy = BaselineV1Live(
        expiry_date=expiry_date,
        atm_strike=atm_strike
    )
    strategy_instance = strategy
    
    try:
        strategy.start()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Interrupted. Shutting down...")
        strategy.handle_graceful_shutdown()
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        strategy.handle_graceful_shutdown()



if __name__ == '__main__':
    main()

