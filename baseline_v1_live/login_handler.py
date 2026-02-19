"""
Automated Login Handler for Zerodha, Angel One, and Definedge

Login sequence:
1. Authenticate to OpenAlgo (<username> / <password>)
2. Log in to broker via OpenAlgo (with TOTP)

Generates TOTP codes and attempts automated login via OpenAlgo API.
Only use for paper trading (testing phase).
For live trading, disable and use manual login (more secure).

Supported brokers: Zerodha, Angel One, Definedge
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

    def login_definedge(self, user_id: str, password: str, totp_secret: str,
                         api_key: str = None, api_secret: str = None) -> bool:
        """
        Attempt automated Definedge broker login via OpenAlgo's web flow.

        This uses OpenAlgo's built-in Definedge login flow:
        1. Login to OpenAlgo dashboard to establish session
        2. GET /definedge/callback to trigger OTP (stores otp_token in OpenAlgo session)
        3. POST /definedge/callback with TOTP code to complete authentication

        OpenAlgo then stores the auth tokens in its database, which the WebSocket
        adapter can access for authentication.

        Prerequisites:
        1. Enable External TOTP in Definedge MyAccount → Security → 2FA
        2. Get the TOTP secret key when setting up authenticator app
        3. API key and secret configured in OpenAlgo's .env file

        Args:
            user_id: Definedge user ID (for logging)
            password: Definedge password (not used - TOTP-only flow)
            totp_secret: TOTP secret for 2FA (from Definedge MyAccount setup)
            api_key: Not used (OpenAlgo reads from its own .env)
            api_secret: Not used (OpenAlgo reads from its own .env)

        Returns:
            True if login successful, False otherwise
        """
        import os

        # Step 1: Login to OpenAlgo dashboard first to get session cookie
        try:
            logger.info("[LOGIN] Definedge Step 1: Logging into OpenAlgo dashboard...")
            openalgo_username = os.getenv('OPENALGO_USERNAME', 'admin')
            openalgo_password = os.getenv('OPENALGO_PASSWORD', '')

            if not openalgo_password:
                logger.error("[LOGIN] OPENALGO_PASSWORD not set in environment")
                return False

            # Login to OpenAlgo to get session
            login_url = f"{self.openalgo_host}/auth/login"
            login_data = {
                "username": openalgo_username,
                "password": openalgo_password
            }

            # Use a fresh session for OpenAlgo
            openalgo_session = requests.Session()
            login_response = openalgo_session.post(login_url, data=login_data, timeout=15, allow_redirects=True)

            # Check if we got redirected to dashboard (successful login)
            if login_response.status_code != 200:
                logger.error(f"[LOGIN] OpenAlgo dashboard login failed: HTTP {login_response.status_code}")
                return False

            # Check if already logged in or login succeeded
            if 'dashboard' in login_response.url or 'broker' in login_response.url:
                logger.info("[LOGIN] OpenAlgo dashboard login successful")
            else:
                logger.warning(f"[LOGIN] OpenAlgo login response URL: {login_response.url}")

        except Exception as e:
            logger.error(f"[LOGIN] OpenAlgo dashboard login exception: {e}")
            return False

        # Step 2: Trigger OTP via GET (OpenAlgo calls Definedge login_step1 and stores otp_token)
        try:
            logger.info("[LOGIN] Definedge Step 2: Triggering OTP via OpenAlgo...")
            callback_url = f"{self.openalgo_host}/definedge/callback"
            get_response = openalgo_session.get(callback_url, timeout=15)

            if get_response.status_code != 200:
                logger.error(f"[LOGIN] Definedge OTP trigger failed: HTTP {get_response.status_code}")
                return False

            # Check if OTP was sent successfully (page should contain OTP form)
            if 'otp' not in get_response.text.lower():
                logger.warning("[LOGIN] OTP form not found in response - may already be logged in")
                # If already logged in, this is success
                if 'dashboard' in get_response.url:
                    logger.info("[LOGIN] Already logged in to Definedge")
                    return True

            logger.info("[LOGIN] OTP triggered, waiting for TOTP generation...")

        except Exception as e:
            logger.error(f"[LOGIN] Definedge OTP trigger exception: {e}")
            return False

        # Step 3: Wait a moment and generate fresh TOTP
        # TOTP codes are valid for 30 seconds, so we want a fresh one
        time.sleep(1)  # Small delay to ensure we're not at the edge of a TOTP window

        totp_code = self.generate_totp(totp_secret)
        if not totp_code:
            logger.error("[LOGIN] Failed to generate TOTP code for Definedge")
            return False

        logger.info(f"[LOGIN] Generated TOTP code: {totp_code[:2]}****")

        # Step 4: Submit TOTP code via POST
        try:
            logger.info("[LOGIN] Definedge Step 3: Submitting TOTP to OpenAlgo...")
            post_data = {"otp": totp_code}
            post_response = openalgo_session.post(callback_url, data=post_data, timeout=15, allow_redirects=True)

            if post_response.status_code != 200:
                logger.error(f"[LOGIN] Definedge TOTP submission failed: HTTP {post_response.status_code}")
                return False

            # Check if we got redirected to dashboard (successful login)
            if 'dashboard' in post_response.url:
                logger.info("[LOGIN] Definedge authentication successful! Redirected to dashboard.")
                return True

            # Check for error messages in response
            response_text_lower = post_response.text.lower()
            if 'error' in response_text_lower or 'invalid' in response_text_lower or 'failed' in response_text_lower:
                # Try to extract error message
                if 'invalid otp' in response_text_lower:
                    logger.error("[LOGIN] Definedge TOTP invalid - check TOTP secret")
                elif 'session expired' in response_text_lower:
                    logger.error("[LOGIN] Definedge session expired - try again")
                else:
                    logger.error("[LOGIN] Definedge authentication failed (see OpenAlgo logs for details)")
                return False

            # If no error but not dashboard, assume success
            logger.info("[LOGIN] Definedge login completed (checking status...)")

            # Verify by checking if we can access a protected page
            try:
                verify_response = openalgo_session.get(f"{self.openalgo_host}/dashboard", timeout=10)
                if 'dashboard' in verify_response.url.lower() or verify_response.status_code == 200:
                    logger.info("[LOGIN] Definedge authentication verified - dashboard accessible")
                    return True
            except Exception:
                pass

            return True

        except Exception as e:
            logger.error(f"[LOGIN] Definedge TOTP submission exception: {e}")
            return False

    def auto_login_definedge(self, openalgo_username: str, openalgo_password: str,
                              definedge_user_id: str, definedge_password: str,
                              definedge_totp_secret: str) -> bool:
        """
        Perform automated login sequence for Definedge.

        This calls Definedge API directly (bypassing OpenAlgo web flow):
        1. login_step1: Get OTP token
        2. Generate TOTP code
        3. login_step2: Verify TOTP to get session

        Args:
            openalgo_username: OpenAlgo username (not used for direct API)
            openalgo_password: OpenAlgo password (not used for direct API)
            definedge_user_id: Definedge user ID
            definedge_password: Definedge password
            definedge_totp_secret: Definedge TOTP secret

        Returns:
            True if login successful, False otherwise
        """
        logger.info("[LOGIN] Starting Definedge automated login sequence (direct API)...")

        # Definedge broker login via direct API
        definedge_ok = self.login_definedge(
            definedge_user_id, definedge_password, definedge_totp_secret
        )

        # Send Telegram notification
        try:
            from .telegram_notifier import get_notifier
            notifier = get_notifier()
            if notifier:
                if definedge_ok:
                    notifier.send_message("[LOGIN] Definedge auto-login successful")
                else:
                    notifier.send_message("[LOGIN] Definedge auto-login FAILED — manual login required")
        except Exception as e:
            logger.warning(f"[LOGIN] Could not send Telegram notification: {e}")

        if definedge_ok:
            logger.info("[LOGIN] Definedge login successful")
            return True
        else:
            logger.error("[LOGIN] Definedge broker login failed")
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
