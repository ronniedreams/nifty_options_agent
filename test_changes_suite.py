"""
Comprehensive test suite for the 5 recent changes:

1. order_manager.py  - Duplicate order prevention (cancel-before-place, MODIFICATION_THRESHOLD)
2. position_tracker.py - Alert throttling for orphan/qty-mismatch alerts
3. baseline_v1_live.py - Async run_trading_loop + swing notification callback
4. config.py          - MODIFICATION_THRESHOLD and EXIT_LIMIT_BUFFER_PERCENT constants
5. telegram_notifier.py - Instance tagging + notify_swing_detected method

All broker API calls, Telegram HTTP calls, and environment variables are mocked.
No network access required.
"""

import sys
import os
import asyncio
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Path setup: make baseline_v1_live importable as a package AND allow direct
# imports of individual modules for isolated unit tests.
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.join(REPO_ROOT, "baseline_v1_live")
sys.path.insert(0, REPO_ROOT)
# NOTE: Do NOT add PKG_DIR to sys.path — it shadows the baseline_v1_live package
# with the baseline_v1_live.py module inside it, breaking relative imports.

import pytz
IST = pytz.timezone("Asia/Kolkata")


# ===========================================================================
# SECTION 1 : config.py  -- new constants exist and have correct values
# ===========================================================================

class TestConfigNewConstants:
    """Change 4: config.py added MODIFICATION_THRESHOLD and EXIT_LIMIT_BUFFER_PERCENT."""

    def _load_config(self):
        """Import config fresh; env vars already set by conftest-like patches below."""
        # Reload to pick up any env changes
        import importlib
        import baseline_v1_live.config as cfg
        importlib.reload(cfg)
        return cfg

    @patch.dict(os.environ, {"PAPER_TRADING": "true"}, clear=False)
    def test_modification_threshold_exists(self):
        cfg = self._load_config()
        assert hasattr(cfg, "MODIFICATION_THRESHOLD"), (
            "MODIFICATION_THRESHOLD must be defined in config.py"
        )

    @patch.dict(os.environ, {"PAPER_TRADING": "true"}, clear=False)
    def test_modification_threshold_value(self):
        cfg = self._load_config()
        assert cfg.MODIFICATION_THRESHOLD == 1.00, (
            f"MODIFICATION_THRESHOLD should be 1.00, got {cfg.MODIFICATION_THRESHOLD}"
        )

    @patch.dict(os.environ, {"PAPER_TRADING": "true"}, clear=False)
    def test_exit_limit_buffer_percent_exists(self):
        cfg = self._load_config()
        assert hasattr(cfg, "EXIT_LIMIT_BUFFER_PERCENT"), (
            "EXIT_LIMIT_BUFFER_PERCENT must be defined in config.py"
        )

    @patch.dict(os.environ, {"PAPER_TRADING": "true"}, clear=False)
    def test_exit_limit_buffer_percent_value(self):
        cfg = self._load_config()
        assert cfg.EXIT_LIMIT_BUFFER_PERCENT == 0.05, (
            f"EXIT_LIMIT_BUFFER_PERCENT should be 0.05, got {cfg.EXIT_LIMIT_BUFFER_PERCENT}"
        )

    @patch.dict(os.environ, {"PAPER_TRADING": "true"}, clear=False)
    def test_modification_threshold_is_float(self):
        cfg = self._load_config()
        assert isinstance(cfg.MODIFICATION_THRESHOLD, float), (
            "MODIFICATION_THRESHOLD should be a float"
        )

    @patch.dict(os.environ, {"PAPER_TRADING": "true"}, clear=False)
    def test_exit_limit_buffer_percent_is_float(self):
        cfg = self._load_config()
        assert isinstance(cfg.EXIT_LIMIT_BUFFER_PERCENT, float), (
            "EXIT_LIMIT_BUFFER_PERCENT should be a float"
        )


# ===========================================================================
# SECTION 2 : telegram_notifier.py  -- instance tagging + swing notification
# ===========================================================================

class TestTelegramNotifierInstanceTagging:
    """Change 5a: __init__ accepts instance_name; send_message prefixes [tag]."""

    def _make_notifier(self, instance_name=None, enabled=False):
        """Create a TelegramNotifier with mocked HTTP layer."""
        with patch.dict(os.environ, {
            "TELEGRAM_ENABLED": str(enabled).lower(),
            "TELEGRAM_BOT_TOKEN": "test_token_123",
            "TELEGRAM_CHAT_ID": "999",
            "PAPER_TRADING": "true",
        }, clear=False):
            # Patch config values directly
            with patch("baseline_v1_live.telegram_notifier.TELEGRAM_ENABLED", enabled), \
                 patch("baseline_v1_live.telegram_notifier.TELEGRAM_BOT_TOKEN", "test_token_123"), \
                 patch("baseline_v1_live.telegram_notifier.TELEGRAM_CHAT_ID", "999"):
                from baseline_v1_live.telegram_notifier import TelegramNotifier
                n = TelegramNotifier(instance_name=instance_name)
                return n

    def test_instance_name_explicit(self):
        """Explicitly passed instance_name is stored."""
        n = self._make_notifier(instance_name="MY_LOCAL")
        assert n.instance_name == "MY_LOCAL"

    def test_instance_name_from_env(self):
        """When instance_name is None, falls back to INSTANCE_NAME env var."""
        with patch.dict(os.environ, {"INSTANCE_NAME": "EC2_PROD"}, clear=False):
            n = self._make_notifier(instance_name=None)
            assert n.instance_name == "EC2_PROD"

    def test_instance_name_default_unknown(self):
        """When env var is also missing, defaults to UNKNOWN."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INSTANCE_NAME", None)
            n = self._make_notifier(instance_name=None)
            assert n.instance_name == "UNKNOWN"

    @patch("baseline_v1_live.telegram_notifier.requests.post")
    def test_send_message_prefixes_known_instance(self, mock_post):
        """Messages are prefixed with [instance_name] when name != UNKNOWN."""
        mock_post.return_value = MagicMock(status_code=200)
        n = self._make_notifier(instance_name="LOCAL", enabled=True)
        n.send_message("hello")
        # Verify the payload text starts with the tag
        call_kwargs = mock_post.call_args[1]  # keyword args to requests.post
        sent_text = call_kwargs["json"]["text"]
        assert sent_text.startswith("[LOCAL] "), (
            f"Expected message to start with '[LOCAL] ', got: {sent_text!r}"
        )

    @patch("baseline_v1_live.telegram_notifier.requests.post")
    def test_send_message_no_prefix_when_unknown(self, mock_post):
        """When instance_name is UNKNOWN, no prefix is added."""
        mock_post.return_value = MagicMock(status_code=200)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INSTANCE_NAME", None)
            n = self._make_notifier(instance_name=None, enabled=True)
            # Force UNKNOWN
            n.instance_name = "UNKNOWN"
            n.send_message("bare message")
            sent_text = mock_post.call_args[1]["json"]["text"]
            assert sent_text == "bare message", (
                f"Expected no prefix for UNKNOWN, got: {sent_text!r}"
            )

    def test_send_message_disabled_returns_false(self):
        """Disabled notifier returns False without network call."""
        n = self._make_notifier(instance_name="X", enabled=False)
        n.enabled = False  # explicitly ensure disabled
        result = n.send_message("should not send")
        assert result is False


class TestTelegramNotifierSwingDetected:
    """Change 5b: notify_swing_detected method produces correct message body."""

    def _make_enabled_notifier(self, instance_name="TEST"):
        with patch("baseline_v1_live.telegram_notifier.TELEGRAM_ENABLED", True), \
             patch("baseline_v1_live.telegram_notifier.TELEGRAM_BOT_TOKEN", "tok"), \
             patch("baseline_v1_live.telegram_notifier.TELEGRAM_CHAT_ID", "123"):
            from baseline_v1_live.telegram_notifier import TelegramNotifier
            n = TelegramNotifier(instance_name=instance_name)
            n.enabled = True
            return n

    @patch("baseline_v1_live.telegram_notifier.requests.post")
    def test_swing_low_message_contains_swing_low(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        n = self._make_enabled_notifier()
        swing_info = {
            "type": "Low",
            "price": 125.50,
            "timestamp": datetime(2026, 2, 5, 10, 30, tzinfo=IST),
            "vwap": 120.00,
            "option_type": "CE",
        }
        n.notify_swing_detected("NIFTY06FEB2626000CE", swing_info)
        sent_text = mock_post.call_args[1]["json"]["text"]
        assert "SWING LOW" in sent_text
        assert "125.50" in sent_text
        assert "NIFTY06FEB2626000CE" in sent_text

    @patch("baseline_v1_live.telegram_notifier.requests.post")
    def test_swing_high_message_contains_swing_high(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        n = self._make_enabled_notifier()
        swing_info = {
            "type": "High",
            "price": 200.00,
            "timestamp": datetime(2026, 2, 5, 11, 0, tzinfo=IST),
            "vwap": 190.00,
            "option_type": "PE",
        }
        n.notify_swing_detected("NIFTY06FEB2625500PE", swing_info)
        sent_text = mock_post.call_args[1]["json"]["text"]
        assert "SWING HIGH" in sent_text
        assert "200.00" in sent_text

    @patch("baseline_v1_live.telegram_notifier.requests.post")
    def test_swing_vwap_premium_calculated(self, mock_post):
        """VWAP premium = (price - vwap) / vwap * 100 should appear in message."""
        mock_post.return_value = MagicMock(status_code=200)
        n = self._make_enabled_notifier()
        # price=132, vwap=120 => premium = 10.0%
        swing_info = {
            "type": "Low",
            "price": 132.00,
            "timestamp": datetime(2026, 2, 5, 10, 0, tzinfo=IST),
            "vwap": 120.00,
        }
        n.notify_swing_detected("NIFTY06FEB2626000CE", swing_info)
        sent_text = mock_post.call_args[1]["json"]["text"]
        assert "10.0" in sent_text, (
            f"Expected VWAP premium '10.0' in message, got: {sent_text}"
        )

    @patch("baseline_v1_live.telegram_notifier.requests.post")
    def test_swing_time_formatted_when_datetime(self, mock_post):
        """Swing time from datetime is formatted as HH:MM."""
        mock_post.return_value = MagicMock(status_code=200)
        n = self._make_enabled_notifier()
        swing_time = datetime(2026, 2, 5, 14, 25, tzinfo=IST)
        swing_info = {
            "type": "Low",
            "price": 100.00,
            "timestamp": swing_time,
            "vwap": 95.00,
        }
        n.notify_swing_detected("NIFTY06FEB2626000CE", swing_info)
        sent_text = mock_post.call_args[1]["json"]["text"]
        assert "14:25" in sent_text, (
            f"Expected swing time '14:25' in message, got: {sent_text}"
        )

    @patch("baseline_v1_live.telegram_notifier.requests.post")
    def test_swing_time_fallback_when_none(self, mock_post):
        """When timestamp is None, current time is used -- message still sends."""
        mock_post.return_value = MagicMock(status_code=200)
        n = self._make_enabled_notifier()
        swing_info = {
            "type": "Low",
            "price": 100.00,
            "timestamp": None,
            "vwap": 95.00,
        }
        # Should not raise
        n.notify_swing_detected("NIFTY06FEB2626000CE", swing_info)
        assert mock_post.called

    @patch("baseline_v1_live.telegram_notifier.requests.post")
    def test_swing_vwap_zero_premium_zero(self, mock_post):
        """When vwap is 0, premium should be 0 (no division by zero)."""
        mock_post.return_value = MagicMock(status_code=200)
        n = self._make_enabled_notifier()
        swing_info = {
            "type": "Low",
            "price": 100.00,
            "timestamp": datetime(2026, 2, 5, 10, 0, tzinfo=IST),
            "vwap": 0,
        }
        # Must not raise ZeroDivisionError
        n.notify_swing_detected("NIFTY06FEB2626000CE", swing_info)
        sent_text = mock_post.call_args[1]["json"]["text"]
        assert "0.0%" in sent_text or "0.0" in sent_text


# ===========================================================================
# SECTION 3 : order_manager.py  -- cancel-before-place guard + threshold
# ===========================================================================

def _make_order_manager(dry_run=True):
    """
    Build an OrderManager with a fully mocked broker client.
    dry_run=True avoids real API calls by default.
    Sets module-level DRY_RUN directly so it persists after this function returns.
    """
    import baseline_v1_live.order_manager as om_mod
    om_mod.DRY_RUN = dry_run
    om_mod.MODIFICATION_THRESHOLD = 1.00
    from baseline_v1_live.order_manager import OrderManager
    mock_client = MagicMock()
    om = OrderManager(client=mock_client)
    om.client = mock_client
    return om, mock_client


def _candidate(symbol="NIFTY06FEB2626000CE", swing_low=130.00, tick_size=0.05):
    """Helper: build a minimal candidate dict."""
    return {
        "symbol": symbol,
        "swing_low": swing_low,
        "tick_size": tick_size,
        "quantity": 650,
        "sl_price": swing_low + 10,
        "actual_R": 6500,
        "option_type": "CE",
        "lots": 10,
    }


class TestOrderManagerCase3SymbolSwitch:
    """Change 1a: Case 3 -- cancel-before-place on symbol switch."""

    def test_symbol_switch_cancel_fails_keeps_existing(self):
        """If cancel returns False, do NOT place new order; return 'kept'."""
        om, client = _make_order_manager(dry_run=False)
        # Pre-populate an existing CE order
        om.pending_limit_orders["CE"] = {
            "order_id": "OLD_111",
            "symbol": "NIFTY06FEB2626000CE",
            "trigger_price": 129.95,
            "limit_price": 126.95,
            "quantity": 650,
            "status": "pending",
            "placed_at": datetime.now(IST),
            "candidate_info": _candidate("NIFTY06FEB2626000CE"),
        }
        # Cancel will fail
        client.cancelorder.return_value = {"status": "error", "message": "already triggered"}

        # New candidate with DIFFERENT symbol
        new_cand = _candidate(symbol="NIFTY06FEB2626100CE", swing_low=140.00)
        result = om.manage_limit_order_for_type("CE", new_cand, 137.00)

        assert result == "kept", (
            f"Expected 'kept' when cancel fails on symbol switch, got '{result}'"
        )
        # New order must NOT have been placed
        client.placeorder.assert_not_called()
        # Existing order still in pending
        assert om.pending_limit_orders["CE"]["order_id"] == "OLD_111"

    def test_symbol_switch_cancel_succeeds_places_new(self):
        """If cancel succeeds, new order is placed for the new symbol."""
        om, client = _make_order_manager(dry_run=False)
        om.pending_limit_orders["CE"] = {
            "order_id": "OLD_222",
            "symbol": "NIFTY06FEB2626000CE",
            "trigger_price": 129.95,
            "limit_price": 126.95,
            "quantity": 650,
            "status": "pending",
            "placed_at": datetime.now(IST),
            "candidate_info": _candidate("NIFTY06FEB2626000CE"),
        }
        client.cancelorder.return_value = {"status": "success"}
        client.orderbook.return_value = {
            "status": "success",
            "data": {"orders": [{"orderid": "OLD_222", "order_status": "cancelled"}]}
        }
        client.placeorder.return_value = {"status": "success", "orderid": "NEW_333"}

        with patch('time.sleep'):
            new_cand = _candidate(symbol="NIFTY06FEB2626100CE", swing_low=140.00)
            result = om.manage_limit_order_for_type("CE", new_cand, 137.00)

        assert result == "modified", (
            f"Expected 'modified' on successful symbol switch, got '{result}'"
        )
        assert om.pending_limit_orders["CE"]["symbol"] == "NIFTY06FEB2626100CE"
        assert om.pending_limit_orders["CE"]["order_id"] == "NEW_333"


class TestOrderManagerCase4PriceChange:
    """Change 1b: Case 4 -- MODIFICATION_THRESHOLD (0.50) + cancel-before-place."""

    def test_price_change_below_threshold_keeps_order(self):
        """Price diff < 0.50 => no modification, return 'kept'."""
        om, client = _make_order_manager(dry_run=False)
        existing_trigger = 129.95
        existing_limit = 126.95
        om.pending_limit_orders["CE"] = {
            "order_id": "EXIST_1",
            "symbol": "NIFTY06FEB2626000CE",
            "trigger_price": existing_trigger,
            "limit_price": existing_limit,
            "quantity": 650,
            "status": "pending",
            "placed_at": datetime.now(IST),
            "candidate_info": _candidate("NIFTY06FEB2626000CE", swing_low=130.00),
        }
        # Same symbol, swing_low chosen so trigger/limit diffs are < 0.50
        # trigger = swing_low - 0.05; limit = trigger - 3
        # We need swing_low such that new trigger is within 0.49 of 129.95
        # new trigger = 130.00 - 0.05 = 129.95  (diff = 0.00) -- exact match
        cand = _candidate("NIFTY06FEB2626000CE", swing_low=130.00)
        result = om.manage_limit_order_for_type("CE", cand, 126.95)

        assert result == "kept"
        client.cancelorder.assert_not_called()
        client.placeorder.assert_not_called()

    def test_price_change_above_threshold_cancel_fails_keeps(self):
        """Price diff > 0.50 but cancel fails => return 'kept', no new order."""
        om, client = _make_order_manager(dry_run=False)
        om.pending_limit_orders["CE"] = {
            "order_id": "EXIST_2",
            "symbol": "NIFTY06FEB2626000CE",
            "trigger_price": 129.95,
            "limit_price": 126.95,
            "quantity": 650,
            "status": "pending",
            "placed_at": datetime.now(IST),
            "candidate_info": _candidate("NIFTY06FEB2626000CE", swing_low=130.00),
        }
        client.cancelorder.return_value = {"status": "error"}

        # swing_low = 132 => trigger = 131.95 (diff from 129.95 = 2.00 > 0.50)
        cand = _candidate("NIFTY06FEB2626000CE", swing_low=132.00)
        result = om.manage_limit_order_for_type("CE", cand, 128.95)

        assert result == "kept"
        client.placeorder.assert_not_called()

    def test_price_change_above_threshold_cancel_succeeds_modifies(self):
        """Price diff > 1.00 and cancel succeeds => new order placed, return 'modified'."""
        om, client = _make_order_manager(dry_run=False)
        om.pending_limit_orders["CE"] = {
            "order_id": "EXIST_3",
            "symbol": "NIFTY06FEB2626000CE",
            "trigger_price": 129.95,
            "limit_price": 126.95,
            "quantity": 650,
            "status": "pending",
            "placed_at": datetime.now(IST),
            "candidate_info": _candidate("NIFTY06FEB2626000CE", swing_low=130.00),
        }
        client.cancelorder.return_value = {"status": "success"}
        client.orderbook.return_value = {
            "status": "success",
            "data": {"orders": [{"orderid": "EXIST_3", "order_status": "cancelled"}]}
        }
        client.placeorder.return_value = {"status": "success", "orderid": "MOD_44"}

        # swing_low = 132 => trigger = 131.95, limit = 128.95
        with patch('time.sleep'):
            cand = _candidate("NIFTY06FEB2626000CE", swing_low=132.00)
            result = om.manage_limit_order_for_type("CE", cand, 128.95)

        assert result == "modified"
        assert om.pending_limit_orders["CE"]["order_id"] == "MOD_44"
        assert om.pending_limit_orders["CE"]["trigger_price"] == 131.95

    def test_modification_threshold_boundary_exactly_at_threshold(self):
        """Price diff == exactly 1.00 should NOT trigger modification (> not >=)."""
        om, client = _make_order_manager(dry_run=False)
        # Set existing trigger so that new trigger is exactly 1.00 away
        # existing trigger = 130.00; new swing_low = 131.05 => new trigger = 131.00
        # diff = 1.00 -- the code uses >, so 1.00 is NOT > 1.00
        om.pending_limit_orders["CE"] = {
            "order_id": "BOUNDARY",
            "symbol": "NIFTY06FEB2626000CE",
            "trigger_price": 130.00,
            "limit_price": 127.00,
            "quantity": 650,
            "status": "pending",
            "placed_at": datetime.now(IST),
            "candidate_info": _candidate("NIFTY06FEB2626000CE", swing_low=130.05),
        }
        # new swing_low = 131.05 => trigger = 131.00 (diff = 1.00 exactly)
        # new limit = 131.00 - 3 = 128.00 (diff from 127.00 = 1.00 exactly)
        cand = _candidate("NIFTY06FEB2626000CE", swing_low=131.05)
        result = om.manage_limit_order_for_type("CE", cand, 128.00)

        assert result == "kept", (
            f"At exactly threshold boundary (1.00), should keep. Got '{result}'"
        )
        client.cancelorder.assert_not_called()


class TestOrderManagerDryRun:
    """Verify DRY_RUN path works for the new flow (no broker calls)."""

    def test_dry_run_place_new_order(self):
        om, client = _make_order_manager(dry_run=True)
        cand = _candidate("NIFTY06FEB2626000CE", swing_low=130.00)
        result = om.manage_limit_order_for_type("CE", cand, 127.00)
        assert result == "placed"
        assert "CE" in om.pending_limit_orders
        # No real broker call
        client.placeorder.assert_not_called()

    def test_dry_run_cancel_existing(self):
        om, client = _make_order_manager(dry_run=True)
        om.pending_limit_orders["PE"] = {
            "order_id": "DRY_OLD",
            "symbol": "NIFTY06FEB2625500PE",
            "trigger_price": 100.0,
            "limit_price": 97.0,
            "quantity": 650,
            "status": "pending",
            "placed_at": datetime.now(IST),
            "candidate_info": {},
        }
        result = om.manage_limit_order_for_type("PE", None, None)
        assert result == "cancelled"
        assert "PE" not in om.pending_limit_orders


class TestOrderManagerInvalidOptionType:
    """Passing a symbol string instead of CE/PE should assert."""

    def test_invalid_option_type_raises(self):
        om, _ = _make_order_manager(dry_run=True)
        cand = _candidate()
        with pytest.raises(AssertionError):
            om.manage_limit_order_for_type("NIFTY06FEB2626000CE", cand, 127.0)


# ===========================================================================
# SECTION 4 : position_tracker.py  -- alert throttling
# ===========================================================================

def _make_position_tracker():
    """Build PositionTracker with mocked broker client and telegram."""
    mock_client = MagicMock()
    mock_telegram = MagicMock()

    with patch("baseline_v1_live.position_tracker.DRY_RUN", False), \
         patch("baseline_v1_live.position_tracker.TELEGRAM_AVAILABLE", True), \
         patch("baseline_v1_live.position_tracker.get_notifier", return_value=mock_telegram):
        from baseline_v1_live.position_tracker import PositionTracker
        pt = PositionTracker(client=mock_client)
        pt.telegram = mock_telegram
        return pt, mock_client, mock_telegram


class TestPositionTrackerThrottleSetsInit:
    """Change 2a: __init__ creates the two throttle sets."""

    def test_alerted_orphaned_positions_is_set(self):
        pt, _, _ = _make_position_tracker()
        assert isinstance(pt._alerted_orphaned_positions, set)
        assert len(pt._alerted_orphaned_positions) == 0

    def test_alerted_qty_mismatches_is_set(self):
        pt, _, _ = _make_position_tracker()
        assert isinstance(pt._alerted_qty_mismatches, set)
        assert len(pt._alerted_qty_mismatches) == 0


class TestPositionTrackerThrottleResetOnNewDay:
    """Change 2b: reset_for_new_day clears both throttle sets."""

    def test_reset_clears_orphaned_set(self):
        pt, _, _ = _make_position_tracker()
        pt._alerted_orphaned_positions.add("NIFTY06FEB2626000CE")
        pt._alerted_orphaned_positions.add("NIFTY06FEB2625500PE")
        pt.reset_for_new_day()
        assert len(pt._alerted_orphaned_positions) == 0

    def test_reset_clears_qty_mismatch_set(self):
        pt, _, _ = _make_position_tracker()
        pt._alerted_qty_mismatches.add("NIFTY06FEB2626000CE:650:325")
        pt.reset_for_new_day()
        assert len(pt._alerted_qty_mismatches) == 0

    def test_reset_also_clears_positions(self):
        """Ensure reset_for_new_day still performs its other duties."""
        pt, _, _ = _make_position_tracker()
        pt.daily_exit_triggered = True
        pt.daily_exit_reason = "+5R_TARGET"
        pt.reset_for_new_day()
        assert pt.daily_exit_triggered is False
        assert pt.daily_exit_reason is None


class TestPositionTrackerOrphanedAlertThrottle:
    """Change 2c: orphaned position alert fires once per symbol per day."""

    def _setup_broker_with_orphan(self, pt, mock_client, orphan_symbol="NIFTY06FEB2626000CE"):
        """Configure broker to return one orphaned position not in tracker."""
        mock_client.positionbook.return_value = {
            "status": "success",
            "data": [
                {
                    "symbol": orphan_symbol,
                    "quantity": 650,
                    "averageprice": 150.00,
                }
            ],
        }

    def test_first_orphan_alert_sends_telegram(self):
        pt, client, telegram = _make_position_tracker()
        self._setup_broker_with_orphan(pt, client)
        pt.reconcile_with_broker()
        # Should have sent an alert
        assert telegram.send_message.called
        # Symbol now in throttle set
        assert "NIFTY06FEB2626000CE" in pt._alerted_orphaned_positions

    def test_second_orphan_alert_suppressed(self):
        pt, client, telegram = _make_position_tracker()
        self._setup_broker_with_orphan(pt, client)
        # First call
        pt.reconcile_with_broker()
        telegram.reset_mock()
        # Second call -- same orphan still present
        pt.reconcile_with_broker()
        # Telegram should NOT have been called again for this symbol
        # (the critical log still fires, but send_message should not)
        for call in telegram.send_message.call_args_list:
            msg = call[0][0] if call[0] else ""
            assert "ORPHANED POSITION ALERT" not in msg, (
                "Orphaned alert should be throttled on second reconcile"
            )

    def test_new_day_reset_allows_alert_again(self):
        pt, client, telegram = _make_position_tracker()
        self._setup_broker_with_orphan(pt, client)
        pt.reconcile_with_broker()
        telegram.reset_mock()

        # Simulate new day
        pt.reset_for_new_day()

        # Orphan still present at broker
        pt.reconcile_with_broker()
        # Alert should fire again
        assert telegram.send_message.called


class TestPositionTrackerQtyMismatchThrottle:
    """Change 2d: qty mismatch alert fires once per unique mismatch."""

    def _setup_tracked_and_broker(self, pt, mock_client, tracked_qty=650, broker_qty=325):
        """Set up one tracked position and one broker position with mismatched qty."""
        symbol = "NIFTY06FEB2626000CE"
        # Add tracked position
        from baseline_v1_live.position_tracker import Position
        pt.open_positions[symbol] = Position(
            symbol=symbol,
            entry_price=150.0,
            sl_price=160.0,
            quantity=tracked_qty,
            actual_R=6500,
            entry_time=datetime.now(IST),
            candidate_info={"option_type": "CE", "strike": 26000, "lots": 10},
        )
        mock_client.positionbook.return_value = {
            "status": "success",
            "data": [
                {
                    "symbol": symbol,
                    "quantity": broker_qty,
                    "averageprice": 150.00,
                }
            ],
        }

    def test_first_qty_mismatch_sends_alert(self):
        pt, client, telegram = _make_position_tracker()
        self._setup_tracked_and_broker(pt, client, tracked_qty=650, broker_qty=325)
        pt.reconcile_with_broker()
        # Check that send_message was called with mismatch info
        mismatch_alerted = False
        for call in telegram.send_message.call_args_list:
            msg = call[0][0] if call[0] else ""
            if "mismatch" in msg.lower() or "Quantity mismatch" in msg:
                mismatch_alerted = True
        assert mismatch_alerted, "First qty mismatch should trigger an alert"
        assert "NIFTY06FEB2626000CE:650:325" in pt._alerted_qty_mismatches

    def test_repeated_same_mismatch_suppressed(self):
        pt, client, telegram = _make_position_tracker()
        self._setup_tracked_and_broker(pt, client, tracked_qty=650, broker_qty=325)
        pt.reconcile_with_broker()
        telegram.reset_mock()
        # Same mismatch again
        pt.reconcile_with_broker()
        for call in telegram.send_message.call_args_list:
            msg = call[0][0] if call[0] else ""
            assert "Quantity mismatch" not in msg, (
                "Repeated identical mismatch should be suppressed"
            )

    def test_different_mismatch_sends_new_alert(self):
        """If broker qty changes to a new value, that is a NEW mismatch key."""
        pt, client, telegram = _make_position_tracker()
        self._setup_tracked_and_broker(pt, client, tracked_qty=650, broker_qty=325)
        pt.reconcile_with_broker()
        telegram.reset_mock()

        # Now broker reports yet another qty
        client.positionbook.return_value = {
            "status": "success",
            "data": [{"symbol": "NIFTY06FEB2626000CE", "quantity": 130, "averageprice": 150.00}],
        }
        pt.reconcile_with_broker()
        # New key "...:650:130" should trigger alert
        new_key = "NIFTY06FEB2626000CE:650:130"
        assert new_key in pt._alerted_qty_mismatches


# ===========================================================================
# SECTION 5 : baseline_v1_live.py  -- async loop + swing notification callback
# ===========================================================================

class TestBaselineV1LiveAsyncLoop:
    """Change 3a: run_trading_loop is async; uses await asyncio.sleep."""

    def test_run_trading_loop_is_coroutine(self):
        """Verify the method is actually a coroutine function."""
        import inspect
        # We need to import without triggering all the heavy __init__ side effects.
        # Read the source and check with ast or just inspect after a lightweight import.
        # Safest: read source file and check for 'async def run_trading_loop'.
        src_path = os.path.join(PKG_DIR, "baseline_v1_live.py")
        with open(src_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "async def run_trading_loop(self):" in source, (
            "run_trading_loop must be declared as 'async def'"
        )

    def test_asyncio_import_present(self):
        """Verify 'import asyncio' is present in baseline_v1_live.py."""
        src_path = os.path.join(PKG_DIR, "baseline_v1_live.py")
        with open(src_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "import asyncio" in source, (
            "'import asyncio' must be present in baseline_v1_live.py"
        )

    def test_await_asyncio_sleep_replaces_time_sleep_in_loop(self):
        """Inside run_trading_loop, time.sleep must be replaced with await asyncio.sleep."""
        src_path = os.path.join(PKG_DIR, "baseline_v1_live.py")
        with open(src_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Find the run_trading_loop method boundaries
        in_method = False
        method_lines = []
        indent_level = None
        for line in lines:
            stripped = line.rstrip()
            if "async def run_trading_loop(self):" in stripped:
                in_method = True
                indent_level = len(line) - len(line.lstrip())
                method_lines.append(stripped)
                continue
            if in_method:
                # Method ends when we hit another def/class at same or lesser indent
                current_indent = len(line) - len(line.lstrip()) if line.strip() else indent_level + 1
                if line.strip() and current_indent <= indent_level and (
                    line.strip().startswith("def ") or line.strip().startswith("class ") or
                    line.strip().startswith("async def ")
                ):
                    break
                method_lines.append(stripped)

        method_body = "\n".join(method_lines)
        # Should contain await asyncio.sleep
        assert "await asyncio.sleep" in method_body, (
            "run_trading_loop must use 'await asyncio.sleep' instead of 'time.sleep'"
        )
        # Should NOT contain bare time.sleep (inside the method)
        # Note: there may be time.sleep OUTSIDE this method (e.g., in start()), that is fine
        assert "time.sleep" not in method_body, (
            "run_trading_loop must not contain 'time.sleep' -- use 'await asyncio.sleep'"
        )


class TestBaselineV1LiveSwingCallback:
    """Change 3b: _on_swing_detected calls self.telegram.notify_swing_detected."""

    def test_on_swing_detected_calls_notify(self):
        """Source-level verification: notify_swing_detected call exists (active or disabled)."""
        src_path = os.path.join(PKG_DIR, "baseline_v1_live.py")
        with open(src_path, "r", encoding="utf-8") as f:
            source = f.read()

        # Check that the notification call exists (active or commented out)
        # Swing notifications are disabled to reduce Telegram spam, but method still exists
        has_active_call = "self.telegram.notify_swing_detected(symbol, swing_info)" in source
        has_disabled_call = "#     self.telegram.notify_swing_detected(symbol, swing_info)" in source

        assert has_active_call or has_disabled_call, (
            "_on_swing_detected must have notify_swing_detected call (active or disabled)"
        )

    def test_on_swing_detected_also_calls_continuous_filter(self):
        """The original add_swing_candidate call must still be present."""
        src_path = os.path.join(PKG_DIR, "baseline_v1_live.py")
        with open(src_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "self.continuous_filter.add_swing_candidate(symbol, swing_info)" in source


class TestBaselineV1LiveHandleDailySummaryBug:
    """
    Previously documented bug: handle_daily_exit used 'summary' before assignment.
    This bug has been FIXED — summary is now assigned before notify_daily_target.
    This test verifies the fix is in place.
    """

    def test_handle_daily_exit_summary_assigned_before_use(self):
        """
        Verify that in handle_daily_exit:
            summary = self.position_tracker.get_position_summary()  <-- assigned first
            ...
            self.telegram.notify_daily_target(summary)              <-- used after

        Previously this was reversed (NameError at runtime). Now fixed.
        """
        src_path = os.path.join(PKG_DIR, "baseline_v1_live.py")
        with open(src_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Find handle_daily_exit method
        in_method = False
        notify_line = None
        assign_line = None
        for i, line in enumerate(lines):
            if "def handle_daily_exit(self" in line:
                in_method = True
                continue
            if in_method:
                if line.strip().startswith("def ") or line.strip().startswith("class "):
                    break
                if "self.telegram.notify_daily_target(summary)" in line and notify_line is None:
                    notify_line = i
                if "summary = self.position_tracker.get_position_summary()" in line and assign_line is None:
                    assign_line = i

        # Both lines must exist
        assert notify_line is not None, "notify_daily_target(summary) call not found"
        assert assign_line is not None, "summary = ...get_position_summary() not found"
        # Fix verified: assignment comes BEFORE notify
        assert assign_line < notify_line, (
            f"REGRESSION: summary must be assigned (line {assign_line+1}) "
            f"before notify_daily_target(summary) (line {notify_line+1}). "
            f"This was a known bug that was fixed — do not revert."
        )


# ===========================================================================
# SECTION 6 : MODIFICATION_THRESHOLD import in order_manager
# ===========================================================================

class TestOrderManagerImportsModificationThreshold:
    """Change 1 prerequisite: MODIFICATION_THRESHOLD is imported from config."""

    def test_modification_threshold_imported(self):
        src_path = os.path.join(PKG_DIR, "order_manager.py")
        with open(src_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "MODIFICATION_THRESHOLD" in source, (
            "MODIFICATION_THRESHOLD must be imported in order_manager.py"
        )
        # Verify it is in the from .config import block
        assert "MODIFICATION_THRESHOLD," in source or "MODIFICATION_THRESHOLD\n" in source


# ===========================================================================
# SECTION 7 : Integration-style tests -- end-to-end candidate lifecycle
# ===========================================================================

class TestOrderManagerFullLifecycle:
    """Full lifecycle: place -> keep (below threshold) -> modify (above) -> cancel."""

    def test_place_then_keep_then_modify_then_cancel(self):
        om, client = _make_order_manager(dry_run=False)
        client.cancelorder.return_value = {"status": "success"}
        client.placeorder.return_value = {"status": "success", "orderid": "LC_001"}

        # Step 1: Place new order (no existing)
        cand = _candidate("NIFTY06FEB2626000CE", swing_low=130.00)
        r1 = om.manage_limit_order_for_type("CE", cand, 127.00)
        assert r1 == "placed"

        # Step 2: Same symbol, same price -> keep
        r2 = om.manage_limit_order_for_type("CE", cand, 127.00)
        assert r2 == "kept"

        # Step 3: Same symbol, big price change -> modify
        client.placeorder.return_value = {"status": "success", "orderid": "LC_002"}
        client.orderbook.return_value = {
            "status": "success",
            "data": {"orders": [{"orderid": "LC_001", "order_status": "cancelled"}]}
        }
        cand_new = _candidate("NIFTY06FEB2626000CE", swing_low=135.00)
        with patch('time.sleep'):
            r3 = om.manage_limit_order_for_type("CE", cand_new, 132.00)
        assert r3 == "modified"
        assert om.pending_limit_orders["CE"]["order_id"] == "LC_002"

        # Step 4: Cancel
        client.orderbook.return_value = {
            "status": "success",
            "data": {"orders": [{"orderid": "LC_002", "order_status": "cancelled"}]}
        }
        with patch('time.sleep'):
            r4 = om.manage_limit_order_for_type("CE", None, None)
        assert r4 == "cancelled"
        assert "CE" not in om.pending_limit_orders

    def test_pe_and_ce_independent(self):
        """CE and PE orders are tracked independently."""
        om, client = _make_order_manager(dry_run=True)

        ce_cand = _candidate("NIFTY06FEB2626000CE", swing_low=130.00)
        pe_cand = _candidate("NIFTY06FEB2625500PE", swing_low=120.00)
        pe_cand["option_type"] = "PE"

        om.manage_limit_order_for_type("CE", ce_cand, 127.00)
        om.manage_limit_order_for_type("PE", pe_cand, 117.00)

        assert "CE" in om.pending_limit_orders
        assert "PE" in om.pending_limit_orders
        assert om.pending_limit_orders["CE"]["symbol"] != om.pending_limit_orders["PE"]["symbol"]

        # Cancel CE -- PE should remain
        om.manage_limit_order_for_type("CE", None, None)
        assert "CE" not in om.pending_limit_orders
        assert "PE" in om.pending_limit_orders


# ===========================================================================
# SECTION 8 : Edge cases and safety
# ===========================================================================

class TestOrderManagerPlacementFailure:
    """If broker placeorder returns error, method returns 'failed'."""

    def test_new_order_placement_fails_returns_failed(self):
        om, client = _make_order_manager(dry_run=False)
        # All placeorder attempts fail
        client.placeorder.return_value = {"status": "error", "message": "margin"}
        cand = _candidate("NIFTY06FEB2626000CE", swing_low=130.00)
        result = om.manage_limit_order_for_type("CE", cand, 127.00)
        assert result == "failed"
        assert "CE" not in om.pending_limit_orders

    def test_switch_cancel_ok_but_new_place_fails(self):
        """Cancel succeeds but new placement fails -> 'failed', old order removed."""
        om, client = _make_order_manager(dry_run=False)
        om.pending_limit_orders["CE"] = {
            "order_id": "OLD_X",
            "symbol": "NIFTY06FEB2626000CE",
            "trigger_price": 129.95,
            "limit_price": 126.95,
            "quantity": 650,
            "status": "pending",
            "placed_at": datetime.now(IST),
            "candidate_info": _candidate("NIFTY06FEB2626000CE"),
        }
        client.cancelorder.return_value = {"status": "success"}
        client.orderbook.return_value = {
            "status": "success",
            "data": {"orders": [{"orderid": "OLD_X", "order_status": "cancelled"}]}
        }
        client.placeorder.return_value = {"status": "error", "message": "bad symbol"}

        with patch('time.sleep'):
            new_cand = _candidate("NIFTY06FEB2626100CE", swing_low=140.00)
            result = om.manage_limit_order_for_type("CE", new_cand, 137.00)
        assert result == "failed"


class TestOrderManagerDeprecatedMethods:
    """Deprecated methods raise RuntimeError to prevent accidental use."""

    def test_place_limit_order_raises(self):
        om, _ = _make_order_manager(dry_run=True)
        with pytest.raises(RuntimeError, match="DEPRECATED"):
            om.place_limit_order("SYM", 100.0, 650, {})

    def test_update_limit_order_for_candidate_raises(self):
        om, _ = _make_order_manager(dry_run=True)
        with pytest.raises(RuntimeError, match="DEPRECATED"):
            om.update_limit_order_for_candidate({}, 100.0)


# ===========================================================================
# ENTRY POINT
# ===========================================================================

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
