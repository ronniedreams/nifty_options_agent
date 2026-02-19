---
paths: baseline_v1_live.py, order_manager.py, position_tracker.py, config.py
---

# Safety Rules — Critical Constraints

## Rule 1: Paper Trading Default
```python
PAPER_TRADING = os.getenv('PAPER_TRADING', 'true').lower() == 'true'
```
- Default is `true` — no live orders without explicit `.env` change
- Log `[CRITICAL] LIVE MODE ACTIVATED` at startup if false
- Never silently place live trades

## Rule 2: Position Limits (config.py)
- MAX_POSITIONS = 5 (total)
- MAX_CE_POSITIONS = 3, MAX_PE_POSITIONS = 3
- Check before every order placement — reject if any limit exceeded

## Rule 3–4: Daily Auto-Exits
- DAILY_TARGET_R = +5.0R → close all, cancel all pending, stop trading
- DAILY_STOP_R = −5.0R → close all, cancel all pending, stop trading
- Check every 10 seconds in main loop

## Rule 5: Force Market Close (3:15 PM IST)
FORCE_EXIT_TIME = 3:15 PM → force close all positions + cancel all orders + emit daily summary

## Rule 6: Position Sizing
```
R_VALUE = 6500
MAX_LOTS_PER_POSITION = 10   (safety cap — not 15)
LOT_SIZE = 65

lots = min(R_VALUE / (risk_per_unit × LOT_SIZE), MAX_LOTS_PER_POSITION)
```
R-based sizing is PRIMARY. Cap only activates for very tight SL scenarios.
Log when cap applies: `[SIZING] Capped from X.X to 10 lots`

## Rule 7: Capital Preservation
- Max daily loss = abs(DAILY_STOP_R) × R_VALUE (default: 5 × 6500 = Rs.32,500)
- No hardcoded rupee amounts — always derived from R_VALUE

## Rule 8: Data Quality Gate
- STALE_DATA_TIMEOUT = 30s: auto-shutdown if no fresh data for 30s
- MIN_DATA_COVERAGE_THRESHOLD = 50%: shutdown if <50% symbols fresh
- MAX_BAR_AGE_SECONDS = 120: shutdown if last bar >2 min old

## Rule 9: No Duplicate Orders
- One pending order per symbol at a time
- Check `pending_orders` before placing

## Rule 10: Entry Validation
Before any order:
- Price in [100, 300]
- VWAP premium ≥ 4%
- SL% in [2%, 10%]
- Position limits not exceeded

## Rule 11: SL Failure Circuit Breaker
- MAX_SL_FAILURE_COUNT = 3 consecutive SL failures → halt trading + Telegram alert
- EMERGENCY_EXIT_RETRY_COUNT = 5 retries for emergency market exits

## Rule 12: Order Modification Threshold
- MODIFICATION_THRESHOLD = 1.00 Rs (min price change to trigger order modification)
- Prevents excessive broker API calls and RMS flags from micro-modifications

## Rule 13: Graceful Shutdown
On shutdown:
1. Cancel all pending entry orders
2. Close all active positions (MIS intraday — must square off)
3. Persist state to SQLite
4. Close WebSocket connections
- SHUTDOWN_TIMEOUT = 9 seconds
- SHUTDOWN_FORCE_MARKET_ORDERS = True (use MARKET orders for fast exit)

## Rule 14: Reconciliation
- Sync internal positions with broker every 60 seconds
- Trust broker as source of truth
- On mismatch: update internal state to match broker, log warning

## Rule 15: Log All Critical Events
Use structured tags:
```
[ORDER]  Placing LIMIT for 24000CE @ 130.00 qty=650
[FILL]   Entry 24000CE @ 129.95 — placing exit SL trigger=143 limit=146
[EXIT]   24000CE closed Entry=129.95 Exit=135.00 PnL=+3244 R=+0.50
[SUMMARY] Day: +2.5R (5 trades, 3W 2L)
[CRITICAL] LIVE MODE ACTIVATED — ORDERS ARE REAL
```

## Rule 16: NO Emojis in Python Logs/Terminal
Use ASCII only in all `logger.*()` calls and `print()` statements:
- Wrong: `logger.info("✅ Connected")`
- Right: `logger.info("[CONNECT] Connected")`
- **Exception**: `telegram_notifier.py` may use emojis (Telegram renders them correctly)

## Rule 17: EC2 Deployment Safety
- **Never deploy during market hours (9:15 AM – 3:30 PM IST)**
- Always `git pull` and test locally first
- `docker-compose down` and `docker-compose down -v` are both safe (data is in bind mounts under `./data/`, not named volumes)
- **Never delete `./data/` directory** on EC2 — contains Historify DuckDB, trading state, logs
- Container health monitor via cron (every 2 min) — sends Telegram on crash

## Rule 18: Three-Way Sync
Laptop ↔ GitHub ↔ EC2: always pull before making changes; never force push; commit EC2 changes immediately.

## Non-Negotiables Summary

| Rule | Constraint |
|------|-----------|
| Paper Trading | PAPER_TRADING=true by default |
| Max Positions | 5 total, 3 CE, 3 PE |
| Daily Target | +5R auto-exit |
| Daily Stop | −5R auto-exit |
| Market Close | 3:15 PM force exit |
| Position Sizing | R-based, capped at 10 lots |
| Data Freshness | Auto-shutdown if stale >30s |
| Duplicate Orders | One pending per symbol |
| SL Circuit Breaker | Halt after 3 consecutive failures |
| Intraday Only | MIS product, close by 3:15 PM |
| No Emojis | ASCII only in logs/terminal |
| No Data Deletion | Never delete `./data/` dir on EC2 |
| No Market Hours Deploy | Before 9:15 AM or after 3:30 PM |
