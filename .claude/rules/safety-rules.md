---
paths: baseline_v1_live.py, order_manager.py, position_tracker.py, config.py
---

# Safety Rules - Critical Constraints (Non-Negotiable!)

## Paper Trading Default (ALWAYS!)

### Rule 1: PAPER_TRADING=true by Default

**In .env file:**
```
PAPER_TRADING=true
```

**In config.py:**
```python
PAPER_TRADING = os.getenv('PAPER_TRADING', 'true').lower() == 'true'
```

### Enforcement

- **Every startup**: Verify PAPER_TRADING status before any order placement
- **Notification**: Log warning if PAPER_TRADING=false
- **Check Point**: No live order placed without explicit live mode activation

### Going Live Process

1. Thoroughly test with PAPER_TRADING=true for minimum 1 week
2. Document all test results and performance metrics
3. Create backup of live_state.db
4. Change .env: `PAPER_TRADING=false`
5. Restart system
6. Monitor first hour closely
7. Log: `[CRITICAL] LIVE MODE ACTIVATED - ORDERS ARE REAL`

## Position Limits (Hardcoded in config.py)

### Rule 2: Maximum Concurrent Positions

```python
MAX_POSITIONS = 5           # Total concurrent positions
MAX_CE_POSITIONS = 3        # Max CE positions
MAX_PE_POSITIONS = 3        # Max PE positions
```

### Enforcement

**Before placing every order:**
```python
if total_active_positions >= MAX_POSITIONS:
    logger.warning(f"[LIMIT] Cannot place order: {total_active_positions}/{MAX_POSITIONS} positions active")
    return False

if ce_positions >= MAX_CE_POSITIONS and symbol.endswith('CE'):
    logger.warning(f"[LIMIT] Cannot place CE: {ce_positions}/{MAX_CE_POSITIONS} CE positions active")
    return False

if pe_positions >= MAX_PE_POSITIONS and symbol.endswith('PE'):
    logger.warning(f"[LIMIT] Cannot place PE: {pe_positions}/{MAX_PE_POSITIONS} PE positions active")
    return False
```

**Hardcoded Check:**
- These limits are in config.py, NOT in order_manager.py (prevents accidental modification)
- Don't make them configurable or user-adjustable at runtime

## Daily Profit/Loss Targets (Automatic Exit)

### Rule 3: Daily Target Exit (+5R)

When cumulative R-multiple reaches +5.0:

```python
if daily_cumulative_r >= DAILY_TARGET_R:  # +5.0
    logger.info(f"[EXIT] Daily target +{DAILY_TARGET_R}R reached. Closing all positions.")
    close_all_positions()
    cancel_all_pending_orders()
    stop_trading_for_today()
```

### Rule 4: Daily Stop Loss Exit (-5R)

When cumulative R-multiple reaches -5.0:

```python
if daily_cumulative_r <= DAILY_STOP_R:  # -5.0
    logger.info(f"[EXIT] Daily stop loss {DAILY_STOP_R}R hit. Closing all positions.")
    close_all_positions()
    cancel_all_pending_orders()
    stop_trading_for_today()
```

### Rule 5: Force Market Close Exit (3:15 PM IST)

At exactly 3:15 PM IST:

```python
if current_time >= FORCE_EXIT_TIME:  # 15:15 in IST
    logger.info(f"[EXIT] Market close (3:15 PM IST). Force closing all positions.")
    close_all_positions()
    cancel_all_pending_orders()
    emit_daily_summary()
```

### Enforcement

- **Check frequency**: Every 10 seconds (in main event loop)
- **No exceptions**: Always close at these points
- **Log everything**: Log closing reason, positions closed, P&L
- **Persist**: Save daily summary to database

## Capital & Risk Constraints

### Rule 6: Position Sizing

All positions sized using R-multiple formula:

```python
risk_per_unit = entry_price - sl_price
required_lots = R_VALUE / (risk_per_unit × LOT_SIZE)
final_lots = min(required_lots, MAX_LOTS_PER_POSITION)
quantity = final_lots × LOT_SIZE

# Never use flat lot sizing; always use R-based sizing
# This ensures consistent risk across all positions
```

### Rule 7: Maximum Risk Per Position

```python
MAX_LOTS_PER_POSITION = 10  # Max 10 lots per trade
# This caps maximum position size even if risk is tiny

# Example: Entry=200, SL=190
# risk_per_unit = 10
# required_lots = 6500 / (10 × 65) = 10 lots
# final_lots = min(10, 10) = 10 lots (hit max)
```

### Rule 8: Capital Preservation

If daily loss reaches -5R (₹32,500):

```python
DAILY_STOP_R = -5.0
Maximum daily loss = 5 × R_VALUE = 5 × 6500 = ₹32,500
```

This ensures no day loses more than 5% of daily P&L target.

## Data Validation Rules

### Rule 9: Reject Bad Ticks

Before processing any tick:

```python
if not (timestamp_valid and bid < ask and ltp > 0 and volume > 0):
    logger.warning(f"[DATA] Rejecting invalid tick: {symbol}")
    skip_tick()
```

### Rule 10: Verify Data Coverage

Check heartbeat metrics every 60 seconds:

```
[HEARTBEAT] Data: 22/22 | Coverage: 100.0% | Stale: 0
```

**Alert if:**
- Coverage < 90%: "Data gaps detected"
- Stale > 0: "Some symbols have no recent ticks"
- Missing data for > 30 seconds: Pause trading

### Rule 11: Reject Stale Swings

Swing cannot be more than 5 bars old:

```python
bars_since_swing = current_bar_index - swing_bar_index
if bars_since_swing > 5:
    remove_from_swing_candidates()
    logger.info(f"[SWING] Removing stale swing: {bars_since_swing} bars old")
```

## Order Validation Rules

### Rule 12: No Duplicate Orders

Never place two orders for same symbol:

```python
if symbol in pending_orders:
    logger.warning(f"[ORDER] Duplicate order rejected for {symbol}")
    return False
```

### Rule 13: Entry Price Validation

Every order entry must pass filter checks:

```python
# Stage-1 Static Filter
assert MIN_ENTRY_PRICE <= entry_price <= MAX_ENTRY_PRICE, "Price out of range"
assert vwap_premium >= MIN_VWAP_PREMIUM, "VWAP premium too low"

# Stage-2 Dynamic Filter
assert MIN_SL_PERCENT <= sl_percent <= MAX_SL_PERCENT, "SL% out of range"

# If any assertion fails, reject order
```

### Rule 14: Order Cancellation on Disqualification

If a strike gets disqualified (SL% > 10%), cancel its pending order:

```python
if sl_percent > MAX_SL_PERCENT:
    if order_id in pending_orders:
        cancel_order(order_id)
        logger.info(f"[CANCEL] {symbol} disqualified (SL% {sl_percent:.1%})")
```

## Reconciliation Rules

### Rule 15: Daily Position Reconciliation

Every 60 seconds, sync with broker:

```python
internal_positions = get_internal_positions()
broker_positions = get_broker_positionbook()

if internal_positions != broker_positions:
    logger.warning("[RECONCILE] Position mismatch detected")
    # Trust broker, update internal state
    update_internal_to_match_broker()
```

### Rule 16: Order Status Polling

Check order status every 10 seconds:

```python
for order_id, order in pending_orders.items():
    broker_status = check_order_status(order_id)
    if broker_status == 'COMPLETE':
        handle_order_fill(order_id)
    elif broker_status == 'REJECTED':
        handle_order_rejection(order_id)
```

## Logging & Audit Trail

### Rule 17: Log All Critical Events

Every action must be logged:

```python
# Order placed
logger.info("[ORDER] Placing LIMIT for 26000CE @ 129.95 qty=650")

# Order filled
logger.info("[FILL] Entry 26000CE @ 129.95, placing SL @ 141")

# Position closed
logger.info("[EXIT] Position 26000CE closed: Entry=129.95 Exit=135 PnL=+3375 R=+0.5")

# Daily summary
logger.info("[SUMMARY] Day: +2.5R (5 trades, 3 winners, 2 losers)")
```

### Rule 18: Structured Error Logging

All errors logged with context:

```python
try:
    place_order(...)
except Exception as e:
    logger.error(f"[ERROR] Order placement failed: {e}")
    logger.error(f"  Symbol: {symbol}")
    logger.error(f"  Price: {entry_price}")
    logger.error(f"  Quantity: {quantity}")
    # Continue, don't crash
```

## System Health Rules

### Rule 19: Prevent Runaway Orders

If order placement fails 3 times in a row:

```python
consecutive_failures = 0
for order in pending_orders:
    if order.status == 'REJECTED':
        consecutive_failures += 1
        if consecutive_failures >= 3:
            logger.critical("[CRITICAL] 3 consecutive order rejections. Pausing trading.")
            pause_trading()
            alert_user()
```

### Rule 20: Shutdown Gracefully

On system shutdown:

```python
def shutdown():
    logger.info("[SHUTDOWN] Initiating graceful shutdown...")

    # Cancel all pending orders
    cancel_all_pending_orders()

    # Close all positions (optional - may keep positions overnight)
    # close_all_positions()

    # Save state to database
    save_state_to_db()

    # Close WebSocket
    close_websocket()

    logger.info("[SHUTDOWN] Complete")
```

## Terminal Output Rules

### Rule 21: NO Emojis in Terminal Output

When writing Python code that executes in terminals, NEVER use emojis or non-ASCII Unicode characters:

**Why:**
- Many terminals don't support Unicode (Windows CMD, old Linux terminals)
- Emojis cause encoding errors, crashes, or display corruption
- Makes code unreliable and non-portable

**Rule:**
- Use only ASCII characters (A-Z, 0-9, symbols like -, =, *)
- For emphasis, use: `[STEP 1]`, `ERROR:`, `WARNING:`, `SUCCESS:`
- For sections, use: `==`, `--`, `:`, or text headers

**Example - WRONG:**
```python
print("📊 STEP 1: Loading Dataset")  # EMOJI - CAUSES ERRORS
print("✅ Success")                   # EMOJI - CAUSES ERRORS
```

**Example - CORRECT:**
```python
print("STEP 1: Loading Dataset")
print("SUCCESS: Operation completed")
```

**Applies to:**
- All `print()` statements in executable code
- Log messages
- User-facing output
- Terminal-based scripts

---

## File Protection Rules

### Rule 22: Critical Analysis Files - Never Delete

The following files contain critical analytics and must NEVER be deleted when cleaning up unused files:

```
PROTECTED_FILES = [
    "strategy_vwap_filter.py",      # VWAP filter analysis and testing
    "strategy_analytics.py",         # Trading strategy analytics
    "profitability_analytics.py",    # Profitability analysis and metrics
    "offline_data_viewer.py",        # Offline data viewing and analysis tool
    "swing_identifier_v5.py"         # Reference swing detection logic (backtest validation)
]
```

**When Deleting Files:**
- Before deleting any file with pattern `*_filter.py`, `*_analytics.py`, or similar:
  - Check if filename is in PROTECTED_FILES
  - If YES: DO NOT DELETE, ask user for explicit confirmation
  - If NO: Safe to delete

**Why Protected:**
- These files contain analysis logic for strategy optimization
- Results and insights from these files guide strategy improvements
- Deletion would require rewriting analysis from scratch
- Essential for understanding strategy performance

**Exception Process:**
- User must explicitly state: "Delete [filename] even if protected"
- Before deletion, ask: "Are you sure? This file contains critical [analysis type]"

## Summary of Non-Negotiables

| Rule | Constraint | Enforcement |
|------|-----------|------------|
| **Paper Trading** | Default PAPER_TRADING=true | Check before every order |
| **Max Positions** | 5 total, 3 CE max, 3 PE max | Hardcoded in config.py |
| **Daily Target** | Exit all at +5R | Auto-exit at threshold |
| **Daily Stop Loss** | Exit all at -5R | Auto-exit at threshold |
| **Market Close** | Exit all at 3:15 PM IST | Force close at time |
| **Position Sizing** | R-based formula | Never use flat lots |
| **Data Quality** | Coverage ≥90%, no stale > 30s | Monitor every 60s |
| **Order Validation** | All filters pass | Reject if any fail |
| **No Duplicates** | One order per symbol | Check before placing |
| **Reconciliation** | Sync every 60 seconds | Trust broker as truth |
| **NO Emojis in Terminal Code** | ASCII only in print/log statements | Avoid Unicode characters |
| **Protected Files** | Never delete analytics files | Ask explicit confirmation before deletion |

## Testing Checklist

Before going live, verify:

- [ ] PAPER_TRADING=true by default (.env file)
- [ ] Position limits enforced (5 max, 3 CE, 3 PE)
- [ ] Daily target exits at +5R (automatic)
- [ ] Daily stop-loss exits at -5R (automatic)
- [ ] Force close at 3:15 PM IST (automatic)
- [ ] R-based position sizing working (verify calculations)
- [ ] Data quality monitoring (heartbeat logs good)
- [ ] Reconciliation running (every 60s in logs)
- [ ] All critical events logged
- [ ] Error handling graceful (system doesn't crash)
- [ ] Shutdown procedure tested
- [ ] Database persistence working (state saved)
