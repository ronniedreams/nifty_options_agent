"""
Tests for Angel One failover integration in DataPipeline.

Covers:
1. Config imports for new failover variables
2. DataPipeline init state vars
3. Zerodha callback: always updates last_zerodha_tick_time, only calls _process_tick on zerodha source
4. Angel One callback: only calls _process_tick on angelone source
5. _failover_to_angelone: sets state correctly
6. _failback_to_zerodha: restores state correctly
7. _trigger_failover_or_reconnect when Angel One is available
8. _trigger_failover_or_reconnect when Angel One is NOT available
9. No double failover (idempotent)
10. No double failback (idempotent)
11. disconnect() disconnects both clients
12. Tick isolation: Zerodha ticks update last_zerodha_tick_time but NOT last_tick_time when on Angel One
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call
from datetime import datetime

import pytz

# Ensure the project root is on the path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

IST = pytz.timezone('Asia/Kolkata')


# ---------------------------------------------------------------------------
# 1. Import test
# ---------------------------------------------------------------------------

class TestConfigImports(unittest.TestCase):
    """Test that config exports the new failover variables."""

    def test_new_config_vars_exist(self):
        from baseline_v1_live.config import (
            ANGELONE_OPENALGO_API_KEY,
            ANGELONE_HOST,
            ANGELONE_WS_URL,
            FAILOVER_NO_TICK_THRESHOLD,
            FAILOVER_SWITCHBACK_THRESHOLD,
        )
        # ANGELONE_OPENALGO_API_KEY may be empty string (not configured) - that is fine
        self.assertIsInstance(ANGELONE_OPENALGO_API_KEY, str)
        self.assertIsInstance(ANGELONE_HOST, str)
        self.assertIsInstance(ANGELONE_WS_URL, str)
        self.assertIsInstance(FAILOVER_NO_TICK_THRESHOLD, (int, float))
        self.assertIsInstance(FAILOVER_SWITCHBACK_THRESHOLD, (int, float))

    def test_failover_threshold_values(self):
        from baseline_v1_live.config import (
            FAILOVER_NO_TICK_THRESHOLD,
            FAILOVER_SWITCHBACK_THRESHOLD,
        )
        self.assertEqual(FAILOVER_NO_TICK_THRESHOLD, 15)
        self.assertEqual(FAILOVER_SWITCHBACK_THRESHOLD, 10)

    def test_angelone_defaults(self):
        from baseline_v1_live.config import ANGELONE_HOST, ANGELONE_WS_URL
        # Defaults should point to port 5001 / 8766 (Angel One second instance)
        self.assertIn('5001', ANGELONE_HOST)
        self.assertIn('8766', ANGELONE_WS_URL)


# ---------------------------------------------------------------------------
# Helper: build a DataPipeline with mocked api constructor
# ---------------------------------------------------------------------------

def _make_pipeline():
    """Return a DataPipeline instance with the openalgo.api class mocked out."""
    with patch('baseline_v1_live.data_pipeline.api') as mock_api_cls:
        from baseline_v1_live.data_pipeline import DataPipeline
        pipeline = DataPipeline()
    return pipeline


# ---------------------------------------------------------------------------
# 2. Init test
# ---------------------------------------------------------------------------

class TestDataPipelineInit(unittest.TestCase):
    """Test that DataPipeline initializes with correct failover state vars."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()

    def test_angelone_client_is_none(self):
        self.assertIsNone(self.pipeline.angelone_client)

    def test_angelone_is_connected_false(self):
        self.assertFalse(self.pipeline.angelone_is_connected)

    def test_active_source_is_zerodha(self):
        self.assertEqual(self.pipeline.active_source, 'zerodha')

    def test_is_failover_active_false(self):
        self.assertFalse(self.pipeline.is_failover_active)

    def test_last_zerodha_tick_time_empty_dict(self):
        self.assertIsInstance(self.pipeline.last_zerodha_tick_time, dict)
        self.assertEqual(len(self.pipeline.last_zerodha_tick_time), 0)

    def test_zerodha_continuous_tick_start_none(self):
        self.assertIsNone(self.pipeline.zerodha_continuous_tick_start)


# ---------------------------------------------------------------------------
# 3. Zerodha callback test
# ---------------------------------------------------------------------------

class TestZerodhaCallback(unittest.TestCase):
    """Test _on_quote_update_zerodha behavior."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()
        self.pipeline._process_tick = MagicMock()

    def _make_tick(self, symbol='NIFTY01JAN2524000CE', ltp=150.0):
        return {'symbol': symbol, 'data': {'ltp': ltp, 'volume': 10}}

    def test_always_updates_last_zerodha_tick_time(self):
        """Even when on Angel One, Zerodha callback must update last_zerodha_tick_time."""
        tick = self._make_tick()
        self.pipeline.active_source = 'angelone'

        before = datetime.now(IST)
        self.pipeline._on_quote_update_zerodha(tick)
        after = datetime.now(IST)

        self.assertIn('NIFTY01JAN2524000CE', self.pipeline.last_zerodha_tick_time)
        ts = self.pipeline.last_zerodha_tick_time['NIFTY01JAN2524000CE']
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)

    def test_calls_process_tick_when_active_source_is_zerodha(self):
        tick = self._make_tick()
        self.pipeline.active_source = 'zerodha'
        self.pipeline._on_quote_update_zerodha(tick)
        self.pipeline._process_tick.assert_called_once_with(tick)

    def test_does_not_call_process_tick_when_active_source_is_angelone(self):
        tick = self._make_tick()
        self.pipeline.active_source = 'angelone'
        self.pipeline._on_quote_update_zerodha(tick)
        self.pipeline._process_tick.assert_not_called()

    def test_handles_tick_without_symbol(self):
        """Tick with no symbol should not crash and must not add to last_zerodha_tick_time."""
        tick = {'data': {'ltp': 100.0, 'volume': 5}}
        # Should not raise
        self.pipeline._on_quote_update_zerodha(tick)
        # last_zerodha_tick_time should remain empty (no symbol key added)
        self.assertEqual(len(self.pipeline.last_zerodha_tick_time), 0)


# ---------------------------------------------------------------------------
# 4. Angel One callback test
# ---------------------------------------------------------------------------

class TestAngelOneCallback(unittest.TestCase):
    """Test _on_quote_update_angelone behavior."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()
        self.pipeline._process_tick = MagicMock()

    def _make_tick(self, symbol='NIFTY01JAN2524000CE', ltp=150.0):
        return {'symbol': symbol, 'data': {'ltp': ltp, 'volume': 10}}

    def test_calls_process_tick_when_active_source_is_angelone(self):
        tick = self._make_tick()
        self.pipeline.active_source = 'angelone'
        self.pipeline._on_quote_update_angelone(tick)
        self.pipeline._process_tick.assert_called_once_with(tick)

    def test_does_not_call_process_tick_when_active_source_is_zerodha(self):
        tick = self._make_tick()
        self.pipeline.active_source = 'zerodha'
        self.pipeline._on_quote_update_angelone(tick)
        self.pipeline._process_tick.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Failover test
# ---------------------------------------------------------------------------

class TestFailoverToAngelOne(unittest.TestCase):
    """Test _failover_to_angelone sets state correctly."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()
        # Pre-populate some tick times to verify they get cleared
        now = datetime.now(IST)
        self.pipeline.last_tick_time = {'NIFTY01JAN2524000CE': now}
        self.pipeline.first_data_received_at = now
        self.pipeline.angelone_is_connected = True

    def test_sets_active_source_to_angelone(self):
        self.pipeline._failover_to_angelone('TEST_REASON')
        self.assertEqual(self.pipeline.active_source, 'angelone')

    def test_sets_is_failover_active_true(self):
        self.pipeline._failover_to_angelone('TEST_REASON')
        self.assertTrue(self.pipeline.is_failover_active)

    def test_clears_last_tick_time(self):
        """last_tick_time should be empty after failover so Angel One ticks are counted fresh."""
        self.pipeline._failover_to_angelone('TEST_REASON')
        self.assertEqual(len(self.pipeline.last_tick_time), 0)

    def test_clears_first_data_received_at(self):
        self.pipeline._failover_to_angelone('TEST_REASON')
        self.assertIsNone(self.pipeline.first_data_received_at)

    def test_resets_zerodha_continuous_tick_start(self):
        self.pipeline.zerodha_continuous_tick_start = datetime.now(IST)
        self.pipeline._failover_to_angelone('TEST_REASON')
        self.assertIsNone(self.pipeline.zerodha_continuous_tick_start)


# ---------------------------------------------------------------------------
# 6. Failback test
# ---------------------------------------------------------------------------

class TestFailbackToZerodha(unittest.TestCase):
    """Test _failback_to_zerodha restores state correctly."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()

        # Put pipeline in failover state
        now = datetime.now(IST)
        self.pipeline.active_source = 'angelone'
        self.pipeline.is_failover_active = True
        self.pipeline.zerodha_continuous_tick_start = now
        self.zerodha_tick_time = {'NIFTY01JAN2524000CE': now, 'NIFTY01JAN2524000PE': now}
        self.pipeline.last_zerodha_tick_time = dict(self.zerodha_tick_time)

    def test_sets_active_source_to_zerodha(self):
        self.pipeline._failback_to_zerodha()
        self.assertEqual(self.pipeline.active_source, 'zerodha')

    def test_sets_is_failover_active_false(self):
        self.pipeline._failback_to_zerodha()
        self.assertFalse(self.pipeline.is_failover_active)

    def test_restores_last_tick_time_from_zerodha(self):
        """last_tick_time should be restored from last_zerodha_tick_time."""
        self.pipeline._failback_to_zerodha()
        self.assertEqual(self.pipeline.last_tick_time, self.zerodha_tick_time)

    def test_resets_zerodha_continuous_tick_start(self):
        self.pipeline._failback_to_zerodha()
        self.assertIsNone(self.pipeline.zerodha_continuous_tick_start)

    def test_restores_first_data_received_at(self):
        """first_data_received_at should be set to the earliest Zerodha tick time."""
        self.pipeline._failback_to_zerodha()
        expected = min(self.zerodha_tick_time.values())
        self.assertEqual(self.pipeline.first_data_received_at, expected)


# ---------------------------------------------------------------------------
# 7. Trigger test - Angel One available
# ---------------------------------------------------------------------------

class TestTriggerFailoverOrReconnect_AngelOneAvailable(unittest.TestCase):
    """_trigger_failover_or_reconnect should failover when Angel One is connected."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()

        self.pipeline.angelone_is_connected = True
        self.pipeline.is_failover_active = False
        self.pipeline.auto_reconnect_enabled = True
        self.pipeline.is_reconnecting = False

        # Patch internal methods so we can verify calls without side effects
        self.pipeline._failover_to_angelone = MagicMock()
        self.pipeline.reconnect = MagicMock()

    def test_calls_failover_to_angelone(self):
        with patch('baseline_v1_live.data_pipeline.Thread') as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            self.pipeline._trigger_failover_or_reconnect('NO_TICKS:20s')

        self.pipeline._failover_to_angelone.assert_called_once_with('NO_TICKS:20s')

    def test_starts_background_reconnect_thread(self):
        """When failing over, Zerodha reconnect should be kicked off in a background thread."""
        with patch('baseline_v1_live.data_pipeline.Thread') as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            self.pipeline._trigger_failover_or_reconnect('NO_TICKS:20s')

        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()

    def test_does_not_call_plain_reconnect_directly(self):
        """reconnect() should only run inside the background thread, not called synchronously."""
        with patch('baseline_v1_live.data_pipeline.Thread'):
            self.pipeline._trigger_failover_or_reconnect('NO_TICKS:20s')
        # reconnect Mock was NOT called synchronously
        self.pipeline.reconnect.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Trigger test - Angel One unavailable
# ---------------------------------------------------------------------------

class TestTriggerFailoverOrReconnect_AngelOneUnavailable(unittest.TestCase):
    """_trigger_failover_or_reconnect should NOT failover when Angel One is disconnected."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()

        self.pipeline.angelone_is_connected = False
        self.pipeline.is_failover_active = False
        self.pipeline.auto_reconnect_enabled = True
        self.pipeline.is_reconnecting = False
        self.pipeline._failover_to_angelone = MagicMock()
        self.pipeline.reconnect = MagicMock()

    def test_does_not_call_failover_to_angelone(self):
        with patch('baseline_v1_live.data_pipeline.Thread') as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            self.pipeline._trigger_failover_or_reconnect('WEBSOCKET_DISCONNECTED')

        self.pipeline._failover_to_angelone.assert_not_called()

    def test_starts_plain_zerodha_reconnect_thread(self):
        """Without Angel One, a plain Zerodha reconnect thread should start."""
        with patch('baseline_v1_live.data_pipeline.Thread') as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            self.pipeline._trigger_failover_or_reconnect('WEBSOCKET_DISCONNECTED')

        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()

    def test_no_action_when_auto_reconnect_disabled(self):
        self.pipeline.auto_reconnect_enabled = False
        with patch('baseline_v1_live.data_pipeline.Thread') as mock_thread_cls:
            self.pipeline._trigger_failover_or_reconnect('WEBSOCKET_DISCONNECTED')
        mock_thread_cls.assert_not_called()
        self.pipeline._failover_to_angelone.assert_not_called()


# ---------------------------------------------------------------------------
# 9. No double failover
# ---------------------------------------------------------------------------

class TestNoDoubleFailover(unittest.TestCase):
    """_failover_to_angelone is a no-op when is_failover_active is already True."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()

        # Simulate already in failover
        self.pipeline.active_source = 'angelone'
        self.pipeline.is_failover_active = True
        self.pipeline.angelone_is_connected = True

    def test_no_op_when_already_in_failover(self):
        """Calling _failover_to_angelone a second time should be a no-op."""
        # Patch lock to detect if the body executes
        original_source = self.pipeline.active_source
        original_flag = self.pipeline.is_failover_active

        self.pipeline._failover_to_angelone('SECOND_CALL')

        # State should be unchanged (still angelone, still True)
        self.assertEqual(self.pipeline.active_source, original_source)
        self.assertTrue(self.pipeline.is_failover_active)

    def test_trigger_does_not_double_failover_when_already_active(self):
        """_trigger_failover_or_reconnect should skip failover when is_failover_active=True."""
        self.pipeline.auto_reconnect_enabled = True
        self.pipeline.is_reconnecting = False
        self.pipeline._failover_to_angelone = MagicMock()
        self.pipeline.reconnect = MagicMock()

        with patch('baseline_v1_live.data_pipeline.Thread') as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            # When is_failover_active=True and is_reconnecting=False,
            # the guard `if self.is_reconnecting and not self.is_failover_active` allows through
            # but `if self.angelone_is_connected and not self.is_failover_active` is False
            self.pipeline._trigger_failover_or_reconnect('REPEATED_TRIGGER')

        # _failover_to_angelone should NOT be called again
        self.pipeline._failover_to_angelone.assert_not_called()


# ---------------------------------------------------------------------------
# 10. No double failback
# ---------------------------------------------------------------------------

class TestNoDoubleFailback(unittest.TestCase):
    """_failback_to_zerodha is a no-op when is_failover_active is already False."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()

        # Already on Zerodha (not in failover)
        self.pipeline.active_source = 'zerodha'
        self.pipeline.is_failover_active = False

    def test_no_op_when_not_in_failover(self):
        """Calling _failback_to_zerodha when not in failover should be a no-op."""
        original_source = self.pipeline.active_source
        original_flag = self.pipeline.is_failover_active

        self.pipeline._failback_to_zerodha()

        self.assertEqual(self.pipeline.active_source, original_source)
        self.assertFalse(self.pipeline.is_failover_active)


# ---------------------------------------------------------------------------
# 11. Disconnect test
# ---------------------------------------------------------------------------

class TestDisconnect(unittest.TestCase):
    """disconnect() should disconnect both Zerodha and Angel One clients."""

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()

    def test_disconnects_zerodha_client(self):
        mock_zerodha = MagicMock()
        self.pipeline.client = mock_zerodha
        self.pipeline.is_connected = True
        self.pipeline.monitor_running = False

        self.pipeline.disconnect()

        mock_zerodha.disconnect.assert_called_once()
        self.assertFalse(self.pipeline.is_connected)

    def test_disconnects_angelone_client(self):
        mock_angelone = MagicMock()
        self.pipeline.angelone_client = mock_angelone
        self.pipeline.angelone_is_connected = True
        self.pipeline.monitor_running = False

        self.pipeline.disconnect()

        mock_angelone.disconnect.assert_called_once()
        self.assertFalse(self.pipeline.angelone_is_connected)

    def test_disconnects_both_clients(self):
        mock_zerodha = MagicMock()
        mock_angelone = MagicMock()
        self.pipeline.client = mock_zerodha
        self.pipeline.is_connected = True
        self.pipeline.angelone_client = mock_angelone
        self.pipeline.angelone_is_connected = True
        self.pipeline.monitor_running = False

        self.pipeline.disconnect()

        mock_zerodha.disconnect.assert_called_once()
        mock_angelone.disconnect.assert_called_once()

    def test_disconnect_handles_no_angelone_client(self):
        """If angelone_client is None, disconnect should not raise."""
        self.pipeline.client = MagicMock()
        self.pipeline.is_connected = True
        self.pipeline.angelone_client = None
        self.pipeline.angelone_is_connected = False
        self.pipeline.monitor_running = False

        # Should not raise
        self.pipeline.disconnect()

    def test_disconnect_skips_angelone_if_not_connected(self):
        """If angelone_is_connected is False, angelone_client.disconnect should NOT be called."""
        mock_angelone = MagicMock()
        self.pipeline.angelone_client = mock_angelone
        self.pipeline.angelone_is_connected = False
        self.pipeline.client = None
        self.pipeline.is_connected = False
        self.pipeline.monitor_running = False

        self.pipeline.disconnect()

        mock_angelone.disconnect.assert_not_called()


# ---------------------------------------------------------------------------
# 12. Tick isolation test
# ---------------------------------------------------------------------------

class TestTickIsolation(unittest.TestCase):
    """
    When active_source='angelone':
    - Zerodha ticks must update last_zerodha_tick_time
    - Zerodha ticks must NOT update last_tick_time (active source tracker)
    - Angel One ticks DO update last_tick_time (via _process_tick)
    """

    def setUp(self):
        with patch('baseline_v1_live.data_pipeline.api'):
            from baseline_v1_live.data_pipeline import DataPipeline
            self.pipeline = DataPipeline()

        # Put pipeline into failover mode
        self.pipeline.active_source = 'angelone'
        self.pipeline.is_failover_active = True

    def test_zerodha_tick_updates_last_zerodha_tick_time_when_on_angelone(self):
        tick = {'symbol': 'NIFTY01JAN2524000CE', 'data': {'ltp': 150.0, 'volume': 10}}

        before = datetime.now(IST)
        self.pipeline._on_quote_update_zerodha(tick)
        after = datetime.now(IST)

        self.assertIn('NIFTY01JAN2524000CE', self.pipeline.last_zerodha_tick_time)
        ts = self.pipeline.last_zerodha_tick_time['NIFTY01JAN2524000CE']
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)

    def test_zerodha_tick_does_NOT_update_last_tick_time_when_on_angelone(self):
        """last_tick_time is only updated by _process_tick, which Zerodha won't call
        when active_source='angelone'."""
        tick = {'symbol': 'NIFTY01JAN2524000CE', 'data': {'ltp': 150.0, 'volume': 10}}

        # Ensure last_tick_time starts empty
        self.pipeline.last_tick_time.clear()

        self.pipeline._on_quote_update_zerodha(tick)

        # _process_tick was not called, so last_tick_time should still be empty
        self.assertNotIn('NIFTY01JAN2524000CE', self.pipeline.last_tick_time)

    def test_angelone_tick_updates_last_tick_time_when_on_angelone(self):
        """Angel One ticks call _process_tick which updates last_tick_time."""
        symbol = 'NIFTY01JAN2524000CE'
        tick = {'symbol': symbol, 'data': {'ltp': 150.0, 'volume': 10}}

        self.pipeline.last_tick_time.clear()

        # _process_tick is NOT mocked here - let it run (it will update last_tick_time)
        self.pipeline._on_quote_update_angelone(tick)

        self.assertIn(symbol, self.pipeline.last_tick_time)

    def test_zerodha_tick_does_not_contaminate_last_tick_time_across_multiple_symbols(self):
        """Multiple Zerodha ticks for different symbols should none update last_tick_time."""
        symbols = [
            'NIFTY01JAN2524000CE',
            'NIFTY01JAN2524000PE',
            'NIFTY01JAN2524100CE',
        ]
        self.pipeline.last_tick_time.clear()

        for sym in symbols:
            tick = {'symbol': sym, 'data': {'ltp': 150.0, 'volume': 10}}
            self.pipeline._on_quote_update_zerodha(tick)

        # None should be in last_tick_time
        for sym in symbols:
            self.assertNotIn(sym, self.pipeline.last_tick_time)

        # But all should be in last_zerodha_tick_time
        for sym in symbols:
            self.assertIn(sym, self.pipeline.last_zerodha_tick_time)


if __name__ == '__main__':
    unittest.main(verbosity=2)
