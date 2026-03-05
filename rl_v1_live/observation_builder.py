"""
Observation Builder for V3 Live Trading

Direct port of env_v3._compute_observation() for live data.
Builds the 46-feature observation vector that the RL model expects.

Feature layout (46 features):
  0-9:   Break context (from swing break; all zeros at review)
  10-13: Market context (always populated)
  14-19: Session state (cumulative R, unrealized, distances, positions, trades)
  20:    Decision type (0.0=entry, 1.0=review)
  21-45: Per-position state (5 slots x 5 features, oldest first)

Training-live parity is critical — any divergence invalidates the model.
"""

import logging
from collections import deque
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

import numpy as np

from .config import DECISION_ENTRY, DECISION_REVIEW, R_VALUE

logger = logging.getLogger(__name__)

MARKET_OPEN = dtime(9, 15)

# V3 observation dimensions
NUM_FEATURES = 46
NUM_POSITION_SLOTS = 5
FEATURES_PER_POSITION = 5


class VWAPCalculator:
    """Session VWAP calculator — matches env_v3.VWAPCalculator exactly."""

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


class ObservationBuilder:
    """Builds 46-feature observation vectors for the V3 RL model.

    Maintains per-symbol tracking state (day high/low, bar ranges, spot ranges)
    and delegates to the same feature computation logic as env_v3.
    """

    def __init__(self):
        self.vwap_calc = VWAPCalculator()
        self.day_high: Dict[str, float] = {}
        self.day_low: Dict[str, float] = {}
        self.bar_ranges: Dict[str, deque] = {}
        self.spot_high_so_far = 0.0
        self.spot_low_so_far = float('inf')
        self.spot_bar_ranges: deque = deque(maxlen=50)
        self.spot_open: float = 0.0       # Set once when first spot data arrives
        self.spot_close: float = 0.0      # Latest spot close
        self._spot_open_set: bool = False  # Track if spot_open has been set today
        self.swing_break_count: Dict[str, int] = {'CE': 0, 'PE': 0}
        self._latest_bars: Dict[str, dict] = {}

    def reset_daily(self):
        """Reset at start of each trading day."""
        self.vwap_calc.reset()
        self.day_high.clear()
        self.day_low.clear()
        self.bar_ranges.clear()
        self.spot_high_so_far = 0.0
        self.spot_low_so_far = float('inf')
        self.spot_bar_ranges.clear()
        self.spot_open = 0.0
        self.spot_close = 0.0
        self._spot_open_set = False
        self.swing_break_count = {'CE': 0, 'PE': 0}
        self._latest_bars.clear()

    def update_bar(self, symbol: str, bar: dict):
        """Update tracking state with a new bar.

        Args:
            bar: dict with keys: open, high, low, close, volume, vwap, timestamp
        """
        h = bar['high']
        l = bar['low']
        c = bar['close']

        # Update VWAP (cumulative from session start)
        self.vwap_calc.update(symbol, h, l, c, bar.get('volume', 0))

        # Day high/low
        if symbol not in self.day_high or h > self.day_high[symbol]:
            self.day_high[symbol] = h
        if symbol not in self.day_low or l < self.day_low[symbol]:
            self.day_low[symbol] = l

        # Bar range (5-bar rolling)
        if c > 0:
            if symbol not in self.bar_ranges:
                self.bar_ranges[symbol] = deque(maxlen=5)
            self.bar_ranges[symbol].append((h - l) / c)

        # Cache latest bar
        self._latest_bars[symbol] = bar

    def update_spot(self, spot_high: float, spot_low: float, spot_close: float):
        """Update spot tracking with NIFTY index bar data."""
        # Set spot_open once (first spot data of the day)
        if not self._spot_open_set and spot_close > 0:
            self.spot_open = spot_close  # Use first close as proxy for open
            self._spot_open_set = True

        if spot_high > self.spot_high_so_far:
            self.spot_high_so_far = spot_high
        if spot_low < self.spot_low_so_far:
            self.spot_low_so_far = spot_low
        if spot_close > 0:
            self.spot_close = spot_close
            self.spot_bar_ranges.append((spot_high - spot_low) / spot_close)

    def record_swing_break(self, option_type: str):
        """Increment swing break count for CE/PE."""
        self.swing_break_count[option_type] = (
            self.swing_break_count.get(option_type, 0) + 1
        )

    def build(self, decision_type: float, break_info: Optional[dict],
              positions: List, swing_detector, bar_idx: int,
              cumulative_R: float, trades_today: int,
              target_R: float, stop_R: float,
              d_to_expiry: int = 0) -> np.ndarray:
        """Build the 46-feature observation vector.

        Args:
            decision_type: DECISION_ENTRY (0.0) or DECISION_REVIEW (1.0)
            break_info: Break dict for entry decisions (None for review)
            positions: List of PyramidPosition objects
            swing_detector: MultiSwingDetector instance
            bar_idx: Current bar index in session
            cumulative_R: Realized R so far today
            trades_today: Number of trades today
            target_R: Daily target R
            stop_R: Daily stop R
            d_to_expiry: Days to expiry (0 = expiry day)

        Returns:
            np.ndarray of shape (46,), dtype float32
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
            detector = swing_detector.get_detector(symbol)

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

            # 8: swing_break_count for this option type
            obs[8] = float(self.swing_break_count.get(opt_type, 0))

            # 9: d_to_expiry_norm
            obs[9] = min(d_to_expiry / 7.0, 4.0)

        # --- Group 2: Market Context (features 10-13) ---
        # Always populated

        # 10: minutes_since_open (normalized to ~0-1 by /360.0)
        now = datetime.now()
        market_open_dt = datetime.combine(now.date(), MARKET_OPEN)
        mins = (now - market_open_dt).total_seconds() / 60.0
        obs[10] = max(0.0, mins) / 360.0

        # 11: spot_volatility_ratio
        obs[11] = self._compute_spot_volatility_ratio()

        # 12: spot_pct_from_open
        if self.spot_open > 0 and self.spot_close > 0:
            obs[12] = (self.spot_close - self.spot_open) / self.spot_open

        # 13: spot_day_range_pct
        if self.spot_open > 0 and self.spot_low_so_far < float('inf'):
            spot_range = self.spot_high_so_far - self.spot_low_so_far
            if spot_range > 0:
                obs[13] = spot_range / self.spot_open

        # --- Group 3: Session State (features 14-19) ---
        unrealized = self._total_unrealized_R(positions)
        total_R = cumulative_R + unrealized

        # 14: cumulative_R
        obs[14] = cumulative_R

        # 15: unrealized_R
        obs[15] = unrealized

        # 16: dist_to_target
        obs[16] = target_R - total_R

        # 17: dist_to_stop
        obs[17] = total_R - stop_R

        # 18: n_positions
        obs[18] = float(len(positions))

        # 19: trades_today
        obs[19] = float(trades_today)

        # --- Group 4: Decision Type (feature 20) ---
        obs[20] = decision_type

        # --- Group 5: Per-Position State (features 21-45) ---
        # 5 slots x 5 features. Ordered by entry time (oldest first).
        # Empty slots = all zeros.
        positions_sorted = sorted(positions, key=lambda p: p.entry_bar_idx)

        for i, pos in enumerate(positions_sorted[:NUM_POSITION_SLOTS]):
            base = 21 + i * FEATURES_PER_POSITION

            # +0: pos_unrealized_R
            bar_p = self._latest_bars.get(pos.symbol)
            if bar_p is not None and pos.actual_R_value > 0:
                pos_unrealized = (
                    (pos.entry_price - bar_p['close']) * pos.quantity
                    / pos.actual_R_value
                )
                obs[base + 0] = pos_unrealized
            else:
                obs[base + 0] = 0.0

            # +1: pos_bars_held (normalized by 360)
            bars_held = max(0, bar_idx - pos.entry_bar_idx)
            obs[base + 1] = bars_held / 360.0

            # +2: pos_pct_from_sl
            sl_trigger = getattr(pos, 'sl_trigger', 0.0)
            if bar_p and bar_p['close'] > 0 and sl_trigger > 0:
                obs[base + 2] = (sl_trigger - bar_p['close']) / bar_p['close']

            # +3: pos_option_type (CE=+1, PE=-1)
            obs[base + 3] = 1.0 if pos.option_type == 'CE' else -1.0

            # +4: pos_tp_R_level (0.5, 1.0, 2.0, 3.0)
            obs[base + 4] = getattr(pos, 'tp_R_level', 1.0)

        return obs

    # ------------------------------------------------------------------
    # Helper methods (identical to env_v3)
    # ------------------------------------------------------------------

    def _get_prev_swing_high(self, detector) -> float:
        if detector is None:
            return 0.0
        for swing in reversed(detector.swings):
            if swing['type'] == 'High':
                return swing['price']
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

    def _total_unrealized_R(self, positions: List) -> float:
        total = 0.0
        for pos in positions:
            bar = self._latest_bars.get(pos.symbol)
            if bar is None or pos.actual_R_value <= 0:
                continue
            unrealized = (
                (pos.entry_price - bar['close']) * pos.quantity
                / pos.actual_R_value
            )
            total += unrealized
        return total
