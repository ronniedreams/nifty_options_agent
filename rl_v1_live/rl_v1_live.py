"""
RL V1 Live Trading Orchestrator

Main script for running the V3 QR-DQN RL agent in live markets via Upstox OpenAlgo.
Runs parallel to baseline on the same WebSocket data feeds.

Trading Logic (RL-driven, V3 Discrete(12)):
1. Monitor options for swing low breaks (same swing detector as baseline)
2. Select best strike (SL closest to 20pts, round strike, highest premium)
3. On break: build 46-feature obs -> model.predict() -> 4 TP entry levels / EXIT actions / STOP
4. Every bar with positions: review obs -> model.predict() -> per-position exit / EXIT_ALL / STOP
5. Bracket orders: SL + TP placed on entry. Track cumulative R, exit at +/-5R or 3:15 PM

Order Flow:
- Entry: Market SELL order AFTER RL decides ENTER (not proactive like baseline)
- Exit SL: SL-L BUY order placed after entry fill
- Pyramid SL shifting: cancel old SL -> place new SL for total quantity
"""

import argparse
import logging
import os
import signal
import sys
import time
from collections import deque
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pytz

from .config import (
    # Shared
    OPENALGO_API_KEY,
    OPENALGO_HOST,
    OPENALGO_WS_URL,
    PAPER_TRADING,
    MARKET_START_TIME,
    FORCE_EXIT_TIME,
    MARKET_CLOSE_TIME,
    STRIKE_SCAN_RANGE,
    STRIKE_INTERVAL,
    LOT_SIZE,
    R_VALUE,
    # V3-specific
    RLV1_STRATEGY_NAME,
    RLV1_MODEL_PATH,
    RLV1_LOG_DIR,
    RLV1_LOG_LEVEL,
    RLV1_KILL_SWITCH_FILE,
    RLV1_PAUSE_SWITCH_FILE,
    RLV1_STATE_DB_PATH,
    UPSTOX_OPENALGO_HOST,
    UPSTOX_OPENALGO_API_KEY,
    # RL params (V3 Discrete(12))
    ACTION_HOLD,
    ACTION_ENTER_TP_05,
    ACTION_ENTER_TP_10,
    ACTION_ENTER_TP_20,
    ACTION_ENTER_TP_30,
    ACTION_MARKET_EXIT_1,
    ACTION_MARKET_EXIT_5,
    ACTION_EXIT_ALL,
    ACTION_STOP_SESSION,
    TP_R_LEVELS,
    DECISION_ENTRY,
    DECISION_REVIEW,
    REVIEW_INTERVAL,
    MIN_PRICE,
    MAX_PRICE,
    TARGET_SL_POINTS,
    DAILY_TARGET_R,
    DAILY_STOP_R,
    # Auto-login
    AUTOMATED_LOGIN,
    UPSTOX_USER_ID,
    UPSTOX_MOBILE,
    UPSTOX_PASSWORD,
    UPSTOX_PIN,
    UPSTOX_TOTP_SECRET,
    UPSTOX_API_KEY,
    UPSTOX_API_SECRET,
    UPSTOX_REDIRECT_URI,
)
from .observation_builder import ObservationBuilder
from .order_manager import OrderManagerV3
from .position_tracker import PositionTrackerV3
from .pyramid_manager import PyramidManager, PyramidSequence
from .state_manager import StateManagerV3
from .telegram_notifier import TelegramNotifierV3

# Imported from baseline (shared components)
from baseline_v1_live.data_pipeline import DataPipeline
from baseline_v1_live.swing_detector import MultiSwingDetector

# Setup logging with IST timestamps
os.makedirs(RLV1_LOG_DIR, exist_ok=True)

IST = pytz.timezone('Asia/Kolkata')


class _ISTFormatter(logging.Formatter):
    def converter(self, timestamp):
        import datetime as _dt
        return _dt.datetime.fromtimestamp(timestamp, IST).timetuple()


_formatter = _ISTFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_file_handler = logging.FileHandler(
    os.path.join(RLV1_LOG_DIR, f'rl_v1_live_{datetime.now(IST).strftime("%Y%m%d")}.log')
)
_file_handler.setFormatter(_formatter)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_formatter)

logging.basicConfig(
    level=getattr(logging, RLV1_LOG_LEVEL),
    handlers=[_file_handler, _stream_handler]
)

logger = logging.getLogger(__name__)

ACTION_NAMES = {
    0: 'HOLD/SKIP', 1: 'ENTER_TP_0.5R', 2: 'ENTER_TP_1.0R',
    3: 'ENTER_TP_2.0R', 4: 'ENTER_TP_3.0R',
    5: 'MKT_EXIT_1', 6: 'MKT_EXIT_2', 7: 'MKT_EXIT_3',
    8: 'MKT_EXIT_4', 9: 'MKT_EXIT_5',
    10: 'EXIT_ALL', 11: 'STOP_SESSION',
}


def _next_weekday_916(from_dt: datetime) -> datetime:
    """Returns next weekday's 9:16 AM IST (skips Saturday/Sunday)."""
    candidate = (from_dt + timedelta(days=1)).replace(
        hour=9, minute=16, second=0, microsecond=0
    )
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _parse_symbol(symbol: str) -> Tuple[Optional[int], Optional[str]]:
    """Parse strike and option type from NIFTY symbol."""
    try:
        option_type = symbol[-2:]
        strike = int(symbol[12:-2])
        return strike, option_type
    except (ValueError, IndexError):
        return None, None


def _make_symbol(expiry_ddmmmyy: str, strike: int, option_type: str) -> str:
    return f"NIFTY{expiry_ddmmmyy}{strike}{option_type}"


class MLV3Live:
    """
    Main orchestrator for V3 RL live trading.

    Uses shared DataPipeline and MultiSwingDetector from baseline.
    Orders go through Upstox OpenAlgo (separate broker from baseline).
    """

    def __init__(self, expiry_date: str, atm_strike: int):
        self.expiry_date = expiry_date
        self.atm_strike = atm_strike
        self.shutdown_requested = False

        logger.info("=" * 80)
        logger.info("RL V1 Live Trading - Initialization")
        logger.info("=" * 80)
        logger.info(f"Expiry: {expiry_date}, ATM Strike: {atm_strike}")
        logger.info(f"Paper Trading: {PAPER_TRADING}")
        logger.info(f"Model: {RLV1_MODEL_PATH}")

        # Build symbol list (same pattern as env_v3: +-20 strikes around ATM)
        self.symbols = []
        for i in range(-STRIKE_SCAN_RANGE, STRIKE_SCAN_RANGE + 1):
            strike = atm_strike + i * STRIKE_INTERVAL
            for opt in ['CE', 'PE']:
                self.symbols.append(_make_symbol(expiry_date, strike, opt))
        logger.info(f"Monitoring {len(self.symbols)} symbols")

        # Shared components (same WS feeds as baseline)
        self.data_pipeline = DataPipeline()
        self.swing_detector = MultiSwingDetector()

        # V3-specific components
        self.obs_builder = ObservationBuilder()
        self.pyramid_mgr = PyramidManager()
        self.order_mgr = OrderManagerV3()
        self.position_tracker = PositionTrackerV3()
        self.state_mgr = StateManagerV3()
        self.telegram = TelegramNotifierV3()

        # Load RL model
        self.model = None
        self._load_model()

        # Session state
        self.bar_idx = 0
        self.session_stopped = False

        # Confirmed swings tracking (same as env_v3)
        self._confirmed_swings: Dict[str, dict] = {}

        # Crash recovery: load today's state if exists
        self._try_restore_state()

    def _load_model(self):
        """Load the trained QR-DQN model."""
        try:
            from sb3_contrib import QRDQN
            model_path = RLV1_MODEL_PATH
            if not os.path.exists(model_path):
                logger.error(f"[RL-V1-MODEL] Model not found: {model_path}")
                return
            self.model = QRDQN.load(model_path)
            logger.info(f"[RL-V1-MODEL] Loaded model from {model_path}")
        except ImportError:
            logger.error("[RL-V1-MODEL] sb3_contrib not installed, cannot load model")
        except Exception as e:
            logger.error(f"[RL-V1-MODEL] Failed to load model: {e}")

    def _try_restore_state(self):
        """Attempt crash recovery from today's state."""
        try:
            state = self.state_mgr.load_daily_state()
            if state is None:
                logger.info("[RL-V1-RECOVERY] No state to restore (new day)")
                return

            self.position_tracker.cumulative_R = state.get('cumulative_R', 0.0)
            self.position_tracker.trades_today = state.get('trades_today', 0)
            self.bar_idx = state.get('bar_idx', 0)
            self.session_stopped = bool(state.get('session_stopped', 0))

            pyramid_data = state.get('pyramid_state', {})
            if pyramid_data:
                self.pyramid_mgr = PyramidManager.from_dict(pyramid_data)

            n_pos = self.pyramid_mgr.position_count()
            if n_pos > 0 or self.position_tracker.cumulative_R != 0:
                logger.info(
                    f"[RL-V1-RECOVERY] Restored: {n_pos} positions, "
                    f"cumR={self.position_tracker.cumulative_R:+.2f}, "
                    f"trades={self.position_tracker.trades_today}, "
                    f"bar_idx={self.bar_idx}"
                )
        except Exception as e:
            logger.error(f"[RL-V1-RECOVERY] Failed to restore state: {e}")

    def _save_state(self):
        """Persist current state to SQLite."""
        try:
            self.state_mgr.save_daily_state(
                cumulative_R=self.position_tracker.cumulative_R,
                trades_today=self.position_tracker.trades_today,
                bar_idx=self.bar_idx,
                target_R=DAILY_TARGET_R,
                stop_R=DAILY_STOP_R,
                session_stopped=self.session_stopped,
                pyramid_state=self.pyramid_mgr.to_dict(),
            )
        except Exception as e:
            logger.error(f"[RL-V1-STATE] Failed to save state: {e}")

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def start(self):
        """Start V3 live trading."""
        logger.info("=" * 80)
        logger.info("Starting RL V1 Live Trading")
        logger.info("=" * 80)

        if self.model is None:
            logger.critical("[RL-V1] No RL model loaded. Cannot start.")
            self.telegram.send_error("No RL model loaded. Agent cannot start.")
            sys.exit(1)

        if self.session_stopped:
            logger.info("[RL-V1] Session was stopped by model in previous run. Exiting.")
            self.telegram.send_message("Session stopped (model decision from earlier). Not restarting.")
            return

        # If daily limit was already hit today, don't re-enter the trading loop
        cum_r = self.position_tracker.cumulative_R
        if cum_r >= DAILY_TARGET_R or cum_r <= DAILY_STOP_R:
            logger.info(
                f"[RL-V1] Daily limit already hit (cumR={cum_r:+.2f}). "
                f"Not restarting trading loop."
            )
            self.telegram.send_message(
                f"Daily limit already hit (cumR={cum_r:+.2f}). Sleeping until next session."
            )
            return

        # Connect data pipeline (Zerodha primary WS)
        self.data_pipeline.connect()
        self.data_pipeline.connect_angelone_backup()

        # Subscribe to options
        self.data_pipeline.subscribe_options(self.symbols)
        self.data_pipeline.subscribe_angelone_backup(self.symbols)

        # Load historical data
        logger.info("[RL-V1-HIST] Loading historical data...")
        self.data_pipeline.load_historical_data(symbols=self.symbols)
        self.data_pipeline.fill_initial_gap()

        # Initialize swing detector with historical bars
        for symbol in self.symbols:
            detector = self.swing_detector.get_detector(symbol)
            if detector:
                detector.current_date = None

        self.swing_detector.add_symbols(self.symbols)
        for det in self.swing_detector.detectors.values():
            det.is_historical_processing = True

        for symbol in self.symbols:
            bars = self.data_pipeline.get_bars_for_symbol(symbol)
            if bars:
                for bar in bars:
                    bar_dict = {
                        'timestamp': bar.timestamp,
                        'open': bar.open, 'high': bar.high,
                        'low': bar.low, 'close': bar.close,
                        'volume': bar.volume, 'vwap': bar.vwap,
                    }
                    self.swing_detector.update(symbol, bar_dict)
                    self.obs_builder.update_bar(symbol, bar_dict)

        self.swing_detector.enable_live_mode()
        logger.info("[RL-V1-HIST] Historical data processing complete")

        # Wait for live stream to stabilize
        time.sleep(5)

        self.telegram.send_startup(self.expiry_date, self.atm_strike, RLV1_MODEL_PATH)

        # Main trading loop
        logger.info("[RL-V1] Starting main trading loop...")
        self._run_trading_loop()

    # ------------------------------------------------------------------
    # Main trading loop
    # ------------------------------------------------------------------

    def _run_trading_loop(self):
        """Main synchronous trading loop."""
        last_state_save = time.time()
        last_bar_count: Dict[str, int] = {}

        while not self.shutdown_requested and not self.session_stopped:
            try:
                now = datetime.now(IST)

                # Kill switch check
                if os.path.exists(RLV1_KILL_SWITCH_FILE):
                    logger.info("[RL-V1] Kill switch detected. Shutting down.")
                    self._exit_all_positions('KILL_SWITCH')
                    break

                # Pause switch check
                if os.path.exists(RLV1_PAUSE_SWITCH_FILE):
                    logger.info("[RL-V1] Pause switch active. Sleeping 30s...")
                    time.sleep(30)
                    continue

                # Force exit at 3:15 PM
                if now.time() >= FORCE_EXIT_TIME:
                    logger.info("[RL-V1] Force exit time reached (3:15 PM)")
                    self._exit_all_positions('FORCE_EXIT')
                    break

                # Past market close
                if now.time() >= MARKET_CLOSE_TIME:
                    logger.info("[RL-V1] Market closed (3:30 PM)")
                    break

                # Get new bars
                new_bars = self._get_new_bars(last_bar_count)
                if not new_bars:
                    time.sleep(1)
                    continue

                self.bar_idx += 1

                # Feed bars to swing detector and observation builder
                swing_bars = {}
                for symbol, bar in new_bars.items():
                    bar_dict = {
                        'timestamp': bar.timestamp,
                        'open': bar.open, 'high': bar.high,
                        'low': bar.low, 'close': bar.close,
                        'volume': bar.volume, 'vwap': bar.vwap,
                    }
                    self.swing_detector.update(symbol, bar_dict)
                    self.obs_builder.update_bar(symbol, bar_dict)
                    swing_bars[symbol] = bar_dict

                # Update spot tracking if available
                self._update_spot_tracking()

                # Check SL fills for pyramid sequences
                self._check_sl_fills(swing_bars)

                # Check daily limits
                limit_hit = self.position_tracker.check_daily_limits(
                    self.pyramid_mgr, self.obs_builder._latest_bars
                )
                if limit_hit:
                    logger.info(f"[RL-V1] Daily {limit_hit} hit. Exiting all.")
                    self._exit_all_positions(f'DAILY_{limit_hit}')
                    break

                # Detect swing breaks
                breaks = self._detect_breaks(swing_bars)
                best = self._select_best_strike(breaks)

                # ENTRY DECISION (at swing break)
                if best is not None and not self.session_stopped:
                    self._handle_entry_decision(best)

                # REVIEW DECISION (every bar with open positions, V3)
                if (self.pyramid_mgr.position_count() > 0
                        and not self.session_stopped):
                    self._handle_review_decision()

                # Periodic state save
                if time.time() - last_state_save > 30:
                    self._save_state()
                    last_state_save = time.time()

                # Heartbeat log every 60s
                if self.bar_idx % 60 == 0:
                    n_pos = self.pyramid_mgr.position_count()
                    cum_r = self.position_tracker.cumulative_R
                    logger.info(
                        f"[RL-V1-HEARTBEAT] bar_idx={self.bar_idx} | "
                        f"positions={n_pos} | cumR={cum_r:+.2f} | "
                        f"trades={self.position_tracker.trades_today}"
                    )

            except KeyboardInterrupt:
                logger.info("[RL-V1] Interrupted by user")
                break
            except Exception as e:
                logger.error(f"[RL-V1] Error in main loop: {e}", exc_info=True)
                self.telegram.send_error(f"Main loop error: {e}")
                time.sleep(5)

        # End of session
        self._shutdown()

    # ------------------------------------------------------------------
    # Bar polling
    # ------------------------------------------------------------------

    def _get_new_bars(self, last_bar_count: Dict[str, int]) -> Dict[str, object]:
        """Check for new bars across all symbols. Returns new bars only."""
        new_bars = {}
        for symbol in self.symbols:
            bars = self.data_pipeline.get_bars_for_symbol(symbol)
            if not bars:
                continue
            prev_count = last_bar_count.get(symbol, 0)
            current_count = len(bars)
            if current_count > prev_count:
                new_bars[symbol] = bars[-1]  # Latest bar
                last_bar_count[symbol] = current_count
        return new_bars

    # ------------------------------------------------------------------
    # Spot tracking
    # ------------------------------------------------------------------

    def _update_spot_tracking(self):
        """Update spot NIFTY tracking for observation builder."""
        try:
            spot_data = self.data_pipeline.get_spot_data()
            if spot_data:
                self.obs_builder.update_spot(
                    spot_data.get('high', 0),
                    spot_data.get('low', 0),
                    spot_data.get('close', 0),
                )
        except Exception:
            pass  # Spot data is optional

    # ------------------------------------------------------------------
    # Swing break detection (from env_v3)
    # ------------------------------------------------------------------

    def _detect_breaks(self, bars: Dict[str, dict]) -> List[dict]:
        """Detect swing low breaks. Matches env_v3._detect_breaks logic."""
        breaks = []
        now = datetime.now(IST)

        for symbol in self.symbols:
            detector = self.swing_detector.get_detector(symbol)
            if detector is None or detector.last_swing is None:
                continue

            swing = detector.last_swing

            if swing['type'] == 'Low' and not swing.get('broken', False):
                swing_key_id = (symbol, swing['index'], swing['price'])
                existing = self._confirmed_swings.get(symbol)

                if existing is None or existing['_key'] != swing_key_id:
                    # New confirmed swing — compute highest high since swing
                    swing_idx = swing['index']
                    bars_after = detector.bars[swing_idx + 1:]
                    hh = max(
                        (b['high'] for b in bars_after),
                        default=swing.get('high', swing['price']),
                    )
                    self._confirmed_swings[symbol] = {
                        'price': swing['price'],
                        'timestamp': swing['timestamp'],
                        'vwap': swing['vwap'],
                        'highest_high': hh,
                        'traded': False,
                        '_key': swing_key_id,
                    }
                else:
                    # Update highest high
                    if symbol in bars:
                        bar_h = bars[symbol]['high']
                        if bar_h > existing['highest_high']:
                            existing['highest_high'] = bar_h

            if symbol not in self._confirmed_swings:
                continue
            cs = self._confirmed_swings[symbol]
            if cs['traded']:
                continue
            if symbol not in bars:
                continue

            bar = bars[symbol]
            if bar['low'] <= cs['price']:
                cs['traded'] = True

                highest_high = max(cs['highest_high'], bar['high'])
                sl_trigger = highest_high + 1
                sl_points = sl_trigger - cs['price']
                if sl_points <= 0:
                    continue

                strike, opt_type = _parse_symbol(symbol)
                if strike is None:
                    continue

                self.obs_builder.record_swing_break(opt_type)

                breaks.append({
                    'symbol': symbol,
                    'option_type': opt_type,
                    'strike': strike,
                    'entry_price': cs['price'],
                    'highest_high': highest_high,
                    'sl_trigger': sl_trigger,
                    'sl_points': sl_points,
                    'break_time': now,
                    'swing_time': cs['timestamp'],
                    'vwap': cs['vwap'],
                    'bar': bar,
                })

        return breaks

    def _select_best_strike(self, breaks: List[dict]) -> Optional[dict]:
        """Select best strike from breaks. Matches env_v3._select_best_strike."""
        if not breaks:
            return None
        candidates = []
        for b in breaks:
            if not (MIN_PRICE <= b['entry_price'] <= MAX_PRICE):
                continue
            b['score'] = abs(b['sl_points'] - TARGET_SL_POINTS)
            b['is_round_strike'] = (b['strike'] % 100 == 0)
            candidates.append(b)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda x: (x['score'], not x['is_round_strike'], -x['entry_price']),
        )

    # ------------------------------------------------------------------
    # RL model decisions
    # ------------------------------------------------------------------

    def _handle_entry_decision(self, break_info: dict):
        """Handle entry decision point: build obs -> model.predict()."""
        if self.model is None:
            return

        obs = self.obs_builder.build(
            decision_type=DECISION_ENTRY,
            break_info=break_info,
            positions=self.pyramid_mgr.all_positions(),
            swing_detector=self.swing_detector,
            bar_idx=self.bar_idx,
            cumulative_R=self.position_tracker.cumulative_R,
            trades_today=self.position_tracker.trades_today,
            target_R=DAILY_TARGET_R,
            stop_R=DAILY_STOP_R,
        )

        action, _ = self.model.predict(obs, deterministic=True)
        action = int(action)
        action_name = ACTION_NAMES.get(action, f'UNKNOWN({action})')

        logger.info(
            f"[RL-V1-ENTRY] {break_info['symbol']} break -> model: {action_name} "
            f"(entry={break_info['entry_price']:.2f}, sl={break_info['sl_points']:.1f}pts)"
        )

        if action in TP_R_LEVELS:
            tp_R = TP_R_LEVELS[action]
            self._enter_position(break_info, tp_R)
        elif action == ACTION_EXIT_ALL:
            self._exit_all_positions('MODEL_EXIT_ALL')
        elif action == ACTION_STOP_SESSION:
            self._exit_all_positions('MODEL_STOP_SESSION')
            self.session_stopped = True
        elif ACTION_MARKET_EXIT_1 <= action <= ACTION_MARKET_EXIT_5:
            pos_idx = action - ACTION_MARKET_EXIT_1
            self._market_exit_single(pos_idx)
        # HOLD (action=0): do nothing

    def _handle_review_decision(self):
        """Handle review decision point: build obs -> model.predict()."""
        if self.model is None:
            return

        obs = self.obs_builder.build(
            decision_type=DECISION_REVIEW,
            break_info=None,
            positions=self.pyramid_mgr.all_positions(),
            swing_detector=self.swing_detector,
            bar_idx=self.bar_idx,
            cumulative_R=self.position_tracker.cumulative_R,
            trades_today=self.position_tracker.trades_today,
            target_R=DAILY_TARGET_R,
            stop_R=DAILY_STOP_R,
        )

        action, _ = self.model.predict(obs, deterministic=True)
        action = int(action)
        action_name = ACTION_NAMES.get(action, f'UNKNOWN({action})')

        n_pos = self.pyramid_mgr.position_count()
        cum_r = self.position_tracker.cumulative_R
        logger.info(
            f"[RL-V1-REVIEW] bar_idx={self.bar_idx} positions={n_pos} "
            f"cumR={cum_r:+.2f} -> model: {action_name}"
        )

        if action == ACTION_EXIT_ALL:
            self._exit_all_positions('MODEL_EXIT_ALL')
        elif action == ACTION_STOP_SESSION:
            self._exit_all_positions('MODEL_STOP_SESSION')
            self.session_stopped = True
        elif ACTION_MARKET_EXIT_1 <= action <= ACTION_MARKET_EXIT_5:
            pos_idx = action - ACTION_MARKET_EXIT_1
            self._market_exit_single(pos_idx)
        # HOLD (action=0) and ENTER (actions 1-4, invalid at review) -> do nothing

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def _enter_position(self, break_info: dict, tp_R_level: float = 0.0):
        """Enter a new position via market order with bracket (SL + TP)."""
        pos = self.pyramid_mgr.add_to_pyramid(
            break_info, self.bar_idx, tp_R_level=tp_R_level,
        )
        if pos is None:
            return

        # Place market entry order
        order_id = self.order_mgr.place_market_entry(pos.symbol, pos.quantity)
        if order_id:
            pos.order_id = order_id
            self.state_mgr.log_order(
                order_id=order_id, symbol=pos.symbol, order_type='MARKET',
                action='SELL', price=break_info['entry_price'],
                trigger_price=0, quantity=pos.quantity, status='PLACED',
            )

        # Place/shift SL order for the pyramid sequence
        seq = self.pyramid_mgr.get_sequence(break_info['option_type'])
        if seq:
            new_sl_id = self.order_mgr.shift_sl_order(
                old_sl_order_id=seq.sl_order_id,
                symbol=seq.symbol,
                new_quantity=seq.total_quantity(),
                new_sl_trigger=seq.shared_sl_trigger,
            )
            seq.sl_order_id = new_sl_id

        # Place TP order for this position
        if pos.tp_trigger > 0:
            tp_order_id = self.order_mgr.place_tp_order(
                pos.symbol, pos.quantity, pos.tp_trigger,
            )
            pos.tp_order_id = tp_order_id

        # Telegram notification
        self.telegram.send_entry(
            symbol=pos.symbol, lots=pos.lots, quantity=pos.quantity,
            entry_price=break_info['entry_price'],
            sl_trigger=break_info['sl_trigger'],
            sl_points=break_info['sl_points'],
        )

        self._save_state()

    def _check_sl_fills(self, bars: Dict[str, dict]):
        """Check if shared SL is hit for each pyramid sequence."""
        for opt_type in ['CE', 'PE']:
            seq = self.pyramid_mgr.get_sequence(opt_type)
            if seq is None or seq.position_count == 0:
                continue

            bar = bars.get(seq.symbol)
            if bar is None:
                continue

            if bar['high'] >= seq.shared_sl_trigger:
                # SL hit — exit ALL positions in sequence
                exit_price = seq.shared_sl_trigger
                logger.info(
                    f"[RL-V1-SL] {seq.symbol} SL triggered at {exit_price:.2f} "
                    f"(bar high={bar['high']:.2f})"
                )

                trade_records = self.position_tracker.record_sl_exit(
                    seq, exit_price, 'SL_HIT'
                )

                # Log trades to DB and send Telegram
                for record in trade_records:
                    self.state_mgr.log_trade(**{
                        k: v for k, v in record.items() if k != 'cumulative_R'
                    })
                    self.telegram.send_exit(
                        symbol=record['symbol'],
                        entry_price=record['entry_price'],
                        exit_price=record['exit_price'],
                        realized_R=record['realized_R'],
                        pnl=record['pnl'],
                        exit_reason=record['exit_reason'],
                        cumulative_R=self.position_tracker.cumulative_R,
                    )

                # Clear the sequence
                self.pyramid_mgr.clear_sequence(opt_type)
                self._save_state()

    def _market_exit_single(self, pos_idx: int):
        """Exit a single position by index (actions 5-9)."""
        positions = self.pyramid_mgr.all_positions()
        if pos_idx < 0 or pos_idx >= len(positions):
            return

        pos = positions[pos_idx]

        # Cancel TP order if exists
        if hasattr(pos, 'tp_order_id') and pos.tp_order_id:
            self.order_mgr.cancel_order(pos.tp_order_id)

        # Place market exit
        self.order_mgr.place_market_exit(pos.symbol, pos.quantity)

        # Record the exit
        bar = self.obs_builder._latest_bars.get(pos.symbol)
        exit_price = bar['close'] if bar else pos.entry_price

        if pos.actual_R_value > 0:
            realized_R = (
                (pos.entry_price - exit_price) * pos.quantity
                / pos.actual_R_value
            )
        else:
            realized_R = 0.0

        cost_R = self.position_tracker.transaction_cost_R(
            pos.entry_price, exit_price, pos.quantity,
        )
        realized_R -= cost_R
        pnl = (pos.entry_price - exit_price) * pos.quantity - cost_R * R_VALUE

        self.position_tracker.cumulative_R += realized_R
        self.position_tracker.trades_today += 1
        if realized_R > 0:
            self.position_tracker.winning_trades += 1
        else:
            self.position_tracker.losing_trades += 1

        logger.info(
            f"[RL-V1-EXIT] {pos.symbol} MKT_EXIT_SINGLE: "
            f"Entry={pos.entry_price:.2f} Exit={exit_price:.2f} "
            f"R={realized_R:+.2f} CumR={self.position_tracker.cumulative_R:+.2f}"
        )

        self.telegram.send_exit(
            symbol=pos.symbol, entry_price=pos.entry_price,
            exit_price=exit_price, realized_R=realized_R,
            pnl=pnl, exit_reason='MKT_EXIT',
            cumulative_R=self.position_tracker.cumulative_R,
        )

        # Remove the position from its sequence
        for opt_type in ['CE', 'PE']:
            seq = self.pyramid_mgr.get_sequence(opt_type)
            if seq and pos in seq.positions:
                seq.positions.remove(pos)
                # If sequence is now empty, cancel SL and clear
                if seq.position_count == 0:
                    if seq.sl_order_id:
                        self.order_mgr.cancel_order(seq.sl_order_id)
                    self.pyramid_mgr.clear_sequence(opt_type)
                else:
                    # Shift SL for remaining quantity
                    new_sl_id = self.order_mgr.shift_sl_order(
                        old_sl_order_id=seq.sl_order_id,
                        symbol=seq.symbol,
                        new_quantity=seq.total_quantity(),
                        new_sl_trigger=seq.shared_sl_trigger,
                    )
                    seq.sl_order_id = new_sl_id
                break

        self._save_state()

    def _exit_all_positions(self, reason: str):
        """Close all positions at market price."""
        for opt_type in ['CE', 'PE']:
            seq = self.pyramid_mgr.get_sequence(opt_type)
            if seq is None or seq.position_count == 0:
                continue

            # Cancel SL order
            if seq.sl_order_id:
                self.order_mgr.cancel_order(seq.sl_order_id)

            # Cancel TP orders for all positions
            for pos in seq.positions:
                if hasattr(pos, 'tp_order_id') and pos.tp_order_id:
                    self.order_mgr.cancel_order(pos.tp_order_id)

            # Place market exit for total quantity
            self.order_mgr.place_market_exit(seq.symbol, seq.total_quantity())

            # Record exits
            trade_records = self.position_tracker.record_market_exit(
                seq, self.obs_builder._latest_bars, reason
            )

            for record in trade_records:
                self.state_mgr.log_trade(**{
                    k: v for k, v in record.items() if k != 'cumulative_R'
                })
                self.telegram.send_exit(
                    symbol=record['symbol'],
                    entry_price=record['entry_price'],
                    exit_price=record['exit_price'],
                    realized_R=record['realized_R'],
                    pnl=record['pnl'],
                    exit_reason=record['exit_reason'],
                    cumulative_R=self.position_tracker.cumulative_R,
                )

            self.pyramid_mgr.clear_sequence(opt_type)

        self._save_state()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _shutdown(self):
        """Graceful shutdown: persist state, send summary, disconnect."""
        logger.info("[RL-V1] Shutting down...")

        # Final state save
        self._save_state()

        # Daily summary
        summary = self.position_tracker.get_daily_summary()
        self.state_mgr.save_daily_summary(
            total_trades=summary['total_trades'],
            winning_trades=summary['winning_trades'],
            losing_trades=summary['losing_trades'],
            cumulative_R=summary['cumulative_R'],
            max_drawdown_R=summary['max_drawdown_R'],
            pnl=summary['pnl'],
            session_stopped=self.session_stopped,
        )

        self.telegram.send_daily_summary(
            total_trades=summary['total_trades'],
            winning=summary['winning_trades'],
            losing=summary['losing_trades'],
            cumulative_R=summary['cumulative_R'],
            pnl=summary['pnl'],
            max_dd=summary['max_drawdown_R'],
            session_stopped=self.session_stopped,
        )

        # Disconnect
        try:
            self.data_pipeline.disconnect()
        except Exception:
            pass

        self.state_mgr.close()
        logger.info("[RL-V1] Shutdown complete")

    def handle_graceful_shutdown(self):
        """Called by signal handler."""
        self.shutdown_requested = True
        self._exit_all_positions('SHUTDOWN')
        self._shutdown()


# ------------------------------------------------------------------
# Signal handler
# ------------------------------------------------------------------

strategy_instance: Optional[MLV3Live] = None


def signal_handler(signum, frame):
    global strategy_instance
    logger.info(f"[RL-V1] Signal {signum} received. Initiating shutdown...")
    if strategy_instance:
        strategy_instance.handle_graceful_shutdown()
    sys.exit(0)


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

def main():
    global strategy_instance

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(
        description='RL V1 Live Trading - QR-DQN RL Agent'
    )
    parser.add_argument('--auto', action='store_true',
                        help='Auto-detect ATM and expiry')
    parser.add_argument('--expiry', required=False,
                        help='Expiry date (e.g., 06MAR26)')
    parser.add_argument('--atm', type=int, required=False,
                        help='ATM strike price (e.g., 22500)')
    parser.add_argument('--test-obs', action='store_true',
                        help='Run observation validation test and exit')

    args = parser.parse_args()

    if args.test_obs:
        logger.info("[RL-V1-TEST] Observation validation mode — not implemented yet")
        sys.exit(0)

    if args.auto:
        logger.info("[RL-V1-AUTO] Auto-detection mode enabled")

        from baseline_v1_live.auto_detector import AutoDetector
        from baseline_v1_live.config import OPENALGO_API_KEY as BASE_API_KEY, OPENALGO_HOST as BASE_HOST

        # Initialize Telegram early for startup notifications
        from .telegram_notifier import TelegramNotifierV3
        tg = TelegramNotifierV3()

        # Upstox auto-login if configured
        if AUTOMATED_LOGIN and PAPER_TRADING:
            required = [UPSTOX_MOBILE, UPSTOX_PIN, UPSTOX_TOTP_SECRET,
                        UPSTOX_API_KEY, UPSTOX_API_SECRET]
            if all(required):
                try:
                    from .login_handler import LoginHandlerV1
                    from baseline_v1_live.config import (
                        OPENALGO_USERNAME, OPENALGO_PASSWORD,
                    )
                    handler = LoginHandlerV1(UPSTOX_OPENALGO_HOST)
                    login_ok = handler.auto_login(
                        mobile=UPSTOX_MOBILE,
                        password=UPSTOX_PASSWORD,
                        pin=UPSTOX_PIN,
                        totp_secret=UPSTOX_TOTP_SECRET,
                        api_key=UPSTOX_API_KEY,
                        api_secret=UPSTOX_API_SECRET,
                        redirect_uri=UPSTOX_REDIRECT_URI,
                        openalgo_username=OPENALGO_USERNAME,
                        openalgo_password=OPENALGO_PASSWORD,
                    )
                    if login_ok:
                        tg.send_message("[RL-V1] Upstox auto-login successful")
                    else:
                        tg.send_error("[RL-V1] Upstox auto-login FAILED - manual login required")
                except Exception as e:
                    logger.warning(f"[RL-V1-AUTO] Upstox auto-login failed: {e}")
                    tg.send_error(f"[RL-V1] Upstox auto-login exception: {e}")
            else:
                logger.warning("[RL-V1-AUTO] Upstox auto-login: missing credentials")
                tg.send_error("[RL-V1] Upstox auto-login: missing credentials in .env")

        # Wait for market open and first candle
        now = datetime.now(IST)
        market_close = dt_time(15, 30)
        is_weekend = now.weekday() >= 5
        auto_detect_time = now.replace(hour=9, minute=16, second=0, microsecond=0)

        if is_weekend or now.time() >= market_close:
            reason = "weekend" if is_weekend else "after market hours"
            next_trading_day = _next_weekday_916(now)
            wait_seconds = (next_trading_day - now).total_seconds()
            wait_hours = wait_seconds / 3600
            logger.info(f"[RL-V1-AUTO] Market closed ({reason}). Sleeping {wait_hours:.1f}h until {next_trading_day.strftime('%a %H:%M')}.")
            tg.send_message(
                f"[RL-V1] Market closed ({reason}). Sleeping until next trading day "
                f"({next_trading_day.strftime('%a %d %b %H:%M')} IST, {wait_hours:.1f}h)."
            )
            time.sleep(wait_seconds)
            # Re-exec the process so login runs fresh (tokens expire overnight)
            logger.info("[RL-V1-AUTO] Woke up. Re-executing process for fresh login...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        elif now < auto_detect_time:
            wait_seconds = (auto_detect_time - now).total_seconds()
            wait_min = int(wait_seconds / 60)
            logger.info(f"[RL-V1-AUTO] Waiting {wait_seconds:.0f}s for 9:16 AM...")
            tg.send_message(
                f"[RL-V1] Waiting for market open and first candle to close... "
                f"({wait_min} min until 9:16 AM)"
            )
            time.sleep(wait_seconds)

        # Auto-detect ATM and expiry using baseline's AutoDetector
        temp_pipeline = None
        try:
            temp_pipeline = DataPipeline()
            temp_pipeline.connect()
            temp_pipeline.subscribe_options([], spot_symbol="Nifty 50")
            time.sleep(3)
        except Exception as e:
            logger.warning(f"[RL-V1-AUTO] WebSocket connection failed: {e}")
            temp_pipeline = None

        detector = AutoDetector(
            api_key=BASE_API_KEY, host=BASE_HOST,
            data_pipeline=temp_pipeline, spot_symbol="Nifty 50",
        )
        atm_strike, expiry_date = detector.auto_detect()

        if temp_pipeline:
            temp_pipeline.disconnect()

        logger.info(f"[RL-V1-AUTO] Detected ATM: {atm_strike}, Expiry: {expiry_date}")
    else:
        if not args.expiry or not args.atm:
            parser.error("--expiry and --atm required when --auto not used")
        atm_strike = args.atm
        expiry_date = args.expiry

    # Create and start strategy
    strategy = MLV3Live(expiry_date=expiry_date, atm_strike=atm_strike)
    strategy_instance = strategy

    try:
        strategy.start()
    except KeyboardInterrupt:
        print("\n[RL-V1-SHUTDOWN] Interrupted. Shutting down...")
        strategy.handle_graceful_shutdown()
    except Exception as e:
        logger.critical(f"[RL-V1] Fatal error: {e}", exc_info=True)
        strategy.handle_graceful_shutdown()


if __name__ == '__main__':
    main()
