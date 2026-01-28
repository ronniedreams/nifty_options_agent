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
    
    def __init__(self):
        self.enabled = TELEGRAM_ENABLED
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        
        if self.enabled:
            if not self.bot_token or not self.chat_id:
                logger.warning("Telegram enabled but token/chat_id not configured")
                self.enabled = False
            else:
                logger.info("Telegram notifications enabled")
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

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        payload = {
            'chat_id': self.chat_id,
            'text': message,
        }

        # Only add parse_mode if specified
        if parse_mode:
            payload['parse_mode'] = parse_mode
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.debug("Telegram message sent successfully")
                return True
            else:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
    
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

        message = f"""
{emoji} <b>BEST {option_type} {action}</b>

Symbol: <code>{symbol}</code>
Entry: ₹{entry_price:.2f}
Current: ₹{current_price:.2f}
SL: ₹{sl_price:.2f} ({sl_percent:.1%})

VWAP Premium: {vwap_premium:.1%}
Lots: {lots} ({lots * 65} qty)
Risk: ₹{actual_R:,.0f} (1R)

Time: {datetime.now(IST).strftime('%H:%M:%S')}
        """

        self.send_message(message.strip())


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
