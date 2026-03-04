"""
Telegram Notifier for V3 Live Trading

Uses separate bot tokens from baseline. All messages prefixed with [V3].
Fire-and-forget async sends (same pattern as baseline TelegramNotifier).
"""

import logging
import os
import threading
from datetime import datetime

import pytz
import requests

from .config import (
    V3_TELEGRAM_ENABLED,
    V3_TELEGRAM_BOT_TOKEN,
    V3_TELEGRAM_CHAT_ID,
)

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class TelegramNotifierV3:
    """Send V3 trading notifications via Telegram (separate bot)."""

    def __init__(self, instance_name: str = None):
        self.enabled = V3_TELEGRAM_ENABLED
        self.bot_token = V3_TELEGRAM_BOT_TOKEN
        self.chat_id = V3_TELEGRAM_CHAT_ID
        self.instance_name = instance_name or os.getenv("INSTANCE_NAME", "UNKNOWN")

        if self.enabled:
            if not self.bot_token or not self.chat_id:
                logger.warning("[V3-TG] Telegram enabled but token/chat_id not configured")
                self.enabled = False
            else:
                logger.info(f"[V3-TG] Notifications enabled (instance: {self.instance_name})")

    def _prefix(self) -> str:
        return f"[{self.instance_name}] [V3]"

    def _send_async(self, message: str):
        """Send message in a background thread (fire-and-forget)."""
        if not self.enabled:
            return
        thread = threading.Thread(
            target=self._send_sync, args=(message,), daemon=True
        )
        thread.start()

    def _send_sync(self, message: str):
        """Send message synchronously."""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML',
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.warning(f"[V3-TG] Send failed: {response.status_code}")
        except Exception as e:
            logger.warning(f"[V3-TG] Send error: {e}")

    def send_startup(self, expiry: str, atm: int, model_path: str):
        now = datetime.now(IST).strftime('%H:%M:%S')
        msg = (
            f"{self._prefix()} Strategy Started\n"
            f"Time: {now} IST\n"
            f"Expiry: {expiry} | ATM: {atm}\n"
            f"Model: {model_path}\n"
            f"Mode: Paper Trading"
        )
        self._send_async(msg)

    def send_entry(self, symbol: str, lots: int, quantity: int,
                   entry_price: float, sl_trigger: float,
                   sl_points: float, action_name: str = "ENTER"):
        now = datetime.now(IST).strftime('%H:%M:%S')
        msg = (
            f"{self._prefix()} {action_name}\n"
            f"Symbol: {symbol}\n"
            f"Lots: {lots} | Qty: {quantity}\n"
            f"Entry: {entry_price:.2f}\n"
            f"SL Trigger: {sl_trigger:.2f} ({sl_points:.1f} pts)\n"
            f"Time: {now}"
        )
        self._send_async(msg)

    def send_exit(self, symbol: str, entry_price: float, exit_price: float,
                  realized_R: float, pnl: float, exit_reason: str,
                  cumulative_R: float):
        now = datetime.now(IST).strftime('%H:%M:%S')
        r_emoji = "+" if realized_R >= 0 else ""
        msg = (
            f"{self._prefix()} EXIT ({exit_reason})\n"
            f"Symbol: {symbol}\n"
            f"Entry: {entry_price:.2f} -> Exit: {exit_price:.2f}\n"
            f"R: {r_emoji}{realized_R:.2f} | PnL: {pnl:+,.0f}\n"
            f"Cumulative R: {cumulative_R:+.2f}\n"
            f"Time: {now}"
        )
        self._send_async(msg)

    def send_daily_summary(self, total_trades: int, winning: int,
                           losing: int, cumulative_R: float,
                           pnl: float, max_dd: float,
                           session_stopped: bool = False):
        now = datetime.now(IST).strftime('%H:%M:%S')
        stop_note = " (SESSION STOPPED by model)" if session_stopped else ""
        msg = (
            f"{self._prefix()} Daily Summary{stop_note}\n"
            f"Trades: {total_trades} (W:{winning} L:{losing})\n"
            f"Cumulative R: {cumulative_R:+.2f}\n"
            f"PnL: {pnl:+,.0f}\n"
            f"Max Drawdown: {max_dd:.2f}R\n"
            f"Time: {now}"
        )
        self._send_async(msg)

    def send_model_decision(self, decision_type: str, action: str,
                            obs_summary: str = ""):
        """Log model decisions for debugging."""
        now = datetime.now(IST).strftime('%H:%M:%S')
        msg = (
            f"{self._prefix()} Model Decision\n"
            f"Type: {decision_type} | Action: {action}\n"
            f"{obs_summary}\n"
            f"Time: {now}"
        )
        self._send_async(msg)

    def send_error(self, error_msg: str):
        now = datetime.now(IST).strftime('%H:%M:%S')
        msg = (
            f"{self._prefix()} [ERROR]\n"
            f"{error_msg}\n"
            f"Time: {now}"
        )
        self._send_async(msg)

    def send_message(self, message: str):
        """Send a raw message with V3 prefix."""
        self._send_async(f"{self._prefix()} {message}")
