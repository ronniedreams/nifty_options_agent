"""
Automated Login Handler for Zerodha and Angel One

Login sequence:
1. Authenticate to OpenAlgo (<username> / <password>)
2. Log in to Zerodha via OpenAlgo (with TOTP)
3. Log in to Angel One via OpenAlgo (with TOTP)

Generates TOTP codes and attempts automated login via OpenAlgo API.
Only use for paper trading (testing phase).
For live trading, disable and use manual login (more secure).
"""

import logging
import time
import requests
import pyotp
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class LoginHandler:
    """Handles automated login to OpenAlgo and brokers"""

    def __init__(self, openalgo_host: str, openalgo_api_key: str = ''):
        """
        Initialize login handler

        Args:
            openalgo_host: OpenAlgo API host URL (e.g., http://openalgo:5000)
            openalgo_api_key: OpenAlgo API key (optional for some endpoints)
        """
        self.openalgo_host = openalgo_host.rstrip('/')
        self.openalgo_api_key = openalgo_api_key
        self.session = requests.Session()

    def login_to_openalgo(self, openalgo_username: str, openalgo_password: str) -> bool:
        """
        Authenticate to OpenAlgo first (prerequisite for broker login)

        Args:
            openalgo_username: OpenAlgo username
            openalgo_password: OpenAlgo password

        Returns:
            True if OpenAlgo login successful, False otherwise
        """
        url = f"{self.openalgo_host}/api/v1/login"
        payload = {
            "username": openalgo_username,
            "password": openalgo_password,
        }

        max_retries = 20  # 20 x 5s = 100s max wait (covers EC2 cold boot)
        retry_delay = 5  # seconds between retries

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[LOGIN] Authenticating to OpenAlgo as {openalgo_username} (attempt {attempt}/{max_retries})...")
                response = self.session.post(url, json=payload, timeout=10)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        logger.info("[LOGIN] OpenAlgo authentication successful")
                        return True
                    else:
                        logger.error(f"[LOGIN] OpenAlgo authentication failed: {data.get('message', 'Unknown error')}")
                        return False  # Auth failure — no point retrying
                else:
                    logger.error(f"[LOGIN] OpenAlgo API error: {response.status_code} - {response.text}")
                    return False  # Non-connection error — no point retrying

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt < max_retries:
                    logger.warning(f"[LOGIN] OpenAlgo not ready yet, waiting {retry_delay}s... ({attempt}/{max_retries}): {type(e).__name__}")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"[LOGIN] OpenAlgo still not reachable after {max_retries} attempts: {e}")
                    return False
            except Exception as e:
                logger.error(f"[LOGIN] OpenAlgo authentication exception: {e}")
                return False

        return False

    def generate_totp(self, totp_secret: str) -> str:
        """
        Generate current TOTP code from secret

        Args:
            totp_secret: Base32-encoded TOTP secret

        Returns:
            6-digit TOTP code as string
        """
        if not totp_secret:
            return None

        try:
            totp = pyotp.TOTP(totp_secret)
            code = totp.now()
            return code
        except Exception as e:
            logger.error(f"[LOGIN] Failed to generate TOTP: {e}")
            return None

    def login_zerodha(self, user_id: str, password: str, totp_secret: str) -> bool:
        """
        Attempt automated Zerodha broker login via OpenAlgo
        (Requires prior OpenAlgo authentication)

        Args:
            user_id: Zerodha user ID
            password: Zerodha password
            totp_secret: TOTP secret for 2FA

        Returns:
            True if login successful, False otherwise
        """
        totp_code = self.generate_totp(totp_secret)
        if not totp_code:
            logger.error("[LOGIN] Failed to generate TOTP code for Zerodha")
            return False

        url = f"{self.openalgo_host}/api/v1/brokerlogin"
        payload = {
            "broker": "zerodha",
            "user_id": user_id,
            "password": password,
            "twofa": totp_code,
        }

        try:
            logger.info(f"[LOGIN] Attempting Zerodha broker login for {user_id}...")
            response = self.session.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    logger.info("[LOGIN] Zerodha broker login successful")
                    return True
                else:
                    logger.error(f"[LOGIN] Zerodha broker login failed: {data.get('message', 'Unknown error')}")
                    return False
            else:
                logger.error(f"[LOGIN] Zerodha broker login API error: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"[LOGIN] Zerodha broker login exception: {e}")
            return False

    def login_angelone(self, user_id: str, password: str, totp_secret: str, host: str) -> bool:
        """
        Attempt automated Angel One broker login via OpenAlgo
        (Requires prior OpenAlgo authentication)

        Args:
            user_id: Angel One user ID
            password: Angel One password
            totp_secret: TOTP secret for 2FA
            host: Angel One OpenAlgo host (usually http://127.0.0.1:5001 or Docker service name)

        Returns:
            True if login successful, False otherwise
        """
        totp_code = self.generate_totp(totp_secret)
        if not totp_code:
            logger.error("[LOGIN] Failed to generate TOTP code for Angel One")
            return False

        angelone_host = host.rstrip('/')
        url = f"{angelone_host}/api/v1/brokerlogin"
        payload = {
            "broker": "angelone",
            "user_id": user_id,
            "password": password,
            "twofa": totp_code,
        }

        try:
            logger.info(f"[LOGIN] Attempting Angel One broker login for {user_id}...")
            response = self.session.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    logger.info("[LOGIN] Angel One broker login successful")
                    return True
                else:
                    logger.error(f"[LOGIN] Angel One broker login failed: {data.get('message', 'Unknown error')}")
                    return False
            else:
                logger.error(f"[LOGIN] Angel One broker login API error: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"[LOGIN] Angel One broker login exception: {e}")
            return False

    def auto_login_all(self, openalgo_username: str, openalgo_password: str,
                       zerodha_user_id: str, zerodha_password: str, zerodha_totp_secret: str,
                       angelone_user_id: str, angelone_password: str, angelone_totp_secret: str,
                       angelone_host: str) -> bool:
        """
        Perform complete automated login sequence:
        1. OpenAlgo authentication
        2. Zerodha broker login
        3. Angel One broker login

        Args:
            openalgo_username: OpenAlgo username
            openalgo_password: OpenAlgo password
            zerodha_user_id: Zerodha user ID
            zerodha_password: Zerodha password
            zerodha_totp_secret: Zerodha TOTP secret
            angelone_user_id: Angel One user ID
            angelone_password: Angel One password
            angelone_totp_secret: Angel One TOTP secret
            angelone_host: Angel One OpenAlgo host

        Returns:
            True if all logins successful, False if any fails
        """
        logger.info("[LOGIN] Starting complete automated login sequence...")

        # Step 1: Authenticate to OpenAlgo first
        openalgo_ok = self.login_to_openalgo(openalgo_username, openalgo_password)
        if not openalgo_ok:
            logger.error("[LOGIN] OpenAlgo authentication failed, cannot proceed with broker logins")
            try:
                from .telegram_notifier import get_notifier
                notifier = get_notifier()
                if notifier:
                    notifier.send_message("[LOGIN] OpenAlgo auth FAILED — Zerodha and Angel One logins skipped. Manual login required.")
            except Exception as e:
                logger.warning(f"[LOGIN] Could not send Telegram notification: {e}")
            return False

        # Step 2: Try Zerodha broker login
        zerodha_ok = self.login_zerodha(zerodha_user_id, zerodha_password, zerodha_totp_secret)

        # Step 3: Try Angel One broker login
        angelone_ok = self.login_angelone(angelone_user_id, angelone_password, angelone_totp_secret, angelone_host)

        # Send Telegram notifications for login results
        try:
            from .telegram_notifier import get_notifier
            notifier = get_notifier()
            if notifier:
                if zerodha_ok:
                    notifier.send_message("[LOGIN] Zerodha login successful")
                else:
                    notifier.send_message("[LOGIN] Zerodha login FAILED — manual login required at openalgo.ronniedreams.in")
                if angelone_ok:
                    notifier.send_message("[LOGIN] Angel One login successful")
                else:
                    notifier.send_message("[LOGIN] Angel One login FAILED — check Angel One credentials/TOTP")
        except Exception as e:
            logger.warning(f"[LOGIN] Could not send Telegram notification: {e}")

        if zerodha_ok and angelone_ok:
            logger.info("[LOGIN] All logins successful (OpenAlgo + Zerodha + Angel One)")
            return True
        else:
            if not zerodha_ok:
                logger.error("[LOGIN] Zerodha broker login failed")
            if not angelone_ok:
                logger.error("[LOGIN] Angel One broker login failed")
            return False
