"""
Comprehensive tests for Emergency Kill Switch, Circuit Breaker, and Stale Data fixes.

Covers:
- Fix 1A: File-based kill/pause switch
- Fix 1B: DB-based control flags
- Fix 1C: Dashboard button integration
- Fix 1D: Telegram command listener
- Fix 2:  Order churn circuit breaker + stale blocking
- Fix 3:  Telegram alert throttling
- Fix 4:  Delayed resubscription
- Fix 5:  Orderbook API fix (strategy= removed)
- Fix 6:  Angel One auth logging
"""

import os
import sys
import time
import tempfile
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from collections import deque

import pytest
import pytz

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

IST = pytz.timezone('Asia/Kolkata')


# ═══════════════════════════════════════════════════════════════════
# FIX 5: Orderbook API - strategy= removed
# ═══════════════════════════════════════════════════════════════════

class TestOrderbookAPIFix:
    """Verify strategy= kwarg is removed from all orderbook() calls."""

    def test_no_strategy_kwarg_in_order_manager(self):
        """order_manager.py should NOT pass strategy= to orderbook()."""
        import inspect
        from baseline_v1_live.order_manager import OrderManager

        source = inspect.getsource(OrderManager)

        # Find all orderbook() calls
        lines_with_orderbook = [
            line.strip() for line in source.split('\n')
            if 'orderbook(' in line and not line.strip().startswith('#')
        ]

        for line in lines_with_orderbook:
            assert 'strategy=' not in line, (
                f"Found strategy= in orderbook() call: {line}"
            )

    def test_no_strategy_kwarg_in_position_tracker(self):
        """position_tracker.py should NOT pass strategy= to orderbook()."""
        import inspect
        from baseline_v1_live.position_tracker import PositionTracker

        source = inspect.getsource(PositionTracker)

        lines_with_orderbook = [
            line.strip() for line in source.split('\n')
            if 'orderbook(' in line and not line.strip().startswith('#')
        ]

        for line in lines_with_orderbook:
            assert 'strategy=' not in line, (
                f"Found strategy= in orderbook() call: {line}"
            )


# ═══════════════════════════════════════════════════════════════════
# FIX 2: Order Churn Circuit Breaker
# ═══════════════════════════════════════════════════════════════════

class TestOrderChurnDetector:
    """Test OrderChurnDetector class logic."""

    def setup_method(self):
        from baseline_v1_live.order_manager import OrderChurnDetector
        self.detector = OrderChurnDetector(
            window=300, per_symbol_limit=2, global_limit=5
        )

    def test_no_churn_on_first_place(self):
        """First place without prior cancel is not churn."""
        result = self.detector.record_place('NIFTY25700PE')
        assert result == 'ok'

    def test_no_churn_different_symbols(self):
        """Cancel of one symbol + place of different symbol is not churn."""
        self.detector.record_cancel('NIFTY25700PE')
        result = self.detector.record_place('NIFTY25600PE')
        assert result == 'ok'
        assert not self.detector.is_blocked('NIFTY25600PE')

    def test_single_churn_cycle_ok(self):
        """One cancel+place of same symbol within 30s = 1 cycle, still below limit."""
        self.detector.record_cancel('NIFTY25700PE')
        result = self.detector.record_place('NIFTY25700PE')
        assert result == 'ok'  # 1 cycle < limit of 2

    def test_symbol_blocked_after_two_churn_cycles(self):
        """Two cancel+place cycles of same symbol -> symbol blocked."""
        sym = 'NIFTY25700PE'

        self.detector.record_cancel(sym)
        self.detector.record_place(sym)

        self.detector.record_cancel(sym)
        result = self.detector.record_place(sym)

        assert result == 'symbol_blocked'
        assert self.detector.is_blocked(sym)

    def test_unblock_symbol(self):
        """Unblocking a symbol makes it placeable again."""
        sym = 'NIFTY25700PE'

        # Block it
        self.detector.record_cancel(sym)
        self.detector.record_place(sym)
        self.detector.record_cancel(sym)
        self.detector.record_place(sym)
        assert self.detector.is_blocked(sym)

        # Unblock
        self.detector.unblock_symbol(sym)
        assert not self.detector.is_blocked(sym)

    def test_global_limit_triggers_strategy_pause(self):
        """5+ churn cycles across any symbols -> strategy pause."""
        detector = self.detector
        # Use different symbols to avoid per-symbol block first
        symbols = ['SYM_A', 'SYM_B', 'SYM_C', 'SYM_D', 'SYM_E']

        for sym in symbols:
            detector.record_cancel(sym)
            result = detector.record_place(sym)
            # First 4 should be 'ok' (one cycle each, below per-symbol limit)

        # 5th churn cycle should trigger global pause
        assert result == 'strategy_pause'

    def test_is_blocked_check_in_place(self):
        """Blocked symbol should be refused by is_blocked()."""
        sym = 'NIFTY25700PE'
        self.detector.blocked_symbols.add(sym)
        assert self.detector.is_blocked(sym)
        assert not self.detector.is_blocked('NIFTY25600PE')

    def test_churn_window_expiry(self):
        """Churn cycles older than window should not count."""
        detector = self.detector
        sym = 'NIFTY25700PE'

        # Manually inject old events
        old_time = time.time() - 400  # 400s ago, outside 300s window
        detector.cancel_events[sym] = deque([old_time])
        detector._churn_cycle_log.append((old_time, sym))

        # New cancel+place should not count as 2nd cycle
        detector.record_cancel(sym)
        result = detector.record_place(sym)
        assert result == 'ok'  # Only 1 cycle in window

    def test_no_churn_place_without_recent_cancel(self):
        """Place without a cancel within 30s of it is not churn."""
        sym = 'NIFTY25700PE'
        # Cancel happened > 30s ago
        detector = self.detector
        old_time = time.time() - 35
        detector.cancel_events[sym] = deque([old_time])

        result = detector.record_place(sym)
        assert result == 'ok'


class TestChurnIntegrationWithOrderManager:
    """Test churn detector wired into OrderManager.manage_limit_order_for_type()."""

    def setup_method(self):
        from baseline_v1_live.order_manager import OrderManager
        self.manager = OrderManager.__new__(OrderManager)
        self.manager.client = MagicMock()
        self.manager.pending_limit_orders = {}
        self.manager.active_sl_orders = {}
        self.manager.filled_orders = []
        self.manager.last_orderbook_check = None
        self.manager.sl_placement_failures = 0
        self.manager.consecutive_sl_failures = 0
        self.manager.emergency_exit_triggered = False
        self.manager._on_order_placed_callback = None

        from baseline_v1_live.order_manager import OrderChurnDetector
        self.manager.churn_detector = OrderChurnDetector()

    def test_blocked_symbol_skipped_in_place(self):
        """A churn-blocked symbol should return None from _place_broker_stop_limit_order."""
        sym = 'NIFTY25700PE'
        self.manager.churn_detector.blocked_symbols.add(sym)

        result = self.manager._place_broker_stop_limit_order(sym, 150.0, 147.0, 650)
        assert result is None

    def test_cancel_records_churn_event(self):
        """Case 1 cancel should record churn event."""
        sym = 'NIFTY25700PE'
        self.manager.pending_limit_orders['PE'] = {
            'order_id': 'ORD123',
            'symbol': sym,
            'trigger_price': 150.0,
            'limit_price': 147.0,
            'quantity': 650,
            'status': 'pending',
            'placed_at': datetime.now(IST),
            'candidate_info': {}
        }

        self.manager.client.cancelorder.return_value = {'status': 'success'}

        result = self.manager.manage_limit_order_for_type('PE', None, None)
        assert result == 'cancelled'
        # Should have recorded the cancel event
        assert sym in self.manager.churn_detector.cancel_events


# ═══════════════════════════════════════════════════════════════════
# FIX 1A: File-based Kill/Pause Switch
# ═══════════════════════════════════════════════════════════════════

class TestFileBasedKillSwitch:
    """Test KILL_SWITCH and PAUSE_SWITCH file detection."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.kill_file = os.path.join(self.tmpdir, 'KILL_SWITCH')
        self.pause_file = os.path.join(self.tmpdir, 'PAUSE_SWITCH')

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_config_paths_exist(self):
        """KILL_SWITCH_FILE and PAUSE_SWITCH_FILE should be defined in config."""
        from baseline_v1_live.config import KILL_SWITCH_FILE, PAUSE_SWITCH_FILE
        assert KILL_SWITCH_FILE is not None
        assert PAUSE_SWITCH_FILE is not None
        assert 'KILL_SWITCH' in KILL_SWITCH_FILE
        assert 'PAUSE_SWITCH' in PAUSE_SWITCH_FILE

    def test_kill_file_detection(self):
        """Creating KILL_SWITCH file should be detectable."""
        assert not os.path.exists(self.kill_file)
        with open(self.kill_file, 'w') as f:
            f.write('test')
        assert os.path.exists(self.kill_file)

    def test_pause_file_creation_and_removal(self):
        """PAUSE_SWITCH file can be created and removed."""
        with open(self.pause_file, 'w') as f:
            f.write('test')
        assert os.path.exists(self.pause_file)
        os.remove(self.pause_file)
        assert not os.path.exists(self.pause_file)

    def test_kill_switch_in_baseline(self):
        """BaselineV1Live should import KILL_SWITCH_FILE and PAUSE_SWITCH_FILE."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live

        source = inspect.getsource(BaselineV1Live)
        assert 'KILL_SWITCH_FILE' in source
        assert 'PAUSE_SWITCH_FILE' in source
        assert '_emergency_kill_shutdown' in source
        assert '_is_paused' in source


class TestEmergencyKillShutdown:
    """Test _emergency_kill_shutdown method."""

    def test_kill_shutdown_cancels_both_types(self):
        """_emergency_kill_shutdown should cancel both CE and PE orders."""
        from baseline_v1_live.baseline_v1_live import BaselineV1Live

        # Create a minimal mock instance
        instance = BaselineV1Live.__new__(BaselineV1Live)
        instance.order_manager = MagicMock()
        instance.telegram = MagicMock()
        instance.shutdown_requested = False
        instance.state_manager = MagicMock()
        instance.position_tracker = MagicMock()
        instance.position_tracker.get_all_positions.return_value = []
        instance.position_tracker.get_position_summary.return_value = {
            'cumulative_R': 0, 'total_pnl': 0
        }
        instance.continuous_filter = MagicMock()

        # Mock save_state to avoid full pipeline
        instance.save_state = MagicMock()

        instance._emergency_kill_shutdown()

        # Should cancel CE and PE
        calls = instance.order_manager.manage_limit_order_for_type.call_args_list
        assert len(calls) == 2
        assert calls[0].args == ('CE', None, None)
        assert calls[1].args == ('PE', None, None)

        # Should set shutdown_requested
        assert instance.shutdown_requested is True

        # Should send Telegram
        instance.telegram.send_message.assert_called()


class TestPauseSkipsOrderPlacement:
    """Test that _is_paused skips order placement but keeps monitoring."""

    def test_process_tick_paused_skips_orders(self):
        """When _is_paused=True, process_tick should skip order steps."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live

        source = inspect.getsource(BaselineV1Live.process_tick)
        # Check that the pause guard exists before order triggers
        assert '_is_paused' in source
        assert 'Skipping order placement steps' in source


# ═══════════════════════════════════════════════════════════════════
# FIX 1B: DB-based Control Flags
# ═══════════════════════════════════════════════════════════════════

class TestDBControlFlags:
    """Test state_manager control flag methods."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, 'test_state.db')
        os.environ['STATE_DB_PATH'] = self.db_path

    def teardown_method(self):
        if hasattr(self, '_sm'):
            try:
                self._sm.close()
            except Exception:
                pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if 'STATE_DB_PATH' in os.environ:
            del os.environ['STATE_DB_PATH']

    def _get_state_manager(self):
        from baseline_v1_live.state_manager import StateManager
        self._sm = StateManager(db_path=self.db_path)
        return self._sm

    def test_migration_adds_columns(self):
        """Migration 7 should add pause_requested and kill_requested columns."""
        sm = self._get_state_manager()
        cursor = sm.conn.cursor()
        cursor.execute("PRAGMA table_info(operational_state)")
        columns = {col[1] for col in cursor.fetchall()}
        assert 'pause_requested' in columns
        assert 'kill_requested' in columns

    def test_get_control_flags_default(self):
        """Default control flags should be 0."""
        sm = self._get_state_manager()
        flags = sm.get_control_flags()
        assert flags['pause_requested'] == 0
        assert flags['kill_requested'] == 0

    def test_set_and_get_pause_flag(self):
        """Setting pause flag should persist."""
        sm = self._get_state_manager()
        sm.set_control_flag('pause_requested', 1)
        flags = sm.get_control_flags()
        assert flags['pause_requested'] == 1

    def test_set_and_get_kill_flag(self):
        """Setting kill flag should persist."""
        sm = self._get_state_manager()
        sm.set_control_flag('kill_requested', 1)
        flags = sm.get_control_flags()
        assert flags['kill_requested'] == 1

    def test_clear_flags(self):
        """Clearing flags should work."""
        sm = self._get_state_manager()
        sm.set_control_flag('pause_requested', 1)
        sm.set_control_flag('pause_requested', 0)
        flags = sm.get_control_flags()
        assert flags['pause_requested'] == 0

    def test_invalid_flag_name_rejected(self):
        """Invalid flag names should be rejected."""
        sm = self._get_state_manager()
        # Should not raise, just log error
        sm.set_control_flag('invalid_flag', 1)
        # Verify it didn't corrupt the table
        flags = sm.get_control_flags()
        assert flags['pause_requested'] == 0


# ═══════════════════════════════════════════════════════════════════
# FIX 1C: Dashboard Button Integration
# ═══════════════════════════════════════════════════════════════════

def _import_dashboard_db():
    """Import monitor_dashboard/db.py with sys.path shimmed for bare 'config' import."""
    dashboard_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'baseline_v1_live', 'monitor_dashboard'
    )
    inserted = False
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)
        inserted = True
    # Force reimport if already cached with wrong config
    if 'baseline_v1_live.monitor_dashboard.db' in sys.modules:
        del sys.modules['baseline_v1_live.monitor_dashboard.db']
    from baseline_v1_live.monitor_dashboard import db as db_mod
    if inserted:
        sys.path.remove(dashboard_dir)
    return db_mod


class TestDashboardDB:
    """Test monitor_dashboard/db.py control flag functions."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, 'test_state.db')

        # Create the database with required schema
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE operational_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_state TEXT NOT NULL DEFAULT 'STARTING',
                state_entered_at TIMESTAMP,
                last_check_at TIMESTAMP,
                error_reason TEXT,
                updated_at TIMESTAMP,
                pause_requested INTEGER DEFAULT 0,
                kill_requested INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            INSERT INTO operational_state (id, current_state, state_entered_at, updated_at)
            VALUES (1, 'ACTIVE', datetime('now'), datetime('now'))
        ''')
        conn.commit()
        conn.close()

        self.db_mod = _import_dashboard_db()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_control_flag_creates_pause_file(self):
        """write_control_flag('pause_requested', 1) should create PAUSE_SWITCH file."""
        db_mod = self.db_mod
        orig_path = db_mod.STATE_DB_PATH
        orig_pause = db_mod.PAUSE_SWITCH_FILE
        orig_kill = db_mod.KILL_SWITCH_FILE

        try:
            db_mod.STATE_DB_PATH = self.db_path
            db_mod.PAUSE_SWITCH_FILE = os.path.join(self.tmpdir, 'PAUSE_SWITCH')
            db_mod.KILL_SWITCH_FILE = os.path.join(self.tmpdir, 'KILL_SWITCH')

            db_mod.write_control_flag('pause_requested', 1)
            assert os.path.exists(db_mod.PAUSE_SWITCH_FILE)

            # Check DB updated
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT pause_requested FROM operational_state WHERE id = 1")
            assert cursor.fetchone()[0] == 1
            conn.close()
        finally:
            db_mod.STATE_DB_PATH = orig_path
            db_mod.PAUSE_SWITCH_FILE = orig_pause
            db_mod.KILL_SWITCH_FILE = orig_kill

    def test_write_control_flag_removes_pause_file(self):
        """write_control_flag('pause_requested', 0) should remove PAUSE_SWITCH file."""
        db_mod = self.db_mod
        orig_path = db_mod.STATE_DB_PATH
        orig_pause = db_mod.PAUSE_SWITCH_FILE

        try:
            db_mod.STATE_DB_PATH = self.db_path
            db_mod.PAUSE_SWITCH_FILE = os.path.join(self.tmpdir, 'PAUSE_SWITCH')

            # Create the file first
            with open(db_mod.PAUSE_SWITCH_FILE, 'w') as f:
                f.write('test')

            db_mod.write_control_flag('pause_requested', 0)
            assert not os.path.exists(db_mod.PAUSE_SWITCH_FILE)
        finally:
            db_mod.STATE_DB_PATH = orig_path
            db_mod.PAUSE_SWITCH_FILE = orig_pause

    def test_write_control_flag_creates_kill_file(self):
        """write_control_flag('kill_requested', 1) should create KILL_SWITCH file."""
        db_mod = self.db_mod
        orig_path = db_mod.STATE_DB_PATH
        orig_kill = db_mod.KILL_SWITCH_FILE

        try:
            db_mod.STATE_DB_PATH = self.db_path
            db_mod.KILL_SWITCH_FILE = os.path.join(self.tmpdir, 'KILL_SWITCH')

            db_mod.write_control_flag('kill_requested', 1)
            assert os.path.exists(db_mod.KILL_SWITCH_FILE)
        finally:
            db_mod.STATE_DB_PATH = orig_path
            db_mod.KILL_SWITCH_FILE = orig_kill

    def test_get_control_flags(self):
        """get_control_flags() should return current flags."""
        db_mod = self.db_mod
        orig_path = db_mod.STATE_DB_PATH

        try:
            db_mod.STATE_DB_PATH = self.db_path
            flags = db_mod.get_control_flags()
            assert flags['pause_requested'] == 0
            assert flags['kill_requested'] == 0
        finally:
            db_mod.STATE_DB_PATH = orig_path

    def test_invalid_flag_ignored(self):
        """Invalid flag name should not raise."""
        db_mod = self.db_mod
        orig_path = db_mod.STATE_DB_PATH

        try:
            db_mod.STATE_DB_PATH = self.db_path
            # Should just return without error
            db_mod.write_control_flag('invalid', 1)
        finally:
            db_mod.STATE_DB_PATH = orig_path


# ═══════════════════════════════════════════════════════════════════
# FIX 1D: Telegram Command Listener
# ═══════════════════════════════════════════════════════════════════

class TestTelegramCommandListener:
    """Test TelegramCommandListener command handling."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        from baseline_v1_live.telegram_notifier import TelegramCommandListener
        self.notifier = MagicMock()
        self.listener = TelegramCommandListener(
            bot_token='test_token',
            chat_id='12345',
            state_dir=self.tmpdir,
            notifier=self.notifier,
            status_callback=lambda: "Positions: 2\nPending orders: CE: SYM1 @ 150, PE: None"
        )

    def teardown_method(self):
        if hasattr(self, 'listener'):
            self.listener.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_kill_creates_file(self):
        """_handle_kill should create KILL_SWITCH file."""
        self.listener._handle_kill()
        assert os.path.exists(self.listener.kill_switch_file)
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        assert 'pending entry orders will be cancelled' in msg
        assert 'Strategy will stop' in msg

    def test_pause_creates_file(self):
        """_handle_pause should create PAUSE_SWITCH file."""
        self.listener._handle_pause()
        assert os.path.exists(self.listener.pause_switch_file)
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        assert 'paused' in msg.lower()

    def test_pause_includes_pending_orders(self):
        """_handle_pause should include pending orders in the message."""
        self.listener._handle_pause()
        msg = self.notifier.send_message.call_args[0][0]
        assert 'Pending orders' in msg

    def test_resume_removes_file(self):
        """_handle_resume should remove PAUSE_SWITCH file."""
        # Create pause file first
        with open(self.listener.pause_switch_file, 'w') as f:
            f.write('test')

        self.listener._handle_resume()
        assert not os.path.exists(self.listener.pause_switch_file)
        self.notifier.send_message.assert_called_once()

    def test_resume_without_file_is_safe(self):
        """_handle_resume when no file exists should not raise."""
        self.listener._handle_resume()
        # Should still send message
        self.notifier.send_message.assert_called_once()

    def test_status_reports_active(self):
        """_handle_status should report ACTIVE when no switch files."""
        self.listener._handle_status()
        msg = self.notifier.send_message.call_args[0][0]
        assert 'ACTIVE' in msg
        assert 'Positions: 2' in msg

    def test_status_reports_paused(self):
        """_handle_status should report PAUSED when pause file exists."""
        with open(self.listener.pause_switch_file, 'w') as f:
            f.write('test')

        self.listener._handle_status()
        msg = self.notifier.send_message.call_args[0][0]
        assert 'PAUSED' in msg

    def test_status_reports_killed(self):
        """_handle_status should report KILLED when kill file exists."""
        with open(self.listener.kill_switch_file, 'w') as f:
            f.write('test')

        self.listener._handle_status()
        msg = self.notifier.send_message.call_args[0][0]
        assert 'KILLED' in msg

    def test_menu_lists_all_commands(self):
        """_handle_menu should list all available commands."""
        self.listener._handle_menu()
        msg = self.notifier.send_message.call_args[0][0]
        assert '/status' in msg
        assert '/pause' in msg
        assert '/resume' in msg
        assert '/kill' in msg
        assert '/menu' in msg

    def test_unauthorized_chat_ignored(self):
        """Messages from wrong chat_id should be ignored."""
        # Simulate an update from wrong chat
        with patch('requests.get') as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    'ok': True,
                    'result': [{
                        'update_id': 1,
                        'message': {
                            'chat': {'id': 99999},  # wrong chat_id
                            'text': '/kill'
                        }
                    }]
                }
            )
            self.listener._process_updates()

        # Kill file should NOT be created
        assert not os.path.exists(self.listener.kill_switch_file)

    def test_authorized_chat_processed(self):
        """Messages from correct chat_id should be processed."""
        with patch('requests.get') as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    'ok': True,
                    'result': [{
                        'update_id': 1,
                        'message': {
                            'chat': {'id': 12345},  # correct chat_id
                            'text': '/status'
                        }
                    }]
                }
            )
            self.listener._process_updates()

        # Should have sent status message
        self.notifier.send_message.assert_called()

    def test_start_creates_daemon_thread(self):
        """start() should create a daemon polling thread."""
        self.listener.start()
        assert self.listener._running is True
        assert self.listener._thread is not None
        assert self.listener._thread.daemon is True
        self.listener.stop()

    def test_stop_sets_flag(self):
        """stop() should set _running to False."""
        self.listener.start()
        self.listener.stop()
        assert self.listener._running is False


# ═══════════════════════════════════════════════════════════════════
# FIX 3: Telegram Alert Throttling
# ═══════════════════════════════════════════════════════════════════

class TestStaleAlertThrottling:
    """Test Telegram alert throttling for stale-symbol alerts."""

    def test_throttle_dicts_initialized(self):
        """BaselineV1Live should have throttle tracking dicts."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live
        source = inspect.getsource(BaselineV1Live.__init__)
        assert '_last_stale_telegram' in source
        assert '_stale_suppress_count' in source

    def test_stale_blocked_symbols_initialized(self):
        """BaselineV1Live should have _stale_blocked_symbols set."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live
        source = inspect.getsource(BaselineV1Live.__init__)
        assert '_stale_blocked_symbols' in source

    def test_stale_block_in_process_tick(self):
        """process_tick should add symbols to _stale_blocked_symbols on hard cancel."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live
        source = inspect.getsource(BaselineV1Live.process_tick)
        assert '_stale_blocked_symbols.add' in source

    def test_stale_unblock_in_process_tick(self):
        """process_tick should unblock symbols when bars resume."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live
        source = inspect.getsource(BaselineV1Live.process_tick)
        assert 'STALE-UNBLOCK' in source
        assert '_stale_blocked_symbols.discard' in source

    def test_blocked_filter_in_process_tick(self):
        """process_tick should filter blocked symbols from best_strikes."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live
        source = inspect.getsource(BaselineV1Live.process_tick)
        assert 'BLOCKED-FILTER' in source


# ═══════════════════════════════════════════════════════════════════
# FIX 4: Delayed Resubscription
# ═══════════════════════════════════════════════════════════════════

class TestDelayedResubscription:
    """Test delayed and batch resubscription in data_pipeline."""

    def test_resubscribe_symbol_uses_delay(self):
        """resubscribe_symbol should use a 3-second delayed thread."""
        import inspect
        from baseline_v1_live.data_pipeline import DataPipeline
        source = inspect.getsource(DataPipeline.resubscribe_symbol)
        assert 'sleep(3)' in source
        assert 'Thread' in source or 'threading' in source

    def test_resubscribe_symbols_batch_exists(self):
        """DataPipeline should have resubscribe_symbols_batch method."""
        from baseline_v1_live.data_pipeline import DataPipeline
        assert hasattr(DataPipeline, 'resubscribe_symbols_batch')

    def test_batch_resub_immediate(self):
        """resubscribe_symbols_batch should be immediate (no delay)."""
        import inspect
        from baseline_v1_live.data_pipeline import DataPipeline
        source = inspect.getsource(DataPipeline.resubscribe_symbols_batch)
        assert 'sleep' not in source

    def test_heartbeat_resub_in_baseline(self):
        """Heartbeat block should call resubscribe_symbols_batch for pending order symbols."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live
        source = inspect.getsource(BaselineV1Live.run_trading_loop)
        assert 'resubscribe_symbols_batch' in source

    def test_resubscribe_disconnected_is_safe(self):
        """resubscribe_symbol on disconnected pipeline should not raise."""
        from baseline_v1_live.data_pipeline import DataPipeline
        pipeline = DataPipeline.__new__(DataPipeline)
        pipeline.is_connected = False
        # Should just log warning, not raise
        pipeline.resubscribe_symbol('NIFTY25700PE')

    def test_batch_resub_empty_is_safe(self):
        """resubscribe_symbols_batch with empty list should not raise."""
        from baseline_v1_live.data_pipeline import DataPipeline
        pipeline = DataPipeline.__new__(DataPipeline)
        pipeline.is_connected = True
        pipeline.resubscribe_symbols_batch([])


# ═══════════════════════════════════════════════════════════════════
# FIX 6: Angel One Auth Logging
# ═══════════════════════════════════════════════════════════════════

class TestAngelOneAuthLogging:
    """Test improved Angel One WebSocket auth logging."""

    def test_connect_angelone_logs_return_value(self):
        """connect_angelone_backup should log the actual return value on failure."""
        import inspect
        from baseline_v1_live.data_pipeline import DataPipeline
        source = inspect.getsource(DataPipeline.connect_angelone_backup)
        assert 'return value' in source.lower() or 'return value' in source

    def test_connect_angelone_exc_info(self):
        """connect_angelone_backup should log full traceback on exception."""
        import inspect
        from baseline_v1_live.data_pipeline import DataPipeline
        source = inspect.getsource(DataPipeline.connect_angelone_backup)
        assert 'exc_info=True' in source


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION: End-to-end scenarios
# ═══════════════════════════════════════════════════════════════════

class TestChurnDetectorEdgeCases:
    """Advanced churn detector edge case tests."""

    def test_per_symbol_block_does_not_affect_other_symbols(self):
        """Blocking one symbol should not block others."""
        from baseline_v1_live.order_manager import OrderChurnDetector
        d = OrderChurnDetector()

        # Block SYM_A
        d.record_cancel('SYM_A')
        d.record_place('SYM_A')
        d.record_cancel('SYM_A')
        d.record_place('SYM_A')
        assert d.is_blocked('SYM_A')

        # SYM_B should be fine
        assert not d.is_blocked('SYM_B')
        result = d.record_place('SYM_B')
        assert result == 'ok'

    def test_multiple_rapid_churn_different_symbols_global_limit(self):
        """Global limit should trigger even if each symbol has only 1 cycle."""
        from baseline_v1_live.order_manager import OrderChurnDetector
        d = OrderChurnDetector(global_limit=3)

        for i, sym in enumerate(['A', 'B', 'C']):
            d.record_cancel(sym)
            result = d.record_place(sym)

        assert result == 'strategy_pause'

    def test_unblock_idempotent(self):
        """Unblocking already-unblocked symbol should not raise."""
        from baseline_v1_live.order_manager import OrderChurnDetector
        d = OrderChurnDetector()
        d.unblock_symbol('NONEXISTENT')  # Should not raise


class TestKillPauseIntegration:
    """Integration tests for kill/pause with DB + file coordination."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, 'test_state.db')

        # Create DB
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE operational_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_state TEXT NOT NULL DEFAULT 'STARTING',
                state_entered_at TIMESTAMP,
                last_check_at TIMESTAMP,
                error_reason TEXT,
                updated_at TIMESTAMP,
                pause_requested INTEGER DEFAULT 0,
                kill_requested INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            INSERT INTO operational_state (id, current_state, state_entered_at, updated_at)
            VALUES (1, 'ACTIVE', datetime('now'), datetime('now'))
        ''')
        conn.commit()
        conn.close()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dashboard_pause_creates_file_and_updates_db(self):
        """Dashboard pause button should create file AND update DB."""
        db_mod = _import_dashboard_db()

        orig_path = db_mod.STATE_DB_PATH
        orig_pause = db_mod.PAUSE_SWITCH_FILE
        try:
            db_mod.STATE_DB_PATH = self.db_path
            db_mod.PAUSE_SWITCH_FILE = os.path.join(self.tmpdir, 'PAUSE_SWITCH')

            db_mod.write_control_flag('pause_requested', 1)

            # File created
            assert os.path.exists(db_mod.PAUSE_SWITCH_FILE)

            # DB updated
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT pause_requested FROM operational_state WHERE id = 1")
            assert cursor.fetchone()[0] == 1
            conn.close()
        finally:
            db_mod.STATE_DB_PATH = orig_path
            db_mod.PAUSE_SWITCH_FILE = orig_pause

    def test_telegram_kill_and_dashboard_both_create_file(self):
        """Both Telegram /kill and dashboard kill should create same file."""
        from baseline_v1_live.telegram_notifier import TelegramCommandListener

        kill_file = os.path.join(self.tmpdir, 'KILL_SWITCH')

        listener = TelegramCommandListener(
            bot_token='test', chat_id='123',
            state_dir=self.tmpdir,
            notifier=MagicMock()
        )
        listener._handle_kill()
        assert os.path.exists(kill_file)

        # Clean up and test dashboard path
        os.remove(kill_file)

        db_mod = _import_dashboard_db()
        orig_path = db_mod.STATE_DB_PATH
        orig_kill = db_mod.KILL_SWITCH_FILE
        try:
            db_mod.STATE_DB_PATH = self.db_path
            db_mod.KILL_SWITCH_FILE = kill_file

            db_mod.write_control_flag('kill_requested', 1)
            assert os.path.exists(kill_file)
        finally:
            db_mod.STATE_DB_PATH = orig_path
            db_mod.KILL_SWITCH_FILE = orig_kill


class TestCircuitBreakerWithStaleBlocking:
    """Test that stale-blocked symbols and churn-blocked symbols both filter best_strikes."""

    def test_blocked_filter_code_structure(self):
        """process_tick should combine stale and churn blocked sets."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live
        source = inspect.getsource(BaselineV1Live.process_tick)
        # Should combine both sets
        assert '_stale_blocked_symbols' in source
        assert 'churn_detector.blocked_symbols' in source

    def test_churn_strategy_pause_creates_pause_file(self):
        """Churn strategy_pause result should create PAUSE_SWITCH file."""
        import inspect
        from baseline_v1_live.baseline_v1_live import BaselineV1Live
        source = inspect.getsource(BaselineV1Live.process_tick)
        assert "strategy_pause" in source
        assert "PAUSE_SWITCH_FILE" in source


# ═══════════════════════════════════════════════════════════════════
# STRUCTURAL TESTS: Verify code patterns
# ═══════════════════════════════════════════════════════════════════

class TestCodeStructure:
    """Verify critical code patterns are in place."""

    def test_order_manager_has_churn_detector(self):
        """OrderManager.__init__ should create a churn_detector."""
        import inspect
        from baseline_v1_live.order_manager import OrderManager
        source = inspect.getsource(OrderManager.__init__)
        assert 'churn_detector' in source
        assert 'OrderChurnDetector' in source

    def test_baseline_imports_kill_switch_config(self):
        """baseline_v1_live should import KILL_SWITCH_FILE and PAUSE_SWITCH_FILE."""
        import inspect
        from baseline_v1_live import baseline_v1_live as mod
        source = inspect.getsource(mod)
        assert 'KILL_SWITCH_FILE' in source
        assert 'PAUSE_SWITCH_FILE' in source

    def test_baseline_imports_telegram_command_listener(self):
        """baseline_v1_live should import TelegramCommandListener."""
        import inspect
        from baseline_v1_live import baseline_v1_live as mod
        source = inspect.getsource(mod)
        assert 'TelegramCommandListener' in source

    def test_db_module_has_write_control_flag(self):
        """monitor_dashboard/db.py should have write_control_flag function."""
        db_mod = _import_dashboard_db()
        assert callable(db_mod.write_control_flag)

    def test_db_module_has_get_control_flags(self):
        """monitor_dashboard/db.py should have get_control_flags function."""
        db_mod = _import_dashboard_db()
        assert callable(db_mod.get_control_flags)

    def test_state_manager_has_control_methods(self):
        """StateManager should have set_control_flag and get_control_flags."""
        from baseline_v1_live.state_manager import StateManager
        assert hasattr(StateManager, 'set_control_flag')
        assert hasattr(StateManager, 'get_control_flags')

    def test_data_pipeline_has_batch_resub(self):
        """DataPipeline should have resubscribe_symbols_batch method."""
        from baseline_v1_live.data_pipeline import DataPipeline
        assert hasattr(DataPipeline, 'resubscribe_symbols_batch')

    def test_no_emojis_in_logger_calls(self):
        """New code in order_manager and baseline_v1_live should not use emojis in logger calls."""
        import re
        from baseline_v1_live.order_manager import OrderChurnDetector
        import inspect
        source = inspect.getsource(OrderChurnDetector)

        # Check for common emoji unicode ranges
        emoji_pattern = re.compile(
            "[\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"   # symbols & pictographs
            "\U0001F680-\U0001F6FF"   # transport & map
            "\U0001F1E0-\U0001F1FF"   # flags
            "\U00002702-\U000027B0"   # dingbats
            "\U0001F900-\U0001F9FF"   # supplemental
            "\U00002600-\U000026FF"   # misc
            "]+", flags=re.UNICODE
        )
        # Only check logger lines
        for line in source.split('\n'):
            if 'logger.' in line:
                assert not emoji_pattern.search(line), (
                    f"Emoji found in logger call: {line.strip()}"
                )


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
