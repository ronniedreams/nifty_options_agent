"""
Automated Login Handler for Upstox OpenAlgo

Reuses patterns from baseline login_handler.py.
Handles OpenAlgo dashboard auth + Upstox broker TOTP login.

Only use for paper trading (testing phase).
"""

import logging
import time

import pyotp
import requests

logger = logging.getLogger(__name__)


class LoginHandlerV3:
    """Handles automated login to Upstox via OpenAlgo."""

    def __init__(self, openalgo_host: str):
        self.host = openalgo_host.rstrip('/')
        self.session = requests.Session()

    def _get(self, url, **kwargs):
        r = self.session.get(url, **kwargs)
        self._strip_secure_cookies()
        return r

    def _post(self, url, **kwargs):
        r = self.session.post(url, **kwargs)
        self._strip_secure_cookies()
        return r

    def _strip_secure_cookies(self):
        for cookie in self.session.cookies:
            cookie.secure = False

    def _get_csrf_token(self) -> str | None:
        url = f"{self.host}/auth/csrf-token"
        try:
            response = self._get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data.get('csrf_token')
        except Exception as e:
            logger.warning(f"[V3-LOGIN] CSRF token fetch failed: {e}")
        return None

    def _login_openalgo(self, username: str, password: str) -> bool:
        """Authenticate to OpenAlgo dashboard."""
        csrf_token = self._get_csrf_token()
        if not csrf_token:
            logger.error("[V3-LOGIN] Could not get CSRF token")
            return False

        url = f"{self.host}/auth/login"
        data = {'username': username, 'password': password}
        headers = {'X-CSRFToken': csrf_token}

        try:
            response = self._post(url, data=data, headers=headers, timeout=10)
            if response.status_code == 200:
                logger.info("[V3-LOGIN] OpenAlgo dashboard login successful")
                return True
            logger.error(f"[V3-LOGIN] OpenAlgo login failed: {response.status_code}")
        except Exception as e:
            logger.error(f"[V3-LOGIN] OpenAlgo login error: {e}")
        return False

    def auto_login(self, user_id: str, password: str, totp_secret: str,
                   openalgo_username: str = '', openalgo_password: str = '',
                   max_retries: int = 20, retry_delay: int = 5) -> bool:
        """Attempt automated login to Upstox via OpenAlgo.

        Args:
            user_id: Upstox client ID
            password: Upstox password
            totp_secret: Upstox TOTP secret (base32)
            openalgo_username: OpenAlgo dashboard username
            openalgo_password: OpenAlgo dashboard password
            max_retries: Max connection retries
            retry_delay: Seconds between retries
        """
        # Wait for OpenAlgo to be ready
        for attempt in range(1, max_retries + 1):
            try:
                r = self._get(f"{self.host}/", timeout=5)
                if r.status_code == 200:
                    logger.info(f"[V3-LOGIN] Upstox OpenAlgo is ready (attempt {attempt})")
                    break
            except Exception:
                pass
            if attempt < max_retries:
                logger.info(f"[V3-LOGIN] Waiting for Upstox OpenAlgo... ({attempt}/{max_retries})")
                time.sleep(retry_delay)
        else:
            logger.error("[V3-LOGIN] Upstox OpenAlgo not reachable")
            return False

        # Login to OpenAlgo dashboard
        if openalgo_username and openalgo_password:
            if not self._login_openalgo(openalgo_username, openalgo_password):
                return False

        # Generate TOTP
        try:
            totp = pyotp.TOTP(totp_secret)
            totp_code = totp.now()
        except Exception as e:
            logger.error(f"[V3-LOGIN] TOTP generation failed: {e}")
            return False

        # Broker-specific login via OpenAlgo callback
        # Note: Upstox login flow may differ from Zerodha/Angel One.
        # The exact callback endpoint depends on the OpenAlgo Upstox broker plugin.
        # This is a placeholder — update once openalgo-upstox is configured.
        logger.info(f"[V3-LOGIN] Upstox broker login: user_id={user_id}, TOTP generated")
        logger.warning(
            "[V3-LOGIN] Upstox auto-login callback not yet configured. "
            "Manual login required at Upstox OpenAlgo dashboard."
        )

        return True
