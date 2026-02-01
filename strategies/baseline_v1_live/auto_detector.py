"""
Automatic ATM and Expiry Detection Module

Uses OpenAlgo APIs to:
- Fetch NIFTY spot price at 9:16 AM IST
- Calculate ATM strike (rounded to nearest 100)
- Find nearest expiry (weekly or monthly, handles holidays)
"""

import logging
import time as time_module
from datetime import datetime, time, timedelta
import pytz
import requests

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class AutoDetector:
    """Automatically detect ATM strike and expiry for NIFTY options"""

    def __init__(self, api_key: str, host: str):
        """Initialize with OpenAlgo credentials"""
        self.api_key = api_key
        self.host = host.rstrip('/')

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

    def fetch_spot_price(self):
        """
        Fetch NIFTY spot price from OpenAlgo quotes API
        Returns: float (e.g., 24248.75)
        """
        url = f"{self.host}/api/v1/quotes"
        payload = {
            "apikey": self.api_key,
            "symbol": "NIFTY",
            "exchange": "NSE_INDEX"
        }

        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("status") == "success":
            ltp = data["data"]["ltp"]
            logger.info(f"[AUTO] NIFTY Spot: {ltp}")
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

        response = requests.post(url, json=payload, timeout=10)
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

    def auto_detect(self):
        """
        Main auto-detection method
        Returns: tuple (atm_strike: int, expiry_date: str)
        """
        try:
            # Step 1: Wait for market open (if before 9:16 AM)
            self.wait_for_market_open(wait_minutes=1)

            # Step 2: Fetch spot price (with retry)
            spot_price = self._api_call_with_retry(self.fetch_spot_price)

            # Step 3: Calculate ATM strike
            atm_strike = self.calculate_atm_strike(spot_price)

            # Step 4: Fetch expiries (with retry)
            expiries = self._api_call_with_retry(self.fetch_expiries)

            # Step 5: Find nearest expiry
            nearest_expiry = self.find_nearest_expiry(expiries)

            # Step 6: Convert to system format
            expiry_date = self.convert_expiry_format(nearest_expiry)

            # Step 7: Validate
            self._validate(atm_strike, expiry_date)

            logger.info(f"[AUTO] Auto-detection complete: ATM={atm_strike}, Expiry={expiry_date}")
            return atm_strike, expiry_date

        except Exception as e:
            logger.error(f"[AUTO] Auto-detection failed: {e}")
            logger.error("[AUTO] Please restart with manual --expiry and --atm flags")
            raise

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
