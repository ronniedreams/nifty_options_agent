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

RLV1_MODEL_PATH = os.getenv('RLV1_MODEL_PATH', str(Path(__file__).parent.parent / 'results' / 'rl_models_v3' / 'best_model.zip'))

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
REVIEW_INTERVAL = 1    # Every 1 bar when positions open

# Daily exits (goal-conditioned in training, fixed for live)
DAILY_TARGET_R = 5.0
DAILY_STOP_R = -5.0

# Actions: Discrete(12) from env_v3.py
ACTION_HOLD = 0              # SKIP at entry, HOLD at review
ACTION_ENTER_TP_05 = 1       # Enter + TP at 0.5R profit
ACTION_ENTER_TP_10 = 2       # Enter + TP at 1.0R profit
ACTION_ENTER_TP_20 = 3       # Enter + TP at 2.0R profit
ACTION_ENTER_TP_30 = 4       # Enter + TP at 3.0R profit
ACTION_MARKET_EXIT_1 = 5     # Exit oldest position (only if profitable)
ACTION_MARKET_EXIT_2 = 6
ACTION_MARKET_EXIT_3 = 7
ACTION_MARKET_EXIT_4 = 8
ACTION_MARKET_EXIT_5 = 9
ACTION_EXIT_ALL = 10          # Market exit everything
ACTION_STOP_SESSION = 11      # Exit all + end episode
NUM_ACTIONS = 12

# TP R-level mapping for entry actions
TP_R_LEVELS = {
    ACTION_ENTER_TP_05: 0.5,
    ACTION_ENTER_TP_10: 1.0,
    ACTION_ENTER_TP_20: 2.0,
    ACTION_ENTER_TP_30: 3.0,
}

# Observation dimensions (V3)
NUM_FEATURES = 46
NUM_POSITION_SLOTS = 5
FEATURES_PER_POSITION = 5

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

UPSTOX_USER_ID = os.getenv('UPSTOX_USER_ID', '')        # Upstox client ID
UPSTOX_MOBILE = os.getenv('UPSTOX_MOBILE', '')           # 10-digit mobile number
UPSTOX_PASSWORD = os.getenv('UPSTOX_PASSWORD', '')        # Upstox password
UPSTOX_PIN = os.getenv('UPSTOX_PIN', '')                  # 6-digit PIN
UPSTOX_TOTP_SECRET = os.getenv('UPSTOX_TOTP_SECRET', '') # TOTP secret (base32)
UPSTOX_API_KEY = os.getenv('UPSTOX_API_KEY', '')          # OAuth app API key (client_id)
UPSTOX_API_SECRET = os.getenv('UPSTOX_API_SECRET', '')    # OAuth app API secret
UPSTOX_REDIRECT_URI = os.getenv('UPSTOX_REDIRECT_URI', 'http://127.0.0.1:5002/upstox/callback')
