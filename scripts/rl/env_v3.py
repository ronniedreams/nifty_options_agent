"""
TradingSessionEnv — Unified RL environment for V3 (entry + exit decisions).

Key differences from env.py (V1):
- Discrete(4): SKIP/HOLD, ENTER, EXIT_ALL, STOP_SESSION
- Two decision types: entry (swing break) and review (periodic, positions open)
- PyramidSequence with SL shifting (no per-trade TP orders)
- 24-feature observation (+ decision_type + position summary)
- BC warmstart replaces ForceEntryWrapper

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

# Review decision interval (every N bars when positions are open)
REVIEW_INTERVAL = 5

# Transaction cost parameters (Zerodha F&O Options)
BROKERAGE_PER_TRADE = 40.0
STT_RATE = 0.001
EXCHANGE_TXN_RATE = 0.000356
GST_RATE = 0.18

# Actions
ACTION_SKIP_HOLD = 0   # SKIP at entry, HOLD_ALL at review
ACTION_ENTER = 1        # Open new position in pyramid
ACTION_EXIT_ALL = 2     # Close all positions at market price
ACTION_STOP_SESSION = 3 # Exit all + end episode

# Decision types
DECISION_ENTRY = 0.0
DECISION_REVIEW = 1.0

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
# VWAPCalculator (reused from env.py)
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
# DayData (reused from env.py)
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
    """One open position within a pyramid sequence."""
    symbol: str
    option_type: str
    entry_price: float
    entry_time: datetime
    entry_bar_idx: int
    lots: int
    quantity: int
    actual_R_value: float   # sl_points * quantity at entry (risk in Rs)
    sl_points_at_entry: float


@dataclass
class PyramidSequence:
    """A group of same-symbol positions sharing a single SL.

    When a new position is added, the shared SL shifts to the new
    (tighter) level. If SL is hit, ALL positions in the sequence exit.
    """
    symbol: str
    option_type: str
    positions: List[PyramidPosition] = field(default_factory=list)
    shared_sl_trigger: float = 0.0   # highest_high + 1 (updated on add)
    highest_high: float = 0.0

    def add_position(self, pos: PyramidPosition, sl_trigger: float,
                     highest_high: float):
        self.positions.append(pos)
        # SL shifts to the new (tighter) level
        self.shared_sl_trigger = sl_trigger
        self.highest_high = highest_high

    @property
    def position_count(self) -> int:
        return len(self.positions)

    def total_quantity(self) -> int:
        return sum(p.quantity for p in self.positions)


# ---------------------------------------------------------------------------
# TradingSessionEnv
# ---------------------------------------------------------------------------

class TradingSessionEnv(gymnasium.Env):
    """
    Unified RL environment for entry + exit decisions.

    Action space: Discrete(4)
        0: SKIP (entry) / HOLD_ALL (review)
        1: ENTER — open new position
        2: EXIT_ALL — close all positions at market
        3: STOP_SESSION — exit all + end episode

    Observation space: Box(24,)
        0-11:  Global market context
        12-17: Session state
        18:    Decision type (0=entry, 1=review)
        19-23: Position summary

    Episode: one trading day. Goal-conditioned target_R / stop_R.
    """

    metadata = {'render_modes': []}

    def __init__(
        self,
        data_path: str = 'data/nifty_options_combined.parquet',
        eval_mode: bool = False,
        seed: int = 42,
        start_date: str = None,
        end_date: str = None,
        fixed_target_R: float = None,
        fixed_stop_R: float = None,
        review_interval: int = REVIEW_INTERVAL,
    ):
        super().__init__()
        self._fixed_target_R = fixed_target_R
        self._fixed_stop_R = fixed_stop_R
        self.review_interval = review_interval

        self.action_space = spaces.Discrete(4)
        self.observation_space = spaces.Box(
            -np.inf, np.inf, shape=(24,), dtype=np.float32,
        )

        # Load parquet (with date filtering at read time for memory efficiency)
        data_path = Path(data_path)
        if not data_path.is_absolute():
            data_path = PROJECT_ROOT / data_path
        logger.info(f"Loading data from {data_path} ...")

        # Build pyarrow filters to avoid loading entire file into memory
        pa_filters = []
        if start_date:
            pa_filters.append(('Datetime', '>=', pd.Timestamp(start_date)))
        if end_date:
            pa_filters.append(('Datetime', '<=', pd.Timestamp(end_date) + pd.Timedelta(days=1)))
        filters = pa_filters if pa_filters else None

        self._data = pd.read_parquet(data_path, filters=filters)

        # Downcast numeric columns to reduce memory (~60% savings)
        for col in ['Strike', 'd_to_expiry']:
            if col in self._data.columns:
                self._data[col] = pd.to_numeric(self._data[col], downcast='integer')
        for col in ['Open', 'High', 'Low', 'Close', 'Volume', 'Open Interest',
                     'Spot_Open', 'Spot_High', 'Spot_Low', 'Spot_Close']:
            if col in self._data.columns:
                self._data[col] = pd.to_numeric(self._data[col], downcast='float')

        self._data['_date'] = self._data['Datetime'].dt.date

        # Build day groups using index slicing (avoids groupby copy overhead)
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
        # Drop the full DataFrame reference — day_groups holds the views
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

        # Episode state
        self.day: Optional[DayData] = None
        self.target_R: float = 5.0
        self.stop_R: float = -5.0
        self.cumulative_R: float = 0.0
        self.trades_today: int = 0
        self.bar_idx: int = 0
        self._current_decision: Optional[dict] = None  # {type, break_info}

        # Pyramid sequences (one per option type)
        self.ce_sequence: Optional[PyramidSequence] = None
        self.pe_sequence: Optional[PyramidSequence] = None

    def _all_positions(self) -> List[PyramidPosition]:
        positions = []
        if self.ce_sequence:
            positions.extend(self.ce_sequence.positions)
        if self.pe_sequence:
            positions.extend(self.pe_sequence.positions)
        return positions

    def _position_count(self) -> int:
        return len(self._all_positions())

    def _get_sequence(self, opt_type: str) -> Optional[PyramidSequence]:
        return self.ce_sequence if opt_type == 'CE' else self.pe_sequence

    def _set_sequence(self, opt_type: str, seq: Optional[PyramidSequence]):
        if opt_type == 'CE':
            self.ce_sequence = seq
        else:
            self.pe_sequence = seq

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

        # Goal conditioning
        if self._fixed_target_R is not None:
            self.target_R = self._fixed_target_R
        else:
            self.target_R = float(self._rng.choice([3, 4, 5, 6, 7]))
        if self._fixed_stop_R is not None:
            self.stop_R = self._fixed_stop_R
        else:
            self.stop_R = float(self._rng.choice([-3, -4, -5, -6, -7]))

        # Load day
        self.day = DayData(self._day_groups[day_date], day_date)

        # Swing detector
        self.swing_detector = MultiSwingDetector()
        self.swing_detector.add_symbols(self.day.symbols)
        for det in self.swing_detector.detectors.values():
            det.is_historical_processing = True

        # Reset state
        self.ce_sequence = None
        self.pe_sequence = None
        self.cumulative_R = 0.0
        self.trades_today = 0
        self.bar_idx = 0
        self._current_decision = None
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
        reward = 0.0
        info = {}

        decision = self._current_decision
        if decision is None:
            # No pending decision — episode should end
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            return obs, 0.0, False, True, {'eod': True}

        decision_type = decision['type']

        # --- STOP_SESSION (action=3) ---
        if action == ACTION_STOP_SESSION:
            reward += self._exit_all_sequences()
            info['stop_session'] = True
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            info['final_cumR'] = self.cumulative_R
            info['final_trades'] = self.trades_today
            info['final_target_R'] = self.target_R
            info['final_stop_R'] = self.stop_R
            return obs, reward, True, False, info

        # --- EXIT_ALL (action=2) ---
        if action == ACTION_EXIT_ALL:
            reward += self._exit_all_sequences()
            info['exit_all'] = True

        # --- ENTER (action=1) ---
        elif action == ACTION_ENTER:
            if decision_type == DECISION_ENTRY and decision.get('break_info'):
                added = self._add_to_pyramid(decision['break_info'])
                if added:
                    self.trades_today += 1
                    info['entered'] = True
                else:
                    info['position_limit_hit'] = True
            # ENTER at review is invalid — treated as HOLD
            elif decision_type == DECISION_REVIEW:
                pass

        # --- SKIP/HOLD (action=0): do nothing ---

        # Advance to next decision
        obs, advance_info = self._advance_to_next_decision()

        reward += advance_info.get('reward', 0.0)
        info.update(advance_info)

        # Check termination
        total_R = self.cumulative_R + self._total_unrealized_R()
        terminated = total_R >= self.target_R or total_R <= self.stop_R
        truncated = advance_info.get('eod', False) or obs is None

        if obs is None:
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)

        if terminated or truncated:
            info['final_cumR'] = self.cumulative_R
            info['final_trades'] = self.trades_today
            info['final_target_R'] = self.target_R
            info['final_stop_R'] = self.stop_R

        return obs, reward, terminated, truncated, info

    # ------------------------------------------------------------------
    # Core loop: advance bars until next decision point
    # ------------------------------------------------------------------

    def _advance_to_next_decision(self):
        accumulated_reward = 0.0
        info = {}

        while self.bar_idx < len(self.day.timestamps):
            ts = self.day.timestamps[self.bar_idx]
            ts_time = pd.Timestamp(ts).time()

            # Force exit at 3:15 PM
            if ts_time >= FORCE_EXIT:
                accumulated_reward += self._exit_all_sequences()
                info['eod'] = True
                info['reward'] = accumulated_reward
                return None, info

            ts_pydatetime = pd.Timestamp(ts).to_pydatetime()

            # Build bars
            swing_bars = self._build_bars(ts, ts_pydatetime)

            # Update spot tracking
            self._update_spot_tracking(ts)

            # Check SL fills for pyramid sequences
            fill_reward = self._check_sl_fills(ts)
            accumulated_reward += fill_reward

            # Check daily limits after fills
            total_R = self.cumulative_R + self._total_unrealized_R()
            if total_R >= self.target_R or total_R <= self.stop_R:
                accumulated_reward += self._exit_all_sequences()
                info['reward'] = accumulated_reward
                info['daily_limit'] = True
                return None, info

            # Feed bars to swing detector
            self.swing_detector.update_all(swing_bars)

            # Update per-symbol tracking
            self._update_symbol_tracking(swing_bars)

            # Detect swing breaks
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
                info['reward'] = accumulated_reward
                return obs, info

            # Review decision: positions open AND interval reached
            if (self._position_count() > 0 and
                    self.bar_idx > 0 and
                    self.bar_idx % self.review_interval == 0):
                self._current_decision = {
                    'type': DECISION_REVIEW,
                    'break_info': None,
                    'ts': ts_pydatetime,
                }
                obs = self._compute_observation(
                    DECISION_REVIEW, ts_pydatetime,
                )
                self.bar_idx += 1
                info['reward'] = accumulated_reward
                return obs, info

            self.bar_idx += 1

        # End of day
        if self.day.timestamps:
            accumulated_reward += self._exit_all_sequences()
        info['reward'] = accumulated_reward
        info['eod'] = True
        return None, info

    # ------------------------------------------------------------------
    # Bar building + tracking (reused from env.py)
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
    # Swing break detection (reused from env.py)
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
    # Pyramid management
    # ------------------------------------------------------------------

    def _add_to_pyramid(self, break_info: dict) -> bool:
        """Add a position to the appropriate pyramid sequence."""
        opt_type = break_info['option_type']
        symbol = break_info['symbol']
        entry_price = break_info['entry_price']
        sl_trigger = break_info['sl_trigger']
        sl_points = break_info['sl_points']
        highest_high = break_info['highest_high']

        # Position limits
        if self._position_count() >= MAX_POSITIONS:
            return False

        seq = self._get_sequence(opt_type)

        # Type-specific limits
        if opt_type == 'CE':
            count = self.ce_sequence.position_count if self.ce_sequence else 0
            if count >= MAX_CE_POSITIONS:
                return False
        else:
            count = self.pe_sequence.position_count if self.pe_sequence else 0
            if count >= MAX_PE_POSITIONS:
                return False

        # Same-symbol constraint: if sequence exists, must match symbol
        if seq is not None and seq.position_count > 0:
            if seq.symbol != symbol:
                return False

        # Size position
        lots = min(int(R_VALUE / (sl_points * LOT_SIZE)), MAX_LOTS)
        if lots <= 0:
            return False
        quantity = lots * LOT_SIZE

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
        )

        if seq is None or seq.position_count == 0:
            seq = PyramidSequence(symbol=symbol, option_type=opt_type)
            self._set_sequence(opt_type, seq)

        seq.add_position(pos, sl_trigger, highest_high)
        return True

    def _check_sl_fills(self, ts) -> float:
        """Check if shared SL is hit for each pyramid sequence."""
        reward = 0.0
        for opt_type in ['CE', 'PE']:
            seq = self._get_sequence(opt_type)
            if seq is None or seq.position_count == 0:
                continue

            # Check if any bar for the sequence symbol hits the SL
            key = (ts, seq.symbol)
            if key not in self.day.bar_lookup:
                continue
            _, h, _, _, _ = self.day.bar_lookup[key]

            if h >= seq.shared_sl_trigger:
                # SL hit — exit ALL positions in sequence
                for pos in seq.positions:
                    exit_price = seq.shared_sl_trigger
                    if pos.actual_R_value > 0:
                        realized_R = (
                            (pos.entry_price - exit_price) * pos.quantity
                            / pos.actual_R_value
                        )
                    else:
                        realized_R = 0.0
                    cost_R = self._transaction_cost_R(
                        pos.entry_price, exit_price, pos.quantity,
                    )
                    realized_R -= cost_R
                    self.cumulative_R += realized_R
                    reward += realized_R

                # Clear sequence
                self._set_sequence(opt_type, None)

        return reward

    def _exit_all_sequences(self) -> float:
        """Close all positions at current market price."""
        reward = 0.0
        for opt_type in ['CE', 'PE']:
            seq = self._get_sequence(opt_type)
            if seq is None or seq.position_count == 0:
                continue

            for pos in seq.positions:
                bar = self._latest_bars.get(pos.symbol)
                if bar is not None:
                    exit_price = bar['close']
                else:
                    exit_price = pos.entry_price

                if pos.actual_R_value > 0:
                    realized_R = (
                        (pos.entry_price - exit_price) * pos.quantity
                        / pos.actual_R_value
                    )
                else:
                    realized_R = 0.0
                cost_R = self._transaction_cost_R(
                    pos.entry_price, exit_price, pos.quantity,
                )
                realized_R -= cost_R
                self.cumulative_R += realized_R
                reward += realized_R

            self._set_sequence(opt_type, None)

        return reward

    # ------------------------------------------------------------------
    # Transaction cost (reused from env.py)
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
        for pos in self._all_positions():
            bar = self._latest_bars.get(pos.symbol)
            if bar is None or pos.actual_R_value <= 0:
                continue
            unrealized = (
                (pos.entry_price - bar['close']) * pos.quantity
                / pos.actual_R_value
            )
            total += unrealized
        return total

    def _per_position_unrealized_R(self) -> List[float]:
        """Return list of unrealized R for each open position."""
        result = []
        for pos in self._all_positions():
            bar = self._latest_bars.get(pos.symbol)
            if bar is None or pos.actual_R_value <= 0:
                result.append(0.0)
                continue
            unrealized = (
                (pos.entry_price - bar['close']) * pos.quantity
                / pos.actual_R_value
            )
            result.append(unrealized)
        return result

    # ------------------------------------------------------------------
    # Feature computation (24 features)
    # ------------------------------------------------------------------

    def _compute_observation(self, decision_type: float,
                             ts_pydatetime: datetime,
                             break_info: dict = None) -> np.ndarray:
        """Build 24-feature observation vector.

        At entry: features 0-11 from break_info symbol.
        At review: features 0-11 from most recent position's symbol.
        """
        # Determine reference symbol and bar
        if decision_type == DECISION_ENTRY and break_info is not None:
            symbol = break_info['symbol']
            bar = break_info['bar']
            ref_entry_price = break_info['entry_price']
            ref_vwap = break_info['vwap']
            ref_sl_points = break_info['sl_points']
            ref_option_type = break_info['option_type']
        else:
            # Review: use most recent position's symbol
            positions = self._all_positions()
            if positions:
                latest_pos = positions[-1]
                symbol = latest_pos.symbol
                bar = self._latest_bars.get(symbol)
                if bar is None:
                    bar = {'high': 0, 'low': 0, 'close': 0, 'open': 0}
                ref_entry_price = bar.get('close', 0)
                ref_vwap = self.vwap_calc.get(symbol)
                ref_sl_points = latest_pos.sl_points_at_entry
                ref_option_type = latest_pos.option_type
            else:
                # No positions (shouldn't happen for review, but be safe)
                return np.zeros(24, dtype=np.float32)

        detector = self.swing_detector.get_detector(symbol)

        # 0. vwap_premium_pct
        vwap_premium = (
            (ref_entry_price - ref_vwap) / ref_vwap if ref_vwap > 0 else 0.0
        )

        # 1. sl_pct
        sl_pct = (
            ref_sl_points / ref_entry_price
            if ref_entry_price > 0 else 0.0
        )

        # 2. pct_from_day_high
        dh = self.day_high.get(symbol, bar['high'])
        close = bar['close'] if isinstance(bar, dict) else bar.get('close', 0)
        pct_from_day_high = (dh - close) / dh if dh > 0 else 0.0

        # 3. pct_from_day_low
        dl = self.day_low.get(symbol, bar['low'])
        pct_from_day_low = (close - dl) / close if close > 0 else 0.0

        # 4. pct_diff_swing_low_vs_prev_high
        prev_high = self._get_prev_swing_high(detector)
        pct_diff = (
            (prev_high - ref_entry_price) / prev_high
            if prev_high > 0 else 0.0
        )

        # 5. bars_since_prev_swing_high
        bars_since_high = self._get_bars_since_prev_swing_high(detector)

        # 6. avg_bar_range_pct_5
        ranges = self.bar_ranges.get(symbol)
        avg_range = float(np.mean(ranges)) if ranges and len(ranges) > 0 else 0.02

        # 7. swing_low_count_today
        swing_count = self.swing_break_count.get(ref_option_type, 0)

        # 8. is_lower_low
        is_lower_low = self._check_lower_low(detector, ref_entry_price)

        # 9. day_range_pct
        day_range = (
            (dh - dl) / ref_entry_price
            if ref_entry_price > 0 else 0.0
        )

        # 10. minutes_since_open
        market_open_dt = datetime.combine(ts_pydatetime.date(), MARKET_OPEN)
        mins = (ts_pydatetime - market_open_dt).total_seconds() / 60.0

        # 11. spot_volatility_ratio
        vol_ratio = self._compute_spot_volatility_ratio()

        # 12-17: Session state
        unrealized = self._total_unrealized_R()
        total_R = self.cumulative_R + unrealized
        dist_target = self.target_R - total_R
        dist_stop = total_R - self.stop_R

        # 18: Decision type
        # (0.0 = entry, 1.0 = review)

        # 19-23: Position summary
        pos_unrealized = self._per_position_unrealized_R()
        n_pos = self._position_count()

        avg_pos_unrealized_R = float(np.mean(pos_unrealized)) if pos_unrealized else 0.0
        max_pos_unrealized_R = float(max(pos_unrealized)) if pos_unrealized else 0.0

        # Avg bars held
        positions = self._all_positions()
        if positions:
            avg_bars_held = float(np.mean([
                max(0, self.bar_idx - p.entry_bar_idx) for p in positions
            ]))
        else:
            avg_bars_held = 0.0

        # Pyramid depth (total positions across sequences)
        pyramid_depth = n_pos

        # Avg pct from SL
        avg_pct_from_sl = 0.0
        if positions:
            pct_from_sl_list = []
            for pos in positions:
                seq = self._get_sequence(pos.option_type)
                if seq and seq.shared_sl_trigger > 0:
                    bar_p = self._latest_bars.get(pos.symbol)
                    if bar_p and bar_p['close'] > 0:
                        pct = (seq.shared_sl_trigger - bar_p['close']) / bar_p['close']
                        pct_from_sl_list.append(pct)
            if pct_from_sl_list:
                avg_pct_from_sl = float(np.mean(pct_from_sl_list))

        obs = np.array([
            vwap_premium,               # 0
            sl_pct,                     # 1
            pct_from_day_high,          # 2
            pct_from_day_low,           # 3
            pct_diff,                   # 4
            bars_since_high,            # 5
            avg_range,                  # 6
            swing_count,                # 7
            float(is_lower_low),        # 8
            day_range,                  # 9
            mins,                       # 10
            vol_ratio,                  # 11
            self.cumulative_R,          # 12
            float(n_pos),               # 13
            float(self.trades_today),   # 14
            unrealized,                 # 15
            dist_target,                # 16
            dist_stop,                  # 17
            decision_type,              # 18
            avg_pos_unrealized_R,       # 19
            max_pos_unrealized_R,       # 20
            avg_bars_held,              # 21
            float(pyramid_depth),       # 22
            avg_pct_from_sl,            # 23
        ], dtype=np.float32)

        return obs

    # ------------------------------------------------------------------
    # Helper methods (reused from env.py)
    # ------------------------------------------------------------------

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
