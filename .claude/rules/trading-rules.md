---
paths: baseline_v1_live.py, order_manager.py, position_tracker.py, state_manager.py
---

# Trading System Rules

## Core Principles

1. **Always maintain state** - Persist all position changes to SQLite immediately
2. **Fail safe** - Default to PAPER_TRADING=true; never accidentally go live
3. **Real-time evaluation** - Swing detection on bar close, filter evaluation on every tick
4. **Log everything** - Use standardized tags for debugging
5. **Proactive orders** - Place SL entry orders BEFORE swing breaks, not after

## Timestamp & Timezone

- Always use IST timezone: `datetime.now(IST)` where `IST = pytz.timezone('Asia/Kolkata')`
- Never use UTC or local system time
- Format in logs: `YYYY-MM-DD HH:MM:SS IST`

## Position Management

### Position Sizing Formula (R-Based Primary)
```
Risk per unit = abs(Entry Price - SL Price)
Required lots = R_VALUE / (Risk per unit × LOT_SIZE)
Final lots = min(Required lots, MAX_LOTS_PER_POSITION)  # Configurable safety cap
Final quantity = Final lots × LOT_SIZE
```

**Key Points:**
- R_VALUE is configurable in config.py (default: 6500)
- MAX_LOTS_PER_POSITION is a configurable safety cap (default: 15)
- R-based sizing is PRIMARY, cap is SECONDARY (only activates for tight SL scenarios)

### Position Limits (Configurable in config.py)
- MAX_POSITIONS: Total concurrent positions (default: 5)
- MAX_CE_POSITIONS: Max CE positions (default: 3)
- MAX_PE_POSITIONS: Max PE positions (default: 3)
- Check limits before placing ANY order

### Daily Exits (Automatic, Configurable)
- Exit all positions at DAILY_TARGET_R (configurable, default: +5.0 R)
- Exit all positions at DAILY_STOP_R (configurable, default: -5.0 R)
- Force close all positions at FORCE_EXIT_TIME (3:15 PM IST)
- Calculate cumulative R-multiple from daily summary

## Broker Integration

### Error Handling
- All broker API calls use 3-retry logic with 2-second delay between retries
- Log retry attempt numbers: `Attempt 1/3`, `Attempt 2/3`, etc.
- After 3 failures, log error and skip (don't crash system)
- Trust broker as source of truth for positions and orders

### Order Types
- **Entry**: SL orders (trigger: swing_low - tick, limit: trigger - 3)
- **Exit SL**: SL orders (trigger: highest_high + 1, limit: trigger + 3)
- **Product**: Always MIS (intraday) for NIFTY options
- **Exchange**: Always NFO for NIFTY
- **Action**: SELL for entry (short options), BUY for SL (cover)

### Order Status Polling
- Poll every 10 seconds (ORDERBOOK_POLL_INTERVAL)
- Check for status change: OPEN → COMPLETE
- Update position when order fills
- When swing breaks (price < swing_low) → Order TRIGGERS and FILLS (this is our entry!)

### Order Cancellation (When to Cancel)
Cancel pending orders when:
- Strike disqualified (SL% exceeds MAX_SL_PERCENT)
- Different strike becomes "best" (tie-breaker selects new winner)
- Daily limits hit (DAILY_TARGET_R or DAILY_STOP_R reached)
- Market close approaching (3:15 PM IST)
- System shutdown

**NOT a cancellation reason:** Swing breaking - that's the ENTRY TRIGGER!

## Logging Standards

Use these tags consistently:
```python
logger.info("[SWING] New swing detected: NIFTY30DEC2526000CE @ 130.50, VWAP=125.00, Premium=4.4%")
logger.info("[FILTER] Stage-1 PASS: 26000CE in swing_candidates")
logger.info("[FILTER] Stage-2 FAIL: 26050CE disqualified (SL% 12.1% > 10%)")
logger.info("[ORDER] Placing SL order: 26000CE trigger=129.95 limit=126.95 for 650 qty")
logger.info("[FILL] Entry filled at 129.95, placing SL at 141 (highest_high=140)")
logger.info("[EXIT] +5R target hit at 15:10:30, closing all positions")
logger.info("[RECONCILE] Position sync: 3 active, 0 mismatches")
```

## Symbol Format

Always use this format:
```python
symbol = f"NIFTY{expiry}{strike}CE"  # e.g., NIFTY30DEC2526000CE
symbol = f"NIFTY{expiry}{strike}PE"  # e.g., NIFTY30DEC2526000PE
```

Don't use spaces, dashes, or alternative formats.

## State Persistence

### When to Save
- After every position creation (entry filled)
- After every position modification (SL hit, manual exit)
- After every order placement or cancellation
- Before system shutdown

### What to Save
- Position: symbol, entry_price, entry_time, quantity, sl_price, status, pnl, r_multiple
- Order: order_id, symbol, price, quantity, status, timestamp
- Daily summary: date, total_trades, winning_trades, cumulative_r, pnl

### Database Consistency
- Use SQLite transactions for multi-row updates
- Never leave database in inconsistent state
- Verify writes succeeded before proceeding
- Log database errors explicitly

## Paper Trading Check

```python
# CRITICAL: Check paper trading mode before any real trades
if not PAPER_TRADING:
    logger.warning("[CRITICAL] PAPER_TRADING=false - LIVE MODE ENABLED")
    # Additional validation before going live
    # Never silently place live trades
```

## Daily Startup

- [ ] Check database integrity: `python -m baseline_v1_live.check_system`
- [ ] Verify config parameters loaded correctly
- [ ] Validate OpenAlgo connectivity (http://127.0.0.1:5000 or EC2 dashboard)
- [ ] Verify broker is connected (Status: Connected)
- [ ] Clear any stale pending orders from previous day
- [ ] Initialize daily statistics (reset daily counters)
- [ ] Verify WebSocket connection establishes

## Common Gotchas

### Risk Calculation Mismatch
- **Issue**: SL% calculated differently at order placement vs SL trigger
- **Fix**: Use same formula everywhere: `(highest_high + 1 - swing_low) / swing_low`
- **Test**: Validate SL% in order logs matches position_tracker calculations

### Order Cancellation Race
- **Issue**: Order cancels due to SL% filter, but order was already partially filled
- **Fix**: Check order status BEFORE attempting cancel; handle partial fill gracefully
- **Test**: Monitor logs for unexpected position creations after cancellation

### Position Sync Mismatch
- **Issue**: Internal position count differs from broker's positionbook
- **Fix**: Trust broker as source of truth; reconcile every 60 seconds
- **Test**: Cross-check internal positions with broker dashboard after each trade

### Timezone Confusion
- **Issue**: Using UTC or system local time instead of IST
- **Fix**: Always use `datetime.now(IST)` and validate timezone in logs
- **Test**: Verify 3:15 PM force exit happens at correct IST time

## Validation Checkpoints

Before placing order:
- [ ] Strike passes all filters (Stage-1, Stage-2, Stage-3)
- [ ] Position count < MAX_POSITIONS
- [ ] CE/PE sub-limits not exceeded
- [ ] No duplicate order for same strike/swing
- [ ] Entry price within MIN_ENTRY_PRICE to MAX_ENTRY_PRICE
- [ ] SL% within MIN_SL_PERCENT to MAX_SL_PERCENT

After order fills:
- [ ] Position created in database
- [ ] SL order placed immediately
- [ ] Position tracking updated
- [ ] Notification sent (if enabled)

At market close (3:15 PM):
- [ ] All pending orders cancelled
- [ ] All active positions closed
- [ ] Daily summary calculated
- [ ] State saved to database

## Proactive Order Flow

**Key Concept:** Orders are placed BEFORE swing breaks, not after.

```
1. Strike qualifies (passes all 3 filter stages)
   ↓
2. Place SL entry order immediately
   - Trigger: swing_low - tick_size (e.g., 130.00 - 0.05 = 129.95)
   - Limit: trigger - 3 Rs (e.g., 129.95 - 3 = 126.95)
   - Order sits dormant at exchange
   ↓
3. Price drops to trigger → Order FILLS → Position opened
   ↓
4. IMMEDIATELY place exit SL order
   - Trigger: highest_high + 1 Rs
   - Limit: trigger + 3 Rs
   ↓
5. Monitor position until SL hit or daily exit
```

**Why Proactive?**
- No latency between swing break and order placement
- Order ready at exchange before price reaches trigger
- Better fills vs chasing with market orders

## EC2/Docker Deployment

### Environment Differences

| Aspect | Local (Laptop) | EC2 (Production) |
|--------|----------------|------------------|
| OpenAlgo URL | http://127.0.0.1:5000 | http://openalgo:5000 (Docker) |
| Dashboard | http://127.0.0.1:5000 | https://openalgo.ronniedreams.in |
| Logs | File system | `docker-compose logs` |
| Start command | `python -m baseline_v1_live.baseline_v1_live` | `docker-compose up -d` |

### Three-Way Sync (Laptop ↔ GitHub ↔ EC2)

```bash
# Standard flow: Laptop → GitHub → EC2
git add . && git commit -m "message" && git push origin feature/docker-ec2-fixes

# On EC2
cd ~/nifty_options_agent && ./deploy.sh
```

**Critical Rules:**
- Always pull before making changes
- Never force push
- Commit EC2 changes immediately to maintain sync
- Never deploy during market hours (9:15 AM - 3:30 PM IST)
