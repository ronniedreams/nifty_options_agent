"""
Baseline V1 Live Trading Orchestrator

Main script for running the baseline_v1 swing-break options shorting strategy
in live markets via OpenAlgo.

Trading Logic:
1. Monitor 42 options (±10 strikes from ATM, CE + PE) for swing low breaks
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
)
from .data_pipeline import DataPipeline
from .swing_detector import MultiSwingDetector
from .strike_filter import StrikeFilter
from .continuous_filter import ContinuousFilterEngine
from .order_manager import OrderManager
from .position_tracker import PositionTracker
from .state_manager import StateManager
from .telegram_notifier import get_notifier

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
    Main orchestrator for baseline_v1 live trading
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
        logger.info(f"Expiry: {expiry_date}, ATM Strike: {atm_strike}")
        logger.info(f"Paper Trading: {PAPER_TRADING}")
        logger.info(f"Market Hours: {MARKET_START_TIME} - {MARKET_END_TIME}")
        
        # Initialize components
        self.data_pipeline = DataPipeline()
        
        # State manager must be initialized first
        self.state_manager = StateManager()
        
        # 🔧 CRITICAL: Reset dashboard data for new trading day
        # This clears yesterday's best_strikes, swing_candidates, etc.
        # Prevents stale data from showing in monitor dashboard
        self.state_manager.reset_daily_dashboard_data()
        
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

        logger.info("Initialization complete")
    
    def start(self):
        """Start live trading"""
        logger.info("="*80)
        logger.info("Starting Baseline V1 Live Trading")
        logger.info("="*80)
        
        # Connect to data pipeline
        self.data_pipeline.connect()
        
        # Subscribe to options
        self.data_pipeline.subscribe_options(self.symbols)
        
        # Load today's historical data BEFORE starting live loop
        # This ensures swings are detected correctly even when starting mid-day
        logger.info("="*80)
        logger.info("[HIST] LOADING HISTORICAL DATA (9:15 AM - Current Time)")
        logger.info("="*80)
        self.data_pipeline.load_historical_data(symbols=self.symbols)
        
        # 🔧 FIX: Fill any gap between last historical bar and current time
        # This handles mid-session starts where the current minute bar is incomplete
        logger.info("[GAP-FILL] Checking for missing bars...")
        self.data_pipeline.fill_initial_gap()
        logger.info("[GAP-FILL] Gap fill complete")
        
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
        
        # Main trading loop
        logger.info("Starting main trading loop...")
        self.run_trading_loop()
    
    def run_trading_loop(self):
        """Main trading loop - runs continuously during market hours"""
        logger.info("Entering main trading loop...")
        
        tick_count = 0
        last_heartbeat = time.time()
        last_watchdog_check = time.time()
        
        while True:
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
                        logger.warning("[WATCHDOG] Attempting to reconnect WebSocket...")
                        reconnect_success = self.data_pipeline.reconnect()

                        if reconnect_success:
                            logger.info("[WATCHDOG] ✅ Reconnection successful, reconciling orders...")

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
                                    critical_msg = (
                                        f"🚨 [CRITICAL] MISSING SL ORDERS\n\n"
                                        f"Positions without SL protection:\n"
                                        f"{', '.join(reconcile_results['sl_orders_missing'])}\n\n"
                                        f"[WARNING]️ MANUAL BROKER CHECK REQUIRED!"
                                    )

                                    logger.critical(critical_msg)
                                    self.telegram.send_message(critical_msg)

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

                                logger.info("[WATCHDOG] ✅ Order reconciliation complete")

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
                                f"[WATCHDOG] ❌ Reconnection failed after multiple attempts - "
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
                    time.sleep(60)
                    continue

                # Check if force exit time reached (3:15 PM)
                if self.is_force_exit_time():
                    logger.warning("Force exit time (3:15 PM) reached - initiating EOD exit")

                    # Get current prices
                    latest_bars = self.data_pipeline.get_all_latest_bars()
                    current_prices = {symbol: bar.close for symbol, bar in latest_bars.items()}

                    # Handle EOD exit
                    self.handle_eod_exit()

                    # Continue running but don't process ticks anymore
                    logger.info("EOD exit complete - system will monitor until market close")
                    time.sleep(60)
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
                time.sleep(ORDER_FILL_CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received, shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                time.sleep(10)
        
        self.shutdown()
    
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
        bars_dict = {symbol: bar.to_dict() for symbol, bar in latest_bars.items()}
        self.swing_detector.update_all(bars_dict)
        
        # 3. Evaluate ALL swing candidates with latest data
        best_strikes = self.continuous_filter.evaluate_all_candidates(
            latest_bars,
            self.swing_detector
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

                # Check if we can open position for this type
                can_open, reason = self.position_tracker.can_open_position(
                    candidate['symbol'],
                    option_type
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
        exit_reason = self.position_tracker.check_daily_exit()
        
        if exit_reason:
            self.handle_daily_exit(exit_reason, current_prices)
        
        # 7. Reconcile positions with broker (every 60 seconds)
        if self.last_bar_update is None or \
           (datetime.now(IST) - self.last_bar_update).total_seconds() > 60:
            self.position_tracker.reconcile_with_broker()
            self.last_bar_update = datetime.now(IST)
        
        # 8. Save state
        self.save_state()
    
    def handle_order_fill(self, fill: Dict, current_prices: Dict):
        """Handle filled limit order"""
        symbol = fill['symbol']
        fill_price = fill['fill_price']
        quantity = fill['quantity']
        candidate_info = fill['candidate_info']
        option_type = fill['option_type']
        
        logger.info(f"[FILL-{option_type}] {symbol} @ {fill_price:.2f}, Qty={quantity}")
        
        # Add position
        position = self.position_tracker.add_position(
            symbol=symbol,
            entry_price=fill_price,
            sl_price=candidate_info['sl_price'],
            quantity=quantity,
            actual_R=candidate_info['actual_R'],
            candidate_info=candidate_info
        )
        
        # Place SL order immediately
        sl_order_id = self.order_manager.place_sl_order(
            symbol=symbol,
            trigger_price=candidate_info['sl_price'],
            quantity=quantity
        )
        
        if sl_order_id:
            logger.info(f"[SL-ORDER] {symbol} @ {candidate_info['sl_price']:.2f} | Order: {sl_order_id}")
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
                f"Expected SL: ₹{candidate_info['sl_price']:.2f}\n\n"
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
                logger.critical("🛑 HALTING TRADING DUE TO REPEATED SL FAILURES")
                
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
        
        # Cancel all orders
        self.order_manager.cancel_all_orders()
        
        # Close all positions
        self.position_tracker.close_all_positions(exit_reason, current_prices)
        
        # Save final state
        self.save_state()
        
        # Save daily summary
        # Send Telegram notification
        self.telegram.notify_daily_target(summary)
        
        summary = self.position_tracker.get_position_summary()
        self.state_manager.save_daily_summary(summary)
        
        logger.info(f"Daily Summary: {summary}")
        logger.info("Trading stopped for the day")
    
    def handle_eod_exit(self):
        """Handle end-of-day forced exit at 3:15 PM"""
        logger.warning("End-of-Day Exit (3:15 PM)")

        # Get current prices
        latest_bars = self.data_pipeline.get_all_latest_bars()
        current_prices = {symbol: bar.close for symbol, bar in latest_bars.items()}

        # Cancel all orders
        self.order_manager.cancel_all_orders()

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
    
    def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down...")
        
        # Disconnect data pipeline
        self.data_pipeline.disconnect()
        
        # Close state manager
        self.state_manager.close()
        
        logger.info("Shutdown complete")
    
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


# Global flag for graceful shutdown
shutdown_flag = False

def signal_handler(signum, frame):
    """Handle Ctrl+C signal"""
    global shutdown_flag
    print("\n[SHUTDOWN] Shutdown signal received. Exiting gracefully...")
    shutdown_flag = True
    sys.exit(0)

def main():
    """Main entry point"""
    # Register signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)

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
        # Auto mode - detect ATM and expiry
        logger.info("[AUTO] Auto-detection mode enabled")

        from .auto_detector import AutoDetector
        from .config import OPENALGO_API_KEY, OPENALGO_HOST

        detector = AutoDetector(api_key=OPENALGO_API_KEY, host=OPENALGO_HOST)
        atm_strike, expiry_date = detector.auto_detect()

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
    
    try:
        strategy.start()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Interrupted. Shutting down...")
        strategy.shutdown()


if __name__ == '__main__':
    main()

