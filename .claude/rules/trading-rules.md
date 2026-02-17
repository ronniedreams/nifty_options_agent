---
paths: baseline_v1_live.py, order_manager.py, position_tracker.py, state_manager.py, notification_manager.py, startup_health_check.py
---

# Trading System Rules

## Core Principles
1. **Proactive orders** — Place LIMIT entry orders BEFORE swing breaks, not after
2. **Always persist state** — Save all position/order changes to SQLite immediately
3. **Paper trading default** — PAPER_TRADING=true; never accidentally go live
4. **Real-time evaluation** — Swing detection on bar close, filter on every tick

## Timezone
- Always IST: `datetime.now(IST)` where `IST = pytz.timezone('Asia/Kolkata')`
- Never UTC or system local time

## Symbol Format
```python
symbol = f"NIFTY{expiry}{strike}CE"  # e.g. NIFTY20FEB2624000CE
symbol = f"NIFTY{expiry}{strike}PE"
```

## Position Sizing
```
risk_per_unit = abs(entry_price - sl_price)
lots_required = R_VALUE / (risk_per_unit × LOT_SIZE)
final_lots = min(lots_required, MAX_LOTS_PER_POSITION)   # cap = 10
quantity = int(final_lots) × LOT_SIZE
```
- R_VALUE = 6500, LOT_SIZE = 65, MAX_LOTS_PER_POSITION = 10

## Position Limits (config.py)
- MAX_POSITIONS = 5 (total concurrent)
- MAX_CE_POSITIONS = 3, MAX_PE_POSITIONS = 3
- Check ALL limits before placing any order

## Daily Exits (Automatic)
- DAILY_TARGET_R = +5.0R → close all, stop trading
- DAILY_STOP_R = −5.0R → close all, stop trading
- FORCE_EXIT_TIME = 3:15 PM IST → force close all
- Check frequency: every 10 seconds in main loop

## Order Types
- **Entry**: LIMIT order at swing_low − tick_size (sits dormant until price drops)
- **Exit SL**: SL order (trigger = highest_high + 1, limit = trigger + 3)
- **Product**: MIS, **Exchange**: NFO, **Action**: SELL entry / BUY exit

## Order Timing
- ORDERBOOK_POLL_INTERVAL = 5 seconds (check pending order fills)
- LIMIT_ORDER_TIMEOUT = 300s (cancel unfilled limit entries after 5 min)
- SL failure threshold: MAX_SL_FAILURE_COUNT = 3 → halt trading + alert

## Broker API Pattern
- 3-retry with 2s delay for all broker calls
- `client.cancelorder(order_id=...)` — keyword is `order_id` (underscore)
- Trust broker as source of truth; reconcile positions every 60 seconds

## Order Cancellation Triggers
Cancel pending entry orders when:
- Strike disqualified (SL% exceeds MAX_SL_PERCENT)
- Different strike becomes the best via tie-breaker
- Daily limits hit
- 3:15 PM market close approaching

**NOT a cancellation reason:** Price dropping to trigger — that's the ENTRY FILL!

## State Persistence
Save to SQLite immediately after:
- Position created (entry filled)
- Position modified (SL hit, exit)
- Any order placed or cancelled
- Before shutdown

## Logging Tags
```
[SWING]      New swing detected
[FILTER]     Stage-1/2 pass or fail with reason
[ORDER]      Order placement attempt
[FILL]       Entry filled, placing exit SL
[EXIT]       Position closed, reason, R-multiple
[RECONCILE]  Position sync with broker
[RECOVERY]   Crash recovery actions
[FAILOVER]   Switching to Angel One data feed
[FAILBACK]   Switching back to Zerodha
```

## New Modules (post-Feb 2026)

### notification_manager.py
Throttles/deduplicates Telegram error alerts:
- Throttle windows: STARTUP_FAILURE=1h, WEBSOCKET/BROKER=30m−1h
- `SYSTEM_RECOVERED` always sends (no throttle)
- Aggregates multiple errors within 60s into one message
- Error types: `STARTUP_FAILURE`, `WEBSOCKET_DOWN`, `BROKER_DISCONNECTED`, `DATABASE_ERROR`, `OPENALGO_DOWN`

### startup_health_check.py
Pre-flight validation with error classification:
- Checks: OpenAlgo HTTP, API key, broker login, SQLite r/w, WebSocket
- TRANSIENT errors (connection failures): retry up to MAX_STARTUP_RETRIES=5 with backoff (20s base)
- PERMANENT errors (auth failures, config): don't retry, alert and stop
- Uses NotificationManager to send alerts

### TelegramNotifier (telegram_notifier.py)
- Constructor takes optional `instance_name` (or reads `INSTANCE_NAME` env var)
- Prefixes all messages with `[EC2]` or `[LOCAL]` for multi-instance identification
- Sends async (fire-and-forget threads), non-blocking
- Events: trade entry, trade exit, daily target, daily summary, error, position update, best strike change, swing detected
- **Emojis are OK in Telegram messages** (Rule 20 only applies to terminal/log output)

## Startup Sequence
1. `login_handler.py` — automated TOTP login if AUTOMATED_LOGIN=true
2. Wait until 9:16 AM IST (first 1-min candle closed)
3. `startup_health_check.py` — pre-flight validation
4. `auto_detector.py` — detect ATM + expiry (60 retries, graceful degradation)
5. Pipeline connects, historical bars load, reconcile crash recovery
6. Strategy loop begins

## EC2/Docker Environment

| Aspect | Local | EC2 (Docker) |
|--------|-------|--------------|
| OpenAlgo URL | http://127.0.0.1:5000 | http://openalgo:5000 |
| Angel One URL | http://127.0.0.1:5001 | http://openalgo_angelone:5000 |
| Zerodha WS | ws://127.0.0.1:8765 | ws://openalgo:8765 |
| Angel One WS | ws://127.0.0.1:8766 | ws://openalgo_angelone:8766 |
| Container name | — | baseline_v1_live |
| Logs | file system | docker logs + host-mounted ./baseline_v1_live/logs |
| State DB | live_state.db local | /app/state/live_state.db (bind mount `./data/trading_state`) |
