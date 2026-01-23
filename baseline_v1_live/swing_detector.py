"""
Real-Time Swing Low/High Detector for Options

Implements watch-based swing detection per SWING_DETECTION_THEORY.md:
- low_watch: Candles where current made HIGHER high AND HIGHER close
- high_watch: Candles where current made LOWER low AND LOWER close
- Trigger: Candle marked watch TWICE triggers swing detection
- First swing can be either Low or High (whichever triggers first)
- After first swing, alternates between Low and High
- Swing Updates: If same type swing detected with new extreme, UPDATE (not reject)

Features:
- Per-symbol swing state tracking (independent detectors)
- Intraday scope only (resets at market open)
- Break detection when price crosses below swing low
"""

import logging
from collections import defaultdict
from datetime import datetime, time
import pandas as pd
import pytz

try:
    from .config import MARKET_START_TIME
except ModuleNotFoundError:
    from config import MARKET_START_TIME

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class SwingDetector:
    """
    Detects swing lows/highs using watch-based trigger system

    Watch Logic (per SWING_DETECTION_THEORY.md):
    - low_watch: Current candle has HIGHER high AND HIGHER close than previous
    - high_watch: Current candle has LOWER low AND LOWER close than previous
    - When a candle is marked watch TWICE -> Trigger swing detection
    - First swing can be either Low or High (whichever condition triggers first)
    - Swing Updates: Same-type swing with new extreme UPDATES existing swing

    Break Detection:
    - When current price (Low) falls below the last unbroken swing low price
    """

    def __init__(self, symbol):
        self.symbol = symbol

        # Historical bars (list of bar dicts with OHLCV)
        self.bars = []

        # Swing state
        self.swings = []  # List of {type: 'Low'/'High', price, timestamp, index, vwap, high, low}
        self.last_swing = None  # Most recent swing
        self.last_swing_type = None  # 'low' or 'high' (lowercase to match backtest)
        self.last_swing_idx = None  # Index of last swing bar

        # Watch tracking for each bar index
        self.low_watch_count = {}   # {bar_index: count}
        self.high_watch_count = {}  # {bar_index: count}

        # Today's date for intraday reset
        self.current_date = None

        # Flag to track if we're processing historical data (skip DB logging during reload)
        self.is_historical_processing = True

        # Track logged swings to prevent duplicates
        self._logged_swings = set()  # {(swing_time, swing_type, swing_price)}

        logger.debug(f"SwingDetector initialized for {symbol}")

    def reset_for_new_day(self, date):
        """Reset swing state for new trading day"""
        self.bars = []
        self.swings = []
        self.last_swing = None
        self.last_swing_type = None
        self.last_swing_idx = None
        self.low_watch_count = {}
        self.high_watch_count = {}
        self._logged_swings = set()
        self.current_date = date
        logger.info(f"{self.symbol}: Reset for new day {date}")

    def set_live_mode(self):
        """
        Switch from historical processing mode to live mode

        Call this after historical data load is complete to enable database logging
        """
        self.is_historical_processing = False
        logger.info(f"{self.symbol}: Switched to LIVE mode (DB logging enabled)")

    def add_bar(self, bar_dict):
        """
        Add new 1-minute bar and detect swings using watch-based logic

        Implements logic from SWING_DETECTION_THEORY.md.

        Args:
            bar_dict: {
                'timestamp': datetime,
                'open': float,
                'high': float,
                'low': float,
                'close': float,
                'volume': int,
                'vwap': float
            }

        Returns:
            swing_info dict if new swing LOW detected, None otherwise
        """
        # Check if new day
        bar_date = bar_dict['timestamp'].date()
        if self.current_date is None or bar_date != self.current_date:
            self.reset_for_new_day(bar_date)

        # CRITICAL: Validate bars arrive in chronological order
        if self.bars:
            last_bar_time = self.bars[-1]['timestamp']
            current_bar_time = bar_dict['timestamp']

            if current_bar_time < last_bar_time:
                logger.error(
                    f"[OUT-OF-ORDER] {self.symbol}: Bar received OUT OF ORDER! "
                    f"Current: {current_bar_time.strftime('%H:%M')}, "
                    f"Last: {last_bar_time.strftime('%H:%M')} - SKIPPING BAR"
                )
                return None

            if current_bar_time == last_bar_time:
                logger.warning(
                    f"[DUPLICATE] {self.symbol}: Duplicate bar timestamp "
                    f"{current_bar_time.strftime('%H:%M')} - SKIPPING BAR"
                )
                return None

        # Add bar to history
        current_index = len(self.bars)
        bar_dict['index'] = current_index
        self.bars.append(bar_dict)

        # Need at least 2 bars to start
        if len(self.bars) < 2:
            return None

        current = bar_dict
        i = current_index  # Current bar index (matches 'i' in backtest)

        # If no swing yet, find initial swing (can be Low or High)
        if self.last_swing is None:
            return self._find_initial_swing(i, current)

        # Find alternating swing
        return self._find_alternate_swing(i, current)

    def _find_initial_swing(self, i, current):
        """
        Find the first swing (can be either Low or High - whichever triggers first)

        Per theory SWING_DETECTION_THEORY.md (lines 130-174):
        - Check all previous bars j from 0 to i-1
        - Increment watch counts based on HH+HC (low_watch) or LL+LC (high_watch)
        - When count reaches 2, find the extreme in window [0 to i]
        - Returns whichever swing type triggers first
        """
        # Check all previous bars from 0 to i-1
        for j in range(0, i):
            prev = self.bars[j]

            # low_watch: current makes HIGHER high AND HIGHER close
            if current['high'] > prev['high'] and current['close'] > prev['close']:
                self.low_watch_count[j] = self.low_watch_count.get(j, 0) + 1

                if self.low_watch_count[j] == 2:
                    # Find lowest low in window from 0 to i (inclusive)
                    window = self.bars[:i + 1]
                    lowest_idx = min(range(len(window)), key=lambda x: window[x]['low'])
                    lowest_bar = window[lowest_idx]

                    return self._create_swing('Low', lowest_bar, lowest_idx)

            # high_watch: current makes LOWER low AND LOWER close
            if current['low'] < prev['low'] and current['close'] < prev['close']:
                self.high_watch_count[j] = self.high_watch_count.get(j, 0) + 1

                if self.high_watch_count[j] == 2:
                    # Find highest high in window from 0 to i (inclusive)
                    window = self.bars[:i + 1]
                    highest_idx = max(range(len(window)), key=lambda x: window[x]['high'])
                    highest_bar = window[highest_idx]

                    return self._create_swing('High', highest_bar, highest_idx)

        return None

    def _find_alternate_swing(self, i, current):
        """
        Find alternating swing OR same-direction extreme after initial swing is established

        CRITICAL CHANGE: ALL swings require watch-based confirmation (2 watches), including updates.

        Per theory SWING_DETECTION_THEORY.md:
        - If last swing was Low, look for:
          1. High (alternating) - check high_watch
          2. Lower Low (same-direction update) - check low_watch
        - If last swing was High, look for:
          1. Low (alternating) - check low_watch
          2. Higher High (same-direction update) - check high_watch
        - Window: From bar AFTER last swing to current bar [last_idx+1 to i]
        - Check counters for bars in range [last_idx+1 to i-1] (not current bar, not last swing)
        - Both alternating swings AND updates need 2 watches
        """
        last_idx = self.last_swing_idx

        if self.last_swing_type == 'low':
            # Check bars from last_idx+1 to i-1 for watches
            for j in range(last_idx + 1, i):
                prev = self.bars[j]

                # Check for SWING HIGH (alternating)
                if current['low'] < prev['low'] and current['close'] < prev['close']:
                    self.high_watch_count[j] = self.high_watch_count.get(j, 0) + 1

                    if self.high_watch_count[j] == 2:
                        # Find highest high in window AFTER last swing
                        window = self.bars[last_idx + 1:i + 1]
                        highest_local_idx = max(range(len(window)), key=lambda x: window[x]['high'])
                        highest_idx = last_idx + 1 + highest_local_idx
                        highest_bar = self.bars[highest_idx]

                        return self._create_swing('High', highest_bar, highest_idx)

                # Check for LOWER LOW (same-direction update) - ALSO needs 2 watches!
                if current['high'] > prev['high'] and current['close'] > prev['close']:
                    self.low_watch_count[j] = self.low_watch_count.get(j, 0) + 1

                    if self.low_watch_count[j] == 2:
                        # Find lowest low in window AFTER last swing
                        window = self.bars[last_idx + 1:i + 1]
                        lowest_local_idx = min(range(len(window)), key=lambda x: window[x]['low'])
                        lowest_idx = last_idx + 1 + lowest_local_idx
                        lowest_bar = self.bars[lowest_idx]

                        # Check if this is a new lower low (update candidate)
                        if lowest_bar['low'] < self.last_swing['price']:
                            return self._update_swing_extreme('Low', lowest_bar, lowest_idx)

        elif self.last_swing_type == 'high':
            # Check bars from last_idx+1 to i-1 for watches
            for j in range(last_idx + 1, i):
                prev = self.bars[j]

                # Check for SWING LOW (alternating)
                if current['high'] > prev['high'] and current['close'] > prev['close']:
                    self.low_watch_count[j] = self.low_watch_count.get(j, 0) + 1

                    if self.low_watch_count[j] == 2:
                        # Find lowest low in window AFTER last swing
                        window = self.bars[last_idx + 1:i + 1]
                        lowest_local_idx = min(range(len(window)), key=lambda x: window[x]['low'])
                        lowest_idx = last_idx + 1 + lowest_local_idx
                        lowest_bar = self.bars[lowest_idx]

                        return self._create_swing('Low', lowest_bar, lowest_idx)

                # Check for HIGHER HIGH (same-direction update) - ALSO needs 2 watches!
                if current['low'] < prev['low'] and current['close'] < prev['close']:
                    self.high_watch_count[j] = self.high_watch_count.get(j, 0) + 1

                    if self.high_watch_count[j] == 2:
                        # Find highest high in window AFTER last swing
                        window = self.bars[last_idx + 1:i + 1]
                        highest_local_idx = max(range(len(window)), key=lambda x: window[x]['high'])
                        highest_idx = last_idx + 1 + highest_local_idx
                        highest_bar = self.bars[highest_idx]

                        # Check if this is a new higher high (update candidate)
                        if highest_bar['high'] > self.last_swing['price']:
                            return self._update_swing_extreme('High', highest_bar, highest_idx)

        return None


    def _update_swing_extreme(self, swing_type, bar, idx):
        """
        Update existing swing to new extreme (when same-direction extreme forms WITH watch confirmation)

        CRITICAL: This is called ONLY after 2-watch confirmation in _find_alternate_swing().

        Called when:
        - Last swing was LOW and new lower low confirmed with 2 watches
        - Last swing was HIGH and new higher high confirmed with 2 watches

        Args:
            swing_type: 'Low' or 'High'
            bar: Bar dict at confirmed extreme
            idx: Bar index at confirmed extreme

        Returns:
            Updated swing dict if swing_type is 'Low', None otherwise
        """
        new_price = bar['low'] if swing_type == 'Low' else bar['high']
        last_price = self.last_swing['price']

        logger.info(
            f"[SWING-UPDATE] {self.symbol}: Updating {swing_type.upper()} "
            f"from {last_price:.2f} -> {new_price:.2f} "
            f"@ {bar['timestamp'].strftime('%H:%M')} (watch-confirmed new extreme)"
        )

        # Update the existing swing in place (same swing object, new values)
        self.last_swing['price'] = new_price
        self.last_swing['timestamp'] = bar['timestamp']
        self.last_swing['index'] = idx
        # CRITICAL: Do NOT update VWAP - keep it frozen at original swing formation time
        # VWAP represents market context at FIRST swing detection, not latest extreme
        self.last_swing['high'] = bar['high']
        self.last_swing['low'] = bar['low']
        # Preserve 'broken' status
        # last_swing['broken'] remains unchanged

        # Update last_swing_idx to point to new bar
        self.last_swing_idx = idx

        # Log to database if in live mode
        if (not self.is_historical_processing and
            hasattr(self, '_state_manager') and
            self._state_manager):

            swing_key = (
                bar['timestamp'].isoformat() if hasattr(bar['timestamp'], 'isoformat') else str(bar['timestamp']),
                swing_type,
                round(new_price, 2)
            )

            if swing_key not in self._logged_swings:
                self._state_manager.log_swing_detection(
                    symbol=self.symbol,
                    swing_type=f"{swing_type} Update",
                    swing_price=new_price,
                    swing_time=bar['timestamp'],
                    vwap=self.last_swing['vwap'],  # Use frozen VWAP from original swing
                    bar_index=idx
                )
                self._logged_swings.add(swing_key)

        # Return updated swing if it's a Low (for trading strategy)
        if swing_type == 'Low':
            return self.last_swing
        return None

    def _create_swing(self, swing_type, bar, idx):
        """
        Create a new swing point with alternating pattern enforcement

        CRITICAL CHANGE: Removed immediate update logic. Updates now come through
        watch-based system in _find_alternate_swing() with 2-watch confirmation.

        Args:
            swing_type: 'Low' or 'High'
            bar: Bar dict
            idx: Bar index
        """
        # REMOVED: Immediate swing update logic (now requires 2 watches via _find_alternate_swing)
        # Same-type swings should not reach here anymore - they go through _update_swing_extreme

        option_type = 'CE' if 'CE' in self.symbol else 'PE'

        swing = {
            'type': swing_type,  # 'Low' or 'High'
            'price': bar['low'] if swing_type == 'Low' else bar['high'],
            'timestamp': bar['timestamp'],
            'index': idx,
            'vwap': bar.get('vwap', bar['close']),  # Fallback to close if no vwap
            'high': bar['high'],
            'low': bar['low'],
            'broken': False,
            'option_type': option_type,
            'symbol': self.symbol
        }

        self.swings.append(swing)
        self.last_swing = swing
        self.last_swing_type = swing_type.lower()  # 'low' or 'high'
        self.last_swing_idx = idx

        # Reset watch counts after swing is found (start fresh for next swing)
        self.low_watch_count = {}
        self.high_watch_count = {}

        # Log message
        mode_tag = "HISTORICAL" if self.is_historical_processing else "LIVE"
        logger.info(
            f"[SWING-{mode_tag}] {self.symbol}: SWING {swing_type.upper()} @ {swing['price']:.2f} "
            f"(time: {bar['timestamp'].strftime('%H:%M')}, idx: {idx})"
        )

        # LOG to database ONLY if NOT in historical mode AND state_manager available
        if (not self.is_historical_processing and
            hasattr(self, '_state_manager') and
            self._state_manager):

            # Check if already logged (prevent duplicates)
            swing_key = (
                bar['timestamp'].isoformat() if hasattr(bar['timestamp'], 'isoformat') else str(bar['timestamp']),
                swing_type,
                round(swing['price'], 2)
            )

            if swing_key not in self._logged_swings:
                self._state_manager.log_swing_detection(
                    symbol=self.symbol,
                    swing_type=swing_type,
                    swing_price=swing['price'],
                    swing_time=bar['timestamp'],
                    vwap=swing['vwap'],
                    bar_index=idx
                )
                self._logged_swings.add(swing_key)
                logger.debug(f"[DB-LOG] Logged swing to database: {swing_key}")
            else:
                logger.debug(f"[DB-SKIP] Swing already logged: {swing_key}")

        # Only return swing lows for trading strategy
        if swing_type == 'Low':
            return swing
        return None

    def check_break(self, current_bar):
        """
        Check if current bar breaks the last swing low

        Args:
            current_bar: Latest bar dict

        Returns:
            Dict with break details if broken, None otherwise
        """
        # Only check breaks for swing lows (strategy only trades on swing low breaks)
        if self.last_swing is None or self.last_swing['type'] != 'Low':
            return None

        if self.last_swing.get('broken', False):
            return None

        current_low = current_bar['low']
        swing_price = self.last_swing['price']

        # Check if broken
        if current_low < swing_price:
            # Mark as broken
            self.last_swing['broken'] = True

            # Calculate highest high between swing and break
            swing_idx = self.last_swing['index']
            current_idx = current_bar['index']

            bars_between = self.bars[swing_idx:current_idx + 1]
            highest_high = max(bar['high'] for bar in bars_between)

            # Parse strike and option type from symbol
            strike, option_type = self._parse_symbol(self.symbol)

            break_info = {
                'symbol': self.symbol,
                'strike': strike,
                'option_type': option_type,
                'entry_price': swing_price,
                'break_time': current_bar['timestamp'],
                'swing_low_time': self.last_swing['timestamp'],
                'vwap_at_swing_low': self.last_swing['vwap'],
                'highest_high_since_swing': highest_high,
            }

            logger.info(
                f"{self.symbol}: SWING LOW BREAK at {current_bar['timestamp'].strftime('%H:%M')} "
                f"(Entry: {swing_price:.2f}, High: {highest_high:.2f})"
            )

            return break_info

        return None

    def get_last_swing_low(self):
        """Get the most recent unbroken swing low"""
        if self.last_swing and self.last_swing['type'] == 'Low' and not self.last_swing.get('broken', False):
            return self.last_swing
        return None

    def _parse_symbol(self, symbol):
        """
        Parse NIFTY option symbol to extract strike and type

        Example: NIFTY26DEC2418000CE -> (18000, 'CE')
        """
        try:
            # Extract option type (last 2 chars)
            option_type = symbol[-2:]

            # Extract strike (between date and option type)
            # Example: NIFTY26DEC2418000CE
            # Remove 'NIFTY' and option type, then extract numbers
            temp = symbol[5:-2]  # 26DEC2418000

            # Find where the strike starts (after date)
            # Date format: DDMMMYY (7 chars)
            strike_str = temp[7:]  # 18000
            strike = int(strike_str)

            return strike, option_type

        except Exception as e:
            logger.error(f"Failed to parse symbol {symbol}: {e}")
            return None, None

    def get_latest_bar(self):
        """Get the most recent bar"""
        return self.bars[-1] if self.bars else None

    def get_bars(self, count=100):
        """Get last N bars"""
        return self.bars[-count:] if self.bars else []


class MultiSwingDetector:
    """
    Manages swing detectors for multiple option symbols
    """

    def __init__(self, on_swing_detected=None, state_manager=None):
        """
        Args:
            on_swing_detected: Callback function when new swing detected
            state_manager: StateManager instance for logging all swings
        """
        self.detectors = {}  # {symbol: SwingDetector}
        self.on_swing_detected = on_swing_detected
        self.state_manager = state_manager
        logger.debug("MultiSwingDetector initialized")

    def add_symbols(self, symbols):
        """Initialize detectors for new symbols"""
        for symbol in symbols:
            if symbol not in self.detectors:
                self.detectors[symbol] = SwingDetector(symbol)
                logger.debug(f"Added detector for {symbol}")

    def update(self, symbol, bar_dict):
        """
        Update detector with new bar and check for breaks

        Args:
            symbol: Option symbol
            bar_dict: Bar data dict

        Returns:
            Break info dict if swing low broken, None otherwise
        """
        if symbol not in self.detectors:
            self.add_symbols([symbol])

        detector = self.detectors[symbol]

        # Pass state_manager to detector for logging
        detector._state_manager = self.state_manager

        # Add bar and check if new swing detected
        swing_info = detector.add_bar(bar_dict)

        # Notify callback if new swing detected
        if swing_info and self.on_swing_detected:
            option_type = 'CE' if 'CE' in symbol else 'PE'
            swing_info['option_type'] = option_type
            self.on_swing_detected(symbol, swing_info)

        # Check for break
        break_info = detector.check_break(bar_dict)

        return break_info

    def update_all(self, bars_dict):
        """
        Update all detectors with new bars

        Args:
            bars_dict: {symbol: bar_dict}

        Returns:
            List of break_info dicts for any breaks detected
        """
        breaks = []

        for symbol, bar_dict in bars_dict.items():
            break_info = self.update(symbol, bar_dict)
            if break_info:
                breaks.append(break_info)

        return breaks

    def get_detector(self, symbol):
        """Get detector for specific symbol"""
        return self.detectors.get(symbol)

    def reset_all(self):
        """Reset all detectors for new day"""
        current_date = datetime.now(IST).date()
        for detector in self.detectors.values():
            detector.reset_for_new_day(current_date)
        logger.info("All detectors reset for new day")

    def enable_live_mode(self):
        """
        Enable live mode for all detectors (disable historical processing mode)

        Call this after historical data load is complete to enable database logging
        """
        for detector in self.detectors.values():
            detector.set_live_mode()
        logger.info(f"Live mode enabled for {len(self.detectors)} detectors")


if __name__ == '__main__':
    # Test swing detector with sample data
    logging.basicConfig(level=logging.DEBUG)

    detector = SwingDetector('NIFTY26DEC2418000CE')

    # Simulate bars that should create a swing low then swing high
    from datetime import datetime, timedelta

    base_time = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)

    # Test sequence:
    # Bars 0-3: Price goes down (forming potential swing low)
    # Bar 4-5: Price makes higher highs and higher closes (confirms swing low)
    # Bars 6-8: Price goes up (forming potential swing high)
    # Bars 9-10: Price makes lower lows and lower closes (confirms swing high)
    test_bars = [
        # Initial downtrend
        {'timestamp': base_time, 'open': 250, 'high': 252, 'low': 248, 'close': 249, 'volume': 100, 'vwap': 250},
        {'timestamp': base_time + timedelta(minutes=1), 'open': 249, 'high': 250, 'low': 245, 'close': 246, 'volume': 120, 'vwap': 248},
        {'timestamp': base_time + timedelta(minutes=2), 'open': 246, 'high': 247, 'low': 242, 'close': 243, 'volume': 150, 'vwap': 246},  # Lowest point
        # Reversal up - should trigger swing low
        {'timestamp': base_time + timedelta(minutes=3), 'open': 243, 'high': 248, 'low': 243, 'close': 247, 'volume': 110, 'vwap': 245},  # HH+HC vs bar 2
        {'timestamp': base_time + timedelta(minutes=4), 'open': 247, 'high': 252, 'low': 246, 'close': 251, 'volume': 130, 'vwap': 248},  # HH+HC vs bar 3 -> 2nd watch!
        # Continue up
        {'timestamp': base_time + timedelta(minutes=5), 'open': 251, 'high': 258, 'low': 250, 'close': 257, 'volume': 140, 'vwap': 252},
        {'timestamp': base_time + timedelta(minutes=6), 'open': 257, 'high': 262, 'low': 255, 'close': 260, 'volume': 160, 'vwap': 257},  # Highest point
        # Reversal down - should trigger swing high
        {'timestamp': base_time + timedelta(minutes=7), 'open': 260, 'high': 261, 'low': 254, 'close': 255, 'volume': 120, 'vwap': 258},  # LL+LC vs bar 6
        {'timestamp': base_time + timedelta(minutes=8), 'open': 255, 'high': 256, 'low': 250, 'close': 251, 'volume': 180, 'vwap': 254},  # LL+LC vs bar 7 -> 2nd watch!
        # Continue down - should break swing low
        {'timestamp': base_time + timedelta(minutes=9), 'open': 251, 'high': 252, 'low': 248, 'close': 249, 'volume': 140, 'vwap': 250},
        {'timestamp': base_time + timedelta(minutes=10), 'open': 249, 'high': 250, 'low': 240, 'close': 241, 'volume': 200, 'vwap': 246},  # Break below swing low!
    ]

    print("\n" + "="*60)
    print("SWING DETECTOR TEST")
    print("="*60 + "\n")

    for i, bar in enumerate(test_bars):
        print(f"Bar {i}: O={bar['open']}, H={bar['high']}, L={bar['low']}, C={bar['close']}")
        swing_info = detector.add_bar(bar)

        if swing_info:
            print(f"  >>> SWING LOW DETECTED @ {swing_info['price']:.2f}")

        if detector.last_swing and detector.last_swing['type'] == 'High':
            print(f"  >>> SWING HIGH at {detector.last_swing['price']:.2f}")

        break_info = detector.check_break(bar)
        if break_info:
            print(f"  >>> BREAK DETECTED! Entry: {break_info['entry_price']:.2f}")

        print()

    print("="*60)
    print(f"Total swings detected: {len(detector.swings)}")
    for swing in detector.swings:
        print(f"  - {swing['type']} @ {swing['price']:.2f} (idx: {swing['index']})")
    print("="*60)
