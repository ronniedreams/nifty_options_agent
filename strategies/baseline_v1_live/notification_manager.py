"""
Notification Manager for Throttling and Deduplication

Prevents notification spam while ensuring critical alerts reach the user.

Features:
- Throttling: Max 1 notification per error type per time window
- Deduplication: Same error logged multiple times, notified once
- Aggregation: Multiple errors within 60s sent as single message
- Resolution tracking: Mark errors as resolved when fixed

Usage:
    notification_manager = NotificationManager(telegram_notifier, state_manager)
    notification_manager.send_error_notification('STARTUP_FAILURE', 'OpenAlgo down', is_critical=False)
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pytz

try:
    from .config import (
        NOTIFICATION_THROTTLE_STARTUP,
        NOTIFICATION_THROTTLE_WEBSOCKET,
        NOTIFICATION_THROTTLE_BROKER,
        NOTIFICATION_THROTTLE_DATABASE,
        NOTIFICATION_AGGREGATION_WINDOW,
    )
except ImportError:
    from config import (
        NOTIFICATION_THROTTLE_STARTUP,
        NOTIFICATION_THROTTLE_WEBSOCKET,
        NOTIFICATION_THROTTLE_BROKER,
        NOTIFICATION_THROTTLE_DATABASE,
        NOTIFICATION_AGGREGATION_WINDOW,
    )

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class NotificationManager:
    """
    Manage error notifications with throttling and deduplication
    """

    # Throttle windows per error type (seconds)
    THROTTLE_WINDOWS = {
        'STARTUP_FAILURE': NOTIFICATION_THROTTLE_STARTUP,
        'WEBSOCKET_AUTH_FAILED': NOTIFICATION_THROTTLE_WEBSOCKET,
        'WEBSOCKET_DOWN': NOTIFICATION_THROTTLE_WEBSOCKET,
        'BROKER_DISCONNECTED': NOTIFICATION_THROTTLE_BROKER,
        'DATABASE_ERROR': NOTIFICATION_THROTTLE_DATABASE,
        'OPENALGO_DOWN': NOTIFICATION_THROTTLE_STARTUP,
        'SYSTEM_RECOVERED': 0,  # Always send recovery notifications
    }

    def __init__(self, telegram_notifier, state_manager):
        """
        Initialize notification manager

        Args:
            telegram_notifier: TelegramNotifier instance
            state_manager: StateManager instance (for database access)
        """
        self.telegram = telegram_notifier
        self.state = state_manager
        self.pending_errors = []  # For aggregation
        self.last_aggregation_time = None

        logger.info("NotificationManager initialized")

    def should_send_notification(self, error_type: str, error_msg: str) -> bool:
        """
        Check if notification should be sent based on throttling rules

        Args:
            error_type: Error type (e.g., 'STARTUP_FAILURE')
            error_msg: Error message

        Returns:
            True if notification should be sent
        """
        cursor = self.state.conn.cursor()

        # Get throttle window for this error type
        throttle_window = self.THROTTLE_WINDOWS.get(error_type, 3600)  # Default 1 hour

        # Check if error exists in log
        cursor.execute('''
            SELECT last_notification_sent, is_resolved
            FROM error_notifications_log
            WHERE error_type = ?
            AND error_message = ?
        ''', (error_type, error_msg))

        row = cursor.fetchone()

        if not row:
            # First time seeing this error - send notification
            return True

        last_notification_sent = row[0]
        is_resolved = row[1]

        # If error was resolved, always send new notification (it's reoccurring)
        if is_resolved:
            return True

        # If no throttle window (always send), return True
        if throttle_window == 0:
            return True

        # Check if enough time has passed since last notification
        if last_notification_sent:
            last_sent_time = datetime.fromisoformat(last_notification_sent)
            now = datetime.now(IST)
            time_since_last = (now - last_sent_time).total_seconds()

            if time_since_last < throttle_window:
                # Still within throttle window - don't send
                return False

        # Outside throttle window - send notification
        return True

    def send_error_notification(self, error_type: str, error_msg: str, is_critical: bool = False):
        """
        Send error notification if throttling allows

        Args:
            error_type: Error type (e.g., 'STARTUP_FAILURE')
            error_msg: Error message
            is_critical: If True, bypass throttling and send immediately
        """
        now = datetime.now(IST)
        cursor = self.state.conn.cursor()

        # Check if notification should be sent
        if not is_critical and not self.should_send_notification(error_type, error_msg):
            # Within throttle window - just update occurrence count
            cursor.execute('''
                UPDATE error_notifications_log
                SET last_occurrence = ?,
                    occurrence_count = occurrence_count + 1
                WHERE error_type = ?
                AND error_message = ?
            ''', (now.isoformat(), error_type, error_msg))

            self.state.conn.commit()

            logger.debug(f"[NOTIFICATION] Throttled: {error_type} (within throttle window)")
            return

        # Check if error already exists in log
        cursor.execute('''
            SELECT id, first_occurrence, occurrence_count, notification_count
            FROM error_notifications_log
            WHERE error_type = ?
            AND error_message = ?
        ''', (error_type, error_msg))

        row = cursor.fetchone()

        if row:
            # Update existing entry
            error_id, first_occurrence, occurrence_count, notification_count = row

            cursor.execute('''
                UPDATE error_notifications_log
                SET last_occurrence = ?,
                    occurrence_count = occurrence_count + 1,
                    last_notification_sent = ?,
                    notification_count = notification_count + 1,
                    is_resolved = 0
                WHERE id = ?
            ''', (now.isoformat(), now.isoformat(), error_id))

            logger.info(f"[NOTIFICATION] Sending throttled notification for {error_type} "
                       f"(occurrence #{occurrence_count + 1}, notification #{notification_count + 1})")
        else:
            # Insert new entry
            cursor.execute('''
                INSERT INTO error_notifications_log
                (error_type, error_message, first_occurrence, last_occurrence,
                 occurrence_count, last_notification_sent, notification_count, is_resolved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (error_type, error_msg, now.isoformat(), now.isoformat(),
                  1, now.isoformat(), 1, 0))

            logger.info(f"[NOTIFICATION] Sending first notification for {error_type}")

        self.state.conn.commit()

        # Format message with emoji prefix
        emoji_map = {
            'STARTUP_FAILURE': 'ALERT',
            'WEBSOCKET_AUTH_FAILED': 'ALERT',
            'WEBSOCKET_DOWN': 'ALERT',
            'BROKER_DISCONNECTED': 'ALERT',
            'DATABASE_ERROR': 'ALERT',
            'OPENALGO_DOWN': 'ALERT',
            'SYSTEM_RECOVERED': 'SUCCESS',
        }

        prefix = emoji_map.get(error_type, 'ALERT')

        # Send to Telegram
        formatted_msg = f"[{prefix}] {error_type}\n\n{error_msg}"
        self.telegram.send_message(formatted_msg, parse_mode=None)  # Plain text, no HTML

    def aggregate_and_send_errors(self):
        """
        Aggregate pending errors and send as single notification

        Called when aggregation window expires.
        """
        if not self.pending_errors:
            return

        now = datetime.now(IST)

        # Group errors by type
        error_counts = {}
        for error_type, error_msg in self.pending_errors:
            if error_type not in error_counts:
                error_counts[error_type] = 0
            error_counts[error_type] += 1

        # Format aggregated message
        lines = ["ALERT: MULTIPLE ERRORS DETECTED\n"]
        for error_type, count in error_counts.items():
            lines.append(f"- {error_type}: {count} occurrence(s)")

        lines.append(f"\nFirst seen: {self.last_aggregation_time.strftime('%H:%M:%S')} IST")
        lines.append("Action: Check system logs and restart if needed.")

        message = "\n".join(lines)

        # Send aggregated notification
        self.telegram.send_message(message, parse_mode=None)

        # Log each error to database
        for error_type, error_msg in self.pending_errors:
            self._log_error_occurrence(error_type, error_msg)

        # Clear pending errors
        self.pending_errors = []
        self.last_aggregation_time = None

        logger.info(f"[NOTIFICATION] Sent aggregated notification for {len(error_counts)} error types")

    def queue_error_for_aggregation(self, error_type: str, error_msg: str):
        """
        Queue error for aggregation

        Args:
            error_type: Error type
            error_msg: Error message
        """
        now = datetime.now(IST)

        # Initialize aggregation window if this is first error
        if self.last_aggregation_time is None:
            self.last_aggregation_time = now

        # Add to pending errors
        self.pending_errors.append((error_type, error_msg))

        # Check if aggregation window expired
        time_since_first = (now - self.last_aggregation_time).total_seconds()
        if time_since_first >= NOTIFICATION_AGGREGATION_WINDOW:
            self.aggregate_and_send_errors()

    def _log_error_occurrence(self, error_type: str, error_msg: str):
        """
        Log error occurrence to database (without sending notification)

        Args:
            error_type: Error type
            error_msg: Error message
        """
        now = datetime.now(IST)
        cursor = self.state.conn.cursor()

        # Check if error exists
        cursor.execute('''
            SELECT id
            FROM error_notifications_log
            WHERE error_type = ?
            AND error_message = ?
        ''', (error_type, error_msg))

        row = cursor.fetchone()

        if row:
            # Update existing entry
            cursor.execute('''
                UPDATE error_notifications_log
                SET last_occurrence = ?,
                    occurrence_count = occurrence_count + 1
                WHERE id = ?
            ''', (now.isoformat(), row[0]))
        else:
            # Insert new entry (no notification sent yet)
            cursor.execute('''
                INSERT INTO error_notifications_log
                (error_type, error_message, first_occurrence, last_occurrence,
                 occurrence_count, last_notification_sent, notification_count, is_resolved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (error_type, error_msg, now.isoformat(), now.isoformat(),
                  1, None, 0, 0))

        self.state.conn.commit()

    def mark_resolved(self, error_type: str, error_msg: Optional[str] = None):
        """
        Mark error as resolved (stop future notifications)

        Args:
            error_type: Error type to mark as resolved
            error_msg: Optional specific error message (if None, mark all of this type)
        """
        now = datetime.now(IST)
        cursor = self.state.conn.cursor()

        if error_msg:
            cursor.execute('''
                UPDATE error_notifications_log
                SET is_resolved = 1,
                    resolved_at = ?
                WHERE error_type = ?
                AND error_message = ?
                AND is_resolved = 0
            ''', (now.isoformat(), error_type, error_msg))
        else:
            cursor.execute('''
                UPDATE error_notifications_log
                SET is_resolved = 1,
                    resolved_at = ?
                WHERE error_type = ?
                AND is_resolved = 0
            ''', (now.isoformat(), error_type))

        rows_updated = cursor.rowcount
        self.state.conn.commit()

        if rows_updated > 0:
            logger.info(f"[NOTIFICATION] Marked {rows_updated} error(s) as resolved: {error_type}")

    def get_error_summary(self) -> Dict:
        """
        Get current error state for dashboard

        Returns:
            Dict with error summary
        """
        cursor = self.state.conn.cursor()

        # Get all unresolved errors
        cursor.execute('''
            SELECT error_type, error_message, first_occurrence, last_occurrence,
                   occurrence_count, notification_count
            FROM error_notifications_log
            WHERE is_resolved = 0
            ORDER BY last_occurrence DESC
        ''')

        rows = cursor.fetchall()

        unresolved_errors = []
        for row in rows:
            unresolved_errors.append({
                'error_type': row[0],
                'error_message': row[1],
                'first_occurrence': row[2],
                'last_occurrence': row[3],
                'occurrence_count': row[4],
                'notification_count': row[5],
            })

        return {
            'unresolved_count': len(unresolved_errors),
            'unresolved_errors': unresolved_errors,
        }


if __name__ == '__main__':
    # Test notification manager
    logging.basicConfig(level=logging.INFO)

    from state_manager import StateManager
    from telegram_notifier import TelegramNotifier

    state = StateManager()
    telegram = TelegramNotifier()

    notification_manager = NotificationManager(telegram, state)

    # Test throttling
    print("\nTest 1: Send same error twice (should throttle second)")
    notification_manager.send_error_notification('STARTUP_FAILURE', 'OpenAlgo down', is_critical=False)
    notification_manager.send_error_notification('STARTUP_FAILURE', 'OpenAlgo down', is_critical=False)

    # Test critical bypass
    print("\nTest 2: Send critical error (bypasses throttle)")
    notification_manager.send_error_notification('DATABASE_ERROR', 'Database locked', is_critical=True)

    # Test resolution
    print("\nTest 3: Mark error as resolved")
    notification_manager.mark_resolved('STARTUP_FAILURE', 'OpenAlgo down')

    # Get summary
    print("\nTest 4: Get error summary")
    summary = notification_manager.get_error_summary()
    print(f"Unresolved errors: {summary['unresolved_count']}")

    state.close()
