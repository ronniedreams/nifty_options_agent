"""
Real-Time Data Pipeline for Options Trading

Subscribes to OpenAlgo WebSocket for NIFTY options quotes and aggregates
tick data into 1-minute OHLCV bars with session VWAP calculation.





Features:
- WebSocket subscription to ±20 strikes from ATM (82 options: 41 CE + 41 PE)
- Tick-to-bar aggregation with volume tracking
- Session VWAP calculation: Cumulative from market open (9:15 AM)
  VWAP = Σ(typical_price × volume) / Σ(volume) where typical_price = (H+L+C)/3
- Automatic reconnection on WebSocket disconnect
- Data validation (stale tick detection)
"""

import copy
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta
from threading import RLock, Thread
import time as time_module
import pytz

from openalgo import api
from .config import (
    OPENALGO_API_KEY,
    OPENALGO_HOST,
    OPENALGO_WS_URL,
    ANGELONE_OPENALGO_API_KEY,
    ANGELONE_HOST,
    ANGELONE_WS_URL,
    FAILOVER_NO_TICK_THRESHOLD,
    FAILOVER_SWITCHBACK_THRESHOLD,
    EXCHANGE,
    STRIKE_SCAN_RANGE,
    BAR_INTERVAL_SECONDS,
    MIN_TICKS_PER_BAR,
    MAX_TICK_AGE_SECONDS,
    WEBSOCKET_RECONNECT_DELAY,
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS,
    WEBSOCKET_MODE,
    MIN_DATA_COVERAGE_THRESHOLD,
    STALE_DATA_TIMEOUT,
    MAX_BAR_AGE_SECONDS,
    MAX_BARS_PER_SYMBOL,
    BAR_PRUNING_THRESHOLD,
    MARKET_START_TIME,
    MARKET_CLOSE_TIME,
)

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class BarData:
    """1-minute OHLCV bar with VWAP"""
    
    def __init__(self, timestamp):
        self.timestamp = timestamp
        self.open = None
        self.high = None
        self.low = None
        self.close = None
        self.volume = 0
        self.vwap = None
        self.atp = None   # Exchange-provided session ATP (average_price from WebSocket tick)
        self.tick_count = 0
    
    def update_tick(self, ltp, volume=1):
        """Update bar with new tick data"""
        if self.open is None:
            self.open = ltp
        
        self.high = max(self.high or ltp, ltp)
        self.low = min(self.low or ltp, ltp)
        self.close = ltp
        self.volume += volume
        self.tick_count += 1
    
    def is_valid(self):
        """Check if bar has minimum data quality"""
        return (
            self.open is not None and
            self.tick_count >= MIN_TICKS_PER_BAR and
            self.volume > 0
        )
    
    def to_dict(self):
        return {
            'timestamp': self.timestamp,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'vwap': self.vwap,
            'atp': self.atp,
            'tick_count': self.tick_count,
        }


class DataPipeline:
    """
    Real-time data pipeline for options trading
    
    Manages WebSocket subscriptions, tick aggregation, and bar updates
    for multiple option symbols simultaneously.
    """
    
    def __init__(self):
        self.client = None
        self.is_connected = False
        self.subscribed_symbols = set()

        # Data storage: {symbol: [list of BarData]}
        self.bars = defaultdict(list)
        self.current_bars = {}  # {symbol: BarData}

        # Session VWAP tracking: cumulative from market open (9:15 AM)
        # {symbol: {'cum_pv': float, 'cum_vol': int}}
        self.session_vwap_data = {}

        # Thread safety - use RLock for reentrant locking
        self.lock = RLock()

        # Last update tracking
        self.last_tick_time = {}  # {symbol: datetime}
        self.last_bar_timestamp = {}  # {symbol: datetime} - when bar was RECEIVED (for watchdog)

        # Watchdog tracking
        self.first_data_received_at = None
        self.consecutive_stale_checks = 0
        self.watchdog_triggered = False

        # Reconnection tracking
        self.reconnect_attempts = 0
        self.last_disconnect_time = None
        self.is_reconnecting = False  # Flag to prevent multiple simultaneous reconnections
        self.auto_reconnect_enabled = True  # Enable automatic reconnection

        # Connection monitoring thread
        self.monitor_thread = None
        self.monitor_running = False

        # ATM tracking for strike selection
        self.current_atm_strike = None
        self.spot_price = None

        # Angel One backup feed
        self.angelone_client = None
        self.angelone_is_connected = False
        self.active_source = 'zerodha'       # 'zerodha' or 'angelone'
        self.is_failover_active = False
        self.last_zerodha_tick_time = {}     # Zerodha ticks tracked even when on Angel One
        self.zerodha_continuous_tick_start = None  # When Zerodha ticks resumed (for switchback)
        self.subscription_started_at = None       # When subscribe_options() was last called

        # VWAP fallback: if history API lag prevents complete VWAP from 9:15 AM,
        # fall back to exchange-provided average_price (ATP) from WebSocket ticks.
        # ATP = session VWAP computed by exchange from 9:15 AM.
        self.vwap_from_websocket = False      # True = use ATP instead of cumulative calc
        self.vwap_websocket_applied = set()   # Symbols already patched from ATP on first tick

        # Optional Telegram notifier for failover/failback alerts (set by caller after init)
        self.telegram = None

        logger.info("DataPipeline initialized")

    def _is_market_open(self):
        """
        Check if market is currently open (data should be flowing)

        Returns True if current time is between 9:15 AM and 3:30 PM IST.
        Used to avoid false disconnection detection after market close.
        """
        now = datetime.now(IST)
        current_time = now.time()
        return MARKET_START_TIME <= current_time <= MARKET_CLOSE_TIME

    def connect(self):
        """Initialize OpenAlgo client and connect WebSocket"""
        try:
            self.client = api(
                api_key=OPENALGO_API_KEY,
                host=OPENALGO_HOST,
                ws_url=OPENALGO_WS_URL
            )
            
            connected = self.client.connect()
            if not connected:
                logger.error("WebSocket authentication failed!")
                self.is_connected = False
                raise Exception("WebSocket authentication failed")
            
            self.is_connected = True
            logger.info(f"Connected and authenticated to OpenAlgo WebSocket: {OPENALGO_WS_URL}")
            
        except Exception as e:
            logger.error(f"Failed to connect to OpenAlgo: {e}")
            self.is_connected = False
            raise
    
    def load_historical_data(self, symbols):
        """
        Load today's historical 1-min bars for all symbols
        
        This ensures swing detection works correctly even when starting mid-day,
        as it populates bar history from market open (9:15 AM) to current time.
        
        🔧 FIX: Handles mid-minute starts by ensuring all complete bars are loaded,
        and prepares to capture the current incomplete bar from live stream.
        
        Args:
            symbols: List of option symbols to fetch history for
        """
        logger.info(f"[HIST] Loading historical data for {len(symbols)} symbols...")
        
        from datetime import date
        today = datetime.now(IST).date()
        start_date = today.strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        
        # Determine the last complete bar timestamp
        now = datetime.now(IST)
        current_minute = now.replace(second=0, microsecond=0)
        
        # Last complete bar is the previous minute
        # (current minute bar is still in progress)
        last_complete_bar_time = current_minute - timedelta(minutes=1)
        
        logger.info(f"[HIST] Current time: {now.strftime('%H:%M:%S')}")
        logger.info(f"[HIST] Last complete bar: {last_complete_bar_time.strftime('%H:%M')}")
        logger.info(f"[HIST] Current bar (in progress): {current_minute.strftime('%H:%M')}")
        
        successful = 0
        failed = 0
        
        for symbol in symbols:
            try:
                # Fetch 1-min historical data for today
                df = self.client.history(
                    symbol=symbol,
                    exchange=EXCHANGE,
                    interval='1m',
                    start_date=start_date,
                    end_date=end_date
                )
                
                # Handle dictionary response (error or empty)
                if isinstance(df, dict):
                    if df.get('status') == 'error':
                        logger.error(f"API error for {symbol}: {df.get('message')}")
                        failed += 1
                        continue
                    # If it's a dict but not error, check if it has data
                    if not df.get('data'):
                        logger.warning(f"No historical data for {symbol}")
                        failed += 1
                        continue
                    # If we reach here, it might be a list of records in 'data'?
                    # OpenAlgo usually returns DataFrame if successful, but just in case:
                    import pandas as pd
                    df = pd.DataFrame(df['data'])
                
                if df is None or df.empty:
                    logger.warning(f"No historical data for {symbol}")
                    failed += 1
                    continue

                # CRITICAL FIX: Sort by timestamp to handle out-of-order data from API
                # Without this, stale/future bars can corrupt swing detection
                df = df.sort_index()

                # CRITICAL FIX: Exclude the in-progress bar from history load.
                # The history API returns the current in-progress bar with incomplete OHLCV.
                # Keeping it causes swing detection to run on partial data, then the live
                # stream's completed bar is rejected as a "DUPLICATE" — leaving the wrong
                # (incomplete) bar permanently in the swing detector's window.
                # Only load bars up to and including last_complete_bar_time.
                last_complete_bar_time_aware = IST.localize(last_complete_bar_time) if last_complete_bar_time.tzinfo is None else last_complete_bar_time
                df = df[df.index <= last_complete_bar_time_aware]

                if df.empty:
                    logger.info(f"[HIST] No complete bars yet for {symbol} (market just opened)")
                    successful += 1
                    continue

                # Debug: Check what columns we have
                if successful == 0:  # Log once
                    logger.info(f"[HIST] DataFrame columns: {df.columns.tolist()}")
                    logger.info(f"[HIST] DataFrame index type: {type(df.index)}")
                    if len(df) > 0:
                        logger.info(f"[HIST] Sample index: {df.index[0]}")
                        logger.info(f"[HIST] Last historical bar: {df.index[-1]}")
                
                # Convert to bars and populate history
                with self.lock:
                    # Initialize session VWAP tracking for this symbol
                    cum_pv = 0.0
                    cum_vol = 0

                    for idx, row in df.iterrows():
                        # Datetime is the index, not a column
                        bar_time = idx

                        if isinstance(bar_time, str):
                            bar_time = datetime.fromisoformat(bar_time)
                        if bar_time.tzinfo is None:
                            bar_time = IST.localize(bar_time)

                        # Round to minute
                        bar_timestamp = bar_time.replace(second=0, microsecond=0)

                        # Create BarData object (lowercase column names from OpenAlgo)
                        bar = BarData(bar_timestamp)
                        bar.open = row.get('open', 0)
                        bar.high = row.get('high', 0)
                        bar.low = row.get('low', 0)
                        bar.close = row.get('close', 0)
                        bar.volume = row.get('volume', 0)
                        bar.tick_count = 10  # Assume complete bar

                        # Calculate cumulative session VWAP from market open
                        # Using typical price = (high + low + close) / 3
                        typical_price = (bar.high + bar.low + bar.close) / 3
                        cum_pv += typical_price * bar.volume
                        cum_vol += bar.volume

                        if cum_vol > 0:
                            bar.vwap = cum_pv / cum_vol
                        else:
                            bar.vwap = typical_price

                        # Add to historical bars
                        self.bars[symbol].append(bar)

                    # Store cumulative values for live bar continuation
                    self.session_vwap_data[symbol] = {
                        'cum_pv': cum_pv,
                        'cum_vol': cum_vol
                    }

                successful += 1
                
            except Exception as e:
                logger.error(f"Failed to load history for {symbol}: {e}")
                failed += 1
        
        logger.info(f"[HIST] Historical data loaded: {successful} success, {failed} failed")

        # Verify completeness; retry up to 3x if Zerodha API lag is detected.
        # Falls back to WebSocket ATP if all retries fail.
        self._ensure_complete_history(now)

        # Log bar counts and gap detection
        if successful > 0:
            sample_symbol = list(self.bars.keys())[0] if self.bars else None
            if sample_symbol:
                bar_count = len(self.bars[sample_symbol])
                logger.info(f"[HIST] Bar history: {bar_count} bars per symbol (from 9:15 AM onwards)")
                
                # Check for gap between last historical bar and current time
                if self.bars[sample_symbol]:
                    last_historical_bar = self.bars[sample_symbol][-1]
                    gap_minutes = int((current_minute - last_historical_bar.timestamp).total_seconds() / 60)
                    
                    if gap_minutes > 1:
                        logger.warning(
                            f"[HIST] GAP DETECTED: {gap_minutes} minutes between last historical bar "
                            f"({last_historical_bar.timestamp.strftime('%H:%M')}) and current minute "
                            f"({current_minute.strftime('%H:%M')})"
                        )
                        logger.warning(
                            f"[HIST] Missing bars will be filled from live stream as data arrives. "
                            f"Current bar ({current_minute.strftime('%H:%M')}) will be built from incoming ticks."
                        )
                    elif gap_minutes == 1:
                        logger.info(
                            f"[HIST]  No gap - last historical bar @ {last_historical_bar.timestamp.strftime('%H:%M')}, "
                            f"current bar @ {current_minute.strftime('%H:%M')} (in progress)"
                        )
    
    def _ensure_complete_history(self, load_time):
        """
        Verify that history loading captured bars from close to 9:15 AM.

        Zerodha's intraday history API can have a ~15-minute lag when polled
        shortly after market open, returning far fewer bars than expected.
        This method retries up to 3 times (60s apart) using the history API.
        If all 3 retries still show insufficient bars, it activates WebSocket
        ATP fallback (vwap_from_websocket=True) so that VWAP values are sourced
        from the exchange-provided average_price field in each tick instead.

        Expected bar count = floor(minutes since 9:15 AM) - 1
        Trigger threshold  = actual_max_bars < expected * 0.8
        """
        market_open = load_time.replace(hour=9, minute=15, second=0, microsecond=0)
        if load_time.time() < time(9, 15):
            return  # Pre-market: no bars expected yet

        minutes_since_open = (load_time - market_open).total_seconds() / 60
        expected_bars = max(0, int(minutes_since_open) - 1)  # -1: current bar incomplete

        if expected_bars < 5:
            return  # Less than 5 min past open — lag cannot meaningfully affect VWAP

        with self.lock:
            max_bars = max((len(v) for v in self.bars.values()), default=0)

        if max_bars >= expected_bars * 0.8:
            logger.info(
                f"[HIST] Bar count OK: {max_bars}/{expected_bars} bars loaded "
                f"(>= 80% threshold). VWAP reliable."
            )
            return

        logger.warning(
            f"[HIST] Incomplete history: {max_bars}/{expected_bars} bars loaded "
            f"(< 80% of expected). Likely Zerodha API lag. "
            f"Retrying up to 3 times (60s apart) before WebSocket ATP fallback."
        )

        for attempt in range(1, 4):
            logger.info(f"[HIST-RETRY] Waiting 60s before attempt {attempt}/3...")
            time_module.sleep(60)

            self._reload_historical_vwap()

            now = datetime.now(IST)
            market_open_now = now.replace(hour=9, minute=15, second=0, microsecond=0)
            expected_now = max(0, int((now - market_open_now).total_seconds() / 60) - 1)

            with self.lock:
                max_bars = max((len(v) for v in self.bars.values()), default=0)

            logger.info(
                f"[HIST-RETRY] Attempt {attempt}/3: {max_bars}/{expected_now} bars after reload."
            )

            if max_bars >= expected_now * 0.8:
                logger.info(
                    f"[HIST-RETRY] History complete after attempt {attempt}. "
                    f"VWAP corrected from full history."
                )
                return

        # All 3 retries exhausted — activate WebSocket ATP fallback
        with self.lock:
            self.vwap_from_websocket = True
        logger.warning(
            f"[HIST-RETRY] All 3 retries failed. Activating WebSocket ATP fallback. "
            f"VWAP will be sourced from exchange average_price as ticks arrive. "
            f"All bar VWAPs will be patched on first tick per symbol."
        )

    def _reload_historical_vwap(self):
        """
        Re-fetch today's full 1-min history and correct VWAP for all symbols.

        On each retry call:
        - Fetches fresh history from OpenAlgo (may now include early bars missed
          due to API lag on first load)
        - Inserts any newly available early bars at the front of self.bars[symbol]
        - Recalculates cumulative VWAP from the first available bar for every bar
          in memory, including live bars added since startup
        - Updates session_vwap_data so all future bar VWAP calculations are correct
        """
        today = datetime.now(IST).date().strftime('%Y-%m-%d')
        now = datetime.now(IST)
        last_complete = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
        if last_complete.tzinfo is None:
            last_complete = IST.localize(last_complete)

        with self.lock:
            symbols = list(self.bars.keys())

        corrected = 0
        for symbol in symbols:
            try:
                df = self.client.history(
                    symbol=symbol,
                    exchange=EXCHANGE,
                    interval='1m',
                    start_date=today,
                    end_date=today
                )

                if isinstance(df, dict) or df is None or (hasattr(df, 'empty') and df.empty):
                    continue

                df = df.sort_index()
                df = df[df.index <= last_complete]

                if df.empty:
                    continue

                with self.lock:
                    existing_timestamps = {b.timestamp for b in self.bars.get(symbol, [])}

                    # Build new early bars and recalculate VWAP for all history rows
                    new_early_bars = []
                    cum_pv = 0.0
                    cum_vol = 0
                    bar_vwap_map = {}

                    for idx, row in df.iterrows():
                        bar_ts = idx.replace(second=0, microsecond=0)
                        tp = (row['high'] + row['low'] + row['close']) / 3
                        cum_pv += tp * row.get('volume', 0)
                        cum_vol += row.get('volume', 0)
                        vwap = cum_pv / cum_vol if cum_vol > 0 else tp
                        bar_vwap_map[bar_ts] = vwap

                        if bar_ts not in existing_timestamps:
                            # Early bar the first load missed — add it now
                            bar = BarData(bar_ts)
                            bar.open = row.get('open', 0)
                            bar.high = row.get('high', 0)
                            bar.low = row.get('low', 0)
                            bar.close = row.get('close', 0)
                            bar.volume = row.get('volume', 0)
                            bar.tick_count = 10
                            bar.vwap = vwap
                            new_early_bars.append(bar)

                    # Prepend missing early bars (they come before all existing bars)
                    if new_early_bars:
                        self.bars[symbol] = new_early_bars + list(self.bars.get(symbol, []))
                        logger.info(
                            f"[HIST-RETRY] {symbol}: inserted {len(new_early_bars)} "
                            f"early bars from history."
                        )

                    # Patch VWAP on every bar now in memory
                    last_known_vwap = None
                    for bar in self.bars.get(symbol, []):
                        corrected_vwap = bar_vwap_map.get(bar.timestamp)
                        if corrected_vwap is not None:
                            bar.vwap = corrected_vwap
                            last_known_vwap = corrected_vwap
                        elif last_known_vwap is not None:
                            # Live bar not yet in history API — carry forward last known
                            bar.vwap = last_known_vwap

                    # Update VWAP accumulator for future live bars
                    self.session_vwap_data[symbol] = {'cum_pv': cum_pv, 'cum_vol': cum_vol}

                corrected += 1

            except Exception as e:
                logger.error(f"[HIST-RETRY] Error reloading {symbol}: {e}")

        logger.info(f"[HIST-RETRY] VWAP reloaded for {corrected}/{len(symbols)} symbols.")
        return corrected

    def fill_initial_gap(self):
        """
        🔧 FIX: Fill any gap between last historical bar and current time
        
        This handles the case where the system starts mid-session and there's
        a gap between the last complete historical bar and the current minute.
        
        Example scenario (starting at 09:16:11):
        - Historical API returns bars up to 09:15 (last complete bar)
        - Current time is 09:16:11 (mid-minute)
        - Gap: 09:16 bar is missing (incomplete, won't be in historical)
        - Solution: Wait a few seconds for 09:16 to complete, then fetch it
        
        Called after load_historical_data() and before subscribe_options()
        """
        now = datetime.now(IST)
        current_minute = now.replace(second=0, microsecond=0)
        
        # Get a sample symbol to check for gaps
        if not self.bars:
            logger.debug("[GAP-FILL] No bars loaded yet, skipping gap fill")
            return
        
        sample_symbol = list(self.bars.keys())[0]
        if not self.bars[sample_symbol]:
            logger.debug("[GAP-FILL] No bars for sample symbol, skipping gap fill")
            return
        
        last_bar = self.bars[sample_symbol][-1]
        last_bar_minute = last_bar.timestamp
        
        # Calculate gap in minutes
        gap_minutes = int((current_minute - last_bar_minute).total_seconds() / 60)
        
        if gap_minutes <= 1:
            # No gap or only current incomplete bar
            logger.info(
                f"[GAP-FILL]  No gap to fill (last bar @ {last_bar_minute.strftime('%H:%M')}, "
                f"current @ {current_minute.strftime('%H:%M')})"
            )
            return
        
        logger.warning(
            f"[GAP-FILL] Gap detected: {gap_minutes} minutes between last bar "
            f"({last_bar_minute.strftime('%H:%M')}) and current time ({current_minute.strftime('%H:%M')})"
        )
        
        # If we're in the first few seconds of a new minute, wait for the previous minute to complete
        # so we can fetch it from historical API
        if now.second < 10:
            wait_seconds = 12 - now.second  # Wait until :12 seconds
            logger.info(
                f"[GAP-FILL] Current time is {now.strftime('%H:%M:%S')} - waiting {wait_seconds}s "
                f"for previous minute to finalize in broker systems..."
            )
            time_module.sleep(wait_seconds)
            logger.info("[GAP-FILL] Wait complete, fetching missed bars...")
        
        # Fetch bars to fill the gap
        today = datetime.now(IST).date()
        start_date = today.strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        
        filled_count = 0
        failed_count = 0
        
        for symbol in self.bars.keys():
            try:
                # Fetch today's history (should now include the missing bars)
                df = self.client.history(
                    symbol=symbol,
                    exchange=EXCHANGE,
                    interval='1m',
                    start_date=start_date,
                    end_date=end_date
                )
                
                # Handle dictionary response
                if isinstance(df, dict):
                    if df.get('status') == 'error':
                        failed_count += 1
                        continue
                    if not df.get('data'):
                        continue
                    import pandas as pd
                    df = pd.DataFrame(df['data'])

                if df is None or df.empty:
                    continue
                
                # Filter to bars after last_bar_minute
                last_bar_time = self.bars[symbol][-1].timestamp if self.bars[symbol] else None
                if last_bar_time is None:
                    continue
                
                missed_bars = df[df.index > last_bar_time]
                
                if missed_bars.empty:
                    continue
                
                # Add missed bars to history
                with self.lock:
                    # Get current session VWAP cumulative values
                    # 🔧 FIX: If VWAP data missing (after reconnect), recalculate from existing bars
                    if symbol not in self.session_vwap_data:
                        logger.warning(
                            f"[GAP-FILL] {symbol}: VWAP data missing - recalculating from "
                            f"{len(self.bars[symbol])} existing bars"
                        )
                        cum_pv = 0.0
                        cum_vol = 0
                        for existing_bar in self.bars[symbol]:
                            typical_price = (existing_bar.high + existing_bar.low + existing_bar.close) / 3
                            cum_pv += typical_price * existing_bar.volume
                            cum_vol += existing_bar.volume
                        self.session_vwap_data[symbol] = {'cum_pv': cum_pv, 'cum_vol': cum_vol}
                        logger.info(
                            f"[GAP-FILL] {symbol}: VWAP restored "
                            f"(cum_pv={cum_pv:.2f}, cum_vol={cum_vol})"
                        )
                    else:
                        vwap_data = self.session_vwap_data[symbol]
                        cum_pv = vwap_data['cum_pv']
                        cum_vol = vwap_data['cum_vol']

                    # Build set of existing bar timestamps for dedup
                    existing_timestamps = {b.timestamp for b in self.bars[symbol]}

                    for idx, row in missed_bars.iterrows():
                        bar_time = idx

                        if isinstance(bar_time, str):
                            bar_time = datetime.fromisoformat(bar_time)
                        if bar_time.tzinfo is None:
                            bar_time = IST.localize(bar_time)

                        bar_timestamp = bar_time.replace(second=0, microsecond=0)

                        # Don't add bars that are in the future or current incomplete bar
                        current_check = datetime.now(IST).replace(second=0, microsecond=0)
                        if bar_timestamp >= current_check:
                            logger.debug(
                                f"[GAP-FILL] Skipping incomplete/future bar @ {bar_timestamp.strftime('%H:%M')}"
                            )
                            continue

                        # Dedup: skip if bar already exists for this timestamp
                        if bar_timestamp in existing_timestamps:
                            logger.debug(
                                f"[GAP-FILL] Skipping duplicate bar @ "
                                f"{bar_timestamp.strftime('%H:%M')} for {symbol}"
                            )
                            continue

                        bar = BarData(bar_timestamp)
                        bar.open = row.get('open', 0)
                        bar.high = row.get('high', 0)
                        bar.low = row.get('low', 0)
                        bar.close = row.get('close', 0)
                        bar.volume = row.get('volume', 0)
                        bar.tick_count = 10  # Assume complete bar

                        # Calculate cumulative session VWAP
                        typical_price = (bar.high + bar.low + bar.close) / 3
                        cum_pv += typical_price * bar.volume
                        cum_vol += bar.volume

                        if cum_vol > 0:
                            bar.vwap = cum_pv / cum_vol
                        else:
                            bar.vwap = typical_price

                        self.bars[symbol].append(bar)
                        existing_timestamps.add(bar_timestamp)  # Track newly added
                        filled_count += 1

                    # Update session VWAP cumulative values
                    self.session_vwap_data[symbol] = {
                        'cum_pv': cum_pv,
                        'cum_vol': cum_vol
                    }
                
                if len(missed_bars) > 0:
                    logger.debug(f"[GAP-FILL] Added {len(missed_bars)} bars for {symbol}")
                
            except Exception as e:
                logger.error(f"[GAP-FILL] Failed to fill gap for {symbol}: {e}")
                failed_count += 1
        
        if filled_count > 0:
            logger.info(
                f"[GAP-FILL]  Filled gap: Added {filled_count} bars across all symbols "
                f"({failed_count} symbols failed)"
            )
            
            # Log updated bar count
            if sample_symbol in self.bars:
                new_bar_count = len(self.bars[sample_symbol])
                new_last_bar = self.bars[sample_symbol][-1]
                logger.info(
                    f"[GAP-FILL] Updated history: {new_bar_count} bars, "
                    f"last bar @ {new_last_bar.timestamp.strftime('%H:%M')}"
                )
        else:
            logger.warning(f"[GAP-FILL] No bars could be filled (failed: {failed_count})")
    
    def get_atm_strike(self, spot_price):
        """Calculate ATM strike from spot price (rounded to nearest 100)"""
        return round(spot_price / 100) * 100
    
    def generate_option_symbols(self, atm_strike, expiry_date):
        """
        Generate list of option symbols to subscribe
        
        Args:
            atm_strike: ATM strike price (e.g., 18000)
            expiry_date: Expiry date string (e.g., '26DEC24')
        
        Returns:
            List of symbols: ['NIFTY26DEC2418000CE', 'NIFTY26DEC2418000PE', ...]
        """
        symbols = []
        strike_interval = 50  # NIFTY strike interval
        
        for i in range(-STRIKE_SCAN_RANGE, STRIKE_SCAN_RANGE + 1):
            strike = atm_strike + (i * strike_interval)
            
            # Generate CE and PE symbols
            ce_symbol = f"NIFTY{expiry_date}{strike}CE"
            pe_symbol = f"NIFTY{expiry_date}{strike}PE"
            
            symbols.extend([ce_symbol, pe_symbol])
        
        logger.info(f"Generated {len(symbols)} option symbols around ATM {atm_strike}")
        logger.info(f"Sample symbols: {symbols[:3]}")  # Debug: show first 3 symbols
        return symbols
    
    def subscribe_options(self, symbols, spot_symbol=None):
        """
        Subscribe to option symbols and NIFTY spot via WebSocket

        Args:
            symbols: List of option symbols to subscribe
            spot_symbol: Optional NIFTY spot symbol (e.g., "Nifty 50")
        """
        if not self.is_connected:
            logger.error("Cannot subscribe: WebSocket not connected")
            return

        all_symbols = list(symbols)

        # Add spot symbol if provided
        if spot_symbol:
            all_symbols.append(spot_symbol)
            logger.info(f"Including NIFTY spot symbol: {spot_symbol}")

        instruments = [
            {"exchange": EXCHANGE, "symbol": symbol}
            for symbol in symbols
        ]

        # Add spot instrument with NSE exchange
        if spot_symbol:
            instruments.append({
                "exchange": "NSE",  # Spot is on NSE, not NFO
                "symbol": spot_symbol
            })

        try:
            # Subscribe to quote mode (LTP, OHLC, Volume)
            self.client.subscribe_quote(
                instruments,
                on_data_received=self._on_quote_update_zerodha
            )

            with self.lock:
                self.subscribed_symbols.update(all_symbols)
                if self.subscription_started_at is None:
                    self.subscription_started_at = datetime.now(IST)

            logger.info(f"Subscribed to {len(symbols)} option symbols + {1 if spot_symbol else 0} spot symbol")

            # Start connection monitoring thread if not already running
            if not self.monitor_running:
                self.start_connection_monitor()

        except Exception as e:
            logger.error(f"Failed to subscribe to options: {e}")
            raise

    def resubscribe_symbol(self, symbol: str):
        """Re-subscribe a single symbol in QUOTE mode after order placement.

        OpenAlgo v2 internally re-subscribes ordered symbols in LTP mode
        (for order monitoring), which kills our QUOTE mode feed. This method
        uses a 3-second delayed thread to let OpenAlgo v2's LTP downgrade
        happen first, then overrides with QUOTE mode.
        """
        if not self.is_connected:
            logger.warning(f"[RESUB] Cannot re-subscribe {symbol}: WebSocket not connected")
            return

        def _delayed_resub():
            import time as _time
            _time.sleep(3)  # Let OpenAlgo v2 LTP downgrade happen first
            if not self.is_connected:
                logger.warning(f"[RESUB] Delayed re-subscribe aborted for {symbol}: disconnected")
                return
            instruments = [{"exchange": EXCHANGE, "symbol": symbol}]
            try:
                self.client.subscribe_quote(
                    instruments,
                    on_data_received=self._on_quote_update_zerodha
                )
                logger.info(f"[RESUB] Re-subscribed {symbol} in QUOTE mode (3s delay)")
            except Exception as e:
                logger.warning(f"[RESUB] Failed to re-subscribe {symbol}: {e}")

        import threading
        t = threading.Thread(target=_delayed_resub, daemon=True, name=f"resub-{symbol}")
        t.start()

    def resubscribe_symbols_batch(self, symbols):
        """Re-subscribe multiple symbols in QUOTE mode (for periodic heartbeat resub).

        Unlike resubscribe_symbol(), this is immediate (no delay) since it's called
        periodically from the heartbeat block, not right after order placement.
        """
        if not self.is_connected or not symbols:
            return

        instruments = [{"exchange": EXCHANGE, "symbol": s} for s in symbols]
        try:
            self.client.subscribe_quote(
                instruments,
                on_data_received=self._on_quote_update_zerodha
            )
            logger.info(f"[RESUB-BATCH] Re-subscribed {len(symbols)} symbols in QUOTE mode")
        except Exception as e:
            logger.warning(f"[RESUB-BATCH] Failed to batch re-subscribe: {e}")

    def start_connection_monitor(self):
        """
        Start background thread to monitor WebSocket connection health

        This thread periodically checks connection status and automatically
        triggers reconnection if the connection is lost.
        """
        if self.monitor_running:
            logger.debug("Connection monitor already running")
            return

        self.monitor_running = True
        self.monitor_thread = Thread(target=self._connection_monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("[MONITOR] Connection health monitoring started")

    def stop_connection_monitor(self):
        """Stop the connection monitoring thread"""
        self.monitor_running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("[MONITOR] Connection monitoring stopped")

    def _connection_monitor_loop(self):
        """
        Background loop that monitors connection health

        🔧 ENHANCED: Detects "socket alive but no ticks" scenario (common with Upstox)

        Checks every 10 seconds:
        1. If WebSocket is connected
        2. If data is flowing (recent ticks received)
        3. 🔧 NEW: No-tick heartbeat detection (10s threshold)
        4. Triggers reconnection if issues detected
        """
        logger.info("[MONITOR] Connection monitor loop started")

        while self.monitor_running:
            try:
                time_module.sleep(10)  # Check every 10 seconds

                if not self.monitor_running:
                    break

                # Skip monitoring if we're already reconnecting
                if self.is_reconnecting:
                    continue

                # Check 1: WebSocket connection status
                if not self.is_connected:
                    logger.warning("[MONITOR] Zerodha WebSocket disconnected - triggering failover/reconnect")
                    self._trigger_failover_or_reconnect("WEBSOCKET_DISCONNECTED")
                    continue

                # Check 2 & 3: Data flow (only check if we have subscribed symbols and data has started)
                with self.lock:
                    # NEW Check 2a: Subscribed but no ticks EVER received.
                    # Catches "WS proxy up but Zerodha session expired" — the proxy TCP
                    # connection stays alive so is_connected stays True, but Zerodha
                    # rejects its own WebSocket with HTTP 403. No ticks flow, so
                    # first_data_received_at is never set and the staleness checks below
                    # are skipped indefinitely. We detect this by timing how long we've
                    # been subscribed with zero ticks.
                    if (self.subscribed_symbols
                            and self.first_data_received_at is None
                            and self.subscription_started_at is not None
                            and self._is_market_open()):
                        _now = datetime.now(IST)
                        seconds_since_subscribed = (
                            _now - self.subscription_started_at
                        ).total_seconds()
                        if seconds_since_subscribed > FAILOVER_NO_TICK_THRESHOLD:
                            logger.warning(
                                f"[MONITOR] No ticks received {seconds_since_subscribed:.0f}s "
                                f"since subscription (threshold: {FAILOVER_NO_TICK_THRESHOLD}s)"
                                f" - triggering failover"
                            )
                            self._trigger_failover_or_reconnect(
                                f"NO_TICKS_SINCE_SUBSCRIBE:{seconds_since_subscribed:.0f}s"
                            )
                            continue

                    if self.subscribed_symbols and self.first_data_received_at is not None:
                        now = datetime.now(IST)

                        # Skip data staleness checks if market is closed
                        # After 3:30 PM, WebSocket stops sending data - this is expected behavior
                        if not self._is_market_open():
                            logger.debug(
                                f"[MONITOR] Market closed (current time: {now.strftime('%H:%M:%S')}) - "
                                f"skipping data freshness checks"
                            )
                            continue

                        # --- Zerodha tick staleness check ---
                        if not self.is_failover_active:
                            # On Zerodha: check if ticks have gone stale → failover
                            # Fall back to last_tick_time if last_zerodha_tick_time empty
                            # (both are Zerodha ticks when active_source == 'zerodha')
                            zerodha_tick_source = self.last_zerodha_tick_time if self.last_zerodha_tick_time else self.last_tick_time
                            if zerodha_tick_source:
                                most_recent_zerodha_tick = max(zerodha_tick_source.values())
                                seconds_since_zerodha_tick = (now - most_recent_zerodha_tick).total_seconds()
                                if seconds_since_zerodha_tick > FAILOVER_NO_TICK_THRESHOLD:
                                    logger.warning(
                                        f"[MONITOR] No Zerodha ticks for {seconds_since_zerodha_tick:.0f}s "
                                        f"(threshold: {FAILOVER_NO_TICK_THRESHOLD}s) - triggering failover"
                                    )
                                    self._trigger_failover_or_reconnect(f"NO_TICKS:{seconds_since_zerodha_tick:.0f}s")
                                    continue
                        else:
                            # On Angel One: check if Zerodha ticks have RESUMED → switchback.
                            # MUST use last_zerodha_tick_time ONLY — last_tick_time contains
                            # Angel One ticks which would falsely indicate Zerodha is alive
                            # and trigger a premature switchback to the dead Zerodha feed.
                            if self.last_zerodha_tick_time:
                                most_recent_zerodha_tick = max(self.last_zerodha_tick_time.values())
                                seconds_since_zerodha_tick = (now - most_recent_zerodha_tick).total_seconds()
                                if seconds_since_zerodha_tick <= FAILOVER_NO_TICK_THRESHOLD:
                                    # Zerodha ticks are flowing again - track how long
                                    if self.zerodha_continuous_tick_start is None:
                                        self.zerodha_continuous_tick_start = now
                                        logger.info("[MONITOR] Zerodha ticks resumed - monitoring for switchback...")
                                    else:
                                        seconds_flowing = (now - self.zerodha_continuous_tick_start).total_seconds()
                                        if seconds_flowing >= FAILOVER_SWITCHBACK_THRESHOLD:
                                            logger.info(
                                                f"[MONITOR] Zerodha ticks stable for {seconds_flowing:.0f}s - switching back"
                                            )
                                            self._failback_to_zerodha()
                                else:
                                    # Zerodha ticks not flowing yet - reset the counter
                                    self.zerodha_continuous_tick_start = None
                            else:
                                # No Zerodha ticks at all — don't attempt switchback
                                self.zerodha_continuous_tick_start = None

                        # Count fresh symbols (from active source)
                        fresh_count = 0
                        for symbol in self.subscribed_symbols:
                            last_tick = self.last_tick_time.get(symbol)
                            if last_tick:
                                age = (now - last_tick).total_seconds()
                                if age <= MAX_TICK_AGE_SECONDS:
                                    fresh_count += 1

                        total_symbols = len(self.subscribed_symbols)
                        coverage = fresh_count / total_symbols if total_symbols > 0 else 0

                        # If active source data coverage drops below threshold, trigger action
                        if coverage < MIN_DATA_COVERAGE_THRESHOLD:
                            logger.warning(
                                f"[MONITOR] Data coverage low ({coverage:.1%}, {fresh_count}/{total_symbols} fresh) - "
                                f"triggering failover/reconnect"
                            )
                            self._trigger_failover_or_reconnect(f"LOW_DATA_COVERAGE:{coverage:.1%}")
                            continue

            except Exception as e:
                logger.error(f"[MONITOR] Error in connection monitor: {e}")
                time_module.sleep(5)

        logger.info("[MONITOR] Connection monitor loop stopped")

    def _trigger_auto_reconnect(self, reason):
        """Legacy wrapper - delegates to _trigger_failover_or_reconnect"""
        self._trigger_failover_or_reconnect(reason)

    def _trigger_failover_or_reconnect(self, reason):
        """
        Triggered when Zerodha data fails.

        If Angel One backup is available → failover to Angel One immediately,
        then reconnect Zerodha in the background.

        If Angel One is not available → fall back to plain Zerodha reconnect.

        Args:
            reason: String describing why this was triggered
        """
        if not self.auto_reconnect_enabled:
            logger.warning(f"[MONITOR] Auto-reconnect disabled, ignoring trigger: {reason}")
            return

        if self.is_reconnecting:
            logger.debug(f"[MONITOR] Already reconnecting, ignoring trigger: {reason}")
            return

        logger.warning(f"[MONITOR] Data failure detected: {reason}")

        if self.angelone_is_connected and not self.is_failover_active:
            # Angel One is ready - failover immediately
            self._failover_to_angelone(reason)
            # Reconnect Zerodha in background
            reconnect_thread = Thread(target=self.reconnect, daemon=True)
            reconnect_thread.start()
        else:
            # No Angel One backup available - plain reconnect
            if not self.is_reconnecting:
                logger.warning("[MONITOR] Angel One backup not available - attempting Zerodha reconnect")
                reconnect_thread = Thread(target=self.reconnect, daemon=True)
                reconnect_thread.start()
    
    def _on_quote_update_zerodha(self, data):
        """
        Zerodha WebSocket tick callback.

        Always updates last_zerodha_tick_time (used for failover/switchback detection).
        Only processes the tick into bars when Zerodha is the active source.
        active_source is read inside the lock to avoid a TOCTOU race with _failover_to_angelone.
        """
        symbol = data.get('symbol')
        should_process = False
        with self.lock:
            if symbol:
                self.last_zerodha_tick_time[symbol] = datetime.now(IST)
            should_process = (self.active_source == 'zerodha')

        if should_process:
            self._process_tick(data, source='zerodha')

    def _on_quote_update_angelone(self, data):
        """
        Angel One WebSocket tick callback.

        Only processes the tick into bars when Angel One is the active source (failover mode).
        active_source is read inside the lock to avoid a TOCTOU race with _failback_to_zerodha.
        Does NOT update last_zerodha_tick_time - switchback detection uses that dict
        exclusively for Zerodha ticks.
        """
        with self.lock:
            should_process = (self.active_source == 'angelone')

        if should_process:
            self._process_tick(data, source='angelone')

    def _process_tick(self, data, source=None):
        """
        Core tick processing - aggregates ticks into 1-min OHLCV bars.

        Called by whichever source is currently active (_on_quote_update_zerodha
        or _on_quote_update_angelone). Source switching is transparent to this method.

        Args:
            data: Tick data dict with 'symbol' and 'data' keys
            source: 'zerodha' or 'angelone' - re-verified to prevent TOCTOU race

        Expected data format:
        {
            'symbol': 'NIFTY26DEC2418000CE',
            'data': {
                'ltp': 245.50,
                'high': 250.00,
                'low': 240.00,
                'volume': 12500,
                'timestamp': '2024-12-20 10:15:30'
            }
        }
        """
        # TOCTOU guard: re-verify source is still active
        if source:
            with self.lock:
                if self.active_source != source:
                    return  # Source switched during handoff, discard tick
        try:
            symbol = data.get('symbol')
            quote_data = data.get('data', {})

            ltp = quote_data.get('ltp')
            volume = quote_data.get('volume', 1)
            # Exchange-provided session VWAP (ATP). Present in quote/full mode ticks.
            average_price = quote_data.get('average_price', 0)

            if not symbol or ltp is None:
                return

            now = datetime.now(IST)

            # Update last tick time (active source)
            with self.lock:
                self.last_tick_time[symbol] = now

                # Track first data received
                if self.first_data_received_at is None:
                    self.first_data_received_at = now

            # Get current minute timestamp (rounded down)
            bar_timestamp = now.replace(second=0, microsecond=0)
            
            with self.lock:
                # WebSocket ATP fallback: on first tick for this symbol, patch all
                # historical bar VWAPs with exchange ATP (accurate from 9:15 AM).
                if (self.vwap_from_websocket
                        and average_price > 0
                        and symbol not in self.vwap_websocket_applied):
                    for bar in self.bars.get(symbol, []):
                        bar.vwap = average_price
                    self.vwap_websocket_applied.add(symbol)
                    logger.info(
                        f"[VWAP-ATP] {symbol}: patched {len(self.bars.get(symbol, []))} "
                        f"historical bars with exchange ATP={average_price:.2f}"
                    )

                # Check if we need to start a new bar
                current_bar = self.current_bars.get(symbol)

                if current_bar is None or current_bar.timestamp != bar_timestamp:
                    # Save completed bar
                    if current_bar is not None and current_bar.is_valid():
                        if self.vwap_from_websocket and average_price > 0:
                            # ATP fallback: use exchange-provided session VWAP directly
                            current_bar.vwap = average_price
                        else:
                            # Normal path: update cumulative VWAP accumulator
                            vwap_data = self.session_vwap_data.get(symbol, {'cum_pv': 0.0, 'cum_vol': 0})
                            typical_price = (current_bar.high + current_bar.low + current_bar.close) / 3
                            vwap_data['cum_pv'] += typical_price * current_bar.volume
                            vwap_data['cum_vol'] += current_bar.volume

                            # Calculate and set session VWAP for this bar
                            if vwap_data['cum_vol'] > 0:
                                current_bar.vwap = vwap_data['cum_pv'] / vwap_data['cum_vol']
                            else:
                                current_bar.vwap = typical_price

                            # Store updated cumulative values
                            self.session_vwap_data[symbol] = vwap_data

                        # Capture exchange ATP at bar close
                        if average_price > 0:
                            current_bar.atp = average_price

                        self.bars[symbol].append(current_bar)
                        # Store when bar was RECEIVED, not bar's timestamp (for watchdog)
                        self.last_bar_timestamp[symbol] = datetime.now(IST)
                        logger.info(f"[BAR] {symbol} | O:{current_bar.open:.2f} H:{current_bar.high:.2f} L:{current_bar.low:.2f} C:{current_bar.close:.2f}")

                        # Prune bars if threshold exceeded
                        if len(self.bars[symbol]) > BAR_PRUNING_THRESHOLD:
                            removed = len(self.bars[symbol]) - MAX_BARS_PER_SYMBOL
                            self.bars[symbol] = self.bars[symbol][-MAX_BARS_PER_SYMBOL:]
                            logger.debug(
                                f"Pruned {removed} old bars from {symbol} "
                                f"(kept {MAX_BARS_PER_SYMBOL})"
                            )

                    # Start new bar
                    current_bar = BarData(bar_timestamp)
                    self.current_bars[symbol] = current_bar

                # Update current bar with tick
                current_bar.update_tick(ltp, volume)

                # ATP fallback: keep current bar's VWAP up to date each tick
                # so the filter sees the correct value even mid-bar.
                if self.vwap_from_websocket and average_price > 0:
                    current_bar.vwap = average_price

        except Exception as e:
            logger.error(f"[TICK] Error processing tick: {e}")
    
    def get_latest_bar(self, symbol):
        """
        Get latest completed bar for symbol

        Returns:
            Defensive copy of BarData object or None if no bars available
        """
        with self.lock:
            bars = self.bars.get(symbol, [])
            return copy.copy(bars[-1]) if bars else None

    def get_current_bar(self, symbol):
        """
        Get current (incomplete) bar for symbol

        Returns:
            Defensive copy of BarData object or None
        """
        with self.lock:
            bar = self.current_bars.get(symbol)
            return copy.copy(bar) if bar else None

    def get_bars(self, symbol, count=100):
        """
        Get last N completed bars for symbol

        Args:
            symbol: Option symbol
            count: Number of bars to return

        Returns:
            List of defensive copies of BarData objects
        """
        with self.lock:
            bars = self.bars.get(symbol, [])
            return [copy.copy(b) for b in bars[-count:]] if bars else []

    def get_bars_for_symbol(self, symbol):
        """
        Get ALL completed bars for symbol (used for swing detection)

        Args:
            symbol: Option symbol

        Returns:
            List of defensive copies of BarData objects
        """
        with self.lock:
            return [copy.copy(b) for b in self.bars.get(symbol, [])]
    
    def get_all_latest_bars(self):
        """
        Get latest COMPLETED bar for all subscribed symbols

        Returns:
            Dict {symbol: BarData}
        """
        result = {}
        with self.lock:
            for symbol in self.subscribed_symbols:
                bar = self.get_latest_bar(symbol)
                if bar:
                    result[symbol] = bar
        return result

    def get_all_current_bars(self):
        """
        Get current INCOMPLETE bar for all subscribed symbols (real-time ticks)

        Returns:
            Dict {symbol: BarData}
        """
        result = {}
        with self.lock:
            for symbol in self.subscribed_symbols:
                bar = self.get_current_bar(symbol)
                if bar:
                    result[symbol] = bar
        return result

    def is_data_stale(self, symbol, max_age_seconds=MAX_TICK_AGE_SECONDS):
        """Check if data for symbol is stale (no recent ticks)"""
        with self.lock:
            return self._is_data_stale_unlocked(symbol, max_age_seconds)

    def _is_data_stale_unlocked(self, symbol, max_age_seconds=MAX_TICK_AGE_SECONDS):
        """Internal version without lock - must be called with lock held"""
        last_tick = self.last_tick_time.get(symbol)
        if last_tick is None:
            return True

        age = (datetime.now(IST) - last_tick).total_seconds()
        return age > max_age_seconds

    def get_spot_price(self, spot_symbol="Nifty 50"):
        """
        Get current NIFTY spot price from WebSocket (latest tick)

        Args:
            spot_symbol: Symbol for NIFTY spot (default: "Nifty 50")

        Returns:
            float: Latest LTP (last traded price) or None if unavailable
        """
        with self.lock:
            # Try to get from current bar (real-time tick)
            current_bar = self.current_bars.get(spot_symbol)
            if current_bar and current_bar.close is not None:
                return current_bar.close  # LTP is stored as close

            # Fallback: Get from latest completed bar
            bars = self.bars.get(spot_symbol, [])
            if bars:
                return bars[-1].close

            return None

    def get_spot_bar(self, spot_symbol="Nifty 50", bar_time=None):
        """
        Get NIFTY spot bar at specific timestamp (e.g., 9:16 AM candle)

        Args:
            spot_symbol: Symbol for NIFTY spot (default: "Nifty 50")
            bar_time: datetime object for bar timestamp (e.g., 9:16 AM)

        Returns:
            BarData object or None if not found
        """
        if bar_time is None:
            # Return latest completed bar
            return self.get_latest_bar(spot_symbol)

        # Normalize bar_time to minute boundary
        if bar_time.tzinfo is None:
            bar_time = IST.localize(bar_time)
        target_timestamp = bar_time.replace(second=0, microsecond=0)

        with self.lock:
            bars = self.bars.get(spot_symbol, [])
            for bar in bars:
                if bar.timestamp == target_timestamp:
                    return bar

            return None
    
    def get_health_status(self):
        """
        Get pipeline health status
        
        Returns:
            Dict with health metrics
        """
        with self.lock:
            total_symbols = len(self.subscribed_symbols)
            # Count symbols with either completed bars OR current bars (ticks received)
            symbols_with_data = len([s for s in self.subscribed_symbols 
                                    if self.bars.get(s) or self.current_bars.get(s)])
            # Use internal unlocked version to avoid deadlock
            stale_symbols = len([s for s in self.subscribed_symbols if self._is_data_stale_unlocked(s)])
            
            return {
                'connected': self.is_connected,
                'subscribed_symbols': total_symbols,
                'symbols_with_data': symbols_with_data,
                'stale_symbols': stale_symbols,
                'data_coverage': symbols_with_data / total_symbols if total_symbols > 0 else 0,
                'watchdog_triggered': self.watchdog_triggered,
                'consecutive_stale_checks': self.consecutive_stale_checks,
            }
    
    def check_data_freshness(self):
        """
        WATCHDOG: Check if data is fresh enough for trading

        Returns:
            (bool, str): (is_fresh, reason_if_stale)

        Failure conditions:
        1. Data coverage < 50% (half the symbols are stale)
        2. No fresh data for 30+ seconds (WebSocket frozen)
        3. Last bar >2 minutes old (bar aggregation stopped)

        Note: Returns True (data is fresh) outside market hours (before 9:15 AM
        or after 3:30 PM) since no data flow is expected when market is closed.
        """
        now = datetime.now(IST)

        # Skip freshness check outside market hours
        # After 3:30 PM, WebSocket stops sending data - this is expected
        if not self._is_market_open():
            return True, ""

        with self.lock:
            # Skip check if no data received yet (startup phase)
            if self.first_data_received_at is None:
                return True, ""
            
            # Check 1: Data coverage threshold
            total_symbols = len(self.subscribed_symbols)
            if total_symbols == 0:
                return True, ""  # No symbols yet
            
            fresh_symbols = 0
            for symbol in self.subscribed_symbols:
                if not self._is_data_stale_unlocked(symbol, max_age_seconds=MAX_TICK_AGE_SECONDS):
                    fresh_symbols += 1
            
            data_coverage = fresh_symbols / total_symbols
            
            if data_coverage < MIN_DATA_COVERAGE_THRESHOLD:
                self.consecutive_stale_checks += 1
                logger.warning(
                    f"[WARNING]  Data coverage {data_coverage:.1%} < {MIN_DATA_COVERAGE_THRESHOLD:.1%} "
                    f"({fresh_symbols}/{total_symbols} symbols fresh) | "
                    f"Consecutive failures: {self.consecutive_stale_checks}"
                )
                
                # Trigger watchdog after consecutive failures
                if self.consecutive_stale_checks >= 3:
                    self.watchdog_triggered = True
                    return False, f"DATA_COVERAGE_LOW:{data_coverage:.1%}"
            else:
                # Reset counter on success
                self.consecutive_stale_checks = 0
            
            # Check 2: Stale data timeout (no fresh ticks in 30s)
            if self.last_tick_time:
                latest_tick = max(self.last_tick_time.values())
                time_since_last_tick = (now - latest_tick).total_seconds()
                
                if time_since_last_tick > STALE_DATA_TIMEOUT:
                    self.watchdog_triggered = True
                    return False, f"NO_FRESH_TICKS:{time_since_last_tick:.0f}s"
            
            # Check 3: Time since last bar received (not bar timestamp)
            if self.last_bar_timestamp:
                latest_bar_received = max(self.last_bar_timestamp.values())
                time_since_bar = (now - latest_bar_received).total_seconds()

                if time_since_bar > MAX_BAR_AGE_SECONDS:
                    self.watchdog_triggered = True
                    return False, f"STALE_BARS:{time_since_bar:.0f}s"
            
            return True, ""
    
    def reset_watchdog(self):
        """Reset watchdog state (called after reconnection)"""
        with self.lock:
            self.consecutive_stale_checks = 0
            self.watchdog_triggered = False
            logger.info("Watchdog reset")
    
    def reconnect(self):
        """
        Reconnect WebSocket with exponential backoff

        🔧 CRITICAL FIXES:
        - Forces resubscription on EVERY reconnect (Upstox drops subscriptions silently)
        - Resets bar state to prevent frozen bars
        - Clears tick/bar timestamps to force fresh data validation
        - Backfills missed bars during disconnection

        Returns:
            bool: True if reconnection successful, False otherwise
        """
        # Prevent multiple simultaneous reconnection attempts
        with self.lock:
            if self.is_reconnecting:
                logger.debug("Reconnection already in progress, skipping duplicate attempt")
                return False
            self.is_reconnecting = True

        try:
            logger.warning("[RECONNECT] WebSocket disconnected, attempting reconnection...")

            self.last_disconnect_time = datetime.now(IST)

            # Store subscribed symbols before attempting reconnection
            symbols_to_resubscribe = list(self.subscribed_symbols) if self.subscribed_symbols else []

            if not symbols_to_resubscribe:
                logger.error("[RECONNECT] No symbols to resubscribe - cannot reconnect")
                return False

            for attempt in range(1, WEBSOCKET_MAX_RECONNECT_ATTEMPTS + 1):
                try:
                    delay = WEBSOCKET_RECONNECT_DELAY * attempt  # Exponential backoff
                    logger.info(
                        f"[RECONNECT] Attempt {attempt}/{WEBSOCKET_MAX_RECONNECT_ATTEMPTS} "
                        f"(waiting {delay}s)..."
                    )

                    time_module.sleep(delay)

                    # 🔧 FIX A: Disconnect old client cleanly
                    if self.client:
                        try:
                            self.client.disconnect()
                            logger.debug("[RECONNECT] Old client disconnected")
                        except Exception as e:
                            logger.debug(f"[RECONNECT] Error disconnecting old client: {e}")

                    # Small delay to ensure clean disconnect
                    time_module.sleep(1)

                    # Try to reconnect WebSocket
                    self.connect()

                    if not self.is_connected:
                        logger.warning(f"[RECONNECT] Attempt {attempt} - connect() returned False")
                        continue

                    logger.info(f"[RECONNECT]  WebSocket connected on attempt {attempt}")

                    # 🔧 FIX B: Reset bar state to prevent frozen bars
                    with self.lock:
                        # Clear current incomplete bars (will be rebuilt from fresh ticks)
                        old_current_bars = len(self.current_bars)
                        self.current_bars.clear()
                        logger.info(f"[RECONNECT] Cleared {old_current_bars} incomplete bars")

                        # FIX C: Reset tick and bar timestamps (force fresh data validation)
                        # Save bar timestamps BEFORE clearing — backfill_missed_bars needs them
                        old_tick_count = len(self.last_tick_time)
                        old_bar_count = len(self.last_bar_timestamp)
                        self._saved_bar_timestamps = dict(self.last_bar_timestamp)
                        self.last_tick_time.clear()
                        self.last_bar_timestamp.clear()
                        # Also clear Zerodha tick timestamps so the monitor does not
                        # re-trigger failover immediately after switchback due to
                        # stale pre-disconnect timestamps
                        self.last_zerodha_tick_time.clear()
                        logger.info(
                            f"[RECONNECT] Reset timestamps "
                            f"(ticks: {old_tick_count}, bars: {old_bar_count})"
                        )

                        # Clear subscribed_symbols before resubscribing
                        self.subscribed_symbols.clear()

                    # 🔧 FIX A: Force resubscription (CRITICAL - Upstox drops subs silently)
                    logger.info(f"[RECONNECT] Resubscribing to {len(symbols_to_resubscribe)} symbols...")

                    instruments = [
                        {"exchange": EXCHANGE, "symbol": symbol}
                        for symbol in symbols_to_resubscribe
                    ]

                    try:
                        self.client.subscribe_quote(
                            instruments,
                            on_data_received=self._on_quote_update_zerodha
                        )

                        with self.lock:
                            self.subscribed_symbols.update(symbols_to_resubscribe)
                            self.subscription_started_at = datetime.now(IST)

                        logger.info(f"[RECONNECT]  Resubscribed to {len(symbols_to_resubscribe)} symbols")

                    except Exception as e:
                        logger.error(f"[RECONNECT] Resubscription failed: {e}")
                        self.is_connected = False
                        continue

                    # Wait 2 seconds to verify ticks are flowing
                    logger.info("[RECONNECT] Waiting 2s to verify tick flow...")
                    time_module.sleep(2)

                    # Verify ticks are actually arriving
                    with self.lock:
                        tick_count = len(self.last_tick_time)

                    if tick_count == 0:
                        logger.warning(
                            f"[RECONNECT] No ticks received after resubscription (attempt {attempt})"
                        )
                        # Don't fail immediately - maybe ticks are slow
                        if attempt < WEBSOCKET_MAX_RECONNECT_ATTEMPTS:
                            logger.info("[RECONNECT] Retrying with fresh connection...")
                            self.is_connected = False
                            continue
                        else:
                            logger.error("[RECONNECT] No ticks even after all retries")
                            return False
                    else:
                        logger.info(f"[RECONNECT]  Tick flow verified ({tick_count} symbols)")

                    # Backfill missed bars during disconnection
                    logger.info("[RECONNECT] Backfilling missed data...")
                    try:
                        self.backfill_missed_bars()
                        logger.info("[RECONNECT]  Backfill complete")
                    except Exception as e:
                        logger.warning(f"[RECONNECT] Backfill failed (non-critical): {e}")

                    # Reset counters
                    self.reconnect_attempts = 0
                    self.reset_watchdog()

                    # Switch back to Zerodha if we were on Angel One
                    if self.is_failover_active:
                        self._failback_to_zerodha()

                    logger.info("[RECONNECT]  Reconnection complete and operational")
                    return True

                except Exception as e:
                    logger.error(f"[RECONNECT] Attempt {attempt} failed: {e}", exc_info=True)
                    self.reconnect_attempts = attempt
                    self.is_connected = False

            # All reconnection attempts failed
            logger.critical(
                f"[RECONNECT] Failed to reconnect after {WEBSOCKET_MAX_RECONNECT_ATTEMPTS} attempts"
            )
            return False

        finally:
            # Always clear the reconnecting flag
            with self.lock:
                self.is_reconnecting = False
    
    def backfill_missed_bars(self):
        """
        🔴 NEW: Fetch missed 1-min bars during disconnection
        
        Called after successful reconnection to fill data gaps.
        """
        if self.last_disconnect_time is None:
            return
        
        reconnect_time = datetime.now(IST)
        disconnect_duration = (reconnect_time - self.last_disconnect_time).total_seconds()
        
        logger.info(
            f"Backfilling missed bars (disconnected for {disconnect_duration:.0f}s)..."
        )
        
        today = datetime.now(IST).date()
        start_date = today.strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        
        backfilled_count = 0
        failed_count = 0
        
        # Use saved timestamps from before reconnect cleared them
        saved_timestamps = getattr(self, '_saved_bar_timestamps', {})

        for symbol in self.subscribed_symbols:
            try:
                # Get last bar timestamp for this symbol (prefer saved pre-reconnect copy)
                last_bar_time = saved_timestamps.get(symbol) or self.last_bar_timestamp.get(symbol)

                if last_bar_time is None:
                    # No bars yet, fetch from market open
                    logger.debug(f"No previous bars for {symbol}, skipping backfill")
                    continue
                
                # Fetch history from last bar to now
                df = self.client.history(
                    symbol=symbol,
                    exchange=EXCHANGE,
                    interval='1m',
                    start_date=start_date,
                    end_date=end_date
                )
                
                # Handle dictionary response
                if isinstance(df, dict):
                    if df.get('status') == 'error':
                        failed_count += 1
                        continue
                    if not df.get('data'):
                        continue
                    import pandas as pd
                    df = pd.DataFrame(df['data'])

                if df is None or df.empty:
                    continue
                
                # Filter to bars after last_bar_time
                missed_bars = df[df.index > last_bar_time]
                
                if missed_bars.empty:
                    continue
                
                # Add missed bars to history
                with self.lock:
                    # Get current session VWAP cumulative values
                    # 🔧 FIX: If VWAP data missing (after reconnect), recalculate from existing bars
                    if symbol not in self.session_vwap_data:
                        logger.warning(
                            f"[BACKFILL] {symbol}: VWAP data missing - recalculating from "
                            f"{len(self.bars[symbol])} existing bars"
                        )
                        cum_pv = 0.0
                        cum_vol = 0
                        for existing_bar in self.bars[symbol]:
                            typical_price = (existing_bar.high + existing_bar.low + existing_bar.close) / 3
                            cum_pv += typical_price * existing_bar.volume
                            cum_vol += existing_bar.volume
                        self.session_vwap_data[symbol] = {'cum_pv': cum_pv, 'cum_vol': cum_vol}
                        logger.info(
                            f"[BACKFILL] {symbol}: VWAP restored "
                            f"(cum_pv={cum_pv:.2f}, cum_vol={cum_vol})"
                        )
                    else:
                        vwap_data = self.session_vwap_data[symbol]
                        cum_pv = vwap_data['cum_pv']
                        cum_vol = vwap_data['cum_vol']

                    # Build set of existing bar timestamps for dedup
                    existing_timestamps = {b.timestamp for b in self.bars[symbol]}

                    for idx, row in missed_bars.iterrows():
                        # Normalize timestamp to minute boundary for dedup check
                        bar_time = idx
                        if isinstance(bar_time, str):
                            bar_time = datetime.fromisoformat(bar_time)
                        if bar_time.tzinfo is None:
                            bar_time = IST.localize(bar_time)
                        bar_timestamp = bar_time.replace(second=0, microsecond=0)

                        # Dedup: skip if bar already exists for this timestamp
                        if bar_timestamp in existing_timestamps:
                            logger.debug(
                                f"[BACKFILL] Skipping duplicate bar @ "
                                f"{bar_timestamp.strftime('%H:%M')} for {symbol}"
                            )
                            continue

                        bar = BarData(timestamp=bar_timestamp)
                        bar.open = row.get('open', row.get('Open'))
                        bar.high = row.get('high', row.get('High'))
                        bar.low = row.get('low', row.get('Low'))
                        bar.close = row.get('close', row.get('Close'))
                        bar.volume = row.get('volume', row.get('Volume', 0))

                        # Calculate cumulative session VWAP
                        typical_price = (bar.high + bar.low + bar.close) / 3
                        cum_pv += typical_price * bar.volume
                        cum_vol += bar.volume

                        if cum_vol > 0:
                            bar.vwap = cum_pv / cum_vol
                        else:
                            bar.vwap = typical_price

                        bar.tick_count = 1

                        self.bars[symbol].append(bar)
                        existing_timestamps.add(bar_timestamp)  # Track newly added
                        # Store when bar was RECEIVED (for watchdog)
                        self.last_bar_timestamp[symbol] = datetime.now(IST)
                        backfilled_count += 1

                    # Update session VWAP cumulative values
                    self.session_vwap_data[symbol] = {
                        'cum_pv': cum_pv,
                        'cum_vol': cum_vol
                    }

                logger.debug(f"Backfilled {len(missed_bars)} bars for {symbol}")
                
            except Exception as e:
                logger.error(f"Failed to backfill {symbol}: {e}")
                failed_count += 1
        
        logger.info(
            f"[OK] Backfill complete: {backfilled_count} bars added "
            f"({failed_count} symbols failed)"
        )
    
    def prune_bars(self):
        """
        🔴 NEW: Prune old bars to manage memory
        
        Keeps only last MAX_BARS_PER_SYMBOL bars per symbol.
        Called periodically to prevent unbounded memory growth.
        """
        with self.lock:
            pruned_count = 0
            
            for symbol in list(self.bars.keys()):
                bar_count = len(self.bars[symbol])
                
                if bar_count > BAR_PRUNING_THRESHOLD:
                    # Keep only last MAX_BARS_PER_SYMBOL bars
                    removed = bar_count - MAX_BARS_PER_SYMBOL
                    self.bars[symbol] = self.bars[symbol][-MAX_BARS_PER_SYMBOL:]
                    pruned_count += removed
                    
                    logger.debug(
                        f"Pruned {removed} old bars from {symbol} "
                        f"(kept {MAX_BARS_PER_SYMBOL})"
                    )
            
            if pruned_count > 0:
                logger.info(f"[CLEANUP] Memory pruning: removed {pruned_count} old bars")
    
    # -------------------------------------------------------------------------
    # Angel One backup feed methods
    # -------------------------------------------------------------------------

    def connect_angelone_backup(self):
        """
        Connect to Angel One OpenAlgo instance (always-on backup).
        Called once during startup, runs silently in background.
        """
        if not ANGELONE_OPENALGO_API_KEY:
            logger.warning("[BACKUP] ANGELONE_OPENALGO_API_KEY not set - backup feed disabled")
            return

        try:
            self.angelone_client = api(
                api_key=ANGELONE_OPENALGO_API_KEY,
                host=ANGELONE_HOST,
                ws_url=ANGELONE_WS_URL
            )
            connected = self.angelone_client.connect()
            if not connected:
                logger.error(f"[BACKUP] Angel One WebSocket authentication failed (return value: {connected})")
                with self.lock:
                    self.angelone_is_connected = False
                return

            with self.lock:
                self.angelone_is_connected = True
            logger.info(f"[BACKUP] Angel One connected: {ANGELONE_WS_URL}")

        except Exception as e:
            logger.error(f"[BACKUP] Failed to connect Angel One: {e}", exc_info=True)
            with self.lock:
                self.angelone_is_connected = False

    def subscribe_angelone_backup(self, symbols, spot_symbol=None):
        """
        Subscribe Angel One to the same symbols as Zerodha (silent backup).
        Ticks are received but ignored until failover is triggered.
        """
        if not self.angelone_is_connected:
            logger.warning("[BACKUP] Angel One not connected - cannot subscribe")
            return

        all_symbols = list(symbols)
        instruments = [{"exchange": EXCHANGE, "symbol": s} for s in symbols]

        if spot_symbol:
            all_symbols.append(spot_symbol)
            instruments.append({"exchange": "NSE", "symbol": spot_symbol})

        try:
            self.angelone_client.subscribe_quote(
                instruments,
                on_data_received=self._on_quote_update_angelone
            )
            logger.info(f"[BACKUP] Angel One subscribed to {len(all_symbols)} symbols (standby)")
        except Exception as e:
            logger.error(f"[BACKUP] Angel One subscription failed: {e}")
            with self.lock:
                self.angelone_is_connected = False
            # Disconnect the client to avoid leaving an open WebSocket with no subscription
            try:
                self.angelone_client.disconnect()
            except Exception:
                pass

    def _failover_to_angelone(self, reason):
        """
        Switch active data source from Zerodha to Angel One.
        Called when Zerodha ticks stop or WebSocket disconnects.
        """
        if self.is_failover_active:
            return  # Already on Angel One

        if not self.angelone_is_connected:
            logger.error("[FAILOVER] Cannot failover - Angel One not connected")
            if self.telegram:
                self.telegram.send_message(
                    f"[FAILOVER] CRITICAL: Zerodha feed lost but Angel One NOT connected!\n"
                    f"Reason: {reason}\nRunning without backup — check Angel One login!"
                )
            return

        with self.lock:
            self.active_source = 'angelone'
            self.is_failover_active = True
            self.zerodha_continuous_tick_start = None
            # Clear active source tick times so fresh Angel One ticks are counted
            self.last_tick_time.clear()
            self.first_data_received_at = None

        logger.warning(f"[FAILOVER] Switched to Angel One backup feed. Reason: {reason}")
        logger.warning("[FAILOVER] Zerodha reconnect running in background - will auto-switchback when ready")
        if self.telegram:
            self.telegram.send_message(
                f"[FAILOVER] Switched to Angel One backup feed\nReason: {reason}\n"
                "Zerodha reconnect running in background — will auto-switchback when stable."
            )

    def _failback_to_zerodha(self):
        """
        Switch active data source back from Angel One to Zerodha.
        Called when Zerodha reconnects and ticks resume.
        """
        if not self.is_failover_active:
            return  # Already on Zerodha

        with self.lock:
            self.active_source = 'zerodha'
            self.is_failover_active = False
            self.zerodha_continuous_tick_start = None
            # Restore Zerodha tick times as the active source
            self.last_tick_time = dict(self.last_zerodha_tick_time)
            if self.last_tick_time:
                self.first_data_received_at = min(self.last_tick_time.values())

        logger.info("[FAILBACK] Switched back to Zerodha primary feed")
        if self.telegram:
            self.telegram.send_message(
                f"[FAILBACK] Switched back to Zerodha primary feed\n"
                f"Zerodha stable for {FAILOVER_SWITCHBACK_THRESHOLD}s — Angel One back on standby."
            )

    def disconnect(self):
        """Disconnect WebSocket and clean up"""
        # Stop connection monitor first
        if self.monitor_running:
            self.stop_connection_monitor()

        if self.client and self.is_connected:
            try:
                self.last_disconnect_time = datetime.now(IST)
                self.client.disconnect()
                self.is_connected = False
                logger.info("Disconnected from Zerodha OpenAlgo WebSocket")
            except Exception as e:
                logger.error(f"Error disconnecting Zerodha: {e}")

        # Also disconnect Angel One backup
        if self.angelone_client and self.angelone_is_connected:
            try:
                self.angelone_client.disconnect()
                self.angelone_is_connected = False
                logger.info("[BACKUP] Disconnected from Angel One OpenAlgo WebSocket")
            except Exception as e:
                logger.error(f"[BACKUP] Error disconnecting Angel One: {e}")


if __name__ == '__main__':
    # Test the data pipeline
    logging.basicConfig(level=logging.INFO)
    
    pipeline = DataPipeline()
    
    # Connect
    pipeline.connect()
    
    # Example: Subscribe to Dec 26 expiry, ATM 18000
    symbols = pipeline.generate_option_symbols(18000, '26DEC24')
    pipeline.subscribe_options(symbols)
    
    # Monitor for 60 seconds
    logger.info("Monitoring data for 60 seconds...")
    time_module.sleep(60)
    
    # Print health status
    status = pipeline.get_health_status()
    logger.info(f"Health Status: {status}")
    
    # Print sample bars
    for symbol in symbols[:5]:  # First 5 symbols
        bar = pipeline.get_latest_bar(symbol)
        if bar:
            logger.info(f"{symbol}: {bar.to_dict()}")
    
    pipeline.disconnect()
