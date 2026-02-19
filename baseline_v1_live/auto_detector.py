"""
Automatic ATM and Expiry Detection Module

Uses OpenAlgo APIs to:
- Fetch NIFTY spot price at 9:16 AM IST
- Calculate ATM strike (rounded to nearest 100)
- Find nearest expiry (weekly or monthly, handles holidays)

Graceful Degradation:
- If broker not connected, enters waiting mode instead of crashing
- Retries every 30 seconds with exponential backoff (30s -> 60s -> 120s -> 300s cap)
- Sends Telegram alert when broker reconnects
"""

import logging
import time as time_module
from datetime import datetime, time, timedelta
import pytz
import requests
import os

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class AutoDetector:
    """Automatically detect ATM strike and expiry for NIFTY options"""

    def __init__(self, api_key: str, host: str, data_pipeline=None, spot_symbol="Nifty 50", telegram_notifier=None):
        """
        Initialize with OpenAlgo credentials

        Args:
            api_key: OpenAlgo API key
            host: OpenAlgo host URL
            data_pipeline: Optional DataPipeline instance for WebSocket-based spot price
            spot_symbol: NIFTY spot symbol (default: "Nifty 50")
            telegram_notifier: Optional TelegramNotifier instance for alerts
        """
        self.api_key = api_key
        self.host = host.rstrip('/')
        self.data_pipeline = data_pipeline
        self.spot_symbol = spot_symbol
        self.telegram_notifier = telegram_notifier

        # Wait mode configuration
        self.max_wait_retries = 60  # ~30 minutes of retrying (every 30 seconds avg)
        self.periodic_update_interval = 300  # Send Telegram update every 5 minutes

    def wait_for_market_open(self, wait_minutes=1):
        """
        Wait until specified minutes after market open (9:16 AM IST by default)
        If already past target time, proceed immediately
        """
        now = datetime.now(IST)
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        target_time = market_open + timedelta(minutes=wait_minutes)

        if now < target_time:
            wait_seconds = (target_time - now).total_seconds()
            logger.info(f"[AUTO] Waiting {wait_seconds:.0f} seconds until {target_time.strftime('%H:%M:%S')}")
            time_module.sleep(wait_seconds)
            logger.info(f"[AUTO] Target time reached: {target_time.strftime('%H:%M:%S')}")
        else:
            logger.info(f"[AUTO] Already past {target_time.strftime('%H:%M:%S')}, proceeding immediately")

    def fetch_spot_price_from_websocket(self):
        """
        Fetch NIFTY spot price from WebSocket (preferred method)

        Strategy:
        - Use current LTP from WebSocket immediately (pre-market or live)
        - No waiting for 9:16 AM - pre-market spot is accurate enough for ATM calculation
          (NIFTY rarely moves >100 points from pre-market to open)

        Returns: float (e.g., 24248.75) or None if unavailable
        """
        if not self.data_pipeline:
            logger.warning("[AUTO] DataPipeline not available for WebSocket spot price")
            return None

        ltp = self.data_pipeline.get_spot_price(self.spot_symbol)
        if ltp is not None:
            now = datetime.now(IST)
            logger.info(f"[AUTO] NIFTY Spot from WebSocket (LTP at {now.strftime('%H:%M:%S')}): {ltp}")
            return float(ltp)
        else:
            logger.warning("[AUTO] WebSocket LTP not available, will try API fallback")
            return None

    def fetch_spot_price(self):
        """
        Fetch NIFTY spot price from OpenAlgo quotes API (fallback method)
        Returns: float (e.g., 24248.75)
        """
        url = f"{self.host}/api/v1/quotes"
        payload = {
            "apikey": self.api_key,
            "symbol": "NIFTY",
            "exchange": "NSE_INDEX"
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("status") == "success":
            ltp = data["data"]["ltp"]
            logger.info(f"[AUTO] NIFTY Spot from API: {ltp}")
            return float(ltp)
        else:
            raise Exception(f"Quote API failed: {data.get('message', 'Unknown error')}")

    def calculate_atm_strike(self, spot_price):

        """
        Round spot price to nearest 100
        Examples: 24248 -> 24200, 24275 -> 24300
        Returns: int (e.g., 24200)
        """
        atm = round(spot_price / 100) * 100
        logger.info(f"[AUTO] Calculated ATM: {spot_price:.2f} -> {atm}")
        return int(atm)

    def fetch_expiries(self):
        """
        Fetch all NIFTY option expiries from OpenAlgo
        Returns: list of expiry strings (e.g., ["10-JUL-25", "17-JUL-25", ...])
        """
        url = f"{self.host}/api/v1/expiry"
        payload = {
            "apikey": self.api_key,
            "symbol": "NIFTY",
            "exchange": "NFO",
            "instrumenttype": "options"
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("status") == "success":
            expiries = data.get("data", [])
            logger.info(f"[AUTO] Found {len(expiries)} expiries")
            return expiries
        else:
            raise Exception(f"Expiry API failed: {data.get('message', 'Unknown error')}")

    def find_nearest_expiry(self, expiries):
        """
        Find nearest expiry from list of expiries (regardless of day)

        Handles:
        - Weekly expiries (usually Tuesday, but Monday/Wednesday if holiday)
        - Monthly expiries (last Thursday of month)
        - Any other special cases

        Returns: str in "DD-MMM-YY" format (e.g., "28-JAN-26")
        """
        now = datetime.now(IST).date()
        future_expiries = []

        for exp_str in expiries:
            # Parse expiry (handle "DD-MMM-YY" format)
            try:
                exp_date = datetime.strptime(exp_str, "%d-%b-%y")
            except ValueError:
                continue

            # Filter: future dates only
            if exp_date.date() >= now:
                future_expiries.append((exp_date, exp_str))

        if not future_expiries:
            raise Exception("No future expiries found")

        # Sort by date, get nearest
        future_expiries.sort(key=lambda x: x[0])
        nearest_date, nearest_expiry = future_expiries[0]

        logger.info(f"[AUTO] Nearest expiry: {nearest_expiry} ({nearest_date.strftime('%A, %d %B %Y')})")
        return nearest_expiry

    def convert_expiry_format(self, openalgo_expiry):
        """
        Convert OpenAlgo format to system format
        Input: "17-JUL-25" -> Output: "17JUL25"
        """
        system_format = openalgo_expiry.replace("-", "")
        logger.info(f"[AUTO] Converted expiry: {openalgo_expiry} -> {system_format}")
        return system_format

    def _api_call_with_retry(self, func, max_retries=3, delay=5):
        """Wrapper for API calls with retry logic"""
        for attempt in range(1, max_retries + 1):
            try:
                return func()
            except Exception as e:
                logger.warning(f"[AUTO] Attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    logger.info(f"[AUTO] Retrying in {delay} seconds...")
                    time_module.sleep(delay)
                else:
                    logger.error(f"[AUTO] All {max_retries} attempts failed")
                    raise

    def _wait_for_broker_connection(self):
        """
        Graceful degradation: Wait for broker connection instead of crashing

        Features:
        - Retries with exponential backoff (30s -> 60s -> 120s -> 240s -> 300s cap)
        - Max 60 retries (~30 minutes of waiting)
        - Telegram alert on wait mode entry
        - Periodic Telegram updates every 5 minutes
        - Final alert with error if max retries exceeded

        Returns: tuple (atm_strike: int, expiry_date: str) when broker connects
        Raises: Exception if max retries exceeded
        """
        logger.warning("[AUTO] Broker not connected. Entering wait mode...")
        logger.warning("[AUTO] Please log in to Zerodha at https://openalgo.ronniedreams.in")

        # Send initial Telegram alert
        if self.telegram_notifier:
            msg = f"[AUTO] Entering wait mode. Retrying every 30s (max 60 attempts = ~30 min). Please log in to Zerodha."
            self.telegram_notifier.send_message(msg)

        wait_interval = 30  # Start with 30 seconds
        max_wait_interval = 300  # Cap at 5 minutes
        retry_count = 0
        start_time = time_module.time()  # Track actual wall-clock start time
        last_telegram_update = 0  # Track actual elapsed seconds at last Telegram update
        last_error = None

        while True:
            retry_count += 1

            # Check if max retries exceeded
            if retry_count > self.max_wait_retries:
                error_msg = f"[AUTO] Max retries ({self.max_wait_retries}) exceeded after ~30 minutes. Last error: {last_error}"
                logger.error(error_msg)

                # Send final alert via Telegram
                if self.telegram_notifier:
                    telegram_msg = f"[CRITICAL] Auto-detect failed after 30 minutes. Giving up. Error: {str(last_error)[:100]}"
                    self.telegram_notifier.send_message(telegram_msg)

                raise Exception(error_msg)

            logger.info(f"[AUTO] Retry {retry_count}/{self.max_wait_retries}: Retrying in {wait_interval} seconds ({datetime.now(IST).strftime('%H:%M:%S')} IST)...")
            time_module.sleep(wait_interval)

            # Send periodic Telegram update every 5 minutes (using wall-clock time)
            elapsed_seconds = time_module.time() - start_time
            if elapsed_seconds - last_telegram_update >= self.periodic_update_interval:
                elapsed_minutes = int(elapsed_seconds // 60)
                if self.telegram_notifier:
                    msg = f"[AUTO] Still retrying... Attempt {retry_count}/{self.max_wait_retries} ({elapsed_minutes} min elapsed)"
                    self.telegram_notifier.send_message(msg)
                last_telegram_update = elapsed_seconds

            try:
                # Attempt full auto-detection
                spot_price = None
                if self.data_pipeline:
                    spot_price = self.fetch_spot_price_from_websocket()

                if spot_price is None:
                    spot_price = self.fetch_spot_price()

                atm_strike = self.calculate_atm_strike(spot_price)
                expiries = self.fetch_expiries()
                nearest_expiry = self.find_nearest_expiry(expiries)
                expiry_date = self.convert_expiry_format(nearest_expiry)
                self._validate(atm_strike, expiry_date)

                # Success! Broker is connected
                logger.info(f"[AUTO] Broker reconnected on retry {retry_count}! Proceeding with strategy start...")
                logger.info(f"[AUTO] ATM={atm_strike}, Expiry={expiry_date}")

                # Send Telegram alert
                if self.telegram_notifier:
                    msg = f"[AUTO] Broker connected after {retry_count} retries at {datetime.now(IST).strftime('%H:%M:%S')} IST. Strategy starting now!"
                    self.telegram_notifier.send_message(msg)

                return atm_strike, expiry_date

            except Exception as e:
                last_error = e
                logger.warning(f"[AUTO] Retry {retry_count} failed: {e}")
                # Exponential backoff: 30 -> 60 -> 120 -> 240 -> 300 -> 300...
                wait_interval = min(wait_interval * 2, max_wait_interval)
                logger.info(f"[AUTO] Next retry interval: {wait_interval} seconds")

    def auto_detect(self):
        """
        Main auto-detection method with graceful degradation

        Phase 1: Quick retries (3 attempts with 5s delay)
        Phase 2: Graceful wait mode (exponential backoff) if broker not connected

        Returns: tuple (atm_strike: int, expiry_date: str)
        """
        # Phase 1: Quick retries
        logger.info("[AUTO] Starting auto-detection...")
        for attempt in range(1, 4):
            try:
                # Step 1: Fetch spot price
                spot_price = None
                if self.data_pipeline:
                    logger.info("[AUTO] Attempting WebSocket-based spot price detection...")
                    spot_price = self.fetch_spot_price_from_websocket()

                # Fallback to API if WebSocket failed
                if spot_price is None:
                    logger.info("[AUTO] Using API fallback for spot price...")
                    spot_price = self.fetch_spot_price()

                # Step 2: Calculate ATM strike
                atm_strike = self.calculate_atm_strike(spot_price)

                # Step 3: Fetch expiries
                expiries = self.fetch_expiries()

                # Step 4: Find nearest expiry
                nearest_expiry = self.find_nearest_expiry(expiries)

                # Step 5: Convert to system format
                expiry_date = self.convert_expiry_format(nearest_expiry)

                # Step 6: Validate
                self._validate(atm_strike, expiry_date)

                logger.info(f"[AUTO] Auto-detection complete: ATM={atm_strike}, Expiry={expiry_date}")
                return atm_strike, expiry_date

            except Exception as e:
                logger.warning(f"[AUTO] Attempt {attempt}/3 failed: {e}")
                if attempt < 3:
                    logger.info(f"[AUTO] Retrying in 5 seconds...")
                    time_module.sleep(5)
                else:
                    logger.error(f"[AUTO] Quick retries exhausted (3/3)")

        # Phase 2: Graceful wait mode (exponential backoff with 30s initial interval)
        return self._wait_for_broker_connection()

    def _validate(self, atm_strike, expiry_date):
        """Validate auto-detected values"""
        # ATM Strike validation
        if not (15000 <= atm_strike <= 30000):
            raise ValueError(f"ATM strike {atm_strike} out of reasonable range (15000-30000)")

        if atm_strike % 100 != 0:
            raise ValueError(f"ATM strike {atm_strike} not multiple of 100")

        # Expiry format validation
        if not expiry_date or len(expiry_date) not in [7, 8]:  # DDMMMYY or DDDMMMYY
            raise ValueError(f"Invalid expiry format: {expiry_date}")

        logger.info(f"[AUTO] Validation passed: ATM={atm_strike}, Expiry={expiry_date}")
