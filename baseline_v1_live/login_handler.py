"""
Automated Login Handler for Zerodha and Angel One

Login sequence (OpenAlgo v2):
1. Authenticate to OpenAlgo (<username> / <password>) via /auth/csrf-token + /auth/login (form data)
2. Log in to Zerodha via direct Kite API (TOTP) + pass request_token to /zerodha/callback
3. Log in to Angel One via /angel/callback (form data, CSRF-exempt)

OpenAlgo v2 changes vs v1:
  - Auth endpoint: /api/v1/login (JSON) → /auth/csrf-token + /auth/login (form data + X-CSRFToken)
  - Zerodha broker login: /api/v1/brokerlogin → direct Kite TOTP API + /zerodha/callback
  - Angel One broker login: /api/v1/brokerlogin → /angel/callback (form data, CSRF-exempt)

Only use for paper trading (testing phase).
For live trading, disable and use manual login (more secure).
"""

import logging
import time
import urllib.parse

import pyotp
import requests

logger = logging.getLogger(__name__)


class LoginHandler:
    """Handles automated login to OpenAlgo and brokers (OpenAlgo v2 compatible)"""

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

    def _get(self, url, **kwargs):
        """GET with Secure cookie workaround for Docker HTTP connections."""
        r = self.session.get(url, **kwargs)
        self._strip_secure_cookies()
        return r

    def _post(self, url, **kwargs):
        """POST with Secure cookie workaround for Docker HTTP connections."""
        r = self.session.post(url, **kwargs)
        self._strip_secure_cookies()
        return r

    def _strip_secure_cookies(self):
        """Strip Secure flag from all session cookies.

        OpenAlgo v2 sets USE_HTTPS=True, giving all cookies Secure=True.
        Python's http.cookiejar refuses to send Secure cookies over plain HTTP
        (http://openalgo:5000 inside Docker). Stripping Secure=False after
        each response ensures cookies are included in subsequent HTTP requests.
        """
        for cookie in self.session.cookies:
            cookie.secure = False

    def _get_csrf_token(self, host: str) -> str | None:
        """
        Fetch a CSRF token from an OpenAlgo v2 instance.
        Also sets the session cookie needed for subsequent requests.

        Args:
            host: OpenAlgo host URL (e.g., http://openalgo:5000)

        Returns:
            CSRF token string, or None on failure
        """
        url = f"{host.rstrip('/')}/auth/csrf-token"
        try:
            response = self._get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                token = data.get("csrf_token")
                if token:
                    return token
                logger.error(f"[LOGIN] CSRF token response missing csrf_token field: {data}")
            else:
                logger.error(f"[LOGIN] CSRF token fetch failed: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"[LOGIN] CSRF token fetch exception: {e}")
        return None

    def login_to_openalgo(self, openalgo_username: str, openalgo_password: str,
                          host: str = None) -> bool:
        """
        Authenticate to an OpenAlgo v2 instance.

        OpenAlgo v2 requires:
          1. GET /auth/csrf-token → CSRF token + session cookie
          2. POST /auth/login with form data + X-CSRFToken header

        Args:
            openalgo_username: OpenAlgo dashboard username
            openalgo_password: OpenAlgo dashboard password
            host: Override host URL (defaults to self.openalgo_host)

        Returns:
            True if authentication successful, False otherwise
        """
        host = (host or self.openalgo_host).rstrip('/')
        login_url = f"{host}/auth/login"

        max_retries = 20  # 20 x 5s = 100s max wait (covers EC2 cold boot)
        retry_delay = 5

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[LOGIN] Authenticating to OpenAlgo as {openalgo_username} "
                            f"(attempt {attempt}/{max_retries})...")

                # Step 1: Get CSRF token (also initialises the session cookie)
                csrf_token = self._get_csrf_token(host)
                if not csrf_token:
                    raise requests.exceptions.ConnectionError("Could not obtain CSRF token")

                # Step 2: POST with form data + CSRF header
                headers = {"X-CSRFToken": csrf_token}
                payload = {"username": openalgo_username, "password": openalgo_password}
                response = self._post(
                    login_url, data=payload, headers=headers, timeout=10
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        logger.info("[LOGIN] OpenAlgo authentication successful")
                        return True
                    else:
                        logger.error(
                            f"[LOGIN] OpenAlgo authentication failed: "
                            f"{data.get('message', 'Unknown error')}"
                        )
                        return False  # Bad credentials — no point retrying
                elif response.status_code == 401:
                    logger.error(
                        f"[LOGIN] OpenAlgo authentication failed (401): {response.text[:200]}"
                    )
                    return False
                else:
                    logger.error(
                        f"[LOGIN] OpenAlgo API error: HTTP {response.status_code} - "
                        f"{response.text[:200]}"
                    )
                    return False

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt < max_retries:
                    logger.warning(
                        f"[LOGIN] OpenAlgo not ready yet, waiting {retry_delay}s... "
                        f"({attempt}/{max_retries}): {type(e).__name__}"
                    )
                    time.sleep(retry_delay)
                else:
                    logger.error(
                        f"[LOGIN] OpenAlgo still not reachable after {max_retries} attempts: {e}"
                    )
                    return False
            except Exception as e:
                logger.error(f"[LOGIN] OpenAlgo authentication exception: {e}")
                return False

        return False

    def generate_totp(self, totp_secret: str) -> str:
        """
        Generate current TOTP code from secret.

        Args:
            totp_secret: Base32-encoded TOTP secret

        Returns:
            6-digit TOTP code as string, or None on failure
        """
        if not totp_secret:
            return None
        try:
            totp = pyotp.TOTP(totp_secret)
            return totp.now()
        except Exception as e:
            logger.error(f"[LOGIN] Failed to generate TOTP: {e}")
            return None

    def login_zerodha(self, user_id: str, password: str, totp_secret: str,
                      broker_api_key: str = None) -> bool:
        """
        Automated Zerodha login via direct Kite TOTP API.

        OpenAlgo v2 uses Kite OAuth — this method:
          1. Logs into Kite directly (user_id, password, TOTP)
          2. Intercepts the request_token from the OAuth redirect
          3. Passes request_token to OpenAlgo's /zerodha/callback

        Requires prior login_to_openalgo() call (session cookie must be set).

        Args:
            user_id: Zerodha user ID
            password: Zerodha password
            totp_secret: TOTP secret for 2FA
            broker_api_key: Zerodha API key (BROKER_API_KEY in OpenAlgo .env).
                            Fetched from OpenAlgo's /auth/broker-config if omitted.

        Returns:
            True if login successful, False otherwise
        """
        # Resolve broker_api_key
        if not broker_api_key:
            broker_api_key = self._get_zerodha_api_key()
        if not broker_api_key:
            logger.error("[LOGIN] Zerodha API key not available — cannot proceed")
            return False

        kite_session = requests.Session()
        kite_session.headers.update({"X-Kite-Version": "3"})

        try:
            # Step 1: Kite login (user_id + password)
            logger.info(f"[LOGIN] Zerodha Kite login step 1 for {user_id}...")
            r = kite_session.post(
                "https://kite.zerodha.com/api/login",
                data={"user_id": user_id, "password": password},
                timeout=15,
            )
            if r.status_code != 200:
                logger.error(
                    f"[LOGIN] Zerodha Kite login step 1 failed: HTTP {r.status_code} - "
                    f"{r.text[:200]}"
                )
                return False
            login_data = r.json()
            if login_data.get("status") != "success":
                logger.error(
                    f"[LOGIN] Zerodha Kite login step 1 error: "
                    f"{login_data.get('message', login_data)}"
                )
                return False
            request_id = login_data["data"]["request_id"]
            logger.info(f"[LOGIN] Zerodha Kite login step 1 OK, request_id={request_id}")

            # Step 2: TOTP verification
            totp_code = self.generate_totp(totp_secret)
            if not totp_code:
                logger.error("[LOGIN] Failed to generate TOTP for Zerodha")
                return False

            logger.info("[LOGIN] Zerodha Kite login step 2 (TOTP)...")
            r = kite_session.post(
                "https://kite.zerodha.com/api/twofa",
                data={
                    "user_id": user_id,
                    "request_id": request_id,
                    "twofa_value": totp_code,
                    "twofa_type": "totp",
                    "skip_twofa": False,
                },
                timeout=15,
                allow_redirects=False,  # Don't follow — we need the Location header
            )

            # Kite responds with 200 JSON on success, then the OAuth redirect happens via
            # the browser navigating to the connect/login URL. We capture it differently.
            if r.status_code == 200:
                twofa_data = r.json()
                if twofa_data.get("status") != "success":
                    logger.error(
                        f"[LOGIN] Zerodha TOTP verification failed: "
                        f"{twofa_data.get('message', twofa_data)}"
                    )
                    return False
                logger.info("[LOGIN] Zerodha TOTP verified successfully")
            elif r.status_code in (302, 303):
                # Some Kite versions redirect after twofa
                location = r.headers.get("Location", "")
                logger.info(f"[LOGIN] Zerodha TOTP redirect: {location}")
            else:
                logger.error(
                    f"[LOGIN] Zerodha TOTP step failed: HTTP {r.status_code} - {r.text[:200]}"
                )
                return False

            # Step 3: OAuth redirect — GET the Kite connect login URL (already authenticated)
            connect_url = (
                f"https://kite.zerodha.com/connect/login"
                f"?api_key={broker_api_key}&v=3"
            )
            logger.info("[LOGIN] Zerodha OAuth redirect step (connect/login)...")
            r = kite_session.get(connect_url, timeout=15, allow_redirects=False)

            request_token = self._extract_request_token(r)
            if not request_token:
                # Follow one more redirect if needed
                if r.status_code in (301, 302, 303) and "Location" in r.headers:
                    r2 = kite_session.get(
                        r.headers["Location"], timeout=15, allow_redirects=False
                    )
                    request_token = self._extract_request_token(r2)

            if not request_token:
                logger.error(
                    f"[LOGIN] Could not extract request_token from Kite redirect. "
                    f"Status={r.status_code}, Location={r.headers.get('Location', 'N/A')}"
                )
                return False

            logger.info(f"[LOGIN] Got Zerodha request_token (first 8 chars): "
                        f"{request_token[:8]}...")

            # Step 4: Pass request_token to OpenAlgo callback (uses existing session cookie)
            callback_url = f"{self.openalgo_host}/zerodha/callback"
            logger.info("[LOGIN] Passing request_token to OpenAlgo /zerodha/callback...")
            r = self._get(
                callback_url,
                params={"request_token": request_token, "action": "login", "status": "success"},
                timeout=15,
                allow_redirects=True,
            )
            if r.status_code == 200:
                url_lower = r.url.lower()
                # Check for failure — redirect to login page means auth failed
                if "login" in url_lower and "dashboard" not in url_lower:
                    logger.error(
                        f"[LOGIN] Zerodha callback failed — redirected to login page: {r.url}"
                    )
                    return False
                if "dashboard" in url_lower:
                    logger.info("[LOGIN] Zerodha broker login successful via OpenAlgo callback")
                    return True
                # Fallback — try to parse JSON response
                try:
                    data = r.json()
                    if data.get("status") == "success":
                        logger.info("[LOGIN] Zerodha broker login successful")
                        return True
                    else:
                        logger.error(f"[LOGIN] Zerodha callback error: {data}")
                        return False
                except Exception:
                    logger.error(
                        f"[LOGIN] Zerodha callback unexpected response: url={r.url}"
                    )
                    return False
            else:
                logger.error(
                    f"[LOGIN] OpenAlgo /zerodha/callback failed: HTTP {r.status_code}"
                )
                return False

        except Exception as e:
            logger.error(f"[LOGIN] Zerodha broker login exception: {e}")
            return False

    def _extract_request_token(self, response: requests.Response) -> str | None:
        """
        Parse request_token from a Kite OAuth redirect response.

        Checks both Location header and final URL for ?request_token=XXX.
        """
        for url in [response.headers.get("Location", ""), response.url]:
            if not url:
                continue
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            token = params.get("request_token") or params.get("request-token")
            if token:
                return token[0]
        return None

    def _get_zerodha_api_key(self, max_retries: int = 3) -> str | None:
        """
        Fetch the Zerodha broker API key from OpenAlgo's /auth/broker-config.
        Requires an authenticated session. Retries on timeout.
        """
        url = f"{self.openalgo_host}/auth/broker-config"
        for attempt in range(1, max_retries + 1):
            try:
                r = self._get(url, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    api_key = data.get("api_key") or data.get("broker_api_key")
                    if api_key:
                        logger.info("[LOGIN] Retrieved Zerodha API key from OpenAlgo broker-config")
                        return api_key
                logger.error(
                    f"[LOGIN] Could not get Zerodha API key from broker-config: "
                    f"HTTP {r.status_code} {r.text[:200]}"
                )
                return None  # Non-timeout error, don't retry
            except Exception as e:
                logger.warning(
                    f"[LOGIN] broker-config fetch failed (attempt {attempt}/{max_retries}): {e}"
                )
                if attempt < max_retries:
                    import time as _t
                    _t.sleep(5)
        logger.error("[LOGIN] Zerodha API key not available after retries")
        return None

    def login_angelone(self, user_id: str, password: str, totp_secret: str,
                       host: str = None, openalgo_username: str = '',
                       openalgo_password: str = '') -> bool:
        """
        Automated Angel One login via OpenAlgo v2 /angel/callback endpoint.

        OpenAlgo v2 flow:
          1. Authenticate to the Angel One OpenAlgo instance (/auth/login, CSRF-protected)
          2. POST /angel/callback with form data (userid, pin, totp) — CSRF-exempt

        Args:
            user_id: Angel One user ID / client code
            password: Angel One password / PIN
            totp_secret: TOTP secret for 2FA
            host: Angel One OpenAlgo host (e.g., http://openalgo_angelone:5000)
            openalgo_username: OpenAlgo username for the Angel One instance
            openalgo_password: OpenAlgo password for the Angel One instance

        Returns:
            True if login successful, False otherwise
        """
        host = (host or self.openalgo_host).rstrip('/')

        # Use a separate session for Angel One's OpenAlgo instance so it doesn't
        # interfere with the Zerodha OpenAlgo session
        angelone_handler = LoginHandler(host)
        # angelone_handler already has a _make_session() session with the Secure-strip hook

        # Step 1: Authenticate to Angel One's OpenAlgo instance
        if openalgo_username and openalgo_password:
            logger.info(f"[LOGIN] Authenticating to Angel One OpenAlgo at {host}...")
            auth_ok = angelone_handler.login_to_openalgo(openalgo_username, openalgo_password, host)
            if not auth_ok:
                logger.error("[LOGIN] Angel One OpenAlgo authentication failed")
                return False
        else:
            logger.warning(
                "[LOGIN] No OpenAlgo credentials provided for Angel One instance — "
                "proceeding without auth (may fail)"
            )

        # Step 2: TOTP generation
        totp_code = self.generate_totp(totp_secret)
        if not totp_code:
            logger.error("[LOGIN] Failed to generate TOTP code for Angel One")
            return False

        # Step 3: POST to /angel/callback (CSRF-exempt in OpenAlgo v2)
        callback_url = f"{host}/angel/callback"
        payload = {
            "userid": user_id,
            "clientid": user_id,  # Some OpenAlgo versions use clientid
            "pin": password,
            "totp": totp_code,
        }

        try:
            logger.info(f"[LOGIN] Attempting Angel One broker login for {user_id}...")
            response = angelone_handler._post(
                callback_url, data=payload, timeout=15, allow_redirects=True
            )

            if response.status_code == 200:
                # Check for failure indicators first — OpenAlgo redirects to
                # /auth/broker-login or /login on auth failure, which falsely
                # matches a naive "broker" in URL check.
                url_lower = response.url.lower()
                if "login" in url_lower and "dashboard" not in url_lower:
                    logger.error(
                        f"[LOGIN] Angel One login failed — redirected to login page: "
                        f"{response.url}"
                    )
                    return False

                if "dashboard" in url_lower:
                    logger.info("[LOGIN] Angel One broker login successful via OpenAlgo callback")
                    return True

                # Check response body for success indicators
                try:
                    data = response.json()
                    if data.get("status") == "success":
                        logger.info("[LOGIN] Angel One broker login successful")
                        return True
                    else:
                        logger.error(
                            f"[LOGIN] Angel One login failed: {data.get('message', data)}"
                        )
                        return False
                except Exception:
                    # HTML response — if we got here without dashboard in URL,
                    # it's likely a failure page
                    logger.error(
                        f"[LOGIN] Angel One login — unexpected response page: {response.url}"
                    )
                    return False
            else:
                logger.error(
                    f"[LOGIN] Angel One /angel/callback HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
                return False

        except Exception as e:
            logger.error(f"[LOGIN] Angel One broker login exception: {e}")
            return False

    def auto_login_all(self, openalgo_username: str, openalgo_password: str,
                       zerodha_user_id: str, zerodha_password: str, zerodha_totp_secret: str,
                       angelone_user_id: str, angelone_password: str, angelone_totp_secret: str,
                       angelone_host: str, zerodha_broker_api_key: str = '') -> bool:
        """
        Perform complete automated login sequence (OpenAlgo v2):
          1. Authenticate to Zerodha OpenAlgo
          2. Zerodha broker login (Kite TOTP → request_token → /zerodha/callback)
          3. Angel One broker login (/angel/callback)

        Args:
            openalgo_username: OpenAlgo username (same for both instances)
            openalgo_password: OpenAlgo password (same for both instances)
            zerodha_user_id: Zerodha user ID
            zerodha_password: Zerodha password
            zerodha_totp_secret: Zerodha TOTP secret (base32)
            angelone_user_id: Angel One user ID / client code
            angelone_password: Angel One password / PIN
            angelone_totp_secret: Angel One TOTP secret (base32)
            angelone_host: Angel One OpenAlgo host URL
            zerodha_broker_api_key: Zerodha Kite API key (optional; fetched from OpenAlgo if empty)

        Returns:
            True if all logins successful, False if any fails
        """
        logger.info("[LOGIN] Starting complete automated login sequence...")

        # Step 1: Authenticate to Zerodha OpenAlgo (sets session cookie)
        openalgo_ok = self.login_to_openalgo(openalgo_username, openalgo_password)
        if not openalgo_ok:
            logger.error("[LOGIN] OpenAlgo authentication failed, cannot proceed with broker logins")
            self._send_telegram(
                "[LOGIN] OpenAlgo auth FAILED — Zerodha and Angel One logins skipped. "
                "Manual login required."
            )
            return False

        # Step 2: Zerodha broker login
        zerodha_ok = self.login_zerodha(
            zerodha_user_id, zerodha_password, zerodha_totp_secret,
            broker_api_key=zerodha_broker_api_key or None,
        )

        # Step 3: Angel One broker login
        angelone_ok = self.login_angelone(
            angelone_user_id, angelone_password, angelone_totp_secret,
            host=angelone_host,
            openalgo_username=openalgo_username,
            openalgo_password=openalgo_password,
        )

        # Send Telegram notifications
        if zerodha_ok:
            self._send_telegram("[LOGIN] Zerodha login successful")
        else:
            self._send_telegram(
                "[LOGIN] Zerodha login FAILED — manual login required at openalgo.ronniedreams.in"
            )
        if angelone_ok:
            self._send_telegram("[LOGIN] Angel One login successful")
        else:
            self._send_telegram(
                "[LOGIN] Angel One login FAILED — check Angel One credentials/TOTP"
            )

        if zerodha_ok and angelone_ok:
            logger.info("[LOGIN] All logins successful (OpenAlgo + Zerodha + Angel One)")
            return True
        else:
            if not zerodha_ok:
                logger.error("[LOGIN] Zerodha broker login failed")
            if not angelone_ok:
                logger.error("[LOGIN] Angel One broker login failed")
            return False

    def _send_telegram(self, message: str) -> None:
        """Send a Telegram notification (best-effort, never raises)."""
        try:
            from .telegram_notifier import get_notifier
            notifier = get_notifier()
            if notifier:
                notifier.send_message(message)
        except Exception as e:
            logger.warning(f"[LOGIN] Could not send Telegram notification: {e}")
