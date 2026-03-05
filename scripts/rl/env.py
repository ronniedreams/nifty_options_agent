"""
TradingEntryEnv — Gymnasium environment for RL Entry Model (QR-DQN).

Replays historical 1-minute OHLCV bars through the production SwingDetector,
presents 18 global features at swing-break decision points, simulates TP/SL
limit orders, tracks multiple positions (pyramiding), and manages episodes
with goal-conditioned daily limits.

One episode = one trading day (9:16 AM to 3:15 PM).

Action space: Discrete(8) — SKIP + 6 ENTER targets (0.3-2.0R) + STOP_SESSION
Observation space: Box(18,) or Box(24,) with optional probability model features.
"""

import logging
import sys
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gymnasium
import numpy as np
import pandas as pd
from gymnasium import spaces

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from baseline_v1_live.swing_detector import MultiSwingDetector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGETS = [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]
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
STRIKE_SCAN_RANGE = 20  # ±20 strikes from ATM
MARKET_OPEN = dtime(9, 15)
FORCE_EXIT = dtime(15, 15)

# Transaction cost parameters (Zerodha F&O Options)
BROKERAGE_PER_TRADE = 40.0        # Rs.20 buy + Rs.20 sell (flat)
STT_RATE = 0.001                  # 0.1% on sell-side turnover
EXCHANGE_TXN_RATE = 0.000356      # ~0.0356% on total turnover
GST_RATE = 0.18                   # 18% on (brokerage + exchange txn)

MONTH_ABBREVS = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


def _expiry_yymmdd_to_ddmmmyy(yymmdd: str) -> str:
    """Convert '220106' -> '06JAN22'."""
    yy = yymmdd[:2]
    mm = int(yymmdd[2:4])
    dd = yymmdd[4:6]
    return f"{dd}{MONTH_ABBREVS[mm]}{yy}"


def _make_symbol(expiry_ddmmmyy: str, strike: int, option_type: str) -> str:
    return f"NIFTY{expiry_ddmmmyy}{strike}{option_type}"


def _parse_symbol(symbol: str) -> Tuple[Optional[int], Optional[str]]:
    """Parse NIFTY option symbol -> (strike, option_type)."""
    try:
        option_type = symbol[-2:]
        strike = int(symbol[12:-2])
        return strike, option_type
    except (ValueError, IndexError):
        return None, None


# ---------------------------------------------------------------------------
# Position dataclass
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """One open position tracked by the environment."""
    symbol: str
    option_type: str       # 'CE' or 'PE'
    entry_price: float     # swing_low price (short entry)
    entry_time: datetime
    entry_bar_idx: int
    sl_trigger: float      # highest_high + 1
    sl_points: float       # sl_trigger - entry_price
    target_R: float        # agent's chosen target multiple
    tp_price: float        # entry_price - (target_R * sl_points)
    lots: int
    quantity: int           # lots * LOT_SIZE
    actual_R_value: float   # sl_points * quantity (Rs at risk)


# ---------------------------------------------------------------------------
# VWAPCalculator (same as build_trade_universe.py)
# ---------------------------------------------------------------------------

class VWAPCalculator:
    """Cumulative VWAP = sum(TP * V) / sum(V) per symbol per day."""

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
# DayData — pre-loaded + indexed bar data for one trading day
# ---------------------------------------------------------------------------

class DayData:
    """Pre-loads and indexes one day's data from the parquet subset."""

    def __init__(self, day_df: pd.DataFrame, current_date: date):
        self.current_date = current_date

        # Spot open at 9:15
        open_rows = day_df[day_df['Datetime'].dt.time == MARKET_OPEN]
        if len(open_rows) > 0 and not pd.isna(open_rows['Spot_Open'].iloc[0]):
            self.spot_open = float(open_rows['Spot_Open'].iloc[0])
        else:
            self.spot_open = float(day_df['Spot_Close'].iloc[0])

        self.atm_strike = round(self.spot_open / 100) * 100

        # Find nearest expiry >= current_date
        self.expiry_str = None  # YYMMDD format
        self.expiry_ddmmmyy = None
        self.d_to_expiry = 0
        self._find_expiry(day_df, current_date)

        # Generate symbols: ATM ± STRIKE_SCAN_RANGE at 50pt intervals × CE/PE
        target_strikes = [
            self.atm_strike + i * STRIKE_INTERVAL
            for i in range(-STRIKE_SCAN_RANGE, STRIKE_SCAN_RANGE + 1)
        ]
        self.symbols = []
        for strike in target_strikes:
            for opt in ['CE', 'PE']:
                self.symbols.append(_make_symbol(self.expiry_ddmmmyy, strike, opt))

        # Filter day data to our expiry and strikes
        target_strikes_set = set(target_strikes)
        filtered = day_df[
            (day_df['Expiry'] == self.expiry_str) &
            (day_df['Strike'].isin(target_strikes_set))
        ]

        # Build lookups using numpy arrays for speed
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

        # Sorted unique timestamps (market hours only)
        all_ts = sorted(set(dt_arr))
        self.timestamps = [
            ts for ts in all_ts
            if MARKET_OPEN <= pd.Timestamp(ts).time() <= dtime(15, 30)
        ]

    def _find_expiry(self, day_df: pd.DataFrame, current_date: date):
        """Find nearest expiry >= current_date from available data."""
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

        # Fallback: use latest expiry
        if expiries:
            exp_str, exp_date = expiries[-1]
            self.expiry_str = exp_str
            self.expiry_ddmmmyy = _expiry_yymmdd_to_ddmmmyy(exp_str)
            self.d_to_expiry = max(0, (exp_date - current_date).days)


# ---------------------------------------------------------------------------
# TradingEntryEnv — Gymnasium environment
# ---------------------------------------------------------------------------

class TradingEntryEnv(gymnasium.Env):
    """
    RL environment for the swing-break entry model.

    Replays one trading day per episode. The agent decides ENTER/SKIP at each
    swing break, choosing a target R-multiple for TP placement. Positions are
    tracked with TP/SL limit orders and force-exited at 3:15 PM.

    Goal-conditioned: each episode randomizes target_R and stop_R thresholds.
    """

    metadata = {'render_modes': []}

    def __init__(
        self,
        data_path: str = 'data/nifty_options_combined.parquet',
        outcome_model_path: str = None,
        eval_mode: bool = False,
        seed: int = 42,
        start_date: str = None,
        end_date: str = None,
        fixed_target_R: float = None,
        fixed_stop_R: float = None,
    ):
        super().__init__()
        self._fixed_target_R = fixed_target_R
        self._fixed_stop_R = fixed_stop_R

        # 0=SKIP, 1-6=ENTER at target R, 7=STOP_SESSION
        self.action_space = spaces.Discrete(8)

        # Load optional probability model
        self.outcome_model = None
        if outcome_model_path:
            from scripts.build_probability_model import TradeOutcomeModel
            self.outcome_model = TradeOutcomeModel.load(outcome_model_path)

        n_features = 24 if self.outcome_model is not None else 18
        self.observation_space = spaces.Box(
            -np.inf, np.inf, shape=(n_features,), dtype=np.float32,
        )

        # Load parquet, group by date
        data_path = Path(data_path)
        if not data_path.is_absolute():
            data_path = PROJECT_ROOT / data_path
        logger.info(f"Loading data from {data_path} ...")
        self._data = pd.read_parquet(data_path)
        self._data['_date'] = self._data['Datetime'].dt.date
        self._day_groups: Dict[date, pd.DataFrame] = {
            d: grp for d, grp in self._data.groupby('_date')
        }
        self.trading_days = sorted(self._day_groups.keys())

        # Filter to date range if specified
        if start_date:
            start_d = date.fromisoformat(start_date)
            self.trading_days = [d for d in self.trading_days if d >= start_d]
        if end_date:
            end_d = date.fromisoformat(end_date)
            self.trading_days = [d for d in self.trading_days if d <= end_d]

        logger.info(
            f"Loaded {len(self._data):,} rows, "
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
        self.positions: List[Position] = []
        self.cumulative_R: float = 0.0
        self.trades_today: int = 0
        self.bar_idx: int = 0
        self._current_break: Optional[dict] = None

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

        # Goal conditioning (fixed values override random for eval)
        if self._fixed_target_R is not None:
            self.target_R = self._fixed_target_R
        else:
            self.target_R = float(self._rng.choice([3, 4, 5, 6, 7]))
        if self._fixed_stop_R is not None:
            self.stop_R = self._fixed_stop_R
        else:
            self.stop_R = float(self._rng.choice([-3, -4, -5, -6, -7]))

        # Load day data
        self.day = DayData(self._day_groups[day_date], day_date)

        # Init swing detector
        self.swing_detector = MultiSwingDetector()
        self.swing_detector.add_symbols(self.day.symbols)
        for det in self.swing_detector.detectors.values():
            det.is_historical_processing = True

        # Reset state
        self.positions = []
        self.cumulative_R = 0.0
        self.trades_today = 0
        self.bar_idx = 0
        self._current_break = None
        self.vwap_calc = VWAPCalculator()
        self.swing_break_count = {'CE': 0, 'PE': 0}

        # Per-symbol tracking
        self.day_high: Dict[str, float] = {}
        self.day_low: Dict[str, float] = {}
        self.bar_ranges: Dict[str, deque] = {}  # {symbol: deque(maxlen=5)}

        # Spot tracking for volatility ratio
        self.spot_high_so_far = 0.0
        self.spot_low_so_far = float('inf')
        self.spot_bar_ranges: deque = deque(maxlen=50)

        # Latest bar cache for unrealized P&L / force exit
        self._latest_bars: Dict[str, dict] = {}

        # Confirmed swing lows: {symbol: {price, timestamp, vwap, highest_high, traded, _key}}
        self._confirmed_swings: Dict[str, dict] = {}

        # Advance to first decision point
        obs, info = self._advance_to_next_decision()
        if obs is None:
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            info['no_decisions'] = True
        return obs, info

    def step(self, action):
        reward = 0.0
        info = {}

        # STOP_SESSION: agent decides to end the day
        if action == 7:
            # Force exit all open positions at current prices
            if self.positions:
                ts = self.day.timestamps[min(self.bar_idx, len(self.day.timestamps) - 1)]
                reward += self._force_exit_all(ts)
            info['stop_session'] = True
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            terminated = True
            truncated = False
            # Lazy agent penalty still applies
            if self.trades_today == 0:
                reward -= 0.5
            info['final_cumR'] = self.cumulative_R
            info['final_trades'] = self.trades_today
            info['final_target_R'] = self.target_R
            info['final_stop_R'] = self.stop_R
            return obs, reward, terminated, truncated, info

        # Apply action (SKIP=0, ENTER=1-6)
        if action > 0 and self._current_break is not None:
            target_R_chosen = TARGETS[action - 1]
            pos = self._create_position(self._current_break, target_R_chosen)
            if pos is not None:
                self.positions.append(pos)
                self.trades_today += 1
                info['entered'] = True
                info['target_R'] = target_R_chosen
            else:
                info['position_limit_hit'] = True

        # Advance to next decision point
        obs, advance_info = self._advance_to_next_decision()

        # Collect rewards from closed positions during advance
        reward = advance_info.get('reward', 0.0)
        info.update(advance_info)

        # Check termination
        total_R = self.cumulative_R + self._total_unrealized_R()
        terminated = total_R >= self.target_R or total_R <= self.stop_R
        truncated = advance_info.get('eod', False) or obs is None

        # Lazy agent penalty: zero-trade episodes
        if (terminated or truncated) and self.trades_today == 0:
            reward -= 0.5

        if obs is None:
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)

        # Add episode summary to info (readable by callback before reset)
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
        """Process bars until next swing break decision point.

        Returns (observation, info_dict). observation=None if episode ends.
        """
        accumulated_reward = 0.0
        info = {}

        while self.bar_idx < len(self.day.timestamps):
            ts = self.day.timestamps[self.bar_idx]
            ts_time = pd.Timestamp(ts).time()

            # Force exit at 3:15 PM
            if ts_time >= FORCE_EXIT:
                ts_py = pd.Timestamp(ts).to_pydatetime()
                accumulated_reward += self._force_exit_all(ts_py)
                info['eod'] = True
                info['reward'] = accumulated_reward
                return None, info

            ts_pydatetime = pd.Timestamp(ts).to_pydatetime()

            # Build bars for this timestamp
            swing_bars = self._build_bars(ts, ts_pydatetime)

            # Update spot tracking
            self._update_spot_tracking(ts)

            # Check TP/SL fills for open positions
            fill_reward = self._check_fills(ts)
            accumulated_reward += fill_reward

            # Check daily limits after fills
            total_R = self.cumulative_R + self._total_unrealized_R()
            if total_R >= self.target_R or total_R <= self.stop_R:
                accumulated_reward += self._force_exit_all(ts_pydatetime)
                info['reward'] = accumulated_reward
                info['daily_limit'] = True
                return None, info

            # Feed bars to SwingDetector (handles break detection internally)
            self.swing_detector.update_all(swing_bars)

            # Update per-symbol tracking
            self._update_symbol_tracking(swing_bars)

            # Check for swing breaks via confirmed_swings tracking
            breaks = self._detect_breaks(swing_bars, ts_pydatetime)

            # Select best strike among breaks
            best = self._select_best_strike(breaks)

            self.bar_idx += 1

            if best is not None:
                self._current_break = best
                obs = self._compute_observation(best, ts_pydatetime)
                info['reward'] = accumulated_reward
                return obs, info

        # End of day — no more bars
        if self.day.timestamps:
            last_ts = pd.Timestamp(
                self.day.timestamps[-1]
            ).to_pydatetime()
            accumulated_reward += self._force_exit_all(last_ts)
        info['reward'] = accumulated_reward
        info['eod'] = True
        return None, info

    # ------------------------------------------------------------------
    # Bar building + tracking
    # ------------------------------------------------------------------

    def _build_bars(self, ts, ts_pydatetime) -> Dict[str, dict]:
        """Build bar dicts for all symbols at timestamp, with VWAP."""
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
        """Update spot high/low/bar ranges for volatility ratio."""
        if ts not in self.day.spot_lookup:
            return
        s_close, s_high, s_low = self.day.spot_lookup[ts]
        if s_high > self.spot_high_so_far:
            self.spot_high_so_far = s_high
        if s_low < self.spot_low_so_far:
            self.spot_low_so_far = s_low
        # Spot bar range for volatility ratio
        if s_close > 0:
            self.spot_bar_ranges.append((s_high - s_low) / s_close)

    def _update_symbol_tracking(self, bars: Dict[str, dict]):
        """Update per-symbol day_high, day_low, bar_ranges deques."""
        for symbol, bar in bars.items():
            h, l, c = bar['high'], bar['low'], bar['close']
            # Day high/low
            if symbol not in self.day_high or h > self.day_high[symbol]:
                self.day_high[symbol] = h
            if symbol not in self.day_low or l < self.day_low[symbol]:
                self.day_low[symbol] = l
            # Bar range for avg_bar_range_pct_5
            if c > 0:
                if symbol not in self.bar_ranges:
                    self.bar_ranges[symbol] = deque(maxlen=5)
                self.bar_ranges[symbol].append((h - l) / c)

    # ------------------------------------------------------------------
    # Swing break detection (mirrors build_trade_universe.py pattern)
    # ------------------------------------------------------------------

    def _detect_breaks(self, bars: Dict[str, dict],
                       ts_pydatetime: datetime) -> List[dict]:
        """Track confirmed swing lows and detect breaks."""
        breaks = []

        for symbol in self.day.symbols:
            detector = self.swing_detector.get_detector(symbol)
            if detector is None or detector.last_swing is None:
                continue

            swing = detector.last_swing

            # Track confirmed swing lows
            if swing['type'] == 'Low' and not swing.get('broken', False):
                swing_key_id = (symbol, swing['index'], swing['price'])
                existing = self._confirmed_swings.get(symbol)

                if existing is None or existing['_key'] != swing_key_id:
                    # New or updated swing low — compute highest_high
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
                    # Same swing — update highest_high with current bar
                    if symbol in bars:
                        bar_h = bars[symbol]['high']
                        if bar_h > existing['highest_high']:
                            existing['highest_high'] = bar_h

            # Check for swing break
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
        """Among breaks, pick SL-closest-to-20 with price 50-500."""
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
    # Order simulation
    # ------------------------------------------------------------------

    def _create_position(self, break_info: dict,
                         target_R: float) -> Optional[Position]:
        """Create a new position from a swing break."""
        opt_type = break_info['option_type']

        # Position limits
        ce_count = sum(1 for p in self.positions if p.option_type == 'CE')
        pe_count = sum(1 for p in self.positions if p.option_type == 'PE')
        if len(self.positions) >= MAX_POSITIONS:
            return None
        if opt_type == 'CE' and ce_count >= MAX_CE_POSITIONS:
            return None
        if opt_type == 'PE' and pe_count >= MAX_PE_POSITIONS:
            return None

        entry_price = break_info['entry_price']
        sl_trigger = break_info['sl_trigger']
        sl_points = break_info['sl_points']

        lots = min(int(R_VALUE / (sl_points * LOT_SIZE)), MAX_LOTS)
        if lots <= 0:
            return None
        quantity = lots * LOT_SIZE

        # TP for shorts: profit when price drops
        tp_price = entry_price - (target_R * sl_points)

        return Position(
            symbol=break_info['symbol'],
            option_type=opt_type,
            entry_price=entry_price,
            entry_time=break_info['break_time'],
            entry_bar_idx=self.bar_idx,
            sl_trigger=sl_trigger,
            sl_points=sl_points,
            target_R=target_R,
            tp_price=tp_price,
            lots=lots,
            quantity=quantity,
            actual_R_value=sl_points * quantity,
        )

    def _transaction_cost_R(self, entry_price: float, exit_price: float,
                            quantity: int) -> float:
        """Compute round-trip transaction cost in R-multiples.

        Based on Zerodha F&O charges:
        - Brokerage: Rs.40 flat (Rs.20 buy + Rs.20 sell)
        - STT: 0.1% of sell-side turnover
        - Exchange txn: ~0.0356% of total turnover
        - GST: 18% of (brokerage + exchange txn)
        - SEBI + stamp duty: negligible, ignored
        """
        buy_turnover = entry_price * quantity
        sell_turnover = exit_price * quantity
        total_turnover = buy_turnover + sell_turnover

        brokerage = BROKERAGE_PER_TRADE
        stt = STT_RATE * sell_turnover
        exchange_txn = EXCHANGE_TXN_RATE * total_turnover
        gst = GST_RATE * (brokerage + exchange_txn)

        total_cost = brokerage + stt + exchange_txn + gst
        return total_cost / R_VALUE

    def _check_fills(self, ts) -> float:
        """Check TP and SL fills for all open positions. Return total reward."""
        reward = 0.0
        closed = []

        for i, pos in enumerate(self.positions):
            key = (ts, pos.symbol)
            if key not in self.day.bar_lookup:
                continue
            _, h, l, _, _ = self.day.bar_lookup[key]

            tp_hit = l <= pos.tp_price
            sl_hit = h >= pos.sl_trigger

            if tp_hit and sl_hit:
                # Both in same bar — assume TP fills first (shorts: low before high)
                realized_R = pos.target_R
                exit_price = pos.tp_price
            elif tp_hit:
                realized_R = pos.target_R
                exit_price = pos.tp_price
            elif sl_hit:
                realized_R = -1.0
                exit_price = pos.sl_trigger
            else:
                continue

            # Deduct transaction costs
            cost_R = self._transaction_cost_R(
                pos.entry_price, exit_price, pos.quantity,
            )
            realized_R -= cost_R

            self.cumulative_R += realized_R
            reward += realized_R
            closed.append(i)

        for i in reversed(closed):
            self.positions.pop(i)
        return reward

    def _force_exit_all(self, ts_pydatetime: datetime) -> float:
        """Market exit all positions at current close."""
        reward = 0.0
        for pos in self.positions:
            bar = self._latest_bars.get(pos.symbol)
            if bar is not None:
                exit_price = bar['close']
            else:
                exit_price = pos.entry_price  # No data fallback

            if pos.actual_R_value > 0:
                realized_R = (
                    (pos.entry_price - exit_price) * pos.quantity
                    / pos.actual_R_value
                )
            else:
                realized_R = 0.0

            # Deduct transaction costs
            cost_R = self._transaction_cost_R(
                pos.entry_price, exit_price, pos.quantity,
            )
            realized_R -= cost_R

            self.cumulative_R += realized_R
            reward += realized_R
        self.positions.clear()
        return reward

    # ------------------------------------------------------------------
    # Unrealized P&L
    # ------------------------------------------------------------------

    def _total_unrealized_R(self) -> float:
        """Sum unrealized R across all open positions."""
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

    # ------------------------------------------------------------------
    # Feature computation
    # ------------------------------------------------------------------

    def _compute_observation(self, break_info: dict,
                             ts_pydatetime: datetime) -> np.ndarray:
        """Build 18-feature observation vector at a decision point."""
        symbol = break_info['symbol']
        bar = break_info['bar']
        detector = self.swing_detector.get_detector(symbol)

        # 0. vwap_premium_pct
        vwap = break_info['vwap']
        vwap_premium = (
            (break_info['entry_price'] - vwap) / vwap if vwap > 0 else 0.0
        )

        # 1. sl_pct
        sl_pct = (
            break_info['sl_points'] / break_info['entry_price']
            if break_info['entry_price'] > 0 else 0.0
        )

        # 2. pct_from_day_high (per-symbol)
        dh = self.day_high.get(symbol, bar['high'])
        pct_from_day_high = (dh - bar['close']) / dh if dh > 0 else 0.0

        # 3. pct_from_day_low (per-symbol)
        dl = self.day_low.get(symbol, bar['low'])
        pct_from_day_low = (
            (bar['close'] - dl) / bar['close'] if bar['close'] > 0 else 0.0
        )

        # 4. pct_diff_swing_low_vs_prev_high
        prev_high = self._get_prev_swing_high(detector)
        pct_diff = (
            (prev_high - break_info['entry_price']) / prev_high
            if prev_high > 0 else 0.0
        )

        # 5. bars_since_prev_swing_high
        bars_since_high = self._get_bars_since_prev_swing_high(detector)

        # 6. avg_bar_range_pct_5
        ranges = self.bar_ranges.get(symbol)
        avg_range = float(np.mean(ranges)) if ranges and len(ranges) > 0 else 0.02

        # 7. swing_low_count_today
        opt_type = break_info['option_type']
        swing_count = self.swing_break_count.get(opt_type, 0)

        # 8. is_lower_low
        is_lower_low = self._check_lower_low(
            detector, break_info['entry_price']
        )

        # 9. day_range_pct
        day_range = (
            (dh - dl) / break_info['entry_price']
            if break_info['entry_price'] > 0 else 0.0
        )

        # 10. minutes_since_open
        market_open_dt = datetime.combine(ts_pydatetime.date(), MARKET_OPEN)
        mins = (ts_pydatetime - market_open_dt).total_seconds() / 60.0

        # 11. spot_volatility_ratio
        vol_ratio = self._compute_spot_volatility_ratio()

        # 12-15. Session state
        unrealized = self._total_unrealized_R()

        # 16-17. Game parameters
        total_R = self.cumulative_R + unrealized
        dist_target = self.target_R - total_R
        dist_stop = total_R - self.stop_R

        obs = np.array([
            vwap_premium,           # 0
            sl_pct,                 # 1
            pct_from_day_high,      # 2
            pct_from_day_low,       # 3
            pct_diff,               # 4
            bars_since_high,        # 5
            avg_range,              # 6
            swing_count,            # 7
            float(is_lower_low),    # 8
            day_range,              # 9
            mins,                   # 10
            vol_ratio,              # 11
            self.cumulative_R,      # 12
            len(self.positions),    # 13
            self.trades_today,      # 14
            unrealized,             # 15
            dist_target,            # 16
            dist_stop,              # 17
        ], dtype=np.float32)

        # Optional: append EV values from probability model
        if self.outcome_model is not None:
            features_dict = self._build_outcome_model_features(break_info)
            probs = self.outcome_model.predict_mfe_probs(features_dict)
            evs = np.array([
                probs.get(t, 0.0) * t + (1 - probs.get(t, 0.0)) * (-1.0)
                for t in TARGETS
            ], dtype=np.float32)
            obs = np.concatenate([obs, evs])

        return obs

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _get_prev_swing_high(self, detector) -> float:
        """Find the most recent swing high price from detector.swings."""
        if detector is None:
            return 0.0
        for swing in reversed(detector.swings):
            if swing['type'] == 'High':
                return swing['price']
        return 0.0

    def _get_bars_since_prev_swing_high(self, detector) -> float:
        """Bars from last swing high confirm to current bar."""
        if detector is None:
            return 0.0
        for swing in reversed(detector.swings):
            if swing['type'] == 'High':
                return max(0, len(detector.bars) - 1 - swing['index'])
        return 0.0

    def _check_lower_low(self, detector, current_low: float) -> bool:
        """Check if current swing low is lower than previous swing low."""
        if detector is None:
            return False
        # Find second-to-last swing low (the one before the current break)
        low_count = 0
        for swing in reversed(detector.swings):
            if swing['type'] == 'Low':
                low_count += 1
                if low_count == 2:
                    return current_low < swing['price']
        return False

    def _compute_spot_volatility_ratio(self) -> float:
        """spot 5-bar avg range / spot 50-bar avg range."""
        if len(self.spot_bar_ranges) < 5:
            return 1.0
        ranges = list(self.spot_bar_ranges)
        avg_5 = np.mean(ranges[-5:])
        avg_all = np.mean(ranges)  # up to 50 bars
        if avg_all > 0:
            return float(avg_5 / avg_all)
        return 1.0

    def _build_outcome_model_features(self, break_info: dict) -> dict:
        """Build features dict for TradeOutcomeModel.predict_mfe_probs()."""
        entry_price = break_info['entry_price']
        sl_points = break_info['sl_points']
        sl_percent = sl_points / entry_price if entry_price > 0 else 0.0
        strike = break_info['strike']
        opt_type = break_info['option_type']

        # Time features
        bt = break_info['break_time']
        market_open_dt = datetime.combine(bt.date(), MARKET_OPEN)
        time_of_day_mins = int((bt - market_open_dt).total_seconds() / 60)
        force_exit_dt = datetime.combine(bt.date(), FORCE_EXIT)
        mins_to_eod = max(
            0, int((force_exit_dt - bt).total_seconds() / 60)
        )

        # Moneyness
        spot_open = self.day.spot_open
        moneyness = (
            (strike - spot_open) / spot_open if spot_open > 0 else 0.0
        )

        # Spot move
        spot_close = spot_open  # default
        for ts in reversed(self.day.timestamps[:self.bar_idx + 1]):
            if ts in self.day.spot_lookup:
                spot_close = self.day.spot_lookup[ts][0]
                break
        spot_move = (
            (spot_close - spot_open) / spot_open if spot_open > 0 else 0.0
        )

        # Spot range
        spot_range = 0.0
        if self.spot_low_so_far < float('inf') and spot_open > 0:
            spot_range = (
                (self.spot_high_so_far - self.spot_low_so_far) / spot_open
            )

        # Bars since swing
        detector = self.swing_detector.get_detector(break_info['symbol'])
        bars_since = 0
        if detector:
            # Find swing index from confirmed_swings
            cs = self._confirmed_swings.get(break_info['symbol'])
            if cs:
                swing_idx = cs['_key'][1]
                bars_since = max(0, len(detector.bars) - 1 - swing_idx)

        return {
            'entry_price': entry_price,
            'sl_points': sl_points,
            'sl_percent': sl_percent,
            'vwap_premium': (
                (entry_price - break_info['vwap']) / break_info['vwap']
                if break_info['vwap'] > 0 else 0.0
            ),
            'd_to_expiry': self.day.d_to_expiry,
            'time_of_day_mins': time_of_day_mins,
            'mins_to_eod': mins_to_eod,
            'moneyness': moneyness,
            'bars_since_swing': bars_since,
            'spot_move_at_entry': spot_move,
            'spot_range_so_far': spot_range,
            'swing_number_today': self.swing_break_count.get(opt_type, 0),
            'option_type': opt_type,
            'is_round_strike': break_info.get('is_round_strike', False),
        }
