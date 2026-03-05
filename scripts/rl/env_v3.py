"""
TradingSessionEnv V3 — Complete redesign for RL exit agent.

The Game: Reach +5R before hitting -5R by selling options at swing breaks,
setting profit targets, and rotating between CE and PE.

Key features:
- Discrete(12): HOLD, ENTER_TP_0.5/1.0/2.0/3.0R, MARKET_EXIT_POS_1-5, EXIT_ALL, STOP_SESSION
- 46-feature observation (break context + market context + session state + per-position)
- Bracket orders: each position has SL (above entry) + TP (below entry)
- Intra-bar TP/SL execution at exact trigger prices
- Per-step delta reward + booking bonus + terminal bonus
- Market exit only when position is profitable (unrealized_R >= 0)

One episode = one trading day (9:16 AM to 3:15 PM).
"""

import logging
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gymnasium
import numpy as np
import pandas as pd
from gymnasium import spaces

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from baseline_v1_live.swing_detector import MultiSwingDetector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_POSITIONS = 5
MAX_CE_POSITIONS = 3
MAX_PE_POSITIONS = 3
R_VALUE = 6500
LOT_SIZE = 65
MAX_LOTS = 15
TARGET_SL_POINTS = 20
MIN_PRICE = 50
MAX_PRICE = 500
STRIKE_INTERVAL = 50
STRIKE_SCAN_RANGE = 20
MARKET_OPEN = dtime(9, 15)
FORCE_EXIT = dtime(15, 15)

# Transaction cost parameters (Zerodha F&O Options)
BROKERAGE_PER_TRADE = 40.0
STT_RATE = 0.001
EXCHANGE_TXN_RATE = 0.000356
GST_RATE = 0.18

# Actions: Discrete(12)
ACTION_HOLD = 0             # SKIP at entry, HOLD at review
ACTION_ENTER_TP_05 = 1      # Enter + TP at 0.5R profit
ACTION_ENTER_TP_10 = 2      # Enter + TP at 1.0R profit
ACTION_ENTER_TP_20 = 3      # Enter + TP at 2.0R profit
ACTION_ENTER_TP_30 = 4      # Enter + TP at 3.0R profit
ACTION_MARKET_EXIT_1 = 5    # Exit oldest position (only if profitable)
ACTION_MARKET_EXIT_2 = 6    # Exit 2nd oldest
ACTION_MARKET_EXIT_3 = 7    # Exit 3rd oldest
ACTION_MARKET_EXIT_4 = 8    # Exit 4th oldest
ACTION_MARKET_EXIT_5 = 9    # Exit newest (5th)
ACTION_EXIT_ALL = 10         # Market exit everything
ACTION_STOP_SESSION = 11     # Exit all + end episode

NUM_ACTIONS = 12

# TP R-level mapping for entry actions
TP_R_LEVELS = {
    ACTION_ENTER_TP_05: 0.5,
    ACTION_ENTER_TP_10: 1.0,
    ACTION_ENTER_TP_20: 2.0,
    ACTION_ENTER_TP_30: 3.0,
}

# Observation dimensions
NUM_FEATURES = 46
NUM_POSITION_SLOTS = 5
FEATURES_PER_POSITION = 5

# Decision types
DECISION_ENTRY = 0.0
DECISION_REVIEW = 1.0

# Reward: booking bonus coefficient
BOOKING_BONUS_COEFF = 0.1
# Reward: terminal bonus/penalty
TERMINAL_WIN_BONUS = 5.0
TERMINAL_LOSE_PENALTY = -5.0

MONTH_ABBREVS = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


def _expiry_yymmdd_to_ddmmmyy(yymmdd: str) -> str:
    yy = yymmdd[:2]
    mm = int(yymmdd[2:4])
    dd = yymmdd[4:6]
    return f"{dd}{MONTH_ABBREVS[mm]}{yy}"


def _make_symbol(expiry_ddmmmyy: str, strike: int, option_type: str) -> str:
    return f"NIFTY{expiry_ddmmmyy}{strike}{option_type}"


def _parse_symbol(symbol: str) -> Tuple[Optional[int], Optional[str]]:
    try:
        option_type = symbol[-2:]
        strike = int(symbol[12:-2])
        return strike, option_type
    except (ValueError, IndexError):
        return None, None


# ---------------------------------------------------------------------------
# VWAPCalculator
# ---------------------------------------------------------------------------

class VWAPCalculator:
    def __init__(self):
        self._cum_tpv: Dict[str, float] = {}
        self._cum_vol: Dict[str, float] = {}

    def reset(self):
        self._cum_tpv.clear()
        self._cum_vol.clear()

    def update(self, symbol: str, high: float, low: float, close: float,
               volume: int) -> float:
        tp = (high + low + close) / 3.0
        self._cum_tpv[symbol] = self._cum_tpv.get(symbol, 0.0) + tp * volume
        self._cum_vol[symbol] = self._cum_vol.get(symbol, 0.0) + volume
        cum_vol = self._cum_vol[symbol]
        if cum_vol > 0:
            return self._cum_tpv[symbol] / cum_vol
        return close

    def get(self, symbol: str) -> float:
        cum_vol = self._cum_vol.get(symbol, 0.0)
        if cum_vol > 0:
            return self._cum_tpv[symbol] / cum_vol
        return 0.0


# ---------------------------------------------------------------------------
# DayData
# ---------------------------------------------------------------------------

class DayData:
    def __init__(self, day_df: pd.DataFrame, current_date: date):
        self.current_date = current_date

        open_rows = day_df[day_df['Datetime'].dt.time == MARKET_OPEN]
        if len(open_rows) > 0 and not pd.isna(open_rows['Spot_Open'].iloc[0]):
            self.spot_open = float(open_rows['Spot_Open'].iloc[0])
        else:
            self.spot_open = float(day_df['Spot_Close'].iloc[0])

        self.atm_strike = round(self.spot_open / 100) * 100

        self.expiry_str = None
        self.expiry_ddmmmyy = None
        self.d_to_expiry = 0
        self._find_expiry(day_df, current_date)

        target_strikes = [
            self.atm_strike + i * STRIKE_INTERVAL
            for i in range(-STRIKE_SCAN_RANGE, STRIKE_SCAN_RANGE + 1)
        ]
        self.symbols = []
        for strike in target_strikes:
            for opt in ['CE', 'PE']:
                self.symbols.append(_make_symbol(self.expiry_ddmmmyy, strike, opt))

        target_strikes_set = set(target_strikes)
        filtered = day_df[
            (day_df['Expiry'] == self.expiry_str) &
            (day_df['Strike'].isin(target_strikes_set))
        ]

        dt_arr = filtered['Datetime'].values
        strike_arr = filtered['Strike'].values.astype(int)
        opt_arr = filtered['OptionType'].values
        open_arr = filtered['Open'].values.astype(float)
        high_arr = filtered['High'].values.astype(float)
        low_arr = filtered['Low'].values.astype(float)
        close_arr = filtered['Close'].values.astype(float)
        vol_arr = filtered['Volume'].values.astype(int)
        spot_close_arr = filtered['Spot_Close'].values.astype(float)
        spot_high_arr = filtered['Spot_High'].values.astype(float)
        spot_low_arr = filtered['Spot_Low'].values.astype(float)

        self.bar_lookup: Dict[tuple, tuple] = {}
        self.spot_lookup: Dict = {}

        for idx in range(len(dt_arr)):
            symbol = _make_symbol(
                self.expiry_ddmmmyy, strike_arr[idx], opt_arr[idx]
            )
            self.bar_lookup[(dt_arr[idx], symbol)] = (
                open_arr[idx], high_arr[idx], low_arr[idx],
                close_arr[idx], vol_arr[idx],
            )
            if dt_arr[idx] not in self.spot_lookup:
                self.spot_lookup[dt_arr[idx]] = (
                    spot_close_arr[idx], spot_high_arr[idx], spot_low_arr[idx],
                )

        all_ts = sorted(set(dt_arr))
        self.timestamps = [
            ts for ts in all_ts
            if MARKET_OPEN <= pd.Timestamp(ts).time() <= dtime(15, 30)
        ]

    def _find_expiry(self, day_df: pd.DataFrame, current_date: date):
        expiries = []
        for exp_str in day_df['Expiry'].unique():
            try:
                yy = int(exp_str[:2]) + 2000
                mm = int(exp_str[2:4])
                dd = int(exp_str[4:6])
                exp_date = date(yy, mm, dd)
                expiries.append((exp_str, exp_date))
            except (ValueError, IndexError):
                continue
        expiries.sort(key=lambda x: x[1])
        for exp_str, exp_date in expiries:
            if exp_date >= current_date:
                self.expiry_str = exp_str
                self.expiry_ddmmmyy = _expiry_yymmdd_to_ddmmmyy(exp_str)
                self.d_to_expiry = (exp_date - current_date).days
                return
        if expiries:
            exp_str, exp_date = expiries[-1]
            self.expiry_str = exp_str
            self.expiry_ddmmmyy = _expiry_yymmdd_to_ddmmmyy(exp_str)
            self.d_to_expiry = max(0, (exp_date - current_date).days)


# ---------------------------------------------------------------------------
# Pyramid dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PyramidPosition:
    """One open short position with independent SL and TP."""
    symbol: str
    option_type: str
    entry_price: float
    entry_time: datetime
    entry_bar_idx: int
    lots: int
    quantity: int
    actual_R_value: float       # sl_points * quantity at entry (risk in Rs)
    sl_points_at_entry: float
    sl_trigger: float = 0.0     # buy stop above entry (loss if hit)
    highest_high: float = 0.0
    tp_trigger: float = 0.0     # buy limit below entry (profit if hit)
    tp_R_level: float = 0.0     # TP target in R (0.5, 1.0, 2.0, 3.0)


# ---------------------------------------------------------------------------
# TradingSessionEnv
# ---------------------------------------------------------------------------

class TradingSessionEnv(gymnasium.Env):
    """
    V3 RL environment: The Daily Game.

    Goal: Reach +5R realized profit before hitting -5R by selling options
    at swing breaks, setting profit targets, and rotating CE/PE.

    Action space: Discrete(12)
        0: HOLD (skip entry / hold all positions)
        1-4: ENTER with TP at 0.5R / 1.0R / 2.0R / 3.0R
        5-9: MARKET_EXIT position 1-5 (only if profitable, oldest first)
        10: EXIT_ALL (market exit everything)
        11: STOP_SESSION (exit all + end episode)

    Observation space: Box(46,)
        0-9:   Break context (zeroed during review)
        10-13: Market context
        14-19: Session state
        20:    Decision type (0=entry, 1=review)
        21-45: Per-position state (5 slots x 5 features)

    Episode: one trading day. Fixed target +5R / stop -5R.
    """

    metadata = {'render_modes': []}

    def __init__(
        self,
        data_path: str = 'data/nifty_options_full.parquet',
        eval_mode: bool = False,
        seed: int = 42,
        start_date: str = None,
        end_date: str = None,
        fixed_target_R: float = 5.0,
        fixed_stop_R: float = -5.0,
    ):
        super().__init__()
        self._fixed_target_R = fixed_target_R
        self._fixed_stop_R = fixed_stop_R

        self.action_space = spaces.Discrete(NUM_ACTIONS)
        self.observation_space = spaces.Box(
            -np.inf, np.inf, shape=(NUM_FEATURES,), dtype=np.float32,
        )

        # Load parquet
        data_path = Path(data_path)
        if not data_path.is_absolute():
            data_path = PROJECT_ROOT / data_path
        logger.info(f"Loading data from {data_path} ...")

        pa_filters = []
        if start_date:
            pa_filters.append(('Datetime', '>=', pd.Timestamp(start_date)))
        if end_date:
            pa_filters.append(('Datetime', '<=', pd.Timestamp(end_date) + pd.Timedelta(days=1)))
        filters = pa_filters if pa_filters else None

        self._data = pd.read_parquet(data_path, filters=filters)

        for col in ['Strike', 'd_to_expiry']:
            if col in self._data.columns:
                self._data[col] = pd.to_numeric(self._data[col], downcast='integer')
        for col in ['Open', 'High', 'Low', 'Close', 'Volume', 'Open Interest',
                     'Spot_Open', 'Spot_High', 'Spot_Low', 'Spot_Close']:
            if col in self._data.columns:
                self._data[col] = pd.to_numeric(self._data[col], downcast='float')

        self._data['_date'] = self._data['Datetime'].dt.date
        self._data.sort_values('Datetime', inplace=True)
        self._data.reset_index(drop=True, inplace=True)

        self._day_groups: Dict[date, pd.DataFrame] = {}
        for d, grp in self._data.groupby('_date', sort=False, observed=True):
            self._day_groups[d] = grp
        self.trading_days = sorted(self._day_groups.keys())

        if start_date:
            start_d = date.fromisoformat(start_date)
            self.trading_days = [d for d in self.trading_days if d >= start_d]
        if end_date:
            end_d = date.fromisoformat(end_date)
            self.trading_days = [d for d in self.trading_days if d <= end_d]

        n_rows = len(self._data)
        del self._data
        self._data = None

        logger.info(
            f"Loaded {n_rows:,} rows, "
            f"{len(self.trading_days)} days "
            f"({self.trading_days[0]} to {self.trading_days[-1]})"
        )

        self.eval_mode = eval_mode
        self._day_idx = 0
        self._rng = np.random.default_rng(seed)

        # Episode state (initialized in reset)
        self.day: Optional[DayData] = None
        self.target_R: float = 5.0
        self.stop_R: float = -5.0
        self.cumulative_R: float = 0.0
        self.trades_today: int = 0
        self.bar_idx: int = 0
        self._current_decision: Optional[dict] = None
        self._prev_total_R: float = 0.0  # for delta reward

        # Positions: flat list ordered by entry time (slot 1=oldest)
        self.positions: List[PyramidPosition] = []

    def _position_count(self) -> int:
        return len(self.positions)

    def _ce_count(self) -> int:
        return sum(1 for p in self.positions if p.option_type == 'CE')

    def _pe_count(self) -> int:
        return sum(1 for p in self.positions if p.option_type == 'PE')

    def reset(self, seed=None, options=None):
        if seed is not None:
            self._rng = np.random.default_rng(seed)

        # Pick day
        if self.eval_mode:
            day_date = self.trading_days[
                self._day_idx % len(self.trading_days)
            ]
            self._day_idx += 1
        else:
            day_date = self._rng.choice(self.trading_days)

        self.target_R = self._fixed_target_R if self._fixed_target_R is not None else 5.0
        self.stop_R = self._fixed_stop_R if self._fixed_stop_R is not None else -5.0

        # Load day
        self.day = DayData(self._day_groups[day_date], day_date)

        # Swing detector
        self.swing_detector = MultiSwingDetector()
        self.swing_detector.add_symbols(self.day.symbols)
        for det in self.swing_detector.detectors.values():
            det.is_historical_processing = True

        # Reset state
        self.positions = []
        self.cumulative_R = 0.0
        self.trades_today = 0
        self.bar_idx = 0
        self._current_decision = None
        self._prev_total_R = 0.0
        self.vwap_calc = VWAPCalculator()
        self.swing_break_count = {'CE': 0, 'PE': 0}

        # Per-symbol tracking
        self.day_high: Dict[str, float] = {}
        self.day_low: Dict[str, float] = {}
        self.bar_ranges: Dict[str, deque] = {}

        # Spot tracking
        self.spot_high_so_far = 0.0
        self.spot_low_so_far = float('inf')
        self.spot_bar_ranges: deque = deque(maxlen=50)

        # Latest bar cache
        self._latest_bars: Dict[str, dict] = {}

        # Confirmed swings
        self._confirmed_swings: Dict[str, dict] = {}

        # Advance to first decision
        obs, info = self._advance_to_next_decision()
        if obs is None:
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            info['no_decisions'] = True
        return obs, info

    def step(self, action):
        info = {}
        booking_bonus = 0.0

        decision = self._current_decision
        if decision is None:
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            return obs, 0.0, False, True, {'eod': True}

        decision_type = decision['type']

        # --- STOP_SESSION (action=11) ---
        if action == ACTION_STOP_SESSION:
            self._exit_all_positions()
            info['stop_session'] = True
            total_R = self.cumulative_R
            delta = total_R - self._prev_total_R
            terminal = self._terminal_bonus()
            reward = delta + terminal
            self._prev_total_R = total_R
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            info['final_cumR'] = self.cumulative_R
            info['final_trades'] = self.trades_today
            return obs, reward, True, False, info

        # --- EXIT_ALL (action=10) ---
        if action == ACTION_EXIT_ALL:
            self._exit_all_positions()
            info['exit_all'] = True

        # --- MARKET_EXIT_POS_N (actions 5-9) ---
        elif ACTION_MARKET_EXIT_1 <= action <= ACTION_MARKET_EXIT_5:
            pos_idx = action - ACTION_MARKET_EXIT_1  # 0-based slot index
            realized = self._market_exit_position(pos_idx)
            if realized is not None:
                booking_bonus = BOOKING_BONUS_COEFF * max(0.0, realized)
                info['market_exit'] = True
                info['market_exit_R'] = realized

        # --- ENTER with TP (actions 1-4) ---
        elif action in TP_R_LEVELS:
            if decision_type == DECISION_ENTRY and decision.get('break_info'):
                tp_R = TP_R_LEVELS[action]
                added = self._add_to_pyramid(decision['break_info'], tp_R)
                if added:
                    self.trades_today += 1
                    info['entered'] = True
                    info['tp_R_level'] = tp_R
                else:
                    info['position_limit_hit'] = True
            # ENTER at review is invalid -> treated as HOLD

        # --- HOLD (action=0): do nothing ---

        # Advance to next decision
        obs, advance_info = self._advance_to_next_decision()

        # Compute delta reward
        total_R = self.cumulative_R + self._total_unrealized_R()
        delta = total_R - self._prev_total_R
        self._prev_total_R = total_R

        # Add booking bonus from advance phase (SL/TP fills)
        booking_bonus += advance_info.get('booking_bonus', 0.0)

        info.update(advance_info)

        # Check termination
        terminated = False
        truncated = advance_info.get('eod', False) or obs is None

        if advance_info.get('daily_limit', False):
            terminated = True
            truncated = False

        if obs is None:
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)

        # Terminal bonus
        terminal = 0.0
        if terminated or truncated:
            terminal = self._terminal_bonus()
            info['final_cumR'] = self.cumulative_R
            info['final_trades'] = self.trades_today

        reward = delta + booking_bonus + terminal

        return obs, reward, terminated, truncated, info

    def _terminal_bonus(self) -> float:
        if self.cumulative_R >= self.target_R:
            return TERMINAL_WIN_BONUS
        elif self.cumulative_R <= self.stop_R:
            return TERMINAL_LOSE_PENALTY
        return 0.0

    # ------------------------------------------------------------------
    # Core loop: advance bars until next decision point
    # ------------------------------------------------------------------

    def _advance_to_next_decision(self):
        info = {}
        booking_bonus = 0.0

        while self.bar_idx < len(self.day.timestamps):
            ts = self.day.timestamps[self.bar_idx]
            ts_time = pd.Timestamp(ts).time()

            # Force exit at 3:15 PM
            if ts_time >= FORCE_EXIT:
                self._exit_all_positions()
                info['eod'] = True
                info['booking_bonus'] = booking_bonus
                return None, info

            ts_pydatetime = pd.Timestamp(ts).to_pydatetime()

            # Build bars
            swing_bars = self._build_bars(ts, ts_pydatetime)

            # Update spot tracking
            self._update_spot_tracking(ts)

            # Step 1: Check SL fills (conservative — checked FIRST)
            sl_bonus = self._check_sl_fills(ts)
            booking_bonus += sl_bonus

            # Step 2: Check TP fills
            tp_bonus = self._check_tp_fills(ts)
            booking_bonus += tp_bonus

            # Step 3: Check daily limits after fills
            total_R = self.cumulative_R + self._total_unrealized_R()
            if total_R >= self.target_R or total_R <= self.stop_R:
                self._exit_all_positions()
                info['booking_bonus'] = booking_bonus
                info['daily_limit'] = True
                return None, info

            # Step 4: Feed bars to swing detector
            self.swing_detector.update_all(swing_bars)

            # Step 5: Update per-symbol tracking
            self._update_symbol_tracking(swing_bars)

            # Step 6: Detect swing breaks
            breaks = self._detect_breaks(swing_bars, ts_pydatetime)
            best = self._select_best_strike(breaks)

            # Decision priority: entry > review
            if best is not None:
                self._current_decision = {
                    'type': DECISION_ENTRY,
                    'break_info': best,
                    'ts': ts_pydatetime,
                }
                obs = self._compute_observation(
                    DECISION_ENTRY, ts_pydatetime, break_info=best,
                )
                self.bar_idx += 1
                info['booking_bonus'] = booking_bonus
                return obs, info

            # Review decision: positions open (every bar)
            if self._position_count() > 0:
                self._current_decision = {
                    'type': DECISION_REVIEW,
                    'break_info': None,
                    'ts': ts_pydatetime,
                }
                obs = self._compute_observation(
                    DECISION_REVIEW, ts_pydatetime,
                )
                self.bar_idx += 1
                info['booking_bonus'] = booking_bonus
                return obs, info

            self.bar_idx += 1

        # End of day
        if self.day.timestamps:
            self._exit_all_positions()
        info['booking_bonus'] = booking_bonus
        info['eod'] = True
        return None, info

    # ------------------------------------------------------------------
    # Bar building + tracking
    # ------------------------------------------------------------------

    def _build_bars(self, ts, ts_pydatetime) -> Dict[str, dict]:
        swing_bars = {}
        for symbol in self.day.symbols:
            key = (ts, symbol)
            if key not in self.day.bar_lookup:
                continue
            o, h, l, c, v = self.day.bar_lookup[key]
            vwap = self.vwap_calc.update(symbol, h, l, c, v)
            bar = {
                'timestamp': ts_pydatetime,
                'open': o, 'high': h, 'low': l, 'close': c,
                'volume': v, 'vwap': vwap,
            }
            swing_bars[symbol] = bar
            self._latest_bars[symbol] = bar
        return swing_bars

    def _update_spot_tracking(self, ts):
        if ts not in self.day.spot_lookup:
            return
        s_close, s_high, s_low = self.day.spot_lookup[ts]
        if s_high > self.spot_high_so_far:
            self.spot_high_so_far = s_high
        if s_low < self.spot_low_so_far:
            self.spot_low_so_far = s_low
        if s_close > 0:
            self.spot_bar_ranges.append((s_high - s_low) / s_close)

    def _update_symbol_tracking(self, bars: Dict[str, dict]):
        for symbol, bar in bars.items():
            h, l, c = bar['high'], bar['low'], bar['close']
            if symbol not in self.day_high or h > self.day_high[symbol]:
                self.day_high[symbol] = h
            if symbol not in self.day_low or l < self.day_low[symbol]:
                self.day_low[symbol] = l
            if c > 0:
                if symbol not in self.bar_ranges:
                    self.bar_ranges[symbol] = deque(maxlen=5)
                self.bar_ranges[symbol].append((h - l) / c)

    # ------------------------------------------------------------------
    # Swing break detection
    # ------------------------------------------------------------------

    def _detect_breaks(self, bars: Dict[str, dict],
                       ts_pydatetime: datetime) -> List[dict]:
        breaks = []
        for symbol in self.day.symbols:
            detector = self.swing_detector.get_detector(symbol)
            if detector is None or detector.last_swing is None:
                continue

            swing = detector.last_swing

            if swing['type'] == 'Low' and not swing.get('broken', False):
                swing_key_id = (symbol, swing['index'], swing['price'])
                existing = self._confirmed_swings.get(symbol)

                if existing is None or existing['_key'] != swing_key_id:
                    swing_idx = swing['index']
                    bars_after = detector.bars[swing_idx + 1:]
                    hh = max(
                        (b['high'] for b in bars_after),
                        default=swing['high'],
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

                self.swing_break_count[opt_type] = (
                    self.swing_break_count.get(opt_type, 0) + 1
                )

                breaks.append({
                    'symbol': symbol,
                    'option_type': opt_type,
                    'strike': strike,
                    'entry_price': cs['price'],
                    'highest_high': highest_high,
                    'sl_trigger': sl_trigger,
                    'sl_points': sl_points,
                    'break_time': ts_pydatetime,
                    'swing_time': cs['timestamp'],
                    'vwap': cs['vwap'],
                    'bar': bar,
                })

        return breaks

    def _select_best_strike(self, breaks: List[dict]) -> Optional[dict]:
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
            key=lambda x: (x['score'], not x['is_round_strike'],
                           -x['entry_price']),
        )

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def _add_to_pyramid(self, break_info: dict, tp_R_level: float) -> bool:
        """Add a short position with SL and TP bracket."""
        opt_type = break_info['option_type']
        symbol = break_info['symbol']
        entry_price = break_info['entry_price']
        sl_trigger = break_info['sl_trigger']
        sl_points = break_info['sl_points']
        highest_high = break_info['highest_high']

        # Position limits
        if self._position_count() >= MAX_POSITIONS:
            return False
        if opt_type == 'CE' and self._ce_count() >= MAX_CE_POSITIONS:
            return False
        if opt_type == 'PE' and self._pe_count() >= MAX_PE_POSITIONS:
            return False

        # Size position
        lots = min(int(R_VALUE / (sl_points * LOT_SIZE)), MAX_LOTS)
        if lots <= 0:
            return False
        quantity = lots * LOT_SIZE

        # TP trigger: entry_price - (tp_R_level * sl_points)
        # For short positions, profit when price drops below entry
        tp_trigger = entry_price - (tp_R_level * sl_points)

        pos = PyramidPosition(
            symbol=symbol,
            option_type=opt_type,
            entry_price=entry_price,
            entry_time=break_info['break_time'],
            entry_bar_idx=self.bar_idx,
            lots=lots,
            quantity=quantity,
            actual_R_value=sl_points * quantity,
            sl_points_at_entry=sl_points,
            sl_trigger=sl_trigger,
            highest_high=highest_high,
            tp_trigger=tp_trigger,
            tp_R_level=tp_R_level,
        )

        self.positions.append(pos)
        return True

    def _check_sl_fills(self, ts) -> float:
        """Check SL fills: bar HIGH >= sl_trigger -> -1R loss.
        Stops processing once daily limit is breached.
        Returns booking bonus (always 0 for SL, losses don't get bonus)."""
        surviving = []
        limit_hit = False
        for pos in self.positions:
            if limit_hit:
                surviving.append(pos)
                continue
            key = (ts, pos.symbol)
            if key not in self.day.bar_lookup:
                surviving.append(pos)
                continue
            _, h, _, _, _ = self.day.bar_lookup[key]

            if h >= pos.sl_trigger:
                exit_price = pos.sl_trigger
                realized_R = self._compute_realized_R(pos, exit_price)
                self.cumulative_R += realized_R
                # Stop filling once daily limit breached
                total_R = self.cumulative_R + self._total_unrealized_R_from(surviving)
                if total_R >= self.target_R or total_R <= self.stop_R:
                    limit_hit = True
            else:
                surviving.append(pos)

        self.positions = surviving
        return 0.0

    def _check_tp_fills(self, ts) -> float:
        """Check TP fills: bar LOW <= tp_trigger -> profit at TP level.
        Stops processing once daily limit is breached.
        Returns booking bonus for realized profits."""
        surviving = []
        booking_bonus = 0.0
        limit_hit = False

        for pos in self.positions:
            if limit_hit:
                surviving.append(pos)
                continue
            key = (ts, pos.symbol)
            if key not in self.day.bar_lookup:
                surviving.append(pos)
                continue
            _, _, l, _, _ = self.day.bar_lookup[key]

            if pos.tp_trigger > 0 and l <= pos.tp_trigger:
                exit_price = pos.tp_trigger
                realized_R = self._compute_realized_R(pos, exit_price)
                self.cumulative_R += realized_R
                booking_bonus += BOOKING_BONUS_COEFF * max(0.0, realized_R)
                # Stop filling once daily limit breached
                total_R = self.cumulative_R + self._total_unrealized_R_from(surviving)
                if total_R >= self.target_R or total_R <= self.stop_R:
                    limit_hit = True
            else:
                surviving.append(pos)

        self.positions = surviving
        return booking_bonus

    def _total_unrealized_R_from(self, positions: List[PyramidPosition]) -> float:
        """Unrealized R for a specific list of positions (used during fill checks)."""
        total = 0.0
        for pos in positions:
            bar = self._latest_bars.get(pos.symbol)
            if bar is None or pos.actual_R_value <= 0:
                continue
            total += (pos.entry_price - bar['close']) * pos.quantity / pos.actual_R_value
        return total

    def _market_exit_position(self, slot_idx: int) -> Optional[float]:
        """Market exit a specific position slot (0-based). Only if profitable.
        Returns realized_R if exited, None if invalid/unprofitable."""
        if slot_idx < 0 or slot_idx >= len(self.positions):
            return None

        pos = self.positions[slot_idx]
        bar = self._latest_bars.get(pos.symbol)
        if bar is None:
            return None

        # Compute unrealized R
        if pos.actual_R_value <= 0:
            return None
        unrealized_R = (
            (pos.entry_price - bar['close']) * pos.quantity
            / pos.actual_R_value
        )

        # Only allow market exit if profitable
        if unrealized_R < 0:
            return None

        # Exit at bar close
        exit_price = bar['close']
        realized_R = self._compute_realized_R(pos, exit_price)
        self.cumulative_R += realized_R

        # Remove position
        self.positions.pop(slot_idx)
        return realized_R

    def _exit_all_positions(self):
        """Close all positions at current market price."""
        for pos in self.positions:
            bar = self._latest_bars.get(pos.symbol)
            if bar is not None:
                exit_price = bar['close']
            else:
                exit_price = pos.entry_price
            realized_R = self._compute_realized_R(pos, exit_price)
            self.cumulative_R += realized_R
        self.positions = []

    def _compute_realized_R(self, pos: PyramidPosition, exit_price: float) -> float:
        """Compute realized R-multiple for a short position exit."""
        if pos.actual_R_value <= 0:
            return 0.0
        realized_R = (
            (pos.entry_price - exit_price) * pos.quantity
            / pos.actual_R_value
        )
        cost_R = self._transaction_cost_R(
            pos.entry_price, exit_price, pos.quantity,
        )
        return realized_R - cost_R

    # ------------------------------------------------------------------
    # Transaction cost
    # ------------------------------------------------------------------

    def _transaction_cost_R(self, entry_price: float, exit_price: float,
                            quantity: int) -> float:
        buy_turnover = entry_price * quantity
        sell_turnover = exit_price * quantity
        total_turnover = buy_turnover + sell_turnover
        brokerage = BROKERAGE_PER_TRADE
        stt = STT_RATE * sell_turnover
        exchange_txn = EXCHANGE_TXN_RATE * total_turnover
        gst = GST_RATE * (brokerage + exchange_txn)
        total_cost = brokerage + stt + exchange_txn + gst
        return total_cost / R_VALUE

    # ------------------------------------------------------------------
    # Unrealized P&L
    # ------------------------------------------------------------------

    def _total_unrealized_R(self) -> float:
        total = 0.0
        for pos in self.positions:
            bar = self._latest_bars.get(pos.symbol)
            if bar is None or pos.actual_R_value <= 0:
                continue
            unrealized = (
                (pos.entry_price - bar['close']) * pos.quantity
                / pos.actual_R_value
            )
            total += unrealized
        return total

    def _position_unrealized_R(self, pos: PyramidPosition) -> float:
        """Unrealized R for a single position."""
        bar = self._latest_bars.get(pos.symbol)
        if bar is None or pos.actual_R_value <= 0:
            return 0.0
        return (
            (pos.entry_price - bar['close']) * pos.quantity
            / pos.actual_R_value
        )

    # ------------------------------------------------------------------
    # Feature computation (46 features)
    # ------------------------------------------------------------------

    def _compute_observation(self, decision_type: float,
                             ts_pydatetime: datetime,
                             break_info: dict = None) -> np.ndarray:
        """Build 46-feature observation vector.

        Group 1 (0-9):   Break context (zeroed during review)
        Group 2 (10-13): Market context (always)
        Group 3 (14-19): Session state (always)
        Group 4 (20):    Decision type
        Group 5 (21-45): Per-position state (5 slots x 5 features)
        """
        obs = np.zeros(NUM_FEATURES, dtype=np.float32)

        # --- Group 1: Break Context (features 0-9) ---
        # At entry: from swing break. At review: all zeros.
        if decision_type == DECISION_ENTRY and break_info is not None:
            symbol = break_info['symbol']
            bar = break_info['bar']
            entry_price = break_info['entry_price']
            vwap = break_info['vwap']
            sl_points = break_info['sl_points']
            opt_type = break_info['option_type']
            detector = self.swing_detector.get_detector(symbol)

            # 0: vwap_premium_pct
            obs[0] = (entry_price - vwap) / vwap if vwap > 0 else 0.0

            # 1: sl_pct
            obs[1] = sl_points / entry_price if entry_price > 0 else 0.0

            # 2: option_type (0=CE, 1=PE)
            obs[2] = 0.0 if opt_type == 'CE' else 1.0

            # 3: pct_from_day_high
            close = bar['close']
            dh = self.day_high.get(symbol, bar['high'])
            obs[3] = (dh - close) / dh if dh > 0 else 0.0

            # 4: pct_from_day_low
            dl = self.day_low.get(symbol, bar['low'])
            obs[4] = (close - dl) / close if close > 0 else 0.0

            # 5: is_lower_low
            obs[5] = float(self._check_lower_low(detector, entry_price))

            # 6: avg_bar_range_pct_5
            ranges = self.bar_ranges.get(symbol)
            obs[6] = float(np.mean(ranges)) if ranges and len(ranges) > 0 else 0.02

            # 7: day_range_pct
            obs[7] = (dh - dl) / entry_price if entry_price > 0 else 0.0

            # 8: swing_break_count
            obs[8] = float(self.swing_break_count.get(opt_type, 0))

            # 9: d_to_expiry_norm
            obs[9] = min(self.day.d_to_expiry / 7.0, 4.0) if self.day else 0.0

        # --- Group 2: Market Context (features 10-13) ---
        # Always populated

        # 10: minutes_since_open (normalized to 0-1)
        market_open_dt = datetime.combine(ts_pydatetime.date(), MARKET_OPEN)
        mins = (ts_pydatetime - market_open_dt).total_seconds() / 60.0
        obs[10] = max(0.0, mins) / 360.0

        # 11: spot_volatility_ratio
        obs[11] = self._compute_spot_volatility_ratio()

        # 12: spot_pct_from_open
        if self.day and self.day.spot_open > 0:
            # Get latest spot close
            spot_close = self._get_latest_spot_close()
            obs[12] = (spot_close - self.day.spot_open) / self.day.spot_open if spot_close > 0 else 0.0

        # 13: spot_day_range_pct
        if self.day and self.day.spot_open > 0:
            spot_range = self.spot_high_so_far - self.spot_low_so_far
            if spot_range > 0 and self.spot_low_so_far < float('inf'):
                obs[13] = spot_range / self.day.spot_open

        # --- Group 3: Session State (features 14-19) ---
        unrealized = self._total_unrealized_R()
        total_R = self.cumulative_R + unrealized

        # 14: cumulative_R
        obs[14] = self.cumulative_R

        # 15: unrealized_R
        obs[15] = unrealized

        # 16: dist_to_target
        obs[16] = self.target_R - total_R

        # 17: dist_to_stop
        obs[17] = total_R - self.stop_R

        # 18: n_positions
        obs[18] = float(self._position_count())

        # 19: trades_today
        obs[19] = float(self.trades_today)

        # --- Group 4: Decision Type (feature 20) ---
        obs[20] = decision_type

        # --- Group 5: Per-Position State (features 21-45) ---
        # 5 slots x 5 features. Ordered by entry time (slot 0=oldest).
        # Empty slots = all zeros.
        for i, pos in enumerate(self.positions[:NUM_POSITION_SLOTS]):
            base = 21 + i * FEATURES_PER_POSITION

            # +0: pos_unrealized_R
            obs[base + 0] = self._position_unrealized_R(pos)

            # +1: pos_bars_held (normalized by 360)
            bars_held = max(0, self.bar_idx - pos.entry_bar_idx)
            obs[base + 1] = bars_held / 360.0

            # +2: pos_pct_from_sl
            bar_p = self._latest_bars.get(pos.symbol)
            if bar_p and bar_p['close'] > 0 and pos.sl_trigger > 0:
                obs[base + 2] = (pos.sl_trigger - bar_p['close']) / bar_p['close']

            # +3: pos_option_type (CE=+1, PE=-1, empty=0)
            obs[base + 3] = 1.0 if pos.option_type == 'CE' else -1.0

            # +4: pos_tp_R_level
            obs[base + 4] = pos.tp_R_level

        return obs

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _get_latest_spot_close(self) -> float:
        """Get the most recent spot close price."""
        if not self.day or not self.day.timestamps:
            return 0.0
        # Walk backwards from current bar_idx to find spot data
        idx = min(self.bar_idx, len(self.day.timestamps) - 1)
        while idx >= 0:
            ts = self.day.timestamps[idx]
            if ts in self.day.spot_lookup:
                return self.day.spot_lookup[ts][0]  # spot_close
            idx -= 1
        return self.day.spot_open if self.day else 0.0

    def _get_prev_swing_high(self, detector) -> float:
        if detector is None:
            return 0.0
        for swing in reversed(detector.swings):
            if swing['type'] == 'High':
                return swing['price']
        return 0.0

    def _get_bars_since_prev_swing_high(self, detector) -> float:
        if detector is None:
            return 0.0
        for swing in reversed(detector.swings):
            if swing['type'] == 'High':
                return max(0, len(detector.bars) - 1 - swing['index'])
        return 0.0

    def _check_lower_low(self, detector, current_low: float) -> bool:
        if detector is None:
            return False
        low_count = 0
        for swing in reversed(detector.swings):
            if swing['type'] == 'Low':
                low_count += 1
                if low_count == 2:
                    return current_low < swing['price']
        return False

    def _compute_spot_volatility_ratio(self) -> float:
        if len(self.spot_bar_ranges) < 5:
            return 1.0
        ranges = list(self.spot_bar_ranges)
        avg_5 = np.mean(ranges[-5:])
        avg_all = np.mean(ranges)
        if avg_all > 0:
            return float(avg_5 / avg_all)
        return 1.0
