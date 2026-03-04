"""
Configuration for RL V1 Live Trading Agent

Imports shared constants from baseline_v1_live.config where applicable.
V3-specific parameters (Upstox broker, separate Telegram, model path) defined here.
"""

import os
from datetime import time
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables (same pattern as baseline)
env_path_docker = Path(__file__).parent.parent / '.env'
env_path_local = Path(__file__).parent / '.env'

if env_path_docker.exists():
    load_dotenv(dotenv_path=env_path_docker)
elif env_path_local.exists():
    load_dotenv(dotenv_path=env_path_local)
else:
    load_dotenv()

# ============================================================================
# SHARED FROM BASELINE (re-import for V3 use)
# ============================================================================

from baseline_v1_live.config import (
    # Capital & position sizing
    LOT_SIZE,
    MAX_POSITIONS,
    MAX_CE_POSITIONS,
    MAX_PE_POSITIONS,
    R_VALUE,
    EXCHANGE,
    PRODUCT_TYPE,
    # Data pipeline (V3 reuses same WS feeds)
    OPENALGO_API_KEY,
    OPENALGO_HOST,
    OPENALGO_WS_URL,
    ANGELONE_OPENALGO_API_KEY,
    ANGELONE_HOST,
    ANGELONE_WS_URL,
    FAILOVER_NO_TICK_THRESHOLD,
    FAILOVER_SWITCHBACK_THRESHOLD,
    # Trading hours
    MARKET_START_TIME,
    MARKET_END_TIME,
    MARKET_CLOSE_TIME,
    FORCE_EXIT_TIME,
    MARKET_OPEN_TIME,
    AUTO_DETECT_TIME,
    # Data pipeline params
    STRIKE_SCAN_RANGE,
    MAX_BARS_PER_SYMBOL,
    # Paper trading
    PAPER_TRADING,
    DRY_RUN,
    VERBOSE,
    # Auto-login shared infra
    AUTOMATED_LOGIN,
    OPENALGO_USERNAME,
    OPENALGO_PASSWORD,
    ZERODHA_TOTP_SECRET,
    ANGELONE_TOTP_SECRET,
)

# ============================================================================
# V3-SPECIFIC: STRATEGY IDENTITY
# ============================================================================

RLV1_STRATEGY_NAME = "rl_v1_live"

# ============================================================================
# V3-SPECIFIC: RL MODEL
# ============================================================================

RLV1_MODEL_PATH = os.getenv('RLV1_MODEL_PATH', str(Path(__file__).parent.parent / 'results' / 'rl_models_v1' / 'best_model.zip'))

# ============================================================================
# V3-SPECIFIC: UPSTOX BROKER
# ============================================================================

UPSTOX_OPENALGO_HOST = os.getenv('UPSTOX_OPENALGO_HOST', 'http://127.0.0.1:5002')
UPSTOX_OPENALGO_API_KEY = os.getenv('UPSTOX_OPENALGO_API_KEY', '')

# ============================================================================
# V3-SPECIFIC: TELEGRAM (separate bot for V3 alerts)
# ============================================================================

RLV1_TELEGRAM_ENABLED = os.getenv('RLV1_TELEGRAM_ENABLED', 'true').lower() == 'true'
RLV1_TELEGRAM_BOT_TOKEN = os.getenv('RLV1_TELEGRAM_BOT_TOKEN', '')
RLV1_TELEGRAM_CHAT_ID = os.getenv('RLV1_TELEGRAM_CHAT_ID', '')

# ============================================================================
# V3-SPECIFIC: RL PARAMETERS (from env_v3.py)
# ============================================================================

# Position sizing (same as env_v3 constants)
MAX_LOTS = 15          # V3 uses 15-lot cap (env_v3.py), not baseline's 10
TARGET_SL_POINTS = 20  # V3 targets 20pt SL (env_v3.py), not baseline's 10

# Strike selection
MIN_PRICE = 50         # Wider range than baseline (50-500 vs 100-300)
MAX_PRICE = 500
STRIKE_INTERVAL = 50

# Review decision interval
REVIEW_INTERVAL = 5    # Every 5 bars when positions open

# Daily exits (goal-conditioned in training, fixed for live)
DAILY_TARGET_R = 5.0
DAILY_STOP_R = -5.0

# Actions (from env_v3.py)
ACTION_SKIP_HOLD = 0
ACTION_ENTER = 1
ACTION_EXIT_ALL = 2
ACTION_STOP_SESSION = 3

# Decision types
DECISION_ENTRY = 0.0
DECISION_REVIEW = 1.0

# Transaction costs (Zerodha F&O Options — same for Upstox)
BROKERAGE_PER_TRADE = 40.0
STT_RATE = 0.001
EXCHANGE_TXN_RATE = 0.000356
GST_RATE = 0.18

# ============================================================================
# V3-SPECIFIC: STATE & LOGGING
# ============================================================================

RLV1_STATE_DB_PATH = os.getenv('RLV1_STATE_DB_PATH', os.path.join(os.path.dirname(__file__), 'rl_v1_state.db'))
RLV1_LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
RLV1_LOG_LEVEL = 'INFO'

# Kill/Pause switch files
_RLV1_STATE_DIR = os.getenv('RLV1_STATE_DIR', os.path.dirname(__file__))
RLV1_KILL_SWITCH_FILE = os.path.join(_RLV1_STATE_DIR, 'RLV1_KILL_SWITCH')
RLV1_PAUSE_SWITCH_FILE = os.path.join(_RLV1_STATE_DIR, 'RLV1_PAUSE_SWITCH')

# ============================================================================
# V3-SPECIFIC: ORDER EXECUTION
# ============================================================================

# V3 uses market orders (not proactive limit orders like baseline)
MAX_ORDER_RETRIES = 3
ORDER_RETRY_DELAY = 2
ORDER_FILL_CHECK_INTERVAL = 5
ORDERBOOK_POLL_INTERVAL = 5

# SL orders (same pattern as baseline — SL-L type)
SL_TRIGGER_PRICE_OFFSET = 0
SL_LIMIT_PRICE_OFFSET = 3

# Emergency handling
MAX_SL_FAILURE_COUNT = 3
EMERGENCY_EXIT_RETRY_COUNT = 5
EMERGENCY_EXIT_RETRY_DELAY = 2

# ============================================================================
# V3-SPECIFIC: UPSTOX AUTO-LOGIN
# ============================================================================

UPSTOX_USER_ID = os.getenv('UPSTOX_USER_ID', '')
UPSTOX_PASSWORD = os.getenv('UPSTOX_PASSWORD', '')
UPSTOX_TOTP_SECRET = os.getenv('UPSTOX_TOTP_SECRET', '')
