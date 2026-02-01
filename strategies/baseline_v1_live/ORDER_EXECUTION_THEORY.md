# Order Execution Theory - Proactive SL Order System

## Overview

The strategy uses **proactive SL (stop-limit) order placement** to enter positions when swing lows break. Orders are placed BEFORE the break happens, so they're ready to trigger and fill when price reaches our level.

## Core Concept: Proactive vs Reactive

### Reactive Approach (What We DON'T Do):
```
1. Swing low breaks (price < swing_low)
2. System detects break
3. Place MARKET order
4. Get filled at current price (slippage!)
```

### Proactive Approach (What We DO):
```
1. Swing low detected and qualified
2. Place SL order BEFORE break:
   - Trigger: swing_low - tick (e.g., swing_low - 0.05)
   - Limit: trigger - 3 Rs (buffer for fill)
3. Order sits dormant at exchange until trigger hit
4. When price drops to trigger → Order activates → Fills at limit price
5. No slippage, better entry timing!
```


## Order Placement Trigger: Three-Stage Filter Pipeline

Orders are placed only when a strike passes all three filter stages, as defined in STRIKE_FILTRATION_THEORY.md:

### Stage-1: Static Filter (swing_candidates)
- Applied ONCE when swing forms. Both filters must pass:
  - Entry price within `[MIN_ENTRY_PRICE, MAX_ENTRY_PRICE]` (from config)
  - VWAP premium ≥ `MIN_VWAP_PREMIUM` (from config) at swing formation
- If both pass, swing is added to `swing_candidates` pool (static, immutable)

### Stage-2: Dynamic SL% Filter (qualified_candidates)
- Applied EVERY bar/tick to all swings in `swing_candidates`
- SL percentage must be within `[MIN_SL_PERCENT, MAX_SL_PERCENT]` (from config)
- Formula: `sl_percent = (highest_high + 1 - swing_low) / swing_low` (+1 Rs buffer)
- If passes, swing is added to `qualified_candidates` (dynamic, mutable)

### Stage-3: Tie-Breaker (current_best)
- When multiple swings per option type pass all filters, select the best per option type using:
  1. SL points closest to 10 Rs (abs(sl_points - 10) minimum) - Primary
  2. Strike multiple of 100 (better liquidity) - Secondary tie-breaker
  3. Highest entry price - Final tie-breaker
- The best strike(s) per option type are tracked in `current_best` and are eligible for order placement

### Position Availability
- No pending order for the same strike at the current swing low
- Max positions limit not reached (configurable: MAX_POSITIONS, MAX_CE_POSITIONS, MAX_PE_POSITIONS)
- Do not duplicate orders for the same strike/swing combination

## Order Types

### Entry Order: SL (Stop-Limit)
```
Type: SL (Stop-Limit)
Trigger Price: swing_low - tick (e.g., swing_low - 0.05)
Limit Price: trigger - 3 Rs (buffer for fill)
Quantity: Based on R_VALUE position sizing (configurable)
Product: MIS (intraday)
Action: SELL (short the option)
```

**Why SL (Stop-Limit), not MARKET or regular LIMIT?**
- Order sits dormant until trigger price is hit
- Prevents fills if price never reaches our entry level
- When triggered, becomes a limit order with price control
- Better entry timing aligned with swing break

**Why swing_low - tick as trigger?**
- Options tick size is 0.05
- Trigger just below swing low ensures order activates on break
- When price drops to trigger → Order activates → Fills at limit or better

**Why trigger - 3 Rs as limit?**
- Provides buffer for execution in fast markets
- Ensures fill even with minor slippage

### Exit Stop-Loss Order: SL-L (Stop-Loss Limit)
```
Type: SL-L (Stop-Limit)
Trigger Price: highest_high + 1 Rs (buffer above highest high)
Limit Price: trigger + 3 Rs (buffer for fill)
Quantity: Same as entry
Product: MIS
Action: BUY (close the short)
```

**Placed IMMEDIATELY after entry fills**
- Not placed before entry (no position yet)
- Placed as soon as entry confirmation received

**Why +1 Rs buffer in trigger?**
- Accounts for tick-level slippage during volatile moves
- Prevents premature exits at exact highest high level
- Ensures SL triggers reliably

**Why SL-L instead of SL-M?**
- SL-M (market) can have extreme slippage in fast markets
- SL-L gives 3 Rs buffer above trigger for fill
- Better control over exit price

### Daily Target Exit: MARKET Orders

**NEW (2026-01-24):** When cumulative R reaches daily target/stop or end-of-day, all positions are closed via MARKET orders.

```
Type: MARKET
Quantity: Position quantity
Product: MIS
Action: BUY (close the short)
Triggers:
- +5R target (configurable via DAILY_TARGET_R)
- -5R stop loss (configurable via DAILY_STOP_R)
- 3:15 PM force exit (configurable via FORCE_EXIT_TIME)
```

**Why MARKET orders for daily exits?**
- Guaranteed fills (always execute)
- Exact +5R precision (±0.2R)
- All-or-nothing exit (all positions close together)
- Simple, reliable implementation
- Acceptable slippage at profit levels (~0.5-1% of ₹32,500)

**Exit Sequence:**
1. Daily target/stop reached OR time = FORCE_EXIT_TIME
2. Cancel all pending entry orders (no new positions)
3. For each open position:
   - Cancel existing exit SL order
   - Place MARKET order (BUY to cover short)
   - 3-retry logic with 2-second delay
4. Update internal state (mark positions closed)
5. Save daily summary to database
6. Send Telegram notification

**Example Log Sequence:**
```
[10:46:15] [R-CHECK] Cumulative R: +5.1R (2 closed, 3 open)
[10:46:15] [EXIT] DAILY EXIT TRIGGERED: +5R_TARGET
[10:46:15] [EXIT] Cancelling all pending orders...
[10:46:16] [EXIT] Cancelling SL for NIFTY30DEC2526000CE
[10:46:16] [MARKET-EXIT] NIFTY30DEC2526000CE qty=650 reason=+5R_TARGET
[10:46:17] [MARKET-EXIT] Order placed: ORD123456
[10:46:17] [EXIT] Cancelling SL for NIFTY30DEC2526300PE
[10:46:17] [MARKET-EXIT] NIFTY30DEC2526300PE qty=650 reason=+5R_TARGET
[10:46:18] [MARKET-EXIT] Order placed: ORD123457
[10:46:19] [SUMMARY] All positions closed. Final R: +5.1R
```

**Slippage Tolerance:**
- At +5R profit (₹32,500), typical slippage is ₹200-300 (~0.6%)
- Acceptable trade-off for guaranteed fills and exact targeting

**Fallback Protection:**
- If MARKET order fails after 3 retries, position remains open
- MIS product ensures broker auto-squares at 3:15 PM
- All positions are intraday - cannot be held overnight

**Implementation Details:**
- Method: `order_manager.place_market_order()`
- Called from: `position_tracker.close_all_positions()`
- Parameters: symbol, quantity, action, reason
- Returns: order_id or None (if failed)

## Order Lifecycle

### Stage 1: Qualification
```
Swing detected → Static filter → VWAP filter → SL% filter → Tie-breaker
```

### Stage 2: Order Placement
```
Best strike selected → Check position availability → Place SL order (trigger: swing_low - tick)
```

### Stage 3: Order Monitoring
```
Every 10 seconds: Check order status (OPEN/COMPLETE)
If COMPLETE: Entry filled → Place exit SL-L order immediately
```

### Stage 4: Position Management
```
Monitor position → Track R-multiples
If cumulative_R >= DAILY_TARGET_R or <= DAILY_STOP_R or time = FORCE_EXIT_TIME:
    → Cancel all orders → Place MARKET orders → Close all positions → Save summary
Else:
    → Continue monitoring until exit SL hit or daily exit triggers
```

## Proactive Order Management Rules

### Rule 1: Keep Orders Once Placed
**OLD (bad) behavior:**
- Cancel order if price moves >1 Rs away from swing
- Causes excessive order churn

**NEW (correct) behavior:**
- Keep order even if price moves away
- Only cancel/modify if:
  1. Different strike becomes best candidate
  2. Current strike gets disqualified (SL% exceeds MAX_SL_PERCENT)
  3. Daily limits hit (DAILY_TARGET_R or DAILY_STOP_R reached)
  4. Market close approaching (FORCE_EXIT_TIME)
- **NOT a cancel reason:** Swing breaking - that's the ENTRY TRIGGER!

### Rule 2: One Order Per Option Type
- Maximum one pending CE order
- Maximum one pending PE order
- Cancel old order if new best strike selected

### Rule 3: Order Modification
**When to modify existing order:**
- Same symbol remains best
- But swing_low updated (swing update feature)
- Modify order trigger/limit to: new_swing_low - tick, trigger - 3

**When to cancel and place new:**
- Different symbol becomes best
- Cancel old symbol's order
- Place new order for new symbol

## Integration with Swing Updates

When a swing low gets updated (e.g., 80 → 75):

### If NO order placed yet:
- No action needed
- Next evaluation will use new swing_low (75)

### If order ALREADY placed:
```
Old: SL order with trigger at 80 - 0.05 = 79.95
New swing: 75
Action: Modify order trigger to 75 - 0.05 = 74.95, limit to 74.95 - 3 = 71.95
```

### If order ALREADY filled:
- No change (position already entered at old level)
- Exit SL remains based on highest_high at time of entry

## Position Sizing

Formula based on R_VALUE (configurable, default Rs.6,500 per position):

```
Risk per unit = SL price - Entry price (for short positions)
Required lots = R_VALUE / (Risk per unit × LOT_SIZE)
Final lots = min(Required lots, MAX_LOTS_PER_POSITION)  # Safety cap at 15 (configurable)
Final quantity = Final lots × LOT_SIZE
```

**R-based sizing is PRIMARY, safety cap is SECONDARY** (only activates for tight SL scenarios).

**Example:**
```
Entry: 150
SL: 160 (highest_high + 1)
Risk per unit: 10 Rs
Required lots: 6500 / (10 × 65) = 10 lots
Final lots: min(10, 15) = 10 lots
Quantity: 10 × 65 = 650
```


## Order Cancellation/Disqualification Triggers

### 1. Disqualification
- SL% exceeds `MAX_SL_PERCENT` (highest_high grew too much)
- Action: Cancel order, remove from `qualified_candidates` pool

### 2. Better Strike Available
- Different strike now has better tie-breaker score
- Action: Cancel current order, place new order for new best strike

### 3. Daily Limits Hit
- Cumulative R reaches DAILY_TARGET_R or DAILY_STOP_R (configurable, default ±5R)
- Action: Cancel all pending orders, exit all positions

### 4. Market Close
- FORCE_EXIT_TIME reached (configurable, default 3:15 PM IST)
- Action: Cancel all orders, force exit all positions

### 5. Position Filled
- Order triggers and fills, position created
- Action: Order removed from pending, exit SL order placed immediately

### NOT a Cancellation Trigger:
- **Swing breaking** - That's the ENTRY TRIGGER! When price < swing_low, the SL order triggers and FILLS

## Order Status Flow

```
NO_ORDER → ORDER_PLACED → ORDER_FILLED → POSITION_ACTIVE
   ↓           ↓              ↓               ↓
REJECTED   CANCELLED      SL_HIT         EXITED
```

### State Transitions:

**NO_ORDER → ORDER_PLACED:**
- Trigger: Strike qualifies and passes all filters
- Action: Place SL order (trigger: swing_low - tick, limit: trigger - 3)

**ORDER_PLACED → ORDER_FILLED:**
- Trigger: Order status = COMPLETE (price dropped to trigger, order filled)
- Action: Create position, place exit SL-L order immediately

**ORDER_PLACED → CANCELLED:**
- Triggers: Disqualification (SL% out of range), better strike selected, daily limits hit, or market close
- Action: Remove order, update state
- **NOT a cancel trigger:** Swing breaking - that's when order FILLS!

**POSITION_ACTIVE → EXITED:**
- Trigger: Exit SL hit, daily target/stop hit, or force exit time
- Action: Close position, log trade with R-multiple

## Critical Implementation Details

### 1. Order Status Polling
- Poll every 10 seconds (ORDERBOOK_POLL_INTERVAL)
- Check if status changed from OPEN → COMPLETE
- Don't spam broker API (rate limits!)

### 2. Order ID Tracking
```python
pending_orders = {
    'CE': {
        'symbol': 'NIFTY06JAN2626250CE',
        'order_id': 'ABC123',
        'swing_low': 126.45,
        'placed_at': datetime
    },
    'PE': None
}
```

### 3. Idempotency
- Don't place duplicate orders
- Check if order already exists before placing
- Track order state in memory AND database

### 4. Error Handling
**Order Rejection:**
- Log rejection reason
- Mark candidate as rejected
- Don't retry immediately (wait for next evaluation cycle)

**Broker API Errors:**
- Retry with exponential backoff
- Log error details
- Don't crash strategy

**Position Mismatch:**
- Reconcile with broker's positionbook
- Trust broker as source of truth
- Update internal state to match


## Dashboard Integration

The dashboard reflects the real-time state of the order execution pipeline, matching the three-stage filter model:

- **Stage-1 (Static):** Shows all `swing_candidates` that passed static filters (price range + VWAP premium)
- **Stage-2 (Dynamic):** Shows all `qualified_candidates` that pass the dynamic SL% filter (evaluated every tick)
- **Stage-3 (Final Qualifiers):** Shows the current best strike(s) per option type (`current_best`), eligible for order placement
- **Recent Rejections:** Shows recently rejected strikes with config-based rejection reasons
- **Filter Summary:** Shows counts for each pool and rejection reason

### Best Strikes Table
Shows current qualified strikes and order status:
- Symbol
- Swing Low
- Highest High
- SL Points
- VWAP Premium %
- SL %
- Order Status (No Order / Pending / Filled)
- Order ID (if pending/filled)

### Order Triggers Log
Tracks all order placement/cancellation events:
- Timestamp
- Action (place / cancel / modify)
- Symbol
- Reason (uses config variable names)
- Order details

## Testing Checklist

Before going live, verify:

**Entry Orders:**
- [ ] Orders placed when strike qualifies
- [ ] Orders NOT placed when filters fail
- [ ] Orders cancelled when disqualified
- [ ] Orders modified when swing updates
- [ ] No duplicate orders
- [ ] Order status polling works
- [ ] Multiple strikes handled (tie-breaker)

**Position Management:**
- [ ] SL orders placed after entry fills
- [ ] Position sizing correct (R-based formula)
- [ ] Exit SL triggers correctly

**Daily Exits (NEW):**
- [ ] +5R target triggers market orders
- [ ] -5R stop triggers market orders
- [ ] 3:15 PM force exit triggers market orders
- [ ] All SL orders cancelled before market orders
- [ ] Market orders visible in broker dashboard
- [ ] Positions actually close at broker (not just internal state)
- [ ] Internal state matches broker after exit
- [ ] Daily summary saved correctly
- [ ] Telegram notifications sent

**Error Handling:**
- [ ] Market order retry logic works (3 attempts)
- [ ] Graceful degradation if order fails
- [ ] Network failures handled
- [ ] No crashes on broker API errors

## Common Issues

### Issue 1: Orders Not Placing
**Symptoms:** Qualified strikes shown, but no orders placed

**Check:**
1. Position availability (max positions reached?)
2. Pending order already exists?
3. Order manager service running?
4. API key valid?
5. Broker connectivity?

### Issue 2: Orders Cancelled Immediately
**Symptoms:** Order placed, then cancelled within seconds

**Check:**
1. SL% calculation (is it exceeding MAX_SL_PERCENT right after placement?)
2. Different strike becoming best instantly?
3. Note: Swing breaking should NOT cause cancellation - that's when order should FILL

### Issue 3: Orders Not Filling
**Symptoms:** Order pending for long time, never fills

**Check:**
1. Trigger price never reached (price never dropped to swing_low - tick)?
2. Liquidity issues in that strike?
3. Order quantity too large for available liquidity?
4. Note: SL orders only trigger when price reaches trigger level

### Issue 4: SL Orders Not Placing
**Symptoms:** Entry fills, but no SL order

**Check:**
1. Order fill detection working?
2. Position tracker updated?
3. Order manager received fill notification?
4. API error when placing SL?

### Issue 5: Daily Exit Not Closing Positions (NEW)
**Symptoms:** +5R target reached, logs show exit triggered, but positions still open at broker

**Check:**
1. Is order_manager passed to PositionTracker? (should be in baseline_v1_live.py line 121)
2. Check logs for `[MARKET-EXIT]` tags - are market orders being placed?
3. Check broker dashboard - do you see MARKET orders?
4. API errors when placing market orders? (check 3-retry logs)
5. Network connectivity to broker?

**Expected Logs:**
```
[EXIT] DAILY EXIT TRIGGERED: +5R_TARGET
[EXIT] Cancelling SL for NIFTY30DEC2526000CE
[MARKET-EXIT] NIFTY30DEC2526000CE qty=650 reason=+5R_TARGET
[MARKET-EXIT] Order placed: ORD123456
```

**If market orders failing:**
- Check available margin (though shouldn't be needed for closing)
- Verify symbol format (should match OpenAlgo format)
- Check broker rate limits (retry logic should handle this)
- Verify API key valid and not expired

**Fallback:**
- All positions are MIS (intraday) product
- Broker will auto-square positions at 3:15 PM
- This is your safety net if market orders fail

### Issue 6: Market Order Slippage Too High
**Symptoms:** Positions closing, but exit prices much worse than expected

**Check:**
1. What's the profit level? (slippage more noticeable on small profits)
2. Market volatility at exit time (high volatility = more slippage)
3. Position size vs liquidity (large positions in illiquid strikes)

**Acceptable Slippage:**
- At +5R (₹32,500 profit): ₹200-500 is typical (~0.6-1.5%)
- At -5R (₹32,500 loss): Similar slippage acceptable

**If Excessive (>2% of profit):**
- Consider LIMIT orders instead of MARKET (but may not fill)
- Reduce position sizes (better liquidity)
- Trade more liquid strikes (round strikes like 24000, 24100)

## Summary

The order execution system is designed for:

1. **Proactive placement** - SL orders ready before break
2. **Price control** - SL (stop-limit) orders prevent slippage on entry and individual exits
3. **Minimal churn** - Keep orders unless disqualified
4. **Risk management** - Exit SL-L orders placed immediately on fill
5. **Position sizing** - R-based quantity calculation with configurable safety cap
6. **State tracking** - Full lifecycle monitoring
7. **Daily exits (NEW)** - MARKET orders for guaranteed fills at +5R/-5R/EOD

**Key Principle:** Orders should be placed and kept stable. Only cancel/modify when necessary (disqualification or better opportunity). Swing breaking is the ENTRY TRIGGER, not a cancellation reason. This reduces API calls, broker flags, and execution complexity.

**Daily Exit Mechanism:** When cumulative R reaches daily target/stop or EOD time:
1. Cancel all pending entry orders (prevent new positions)
2. Cancel all exit SL orders (replace with market orders)
3. Place MARKET orders for each position (guaranteed fills)
4. Update internal state and save summary
5. Fallback: MIS product ensures broker auto-squares at 3:15 PM if orders fail
