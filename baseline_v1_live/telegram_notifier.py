"""
Telegram Notifications for Live Trading

Sends real-time alerts for:
- Trade entries (order fills)
- Trade exits (SL hits, profit targets)
- Daily targets (±5R)
- Errors and warnings

Setup:
1. Create Telegram bot via @BotFather
2. Get bot token
3. Get your chat ID from @userinfobot
4. Set in .env:
   TELEGRAM_ENABLED=true
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
"""

import logging
import requests
import threading
from typing import Optional, Dict
from datetime import datetime
import pytz
import os
import sys

# Add parent directory to path if running from live/ directory
if __name__ == '__main__' or 'live' not in sys.modules:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from .config import (
        TELEGRAM_ENABLED,
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID,
        NOTIFY_ON_TRADE_ENTRY,
        NOTIFY_ON_TRADE_EXIT,
        NOTIFY_ON_DAILY_TARGET,
        NOTIFY_ON_ERROR,
        NOTIFY_ON_BEST_STRIKE_CHANGE,
    )
except ImportError:
    from config import (
        TELEGRAM_ENABLED,
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID,
        NOTIFY_ON_TRADE_ENTRY,
        NOTIFY_ON_TRADE_EXIT,
        NOTIFY_ON_DAILY_TARGET,
        NOTIFY_ON_ERROR,
        NOTIFY_ON_BEST_STRIKE_CHANGE,
    )

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class TelegramNotifier:
    """
    Send trading notifications via Telegram
    """

    def __init__(self, instance_name: str = None):
        self.enabled = TELEGRAM_ENABLED
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID

        # Instance identification for multi-instance deployments
        # Defaults to INSTANCE_NAME env var, or "UNKNOWN" if not set
        self.instance_name = instance_name or os.getenv("INSTANCE_NAME", "UNKNOWN")

        if self.enabled:
            if not self.bot_token or not self.chat_id:
                logger.warning("Telegram enabled but token/chat_id not configured")
                self.enabled = False
            else:
                logger.info(f"Telegram notifications enabled (instance: {self.instance_name})")
                # Startup message disabled to prevent spam
                # self.send_message("Baseline V1 Live Trading started", parse_mode=None)
    
    def send_message(self, message: str, parse_mode: Optional[str] = 'HTML') -> bool:
        """
        Send message to Telegram

        Args:
            message: Message text (supports HTML formatting if parse_mode='HTML')
            parse_mode: 'HTML', 'Markdown', or None for plain text

        Returns:
            True if sent successfully
        """
        if not self.enabled:
            return False

        # Prefix message with instance name for multi-instance identification
        # Format: [LOCAL] or [EC2] at the start of each message
        instance_tag = f"[{self.instance_name}] " if self.instance_name != "UNKNOWN" else ""
        tagged_message = f"{instance_tag}{message}"

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        payload = {
            'chat_id': self.chat_id,
            'text': tagged_message,
        }

        # Only add parse_mode if specified
        if parse_mode:
            payload['parse_mode'] = parse_mode
        
        def _do_send():
            try:
                response = requests.post(url, json=payload, timeout=10)
                if response.status_code == 200:
                    logger.debug("Telegram message sent successfully")
                else:
                    logger.error(f"Telegram API error: {response.status_code} - {response.text}")
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")

        thread = threading.Thread(target=_do_send, daemon=True)
        thread.start()
        return True  # Optimistically return True (fire-and-forget)
    
    def notify_trade_entry(self, fill_info: Dict):
        """
        Notify on trade entry (order filled)
        
        Args:
            fill_info: {
                'symbol': str,
                'fill_price': float,
                'quantity': int,
                'candidate_info': {
                    'sl_price': float,
                    'actual_R': float,
                    'lots': int,
                    ...
                }
            }
        """
        if not NOTIFY_ON_TRADE_ENTRY:
            return
        
        symbol = fill_info['symbol']
        fill_price = fill_info['fill_price']
        quantity = fill_info['quantity']
        candidate = fill_info['candidate_info']
        
        sl_price = candidate['sl_price']
        actual_R = candidate['actual_R']
        lots = candidate['lots']
        sl_points = candidate['sl_points']
        
        message = f"""
🟢 <b>TRADE ENTRY</b>

Symbol: <code>{symbol}</code>
Entry: ₹{fill_price:.2f}
SL: ₹{sl_price:.2f} ({sl_points:.1f} pts)
Qty: {quantity} ({lots} lots)
Risk: ₹{actual_R:,.0f} (1R)

Time: {datetime.now(IST).strftime('%H:%M:%S')}
        """
        
        self.send_message(message.strip())
    
    def notify_trade_exit(self, position: Dict, exit_reason: str):
        """
        Notify on trade exit
        
        Args:
            position: Position dict with exit info
            exit_reason: SL_HIT, DAILY_TARGET, EOD, etc.
        """
        if not NOTIFY_ON_TRADE_EXIT:
            return
        
        symbol = position['symbol']
        entry_price = position['entry_price']
        exit_price = position['exit_price']
        realized_pnl = position['realized_pnl']
        realized_R = position['realized_R']
        
        # Emoji based on P&L
        emoji = "🟢" if realized_R > 0 else "🔴" if realized_R < 0 else "⚪"
        
        # Format reason
        reason_map = {
            'SL_HIT': 'Stop Loss Hit',
            'DAILY_TARGET': 'Daily Target',
            'EOD_EXIT': 'End of Day',
            '+5R_TARGET': '+5R Daily Target',
            '-5R_STOP': '-5R Daily Stop',
        }
        reason_text = reason_map.get(exit_reason, exit_reason)
        
        message = f"""
{emoji} <b>TRADE EXIT</b>

Symbol: <code>{symbol}</code>
Entry: ₹{entry_price:.2f}
Exit: ₹{exit_price:.2f}

P&L: ₹{realized_pnl:,.0f} ({realized_R:+.2f}R)
Reason: {reason_text}

Time: {datetime.now(IST).strftime('%H:%M:%S')}
        """
        
        self.send_message(message.strip())
    
    def notify_daily_target(self, summary: Dict):
        """
        Notify when daily ±5R target hit
        
        Args:
            summary: Position summary dict
        """
        if not NOTIFY_ON_DAILY_TARGET:
            return
        
        cumulative_R = summary['cumulative_R']
        total_pnl = summary['total_pnl']
        closed_positions = summary['closed_positions_today']
        exit_reason = summary['daily_exit_reason']
        
        emoji = "🎯" if cumulative_R > 0 else "🛑"
        
        message = f"""
{emoji} <b>DAILY TARGET HIT!</b>

Date: {datetime.now(IST).strftime('%d %b %Y')}
Cumulative R: <b>{cumulative_R:+.2f}R</b>
Total P&L: ₹{total_pnl:,.0f}
Trades: {closed_positions}
Reason: {exit_reason}

All positions closed.
Trading stopped for the day.

Time: {datetime.now(IST).strftime('%H:%M:%S')}
        """
        
        self.send_message(message.strip())
    
    def notify_daily_summary(self, summary: Dict):
        """
        Send end-of-day summary
        
        Args:
            summary: Daily summary dict
        """
        cumulative_R = summary.get('cumulative_R', 0)
        total_pnl = summary.get('total_pnl', 0)
        closed_positions = summary.get('closed_positions_today', 0)
        daily_exit_triggered = summary.get('daily_exit_triggered', False)
        daily_exit_reason = summary.get('daily_exit_reason', None)

        if daily_exit_triggered and daily_exit_reason:
            if 'TARGET' in daily_exit_reason:
                reason_text = 'Daily +5R target hit'
            elif 'STOP' in daily_exit_reason:
                reason_text = 'Daily -5R stop hit'
            else:
                reason_text = daily_exit_reason
        else:
            reason_text = 'Market close (3:15 PM)'

        emoji = "📊"
        if cumulative_R >= 3:
            emoji = "🚀"
        elif cumulative_R <= -3:
            emoji = "📉"

        message = f"""
{emoji} <b>DAILY SUMMARY</b>

Date: {datetime.now(IST).strftime('%d %b %Y')}

Cumulative R: <b>{cumulative_R:+.2f}R</b>
Total P&L: ₹{total_pnl:,.0f}
Trades: {closed_positions}
Reason: {reason_text}

Trading session ended.
        """
        
        self.send_message(message.strip())
    
    def notify_error(self, error_msg: str):
        """
        Notify on critical errors
        
        Args:
            error_msg: Error message
        """
        if not NOTIFY_ON_ERROR:
            return
        
        message = f"""
⚠️ <b>ERROR</b>

{error_msg}

Time: {datetime.now(IST).strftime('%H:%M:%S')}

Please check logs.
        """
        
        self.send_message(message.strip())
    
    def notify_position_update(self, summary: Dict):
        """
        Send position status update

        Args:
            summary: Position summary dict
        """
        total_positions = summary.get('total_positions', 0)
        ce_positions = summary.get('ce_positions', 0)
        pe_positions = summary.get('pe_positions', 0)
        cumulative_R = summary.get('cumulative_R', 0)
        unrealized_pnl = summary.get('unrealized_pnl', 0)

        message = f"""
📈 <b>POSITION UPDATE</b>

Open: {total_positions} ({ce_positions} CE, {pe_positions} PE)
Cumulative R: {cumulative_R:+.2f}R
Unrealized P&L: ₹{unrealized_pnl:,.0f}

Time: {datetime.now(IST).strftime('%H:%M:%S')}
        """

        self.send_message(message.strip())

    def notify_best_strike_change(self, option_type: str, candidate: Dict, is_new: bool = False):
        """
        Notify when a new best strike is selected or changes

        Args:
            option_type: 'CE' or 'PE'
            candidate: Best strike candidate dict
            is_new: True if first selection, False if replacement
        """
        if not NOTIFY_ON_BEST_STRIKE_CHANGE:
            return

        symbol = candidate['symbol']
        entry_price = candidate['swing_low']
        sl_price = candidate['sl_price']
        sl_percent = candidate['sl_percent']
        vwap_premium = candidate['vwap_premium']
        lots = candidate['lots']
        actual_R = candidate['actual_R']
        current_price = candidate.get('current_price', entry_price)

        # Different emoji for new vs replacement
        emoji = "🆕" if is_new else "🔄"
        action = "SELECTED" if is_new else "UPDATED"

        sl_points = sl_price - entry_price

        message = f"""
{emoji} <b>BEST {option_type} {action}</b>

Symbol: <code>{symbol}</code>
Entry: ₹{entry_price:.2f}
Current: ₹{current_price:.2f}
SL: ₹{sl_price:.2f} ({sl_percent:.1%}) | {sl_points:.1f} pts

VWAP Premium: {vwap_premium:.1%}
Lots: {lots} ({lots * 65} qty)
Risk: ₹{actual_R:,.0f} (1R)

Time: {datetime.now(IST).strftime('%H:%M:%S')}
        """

        self.send_message(message.strip())

    def notify_swing_detected(self, symbol: str, swing_info: Dict):
        """
        Notify when a new swing (low or high) is detected

        Args:
            symbol: Option symbol
            swing_info: Dict with swing details including 'type', 'price', 'timestamp', 'vwap'
        """
        swing_type = swing_info.get('type', 'Low')  # 'Low' or 'High'
        swing_price = swing_info.get('price', 0)
        swing_time = swing_info.get('timestamp')  # Use 'timestamp' from swing_info
        vwap = swing_info.get('vwap', 0)
        option_type = swing_info.get('option_type', 'CE' if 'CE' in symbol else 'PE')

        # Format swing time
        if swing_time:
            if hasattr(swing_time, 'strftime'):
                time_str = swing_time.strftime('%H:%M')
            else:
                time_str = str(swing_time)
        else:
            time_str = datetime.now(IST).strftime('%H:%M')

        # Calculate VWAP premium
        vwap_premium = ((swing_price - vwap) / vwap * 100) if vwap > 0 else 0

        # Use different indicators for low vs high
        if swing_type.lower() == 'low':
            indicator = "SWING LOW"
            emoji = "📉"
        else:
            indicator = "SWING HIGH"
            emoji = "📈"

        message = f"""
{emoji} <b>{indicator} DETECTED</b>

Symbol: <code>{symbol}</code>
Type: {option_type}
Price: Rs.{swing_price:.2f}
Swing Time: {time_str}

VWAP: Rs.{vwap:.2f}
Premium: {vwap_premium:.1f}%

Detection: {datetime.now(IST).strftime('%H:%M:%S')}
        """

        self.send_message(message.strip())


class TelegramCommandListener:
    """
    Background daemon thread polling Telegram getUpdates API for commands.

    Recognizes commands from authorized chat_id only:
    - /kill  -> creates KILL_SWITCH file
    - /pause -> creates PAUSE_SWITCH file
    - /resume -> removes PAUSE_SWITCH file
    - /status -> reports current state
    """

    def __init__(self, bot_token: str, chat_id: str, state_dir: str,
                 notifier: 'TelegramNotifier' = None,
                 status_callback=None):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.state_dir = state_dir
        self.notifier = notifier
        self.status_callback = status_callback  # callable returning status string
        self._offset = 0
        self._running = False
        self._thread = None

    @property
    def kill_switch_file(self):
        return os.path.join(self.state_dir, 'KILL_SWITCH')

    @property
    def pause_switch_file(self):
        return os.path.join(self.state_dir, 'PAUSE_SWITCH')

    def start(self):
        """Start the polling thread."""
        if self._running:
            return
        # Flush all pending updates so we don't process stale commands from before startup
        self._flush_pending_updates()
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="telegram-cmd")
        self._thread.start()
        logger.info("[TELEGRAM-CMD] Command listener started")

    def _flush_pending_updates(self):
        """Skip all queued updates so only new commands after startup are processed."""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
            resp = requests.get(url, params={'offset': self._offset, 'timeout': 1}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get('result', [])
                if results:
                    self._offset = results[-1]['update_id'] + 1
                    logger.info(f"[TELEGRAM-CMD] Flushed {len(results)} pending updates on startup")
        except Exception as e:
            logger.debug(f"[TELEGRAM-CMD] Flush error (non-critical): {e}")

    def stop(self):
        self._running = False

    def _poll_loop(self):
        """Poll getUpdates every 3 seconds."""
        while self._running:
            try:
                self._process_updates()
            except Exception as e:
                logger.error(f"[TELEGRAM-CMD] Poll error: {e}")
            import time as _t
            _t.sleep(3)

    def _process_updates(self):
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {'offset': self._offset, 'timeout': 1}
        try:
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                return
            data = resp.json()
            if not data.get('ok'):
                return
            for update in data.get('result', []):
                self._offset = update['update_id'] + 1
                msg = update.get('message', {})
                chat_id = str(msg.get('chat', {}).get('id', ''))
                text = (msg.get('text') or '').strip().lower()

                if chat_id != self.chat_id:
                    continue  # Ignore messages from unauthorized chats

                if text == '/kill':
                    self._handle_kill()
                elif text == '/pause':
                    self._handle_pause()
                elif text == '/resume':
                    self._handle_resume()
                elif text == '/status':
                    self._handle_status()
                elif text in ('/menu', '/help', '/start'):
                    self._handle_menu()
        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            logger.debug(f"[TELEGRAM-CMD] Update fetch error: {e}")

    def _handle_kill(self):
        with open(self.kill_switch_file, 'w') as f:
            f.write(f"triggered via Telegram /kill at {datetime.now(IST).isoformat()}")
        logger.critical("[TELEGRAM-CMD] /kill received -- KILL_SWITCH file created")
        if self.notifier:
            self.notifier.send_message(
                "[CRITICAL] /kill received.\n"
                "All pending entry orders will be cancelled.\n"
                "Strategy will stop within 5 seconds.\n"
                "Existing positions retained (SL orders active at broker).\n"
                "To restart: delete KILL_SWITCH file and restart container."
            )

    def _handle_pause(self):
        with open(self.pause_switch_file, 'w') as f:
            f.write(f"triggered via Telegram /pause at {datetime.now(IST).isoformat()}")
        logger.warning("[TELEGRAM-CMD] /pause received -- PAUSE_SWITCH file created")

        # Report pending orders so user knows what's still live at broker
        pending_info = ""
        if self.status_callback:
            try:
                status = self.status_callback()
                # Extract pending orders line from status
                for line in status.split('\n'):
                    if 'Pending orders' in line:
                        pending_info = f"\n{line}"
                        break
            except Exception:
                pass

        if self.notifier:
            self.notifier.send_message(
                f"[PAUSE] /pause received. Strategy paused.\n"
                f"No new orders will be placed or modified.\n"
                f"Existing pending orders remain live at broker.{pending_info}\n"
                f"Send /resume to resume."
            )

    def _handle_resume(self):
        if os.path.exists(self.pause_switch_file):
            os.remove(self.pause_switch_file)
        logger.info("[TELEGRAM-CMD] /resume received -- PAUSE_SWITCH file removed")
        if self.notifier:
            self.notifier.send_message(
                "[RESUME] /resume received. Strategy resuming.\n"
                "Order placement re-enabled."
            )

    def _handle_status(self):
        is_paused = os.path.exists(self.pause_switch_file)
        is_killed = os.path.exists(self.kill_switch_file)

        if is_killed:
            state = "KILLED"
        elif is_paused:
            state = "PAUSED"
        else:
            state = "ACTIVE"

        extra = ""
        if self.status_callback:
            try:
                extra = self.status_callback()
            except Exception:
                extra = "(status callback error)"

        msg = f"[STATUS] State: {state}\n{extra}" if extra else f"[STATUS] State: {state}"
        if self.notifier:
            self.notifier.send_message(msg)

    def _handle_menu(self):
        if self.notifier:
            self.notifier.send_message(
                "Available commands:\n\n"
                "/status - Current state, positions, R, blocked symbols\n"
                "/pause - Pause order placement (monitoring continues)\n"
                "/resume - Resume order placement\n"
                "/kill - Emergency shutdown (cancels pending orders + stops strategy). Existing positions kept with SL at broker. Requires restart.\n"
                "/menu - Show this list"
            )


# Global instance
_notifier = None

def get_notifier() -> TelegramNotifier:
    """Get global notifier instance"""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier


if __name__ == '__main__':
    # Test notifications
    import os
    os.environ['TELEGRAM_ENABLED'] = 'true'
    
    notifier = TelegramNotifier()
    
    # Test trade entry
    notifier.notify_trade_entry({
        'symbol': 'NIFTY26DEC2418000CE',
        'fill_price': 250.50,
        'quantity': 650,
        'candidate_info': {
            'sl_price': 260.50,
            'actual_R': 6500,
            'lots': 10,
            'sl_points': 10,
        }
    })
    
    # Test trade exit
    notifier.notify_trade_exit({
        'symbol': 'NIFTY26DEC2418000CE',
        'entry_price': 250.50,
        'exit_price': 245.00,
        'realized_pnl': 3575,
        'realized_R': 0.55,
    }, 'PROFIT_TARGET')
    
    # Test daily target
    notifier.notify_daily_target({
        'cumulative_R': 5.2,
        'total_pnl': 33800,
        'closed_positions_today': 6,
        'daily_exit_reason': '+5R_TARGET',
    })
