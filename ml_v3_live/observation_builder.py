"""
Observation Builder for V3 Live Trading

Direct port of env_v3._compute_observation() for live data.
Builds the 24-feature observation vector that the RL model expects.

Feature layout (24 features):
  0-11:  Global market context (from break symbol or latest position symbol)
  12-17: Session state (cumulative R, position count, trades, unrealized, dist to limits)
  18:    Decision type (0.0=entry, 1.0=review)
  19-23: Position summary (avg/max unrealized R, avg bars held, pyramid depth, avg pct from SL)

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
    """Builds 24-feature observation vectors for the V3 RL model.

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
        if spot_high > self.spot_high_so_far:
            self.spot_high_so_far = spot_high
        if spot_low < self.spot_low_so_far:
            self.spot_low_so_far = spot_low
        if spot_close > 0:
            self.spot_bar_ranges.append((spot_high - spot_low) / spot_close)

    def record_swing_break(self, option_type: str):
        """Increment swing break count for CE/PE."""
        self.swing_break_count[option_type] = (
            self.swing_break_count.get(option_type, 0) + 1
        )

    def build(self, decision_type: float, break_info: Optional[dict],
              pyramid_mgr, swing_detector, bar_idx: int,
              cumulative_R: float, trades_today: int,
              target_R: float, stop_R: float) -> np.ndarray:
        """Build the 24-feature observation vector.

        Args:
            decision_type: DECISION_ENTRY (0.0) or DECISION_REVIEW (1.0)
            break_info: Break dict for entry decisions (None for review)
            pyramid_mgr: PyramidManager instance
            swing_detector: MultiSwingDetector instance
            bar_idx: Current bar index in session
            cumulative_R: Realized R so far today
            trades_today: Number of trades today
            target_R: Daily target R
            stop_R: Daily stop R

        Returns:
            np.ndarray of shape (24,), dtype float32
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
            positions = pyramid_mgr.all_positions()
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
                return np.zeros(24, dtype=np.float32)

        detector = swing_detector.get_detector(symbol)

        # 0. vwap_premium_pct
        vwap_premium = (
            (ref_entry_price - ref_vwap) / ref_vwap if ref_vwap > 0 else 0.0
        )

        # 1. sl_pct
        sl_pct = (
            ref_sl_points / ref_entry_price if ref_entry_price > 0 else 0.0
        )

        # 2. pct_from_day_high
        dh = self.day_high.get(symbol, bar['high'])
        close = bar['close'] if isinstance(bar, dict) else 0
        pct_from_day_high = (dh - close) / dh if dh > 0 else 0.0

        # 3. pct_from_day_low
        dl = self.day_low.get(symbol, bar['low'])
        pct_from_day_low = (close - dl) / close if close > 0 else 0.0

        # 4. pct_diff_swing_low_vs_prev_high
        prev_high = self._get_prev_swing_high(detector)
        pct_diff = (
            (prev_high - ref_entry_price) / prev_high if prev_high > 0 else 0.0
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
            (dh - dl) / ref_entry_price if ref_entry_price > 0 else 0.0
        )

        # 10. minutes_since_open
        now = datetime.now()
        if hasattr(bar.get('timestamp', None), 'date'):
            ts_date = bar['timestamp'].date() if hasattr(bar['timestamp'], 'date') else now.date()
        else:
            ts_date = now.date()
        market_open_dt = datetime.combine(ts_date, MARKET_OPEN)
        mins = (now - market_open_dt).total_seconds() / 60.0
        if mins < 0:
            mins = 0.0

        # 11. spot_volatility_ratio
        vol_ratio = self._compute_spot_volatility_ratio()

        # 12-17: Session state
        unrealized = self._total_unrealized_R(pyramid_mgr)
        total_R = cumulative_R + unrealized
        n_pos = pyramid_mgr.position_count()
        dist_target = target_R - total_R
        dist_stop = total_R - stop_R

        # 19-23: Position summary
        pos_unrealized = self._per_position_unrealized_R(pyramid_mgr)

        avg_pos_unrealized_R = float(np.mean(pos_unrealized)) if pos_unrealized else 0.0
        max_pos_unrealized_R = float(max(pos_unrealized)) if pos_unrealized else 0.0

        positions = pyramid_mgr.all_positions()
        if positions:
            avg_bars_held = float(np.mean([
                max(0, bar_idx - p.entry_bar_idx) for p in positions
            ]))
        else:
            avg_bars_held = 0.0

        pyramid_depth = n_pos

        avg_pct_from_sl = 0.0
        if positions:
            pct_from_sl_list = []
            for pos in positions:
                seq = pyramid_mgr.get_sequence(pos.option_type)
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
            cumulative_R,               # 12
            float(n_pos),               # 13
            float(trades_today),        # 14
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
    # Helper methods (identical to env_v3)
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

    def _total_unrealized_R(self, pyramid_mgr) -> float:
        total = 0.0
        for pos in pyramid_mgr.all_positions():
            bar = self._latest_bars.get(pos.symbol)
            if bar is None or pos.actual_R_value <= 0:
                continue
            unrealized = (
                (pos.entry_price - bar['close']) * pos.quantity
                / pos.actual_R_value
            )
            total += unrealized
        return total

    def _per_position_unrealized_R(self, pyramid_mgr) -> List[float]:
        result = []
        for pos in pyramid_mgr.all_positions():
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
