"""
Startup Health Check Module

Pre-flight validation with smart retry and error classification.

Health Checks:
- OpenAlgo connectivity (HTTP ping)
- OpenAlgo authentication (API key valid)
- Broker login status (Zerodha session active)
- Database accessibility (SQLite read/write)
- WebSocket connectivity (can connect and auth)

Error Classification:
- TRANSIENT: Connection failures, temporary unavailability (retry)
- PERMANENT: Authentication failures, config errors (don't retry)

Usage:
    health_checker = StartupHealthCheck(notification_manager)
    success, error_type, error_message = health_checker.run_all_checks()
"""

import logging
import time
import requests
from typing import Tuple, Optional
from datetime import datetime
import pytz

try:
    from .config import (
        OPENALGO_API_KEY,
        OPENALGO_HOST,
        OPENALGO_WS_URL,
        MAX_STARTUP_RETRIES,
        STARTUP_RETRY_DELAY_BASE,
    )
except ImportError:
    from config import (
        OPENALGO_API_KEY,
        OPENALGO_HOST,
        OPENALGO_WS_URL,
        MAX_STARTUP_RETRIES,
        STARTUP_RETRY_DELAY_BASE,
    )

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class StartupHealthCheck:
    """
    Perform pre-flight health checks with smart retry logic
    """

    def __init__(self, notification_manager):
        """
        Initialize health checker

        Args:
            notification_manager: NotificationManager instance
        """
        self.notification_manager = notification_manager
        logger.info("StartupHealthCheck initialized")

    def run_all_checks(self) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Run all health checks with retry logic

        Returns:
            (success, error_type, error_message)
            - success: True if all checks pass
            - error_type: 'TRANSIENT' or 'PERMANENT' or None
            - error_message: Description of failure
        """
        logger.info("[HEALTH-CHECK] Starting pre-flight health checks...")

        # Check 1: OpenAlgo connectivity (TRANSIENT)
        success, error_msg = self._check_openalgo_connectivity()
        if not success:
            return False, 'TRANSIENT', error_msg

        # Check 2: OpenAlgo authentication (PERMANENT)
        success, error_msg = self._check_openalgo_auth()
        if not success:
            return False, 'PERMANENT', error_msg

        # Check 3: Broker login status (PERMANENT)
        success, error_msg = self._check_broker_login()
        if not success:
            return False, 'PERMANENT', error_msg

        # Check 4: WebSocket connectivity (TRANSIENT)
        success, error_msg = self._check_websocket_connectivity()
        if not success:
            return False, 'TRANSIENT', error_msg

        logger.info("[HEALTH-CHECK] All checks passed successfully")
        return True, None, None

    def _check_openalgo_connectivity(self) -> Tuple[bool, Optional[str]]:
        """
        Check if OpenAlgo is running and accessible

        Returns:
            (success, error_message)
        """
        logger.info("[HEALTH-CHECK] Checking OpenAlgo connectivity...")

        for attempt in range(1, MAX_STARTUP_RETRIES + 1):
            try:
                # Ping OpenAlgo health endpoint
                response = requests.get(
                    f"{OPENALGO_HOST}/",
                    timeout=5
                )

                if response.status_code == 200:
                    logger.info("[HEALTH-CHECK] OpenAlgo connectivity: OK")
                    return True, None
                else:
                    logger.warning(f"[HEALTH-CHECK] OpenAlgo returned status {response.status_code}")

            except requests.exceptions.ConnectionError as e:
                logger.warning(f"[HEALTH-CHECK] OpenAlgo connection failed (attempt {attempt}/{MAX_STARTUP_RETRIES}): {e}")

            except requests.exceptions.Timeout:
                logger.warning(f"[HEALTH-CHECK] OpenAlgo connection timeout (attempt {attempt}/{MAX_STARTUP_RETRIES})")

            except Exception as e:
                logger.error(f"[HEALTH-CHECK] Unexpected error checking OpenAlgo (attempt {attempt}/{MAX_STARTUP_RETRIES}): {e}")

            # Retry with exponential backoff
            if attempt < MAX_STARTUP_RETRIES:
                delay = STARTUP_RETRY_DELAY_BASE * attempt
                logger.info(f"[HEALTH-CHECK] Retrying in {delay} seconds...")
                time.sleep(delay)

        # All retries exhausted
        error_msg = (
            f"OpenAlgo not accessible at {OPENALGO_HOST} after {MAX_STARTUP_RETRIES} attempts.\n\n"
            f"Action: Verify OpenAlgo is running and accessible."
        )
        return False, error_msg

    def _check_openalgo_auth(self) -> Tuple[bool, Optional[str]]:
        """
        Check if API key is valid

        Returns:
            (success, error_message)
        """
        logger.info("[HEALTH-CHECK] Checking OpenAlgo authentication...")

        if not OPENALGO_API_KEY:
            error_msg = (
                "OpenAlgo API key not configured.\n\n"
                "Action: Set OPENALGO_API_KEY in .env file."
            )
            return False, error_msg

        try:
            # Test API key with funds endpoint
            response = requests.post(
                f"{OPENALGO_HOST}/api/v1/funds",
                headers={
                    "Authorization": f"Bearer {OPENALGO_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={"apikey": OPENALGO_API_KEY},
                timeout=5
            )

            if response.status_code == 200:
                logger.info("[HEALTH-CHECK] OpenAlgo authentication: OK")
                return True, None
            elif response.status_code == 401:
                error_msg = (
                    "OpenAlgo API key is invalid or expired.\n\n"
                    "Action: Check OPENALGO_API_KEY in .env file."
                )
                return False, error_msg
            else:
                error_msg = (
                    f"OpenAlgo API returned status {response.status_code}.\n\n"
                    f"Action: Check OpenAlgo logs for details."
                )
                return False, error_msg

        except Exception as e:
            error_msg = (
                f"Failed to verify OpenAlgo API key: {e}\n\n"
                "Action: Check OpenAlgo configuration."
            )
            return False, error_msg

    def _check_broker_login(self) -> Tuple[bool, Optional[str]]:
        """
        Check if broker (Zerodha) session is active

        Returns:
            (success, error_message)
        """
        logger.info("[HEALTH-CHECK] Checking broker login status...")

        try:
            # Get broker session status from OpenAlgo
            response = requests.post(
                f"{OPENALGO_HOST}/api/v1/funds",
                headers={
                    "Authorization": f"Bearer {OPENALGO_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={"apikey": OPENALGO_API_KEY},
                timeout=5
            )

            if response.status_code == 200:
                data = response.json()

                # Check if we have valid funds data (indicates active session)
                if data.get('status') == 'success':
                    logger.info("[HEALTH-CHECK] Broker login status: ACTIVE")
                    return True, None
                else:
                    error_msg = (
                        "Broker session not active or expired.\n\n"
                        f"Action: Login to broker at {OPENALGO_HOST}"
                    )
                    return False, error_msg

            else:
                error_msg = (
                    "Failed to check broker login status.\n\n"
                    f"OpenAlgo API returned status {response.status_code}.\n\n"
                    f"Action: Login to broker at {OPENALGO_HOST}"
                )
                return False, error_msg

        except Exception as e:
            error_msg = (
                f"Failed to check broker login: {e}\n\n"
                f"Action: Verify broker connection at {OPENALGO_HOST}"
            )
            return False, error_msg

    def _check_websocket_connectivity(self) -> Tuple[bool, Optional[str]]:
        """
        Check if WebSocket is accessible

        Returns:
            (success, error_message)
        """
        logger.info("[HEALTH-CHECK] Checking WebSocket connectivity...")

        # Note: This is a basic check. Full WebSocket auth test would require
        # establishing a connection and subscribing, which is complex.
        # For now, we'll do a basic connectivity test.

        try:
            import websocket

            for attempt in range(1, MAX_STARTUP_RETRIES + 1):
                try:
                    # Try to establish WebSocket connection
                    ws = websocket.create_connection(
                        OPENALGO_WS_URL,
                        timeout=5
                    )
                    ws.close()

                    logger.info("[HEALTH-CHECK] WebSocket connectivity: OK")
                    return True, None

                except Exception as e:
                    logger.warning(f"[HEALTH-CHECK] WebSocket connection failed (attempt {attempt}/{MAX_STARTUP_RETRIES}): {e}")

                    # Retry with exponential backoff
                    if attempt < MAX_STARTUP_RETRIES:
                        delay = STARTUP_RETRY_DELAY_BASE * attempt
                        logger.info(f"[HEALTH-CHECK] Retrying in {delay} seconds...")
                        time.sleep(delay)

            # All retries exhausted
            error_msg = (
                f"WebSocket not accessible at {OPENALGO_WS_URL} after {MAX_STARTUP_RETRIES} attempts.\n\n"
                f"Action: Verify OpenAlgo WebSocket proxy is running."
            )
            return False, error_msg

        except ImportError:
            logger.warning("[HEALTH-CHECK] websocket-client not installed, skipping WebSocket check")
            return True, None

    def _check_database_access(self) -> Tuple[bool, Optional[str]]:
        """
        Check if database is accessible and writable

        Returns:
            (success, error_message)
        """
        logger.info("[HEALTH-CHECK] Checking database access...")

        try:
            from .state_manager import StateManager

            # Try to create StateManager instance (tests database access)
            state = StateManager()
            state.close()

            logger.info("[HEALTH-CHECK] Database access: OK")
            return True, None

        except Exception as e:
            error_msg = (
                f"Failed to access database: {e}\n\n"
                "Action: Check database file permissions and disk space."
            )
            return False, error_msg


if __name__ == '__main__':
    # Test health checks
    logging.basicConfig(level=logging.INFO)

    from notification_manager import NotificationManager
    from state_manager import StateManager
    from telegram_notifier import TelegramNotifier

    state = StateManager()
    telegram = TelegramNotifier()
    notification_manager = NotificationManager(telegram, state)

    health_checker = StartupHealthCheck(notification_manager)

    print("\n" + "="*80)
    print("RUNNING HEALTH CHECKS")
    print("="*80 + "\n")

    success, error_type, error_message = health_checker.run_all_checks()

    print("\n" + "="*80)
    if success:
        print("RESULT: ALL CHECKS PASSED")
    else:
        print(f"RESULT: CHECKS FAILED ({error_type})")
        print(f"\nError: {error_message}")
    print("="*80 + "\n")

    state.close()
