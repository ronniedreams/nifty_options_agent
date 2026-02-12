# Nifty Options Agent - Project Context

## Project Overview
**Nifty Options Agent** is an automated trading system designed for the Indian Nifty options market. It implements the "baseline_v1" swing-break strategy, which shorts options based on specific price action triggers. The system is containerized using Docker and integrates with brokers via the **OpenAlgo** API.

### Key Components
*   **Core Strategy (`baseline_v1_live/`):** Contains the trading logic, data pipeline, order management, and state persistence.
    *   `baseline_v1_live.py`: Main entry point and orchestrator.
    *   `data_pipeline.py`: Handles WebSocket data ingestion and aggregation (1-min bars).
    *   `swing_detector.py` & `strike_filter.py`: Implements the trading signal logic.
    *   `order_manager.py`: Manages order placement, including proactive limit orders and stop-losses.
    *   `position_tracker.py` & `state_manager.py`: Tracks PnL (R-multiples) and persists state to SQLite to recover from crashes.
*   **Broker Integration:** Uses `OpenAlgo` (running locally or in a container) to bridge the Python strategy with the broker (e.g., Zerodha).
*   **Monitoring:**
    *   `monitor_dashboard/`: A Streamlit application for real-time visualization of trades and system health.
    *   `logs/`: Comprehensive logging for debugging and audit.

## Architecture
*   **Language:** Python 3.10+
*   **Deployment:** Docker Compose (`docker-compose.yaml`) orchestrates the trading agent, OpenAlgo, and monitoring services.
*   **Data Persistence:** SQLite (`live_state.db`) for robust state recovery.
*   **Configuration:** Environment variables stored in `.env` files (not committed).

## Development & Git Workflow (Strict)
**Reference:** `.claude/GIT_SOP.md`
*   **Branching Model:**
    *   `main`: **Production only**. Always safe. Deployed to EC2.
    *   `feature/X`: Active development.
    *   `draft/X`: **Testing snapshots**. Created from `main` + `feature/X` for isolated pre-market testing.
*   **Market Hours Rule:** **NO code changes** on production/testing environments during market hours (09:15 - 15:30 IST).
*   **Deployment:**
    *   EC2 only runs `main`.
    *   Features are merged to `main` only after successful post-market review.

## Setup & Usage

### Local Development (Windows)
1.  **Environment Setup:**
    ```powershell
    python -m venv venv
    .\venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    ```
2.  **Configuration:**
    *   Create `baseline_v1_live/.env` (see `.env.example`).
    *   Set `OPENALGO_API_KEY`, `PAPER_TRADING=true`, etc.
3.  **Run Strategy (Paper Mode):**
    ```powershell
    cd baseline_v1_live
    python baseline_v1_live.py --expiry 26DEC24 --atm 18000
    ```

### Docker Operations (Production/EC2)
**Reference:** `DOCKER_COMMANDS.md`
*   **Start System:** `docker compose up -d`
*   **View Logs:** `docker compose logs -f trading_agent`
*   **Update Code:**
    ```bash
    git pull origin main
    docker compose up -d --build
    ```
*   **Emergency Stop:** `docker compose down`

## Key Files & directories
*   `baseline_v1_live/`: Core strategy code.
*   `tests/`: `pytest` suite for integration and failure handling tests.
*   `docker-compose.yaml`: Service definition.
*   `.claude/GIT_SOP.md`: Mandatory Git procedures.
*   `DOCKER_COMMANDS.md`: Cheat sheet for Docker operations.
*   `docs/`: Detailed implementation and migration docs.

## Coding Conventions
*   **Style:** Follows standard Python PEP 8.
*   **Safety:** 
    *   Always validate order parameters before sending.
    *   Ensure `PAPER_TRADING=true` is set unless explicitly instructed for live runs.
    *   Never commit secrets (API keys) or the `.env` file.
*   **Testing:** Run tests via `pytest` before requesting a merge to `main`.
