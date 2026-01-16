import os
from pathlib import Path

# Absolute path to live_state.db
BASE_DIR = Path(__file__).resolve().parents[1]  # live/
STATE_DB_PATH = BASE_DIR / "live_state.db"

FAST_REFRESH = 5
SLOW_REFRESH = 30

STRATEGY_NAME = "Baseline V1 Live"
