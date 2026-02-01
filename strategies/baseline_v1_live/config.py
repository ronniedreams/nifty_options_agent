"""
Configuration for Baseline V1 Live Trading Strategy

Position Sizing:
- Total Capital: ₹1 Crore
- Margin per lot: ₹2 Lakh
- Max lots available: 50
- Max positions: 5
- Max lots per position: 10
- R_VALUE: ₹6,500 (optimized for ~10 point SL)
"""

import os
from datetime import time
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ============================================================================
# CAPITAL & POSITION SIZING
# ============================================================================

TOTAL_CAPITAL = 10000000  # ₹1 Crore
MARGIN_PER_LOT = 200000   # ₹2 Lakh
MAX_LOTS_AVAILABLE = 50
MAX_POSITIONS = 5
MAX_LOTS_PER_POSITION = 10
LOT_SIZE = 65  # NIFTY lot size

# R-Multiple Configuration
R_VALUE = 6500  # Target ₹6,500 per R (10 points × 10 lots × 65 qty)
TARGET_SL_POINTS = 10  # Prefer strikes with SL close to 10 points

# Daily Exit Thresholds
DAILY_TARGET_R = 5.0   # Exit all positions at +5R
DAILY_STOP_R = -5.0    # Exit all positions at -5R

# ============================================================================
# STRATEGY IDENTIFICATION
# ============================================================================

STRATEGY_NAME = "baseline_v1_live"  # Strategy identifier for OpenAlgo orders

# ============================================================================
# ENTRY FILTERS (from baseline_v1 backtest)
# ============================================================================

MIN_ENTRY_PRICE = 100
MAX_ENTRY_PRICE = 300
MIN_VWAP_PREMIUM = 0.04  # 4% above VWAP (entry price must be >= 4% above VWAP at swing)
MIN_SL_PERCENT = 0.02    # 2% minimum SL
MAX_SL_PERCENT = 0.10    # 10% maximum SL

# ============================================================================
# STRIKE SELECTION
# ============================================================================

# Number of strikes to scan around ATM
STRIKE_SCAN_RANGE = 5  # ±5 strikes from ATM (250 point range) - reduced for broker limits

# Strike selection tie-breaker priority:
# 1. SL points closest to TARGET_SL_POINTS (10)
# 2. Highest entry price
STRIKE_SELECTION_PRIORITY = ['sl_distance', 'price']

# ============================================================================
# ORDER EXECUTION
# ============================================================================

# Order Types
ORDER_TYPE_ENTRY = 'LIMIT'  # Limit order 1 tick below swing break
ORDER_TYPE_SL = 'SL'        # Stop-Loss order

# SL Order Configuration
SL_TRIGGER_PRICE_OFFSET = 0  # Trigger at exact SL price
SL_LIMIT_PRICE_OFFSET = 3    # Limit 3 Rs above SL trigger

# Order Monitoring
ORDER_FILL_CHECK_INTERVAL = 10  # Check for fills every 10 seconds
ORDERBOOK_POLL_INTERVAL = 5     # Poll orderbook every 5 seconds

# Limit Order Timeout
LIMIT_ORDER_TIMEOUT = 300  # Cancel unfilled limit orders after 5 minutes

# Emergency SL Failure Handling
MAX_SL_FAILURE_COUNT = 3  # Halt trading after 3 consecutive SL failures
EMERGENCY_EXIT_RETRY_COUNT = 5  # Retry emergency market exit 5 times
EMERGENCY_EXIT_RETRY_DELAY = 2  # Wait 2 seconds between emergency exit retries

# ============================================================================
# TRADING HOURS (IST)
# ============================================================================

MARKET_START_TIME = time(9, 15)   # 9:15 AM
MARKET_END_TIME = time(15, 15)    # 3:15 PM (stop entering new trades)
FORCE_EXIT_TIME = time(15, 15)    # Force exit all positions at 3:15 PM
MARKET_CLOSE_TIME = time(15, 30)  # 3:30 PM (actual NSE close - WebSocket stops after this)

# ============================================================================
# AUTO-DETECTION TIMING
# ============================================================================

MARKET_OPEN_TIME = time(9, 15)   # Market opens at 9:15 AM IST
AUTO_DETECT_TIME = time(9, 16)   # Fetch spot price 1 min after open
MAX_AUTO_DETECT_RETRIES = 3      # Max retries for API calls
AUTO_DETECT_RETRY_DELAY = 5      # Seconds between retries

# ============================================================================
# DATA PIPELINE
# ============================================================================

# WebSocket Configuration
WEBSOCKET_RECONNECT_DELAY = 5  # Seconds between reconnection attempts
WEBSOCKET_MAX_RECONNECT_ATTEMPTS = 5  # Max reconnection attempts
WEBSOCKET_MODE = 2  # Quote mode (LTP, OHLC, Volume)

# Bar Aggregation
BAR_INTERVAL_SECONDS = 60  # 1-minute bars
MIN_TICKS_PER_BAR = 5      # Minimum ticks to form valid bar

# Memory Management
MAX_BARS_PER_SYMBOL = 400  # Keep full trading session (9:15 AM - 3:30 PM = ~375 bars)
BAR_PRUNING_THRESHOLD = 450  # Prune when bars exceed this threshold
# Swing candidates persist for entire trading day but are cleared at day start
# No intraday time-based expiry - swings valid until market structure invalidates them

# Order Retries
MAX_ORDER_RETRIES = 3       # Retry order placement 3 times
ORDER_RETRY_DELAY = 2       # Wait 2 seconds between retries

# Data Validation
MAX_TICK_AGE_SECONDS = 5   # Consider tick stale if >5 seconds old

# WebSocket Watchdog (Auto-Shutdown)
DATA_FRESHNESS_CHECK_INTERVAL = 30  # Check data freshness every 30 seconds
MIN_DATA_COVERAGE_THRESHOLD = 0.5   # Shutdown if <50% symbols have fresh data
STALE_DATA_TIMEOUT = 30  # Shutdown if no fresh data for 30 seconds
MAX_BAR_AGE_SECONDS = 120  # Shutdown if last bar >2 minutes old

# ============================================================================
# OPENALGO INTEGRATION
# ============================================================================

# API Configuration (read from environment variables)
OPENALGO_API_KEY = os.getenv('OPENALGO_API_KEY', '')
OPENALGO_HOST = os.getenv('OPENALGO_HOST', 'http://127.0.0.1:5000')
OPENALGO_WS_URL = os.getenv('OPENALGO_WS_URL', 'ws://127.0.0.1:8765')

# Exchange
EXCHANGE = 'NFO'  # Options exchange

# Product Type
PRODUCT_TYPE = 'MIS'  # Intraday (positions auto-squared at 3:20 PM)

# ============================================================================
# RISK MANAGEMENT
# ============================================================================

# Position Limits by Option Type
MAX_CE_POSITIONS = 3
MAX_PE_POSITIONS = 3

# Circuit Breakers
MAX_CONSECUTIVE_LOSSES = 3  # Pause trading after 3 losses in a row
PAUSE_DURATION_MINUTES = 30  # Resume after 30 minutes

# Order Rejection Handling
MAX_ORDER_RETRIES = 3
ORDER_RETRY_DELAY = 2  # Seconds between retries

# ============================================================================
# STATE PERSISTENCE
# ============================================================================

STATE_DB_PATH = os.getenv('STATE_DB_PATH', os.path.join(os.path.dirname(__file__), 'live_state.db'))
STATE_SAVE_INTERVAL = 30  # Save state every 30 seconds

# ============================================================================
# LOGGING
# ============================================================================

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
LOG_LEVEL = 'INFO'  # DEBUG, INFO, WARNING, ERROR, CRITICAL

# Trade Log Files
TRADES_LOG_CSV = os.path.join(LOG_DIR, 'baseline_v1_live_trades.csv')
DAILY_SUMMARY_CSV = os.path.join(LOG_DIR, 'baseline_v1_live_daily_summary.csv')
EQUITY_CURVE_CSV = os.path.join(LOG_DIR, 'baseline_v1_live_equity_curve.csv')

# ============================================================================
# TELEGRAM NOTIFICATIONS (Optional)
# ============================================================================

TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'false').lower() == 'true'
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

# Notification Events
NOTIFY_ON_TRADE_ENTRY = True
NOTIFY_ON_TRADE_EXIT = True
NOTIFY_ON_DAILY_TARGET = True
NOTIFY_ON_ERROR = True
NOTIFY_ON_BEST_STRIKE_CHANGE = True  # Notify when best strike changes (not every tick)

# ============================================================================
# STARTUP & FAILURE HANDLING
# ============================================================================

# Startup Health Checks
MAX_STARTUP_RETRIES = 3
STARTUP_RETRY_DELAY_BASE = 30  # seconds (30s, 60s, 90s with exponential backoff)

# Notification Throttling (seconds)
NOTIFICATION_THROTTLE_STARTUP = 3600      # 1 hour
NOTIFICATION_THROTTLE_WEBSOCKET = 3600    # 1 hour
NOTIFICATION_THROTTLE_BROKER = 1800       # 30 minutes
NOTIFICATION_THROTTLE_DATABASE = 3600     # 1 hour
NOTIFICATION_AGGREGATION_WINDOW = 60      # Aggregate errors within 60s

# Waiting Mode Behavior
WAITING_MODE_CHECK_INTERVAL = 300         # Check every 5 minutes
WAITING_MODE_SEND_HOURLY_STATUS = True    # Send hourly "still waiting" updates

# Graceful Shutdown
SHUTDOWN_TIMEOUT = 9                      # Must complete in 9 seconds
SHUTDOWN_FORCE_MARKET_ORDERS = True       # Use MARKET orders for fast exit

# ============================================================================
# DEVELOPMENT/TESTING
# ============================================================================

# Paper Trading Mode (use OpenAlgo Analyzer)
PAPER_TRADING = os.getenv('PAPER_TRADING', 'true').lower() == 'true'

# Dry Run (log orders but don't place)
DRY_RUN = os.getenv('DRY_RUN', 'false').lower() == 'true'

# Verbose Logging
VERBOSE = os.getenv('VERBOSE', 'true').lower() == 'true'  # Changed to true for debugging
