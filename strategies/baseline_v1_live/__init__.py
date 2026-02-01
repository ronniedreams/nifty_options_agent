"""
Baseline V1 Live Trading Module

Components:
- data_pipeline: WebSocket to 1-min OHLCV bars
- swing_detector: Real-time swing low/high detection per option
- strike_filter: Scan strikes for entry criteria (SL closest to 10 points)
- order_manager: Proactive limit order placement & SL order management
- position_tracker: Track positions with R-multiple accounting
- state_manager: SQLite persistence for crash recovery
- baseline_v1_live: Main orchestrator script
"""

__version__ = '1.0.0'
