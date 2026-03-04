"""
Automated Login Handler for Upstox OpenAlgo

Flow:
1. Wait for OpenAlgo Upstox container to be ready
2. Login to OpenAlgo dashboard (same as baseline)
3. Upstox OAuth flow (6 steps — ported from upstox-totp library):
   a. GET /v2/login/authorization/dialog → extract user_id from redirect
   b. POST /login/open/v6/auth/1fa/otp/generate → trigger OTP
   c. POST /login/open/v4/auth/1fa/otp-totp/verify → verify with TOTP
   d. POST /login/open/v3/auth/2fa → submit PIN (2FA)
   e. POST /login/v2/oauth/authorize → get authorization code
   f. Pass code to OpenAlgo /upstox/callback → OpenAlgo exchanges for token

Only use for paper trading (testing phase).
"""

import base64
import logging
import random
import string
import time
from urllib.parse import parse_qs, urlparse

import pyotp
import requests

logger = logging.getLogger(__name__)

# Upstox API domains
UPSTOX_API = "https://api.upstox.com"
UPSTOX_SERVICE = "https://service.upstox.com"
UPSTOX_LOGIN = "https://login.upstox.com"

# Internal redirect URI used by Upstox during login flow
UPSTOX_INTERNAL_REDIRECT = "https://api-v2.upstox.com/login/authorization/redirect"

# Browser-like headers (Upstox requires x-device-details or rejects requests)
def _build_browser_headers(request_id: str) -> dict:
    """Build browser-like headers with a unique device UUID."""
    uuid_str = "".join(random.choices(string.ascii_letters + string.digits, k=20))
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    return {
        "accept": "*/*",
        "accept-language": "en-GB,en;q=0.9",
        "content-type": "application/json",
        "origin": UPSTOX_LOGIN,
        "referer": f"{UPSTOX_LOGIN}/",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": ua,
        "x-device-details": (
            f"platform=WEB|osName=Windows/10|osVersion=Chrome/131.0.0.0|"
            f"appVersion=4.0.0|modelName=Chrome|manufacturer=Unknown|"
            f"uuid={uuid_str}|userAgent=Upstox 3.0 {ua}"
        ),
        "x-request-id": request_id,
    }


def _generate_request_id() -> str:
    """Generate Upstox-style request ID."""
    suffix = "".join(random.choices(string.ascii_letters + string.digits, k=10))
    return f"WPRO-{suffix}"


class LoginHandlerV1:
    """Handles automated login to Upstox via OpenAlgo."""

    def __init__(self, openalgo_host: str):
        self.host = openalgo_host.rstrip("/")
        # OpenAlgo session (for dashboard auth + callback)
        self.oa_session = requests.Session()
        # Upstox session (for broker OAuth flow)
        self._request_id = _generate_request_id()
        self.upstox_session = requests.Session()
        self.upstox_session.headers.update(_build_browser_headers(self._request_id))

    # ------------------------------------------------------------------
    # OpenAlgo dashboard auth (reused from baseline pattern)
    # ------------------------------------------------------------------

    def _oa_get(self, url, **kwargs):
        r = self.oa_session.get(url, **kwargs)
        self._strip_secure_cookies(self.oa_session)
        return r

    def _oa_post(self, url, **kwargs):
        r = self.oa_session.post(url, **kwargs)
        self._strip_secure_cookies(self.oa_session)
        return r

    @staticmethod
    def _strip_secure_cookies(session):
        for cookie in session.cookies:
            cookie.secure = False

    def _get_csrf_token(self) -> str | None:
        url = f"{self.host}/auth/csrf-token"
        try:
            response = self._oa_get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data.get("csrf_token")
        except Exception as e:
            logger.warning(f"[RL-V1-LOGIN] CSRF token fetch failed: {e}")
        return None

    def _login_openalgo(self, username: str, password: str) -> bool:
        """Authenticate to OpenAlgo dashboard."""
        csrf_token = self._get_csrf_token()
        if not csrf_token:
            logger.error("[RL-V1-LOGIN] Could not get CSRF token")
            return False

        url = f"{self.host}/auth/login"
        data = {"username": username, "password": password}
        headers = {"X-CSRFToken": csrf_token}

        try:
            response = self._oa_post(url, data=data, headers=headers, timeout=10)
            if response.status_code == 200:
                resp_data = response.json()
                if resp_data.get("status") == "success":
                    logger.info("[RL-V1-LOGIN] OpenAlgo dashboard login successful")
                    return True
                else:
                    logger.error(
                        f"[RL-V1-LOGIN] OpenAlgo login failed: "
                        f"{resp_data.get('message', 'Unknown error')}"
                    )
                    return False
            logger.error(f"[RL-V1-LOGIN] OpenAlgo login failed: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"[RL-V1-LOGIN] OpenAlgo login error: {e}")
        return False

    # ------------------------------------------------------------------
    # Upstox OAuth flow (ported from upstox-totp library)
    # ------------------------------------------------------------------

    def _upstox_get(self, url, **kwargs):
        """GET to Upstox APIs with browser headers."""
        return self.upstox_session.get(url, timeout=15, **kwargs)

    def _upstox_post(self, url, **kwargs):
        """POST to Upstox APIs with browser headers."""
        return self.upstox_session.post(url, timeout=15, **kwargs)

    def _step1_get_user_info(self, api_key: str, redirect_uri: str) -> dict | None:
        """Step 1: GET authorization dialog → extract user_id from redirect."""
        url = f"{UPSTOX_API}/v2/login/authorization/dialog"
        params = {
            "response_type": "code",
            "client_id": api_key,
            "redirect_uri": redirect_uri,
        }

        try:
            r = self._upstox_get(url, params=params, allow_redirects=True)
            # The redirect URL contains user_id, client_id, user_type as query params
            parsed = urlparse(str(r.url))
            qs = parse_qs(parsed.query)

            user_id = qs.get("user_id", [None])[0]
            client_id = qs.get("client_id", [None])[0]
            user_type = qs.get("user_type", [None])[0]

            if not all([user_id, client_id, user_type]):
                # Check if the response is JSON with an error
                try:
                    data = r.json()
                    if not data.get("success", True):
                        logger.error(f"[RL-V1-LOGIN] Upstox auth dialog error: {data}")
                        return None
                except Exception:
                    pass
                logger.error(
                    f"[RL-V1-LOGIN] Could not extract user info from redirect. "
                    f"URL: {r.url}, params: {qs}"
                )
                return None

            logger.info(f"[RL-V1-LOGIN] Step 1 OK: user_id={user_id}, client_id={client_id}")
            return {"user_id": user_id, "client_id": client_id, "user_type": user_type}

        except Exception as e:
            logger.error(f"[RL-V1-LOGIN] Step 1 (auth dialog) failed: {e}")
            return None

    def _step2_generate_otp(self, mobile: str, user_id: str) -> str | None:
        """Step 2: POST to generate OTP → get validateOTPToken."""
        url = f"{UPSTOX_SERVICE}/login/open/v6/auth/1fa/otp/generate"
        payload = {"data": {"mobileNumber": mobile, "userId": user_id}}

        try:
            r = self._upstox_post(url, json=payload)
            data = r.json()

            if not data.get("success"):
                logger.error(f"[RL-V1-LOGIN] Step 2 (OTP generate) failed: {data}")
                return None

            token = data.get("data", {}).get("validateOTPToken")
            if not token:
                logger.error(f"[RL-V1-LOGIN] Step 2: no validateOTPToken in response")
                return None

            logger.info("[RL-V1-LOGIN] Step 2 OK: OTP generation successful")
            return token

        except Exception as e:
            logger.error(f"[RL-V1-LOGIN] Step 2 (OTP generate) error: {e}")
            return None

    def _step3_verify_totp(self, totp_code: str, otp_token: str) -> bool:
        """Step 3: POST to verify TOTP."""
        url = f"{UPSTOX_SERVICE}/login/open/v4/auth/1fa/otp-totp/verify"
        payload = {"data": {"otp": totp_code, "validateOtpToken": otp_token}}

        try:
            r = self._upstox_post(url, json=payload)
            data = r.json()

            if not data.get("success"):
                logger.error(f"[RL-V1-LOGIN] Step 3 (TOTP verify) failed: {data}")
                return False

            logger.info("[RL-V1-LOGIN] Step 3 OK: TOTP verified")
            return True

        except Exception as e:
            logger.error(f"[RL-V1-LOGIN] Step 3 (TOTP verify) error: {e}")
            return False

    def _step4_submit_pin(self, pin: str, client_id: str) -> bool:
        """Step 4: POST PIN for 2FA."""
        url = f"{UPSTOX_SERVICE}/login/open/v3/auth/2fa"
        pin_encoded = base64.b64encode(pin.encode()).decode()

        params = {
            "client_id": client_id,
            "redirect_uri": UPSTOX_INTERNAL_REDIRECT,
        }
        payload = {"data": {"twoFAMethod": "SECRET_PIN", "inputText": pin_encoded}}

        try:
            r = self._upstox_post(url, params=params, json=payload, allow_redirects=True)
            data = r.json()

            if not data.get("success"):
                logger.error(f"[RL-V1-LOGIN] Step 4 (2FA PIN) failed: {data}")
                return False

            logger.info("[RL-V1-LOGIN] Step 4 OK: 2FA PIN accepted")
            return True

        except Exception as e:
            logger.error(f"[RL-V1-LOGIN] Step 4 (2FA PIN) error: {e}")
            return False

    def _step5_oauth_authorize(self, client_id: str) -> str | None:
        """Step 5: POST OAuth authorize → get authorization code."""
        url = f"{UPSTOX_SERVICE}/login/v2/oauth/authorize"

        params = {
            "client_id": client_id,
            "redirect_uri": UPSTOX_INTERNAL_REDIRECT,
            "requestId": self._request_id,
            "response_type": "code",
        }
        payload = {"data": {"userOAuthApproval": True}}

        try:
            r = self._upstox_post(url, params=params, json=payload, allow_redirects=True)
            data = r.json()

            if not data.get("success"):
                logger.error(f"[RL-V1-LOGIN] Step 5 (OAuth authorize) failed: {data}")
                return None

            redirect_uri = data.get("data", {}).get("redirectUri", "")
            parsed = urlparse(redirect_uri)
            qs = parse_qs(parsed.query)
            code = qs.get("code", [None])[0]

            if not code:
                logger.error(
                    f"[RL-V1-LOGIN] Step 5: no auth code in redirectUri: {redirect_uri}"
                )
                return None

            logger.info(f"[RL-V1-LOGIN] Step 5 OK: got authorization code ({code[:8]}...)")
            return code

        except Exception as e:
            logger.error(f"[RL-V1-LOGIN] Step 5 (OAuth authorize) error: {e}")
            return None

    def _step6_exchange_token(self, code: str, api_key: str, api_secret: str,
                              redirect_uri: str) -> str | None:
        """Step 6: Exchange auth code for access token (direct, no OpenAlgo)."""
        url = f"{UPSTOX_API}/v2/login/authorization/token"

        # Reset session for clean token exchange
        token_session = requests.Session()
        headers = {"accept": "application/json", "content-type": "application/x-www-form-urlencoded"}
        data = (
            f"code={code}"
            f"&client_id={api_key}"
            f"&client_secret={api_secret}"
            f"&redirect_uri={redirect_uri}"
            f"&grant_type=authorization_code"
        )

        try:
            r = token_session.post(url, data=data, headers=headers, timeout=15)
            resp = r.json()

            token = resp.get("access_token")
            if token:
                logger.info("[RL-V1-LOGIN] Step 6 OK: got access token")
                return token

            logger.error(f"[RL-V1-LOGIN] Step 6 (token exchange) failed: {resp}")
            return None

        except Exception as e:
            logger.error(f"[RL-V1-LOGIN] Step 6 (token exchange) error: {e}")
            return None

    def _pass_code_to_openalgo(self, code: str) -> bool:
        """Pass authorization code to OpenAlgo's /upstox/callback."""
        callback_url = f"{self.host}/upstox/callback"

        try:
            r = self._oa_get(callback_url, params={"code": code}, allow_redirects=True)
            url_lower = r.url.lower()

            if "dashboard" in url_lower:
                logger.info("[RL-V1-LOGIN] OpenAlgo /upstox/callback succeeded (dashboard redirect)")
                return True

            if "login" in url_lower and "dashboard" not in url_lower:
                logger.error(
                    f"[RL-V1-LOGIN] OpenAlgo callback redirected to login page: {r.url}"
                )
                return False

            # Try JSON response
            try:
                data = r.json()
                if data.get("status") == "success":
                    logger.info("[RL-V1-LOGIN] OpenAlgo /upstox/callback succeeded (JSON)")
                    return True
                logger.error(f"[RL-V1-LOGIN] OpenAlgo callback error: {data}")
            except Exception:
                # Non-JSON, non-redirect — check HTTP status
                if r.status_code == 200:
                    logger.info("[RL-V1-LOGIN] OpenAlgo /upstox/callback returned 200")
                    return True
                logger.error(
                    f"[RL-V1-LOGIN] OpenAlgo callback unexpected: "
                    f"HTTP {r.status_code}, url={r.url}"
                )

            return False

        except Exception as e:
            logger.error(f"[RL-V1-LOGIN] OpenAlgo callback error: {e}")
            return False

    # ------------------------------------------------------------------
    # Main auto-login entry point
    # ------------------------------------------------------------------

    def auto_login(
        self,
        mobile: str,
        password: str,
        pin: str,
        totp_secret: str,
        api_key: str,
        api_secret: str,
        redirect_uri: str = "",
        openalgo_username: str = "",
        openalgo_password: str = "",
        max_retries: int = 20,
        retry_delay: int = 5,
    ) -> bool:
        """Automated Upstox login via OpenAlgo.

        Args:
            mobile: Upstox 10-digit mobile number
            password: Upstox password (unused in current flow, kept for compat)
            pin: Upstox 6-digit PIN
            totp_secret: Upstox TOTP secret (base32)
            api_key: Upstox OAuth API key (client_id)
            api_secret: Upstox OAuth API secret
            redirect_uri: OAuth redirect URI (must match Upstox app settings)
            openalgo_username: OpenAlgo dashboard username
            openalgo_password: OpenAlgo dashboard password
            max_retries: Max connection retries for OpenAlgo
            retry_delay: Seconds between retries
        """
        # redirect_uri must match Upstox app registration (external URL),
        # NOT the Docker internal URL. Default to localhost:5002.
        if not redirect_uri:
            redirect_uri = "http://127.0.0.1:5002/upstox/callback"

        # Wait for OpenAlgo to be ready
        for attempt in range(1, max_retries + 1):
            try:
                r = self._oa_get(f"{self.host}/", timeout=5)
                if r.status_code == 200:
                    logger.info(
                        f"[RL-V1-LOGIN] Upstox OpenAlgo is ready (attempt {attempt})"
                    )
                    break
            except Exception:
                pass
            if attempt < max_retries:
                logger.info(
                    f"[RL-V1-LOGIN] Waiting for Upstox OpenAlgo... "
                    f"({attempt}/{max_retries})"
                )
                time.sleep(retry_delay)
        else:
            logger.error("[RL-V1-LOGIN] Upstox OpenAlgo not reachable")
            return False

        # Login to OpenAlgo dashboard
        if openalgo_username and openalgo_password:
            if not self._login_openalgo(openalgo_username, openalgo_password):
                return False
        else:
            logger.warning("[RL-V1-LOGIN] No OpenAlgo credentials — skipping dashboard login")

        # Generate TOTP
        try:
            totp_code = pyotp.TOTP(totp_secret).now()
        except Exception as e:
            logger.error(f"[RL-V1-LOGIN] TOTP generation failed: {e}")
            return False

        # Upstox OAuth flow (steps 1-5)
        logger.info("[RL-V1-LOGIN] Starting Upstox OAuth flow...")

        # Step 1: Get user info from auth dialog
        user_info = self._step1_get_user_info(api_key, redirect_uri)
        if not user_info:
            return False
        time.sleep(1)

        # Step 2: Generate OTP
        otp_token = self._step2_generate_otp(mobile, user_info["user_id"])
        if not otp_token:
            return False
        time.sleep(1)

        # Step 3: Verify TOTP
        if not self._step3_verify_totp(totp_code, otp_token):
            return False
        time.sleep(1)

        # Step 4: Submit PIN for 2FA
        if not self._step4_submit_pin(pin, user_info["client_id"]):
            return False
        time.sleep(1)

        # Step 5: OAuth authorize → get code
        code = self._step5_oauth_authorize(user_info["client_id"])
        if not code:
            return False

        # Pass code to OpenAlgo callback
        logger.info("[RL-V1-LOGIN] Passing auth code to OpenAlgo...")
        if self._pass_code_to_openalgo(code):
            logger.info("[RL-V1-LOGIN] Upstox broker login successful via OpenAlgo")
            return True

        # Fallback: try direct token exchange (if OpenAlgo callback fails)
        logger.warning("[RL-V1-LOGIN] OpenAlgo callback failed, trying direct token exchange...")
        token = self._step6_exchange_token(code, api_key, api_secret, redirect_uri)
        if token:
            logger.info(
                "[RL-V1-LOGIN] Got access token directly. "
                "Note: token NOT stored in OpenAlgo — manual dashboard login may be needed."
            )
            return True

        logger.error("[RL-V1-LOGIN] Upstox auto-login failed completely")
        return False
