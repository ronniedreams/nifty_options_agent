# Order Execution Theory - Proactive Limit Order System

## Overview

The strategy uses **proactive limit order placement** to enter positions when swing lows break. This is different from reactive market orders that execute AFTER the break is confirmed.

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
2. Place LIMIT order BEFORE break (at swing_low - 0.05)
3. Order waits in market
4. When price breaks, order fills at our limit price
5. No slippage!
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
  1. SL points closest to 10 Rs (abs(sl_points - 10) minimum)
  2. If tied, highest entry price
- The best strike(s) per option type are tracked in `current_best` and are eligible for order placement

### Position Availability
- No pending order for the same strike at the current swing low
- Max positions limit not reached (5 total, max 3 CE, max 3 PE)
- Do not duplicate orders for the same strike/swing combination

## Order Types

### Entry Order: LIMIT
```
Type: LIMIT
Price: swing_low - 0.05 (1 tick below swing)
Quantity: Based on R_VALUE position sizing
Product: MIS (intraday)
Action: SELL (short the option)
```

**Why LIMIT, not MARKET?**
- Guarantees fill price (no slippage)
- Order waits at our desired level
- Fills when swing breaks naturally

**Why swing_low - 0.05?**
- Options tick size is 0.05
- Ensures order is BELOW swing low
- When price drops to swing_low, order fills

### Stop-Loss Order: SL-L (Stop-Loss Limit)
```
Type: SL-L
Trigger Price: highest_high (SL level)
Limit Price: highest_high + 3 (buffer for fill)
Quantity: Same as entry
Product: MIS
Action: BUY (close the short)
```

**Placed IMMEDIATELY after entry fills**
- Not placed before entry (no position yet)
- Placed as soon as entry confirmation received

**Why SL-L instead of SL-M?**
- SL-M (market) can have extreme slippage in fast markets
- SL-L gives 3 Rs buffer above trigger for fill
- Better control over exit price

## Order Lifecycle

### Stage 1: Qualification
```
Swing detected → Static filter → VWAP filter → SL% filter → Tie-breaker
```

### Stage 2: Order Placement
```
Best strike selected → Check position availability → Place LIMIT order
```

### Stage 3: Order Monitoring
```
Every 10 seconds: Check order status (OPEN/COMPLETE)
If COMPLETE: Entry filled → Place SL-L order
```

### Stage 4: Position Management
```
Monitor position → Track R-multiples → Exit at ±5R or 3:15 PM
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
  2. Current strike gets disqualified (SL% >10%)

### Rule 2: One Order Per Option Type
- Maximum one pending CE order
- Maximum one pending PE order
- Cancel old order if new best strike selected

### Rule 3: Order Modification
**When to modify existing order:**
- Same symbol remains best
- But swing_low updated (swing update feature)
- Modify order price to new swing_low - 0.05

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
Old: LIMIT order at 80 - 0.05 = 79.95
New swing: 75
Action: Modify order price to 75 - 0.05 = 74.95
```

### If order ALREADY filled:
- No change (position already entered at old level)
- SL remains based on highest_high at time of entry

## Position Sizing

Formula based on R_VALUE (₹6,500 per position):

```
Risk per unit = Entry price - SL price
Required lots = R_VALUE / (Risk per unit × LOT_SIZE)
Final lots = min(Required lots, MAX_LOTS_PER_POSITION)
Final quantity = Final lots × LOT_SIZE
```

**Example:**
```
Entry: 150
SL: 160
Risk per unit: 10 Rs
Required lots: 6500 / (10 × 65) = 10 lots
Final lots: min(10, 10) = 10 lots
Quantity: 10 × 65 = 650
```


## Order Cancellation/Disqualification Triggers

### 1. Disqualification
- SL% exceeds `MAX_SL_PERCENT` (from config; highest_high grew too much)
- Swing breaks before order fills
- Action: Cancel order, remove from `qualified_candidates` pool

### 2. Better Strike Available
- Different strike now has better tie-breaker score
- Action: Cancel current order, place new order

### 3. Daily Limits Hit
- Cumulative R reaches +5 or -5
- Action: Cancel all pending orders, exit all positions

### 4. Market Close
- 3:15 PM IST reached
- Action: Cancel all orders, force exit all positions

### 5. Position Filled
- Order fills, position created
- Action: Order removed from pending, SL order placed

## Order Status Flow

```
NO_ORDER → ORDER_PLACED → ORDER_FILLED → POSITION_ACTIVE
   ↓           ↓              ↓               ↓
REJECTED   CANCELLED      SL_HIT         EXITED
```

### State Transitions:

**NO_ORDER → ORDER_PLACED:**
- Trigger: Strike qualifies and passes all filters
- Action: Place LIMIT order at swing_low - 0.05

**ORDER_PLACED → ORDER_FILLED:**
- Trigger: Order status = COMPLETE
- Action: Create position, place SL-L order

**ORDER_PLACED → CANCELLED:**
- Trigger: Disqualification, better strike, or daily limits
- Action: Remove order, update state

**POSITION_ACTIVE → EXITED:**
- Trigger: SL hit, target hit, or force exit
- Action: Close position, log trade

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

- **Stage-1 (Static):** Shows all `swing_candidates` that passed static filters
- **Stage-2 (Dynamic):** Shows all `qualified_candidates` that passed VWAP and are dynamically evaluated for SL%
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

- [ ] Orders placed when strike qualifies
- [ ] Orders NOT placed when filters fail
- [ ] Orders cancelled when disqualified
- [ ] Orders modified when swing updates
- [ ] SL orders placed after entry fills
- [ ] Position sizing correct
- [ ] Order status polling works
- [ ] Multiple strikes handled (tie-breaker)
- [ ] No duplicate orders
- [ ] Error handling graceful

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
1. SL% calculation (is it >10% right after placement?)
2. Different strike becoming best instantly?
3. Swing break detection (is swing breaking immediately?)

### Issue 3: Orders Not Filling
**Symptoms:** Order pending for long time, never fills

**Check:**
1. Limit price too aggressive (swing_low - 0.05 too far from market?)
2. Liquidity issues in that strike?
3. Price never reached swing low?
4. Order quantity too large for available liquidity?

### Issue 4: SL Orders Not Placing
**Symptoms:** Entry fills, but no SL order

**Check:**
1. Order fill detection working?
2. Position tracker updated?
3. Order manager received fill notification?
4. API error when placing SL?

## Summary

The order execution system is designed for:

1. **Proactive placement** - Orders ready before break
2. **Price control** - LIMIT orders prevent slippage
3. **Minimal churn** - Keep orders unless disqualified
4. **Risk management** - SL-L orders placed immediately
5. **Position sizing** - R-based quantity calculation
6. **State tracking** - Full lifecycle monitoring

**Key Principle:** Orders should be placed and kept stable. Only cancel/modify when necessary (disqualification or better opportunity). This reduces API calls, broker flags, and execution complexity.
