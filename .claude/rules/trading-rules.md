---
paths: baseline_v1_live.py, order_manager.py, position_tracker.py, state_manager.py
---

# Trading System Rules

## Core Principles

1. **Always maintain state** - Persist all position changes to SQLite immediately
2. **Fail safe** - Default to PAPER_TRADING=true; never accidentally go live
3. **Real-time evaluation** - Don't batch updates; process every tick/bar
4. **Log everything** - Use standardized tags for debugging

## Timestamp & Timezone

- Always use IST timezone: `datetime.now(IST)` where `IST = pytz.timezone('Asia/Kolkata')`
- Never use UTC or local system time
- Format in logs: `YYYY-MM-DD HH:MM:SS IST`

## Position Management

### Position Sizing Formula
```
Risk per unit = Entry Price - SL Price
Required lots = R_VALUE / (Risk per unit × LOT_SIZE)
Final lots = min(Required lots, MAX_LOTS_PER_POSITION)
Final quantity = Final lots × LOT_SIZE
```

### Position Limits (Hardcoded, Non-Negotiable)
- Max 5 total concurrent positions
- Max 3 CE positions
- Max 3 PE positions
- Check `MAX_POSITIONS` before placing ANY order

### Daily Exits (Automatic)
- Exit all positions at DAILY_TARGET_R = +5.0 R
- Exit all positions at DAILY_STOP_R = -5.0 R
- Force close all positions at FORCE_EXIT_TIME (3:15 PM IST)
- Calculate cumulative R-multiple from daily summary

## Broker Integration

### Error Handling
- All broker API calls use 3-retry logic with 2-second delay between retries
- Log retry attempt numbers: `Attempt 1/3`, `Attempt 2/3`, etc.
- After 3 failures, log error and skip (don't crash system)
- Trust broker as source of truth for positions and orders

### Order Types
- **Entry**: LIMIT orders at `swing_low - 0.05` (proactive placement)
- **SL**: SL-L orders (stop-loss limit) with trigger at highest_high + 1 Rs
- **SL Price Buffer**: `highest_high + 3` for fill protection
- **Product**: Always MIS (intraday) for NIFTY options
- **Exchange**: Always NFO for NIFTY
- **Action**: SELL for entry (short options), BUY for SL (cover)

### Order Status Polling
- Poll every 10 seconds (ORDERBOOK_POLL_INTERVAL)
- Check for status change: OPEN → COMPLETE
- Update position when order fills
- Cancel order if swing breaks (price < swing_low)

## Logging Standards

Use these tags consistently:
```python
logger.info("[SWING] New swing detected: NIFTY30DEC2526000CE @ 130.50, VWAP=125.00, Premium=4.4%")
logger.info("[FILTER] Stage-1 PASS: 26000CE in swing_candidates")
logger.info("[FILTER] Stage-2 FAIL: 26050CE disqualified (SL% 12.1% > 10%)")
logger.info("[ORDER] Placing LIMIT order: 26000CE @ 129.95 for 650 qty")
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

- [ ] Check database integrity: `python -m live.check_system`
- [ ] Verify config parameters loaded correctly
- [ ] Validate OpenAlgo connectivity
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
