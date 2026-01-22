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

## Position Limits (Configurable in config.py)

### Rule 2: Maximum Concurrent Positions

```python
# config.py
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

**Configuration Notes:**
- These limits are defined in config.py and can be adjusted based on capital/risk appetite
- Changes require restart to take effect (not adjustable at runtime)
- Recommended defaults: 5 total, 3 CE max, 3 PE max

## Daily Profit/Loss Targets (Automatic Exit)

### Rule 3: Daily Target Exit (Configurable)

When cumulative R-multiple reaches the daily target:

```python
# config.py
DAILY_TARGET_R = 5.0  # Configurable daily profit target (in R-multiples)

# Enforcement
if daily_cumulative_r >= DAILY_TARGET_R:
    logger.info(f"[EXIT] Daily target +{DAILY_TARGET_R}R reached. Closing all positions.")
    close_all_positions()
    cancel_all_pending_orders()
    stop_trading_for_today()
```

### Rule 4: Daily Stop Loss Exit (Configurable)

When cumulative R-multiple reaches the daily stop:

```python
# config.py
DAILY_STOP_R = -5.0  # Configurable daily stop loss (in R-multiples)

# Enforcement
if daily_cumulative_r <= DAILY_STOP_R:
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

### Rule 6: Position Sizing (R-Based Primary)

All positions sized using R-multiple formula:

```python
risk_per_unit = abs(entry_price - sl_price)
r_based_lots = R_VALUE / (risk_per_unit * LOT_SIZE)
final_lots = min(r_based_lots, MAX_LOTS_PER_POSITION)  # Configurable safety cap
quantity = final_lots * LOT_SIZE

# R-based sizing is PRIMARY - ensures consistent risk across all positions
# MAX_LOTS_PER_POSITION is SECONDARY - configurable safety net for edge cases
# Never use flat lot sizing
```

### Rule 7: Configurable Lot Cap (Safety Net)

R-based sizing is primary, but a **configurable cap** acts as a safety net for edge cases:

```python
# config.py
R_VALUE = 6500                    # Primary: Risk per trade (configurable)
MAX_LOTS_PER_POSITION = 15        # Secondary: Safety cap (configurable)

# Position sizing logic
def calculate_lots(entry_price, sl_price):
    risk_per_unit = abs(entry_price - sl_price)
    r_based_lots = R_VALUE / (risk_per_unit * LOT_SIZE)
    final_lots = min(r_based_lots, MAX_LOTS_PER_POSITION)  # Cap applied

    if final_lots < r_based_lots:
        logger.info(f"[SIZING] Capped from {r_based_lots:.1f} to {MAX_LOTS_PER_POSITION} lots")

    return int(final_lots)
```

**Why a configurable cap?**

| Scenario | Without Cap | With Cap |
|----------|-------------|----------|
| Tight SL (2 Rs risk) | 50 lots - liquidity issues | Capped at 15 lots |
| Gap beyond SL | Huge unexpected loss | Limited exposure |
| Broker order limits | Order rejected | Stays within limits |

**Key Points:**
- R-based sizing determines lots (primary method)
- Cap only activates when R-based lots exceed MAX_LOTS_PER_POSITION
- Both R_VALUE and MAX_LOTS_PER_POSITION are configurable in config.py
- Log when cap is applied for visibility

### Rule 8: Capital Preservation

Daily loss is capped using configurable R-multiples:

```python
# config.py
DAILY_STOP_R = -5.0               # Configurable daily stop (in R-multiples)
R_VALUE = 6500                    # Configurable risk per trade

# Maximum daily loss = DAILY_STOP_R * R_VALUE
# Example: -5.0 * 6500 = -32,500 (but this varies based on config)
```

**Enforcement:**
```python
if daily_cumulative_r <= DAILY_STOP_R:
    logger.info(f"[EXIT] Daily stop {DAILY_STOP_R}R hit. Max loss: {abs(DAILY_STOP_R * R_VALUE)}")
    close_all_positions()
    stop_trading_for_today()
```

**Key Points:**
- No hardcoded rupee amounts - always derived from R_VALUE
- Adjust R_VALUE to change risk per trade
- Adjust DAILY_STOP_R to change daily loss tolerance

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

## Order Validation Rules

### Rule 11: No Duplicate Orders

Never place two orders for same symbol:

```python
if symbol in pending_orders:
    logger.warning(f"[ORDER] Duplicate order rejected for {symbol}")
    return False
```

### Rule 12: Entry Price Validation

Every order entry must pass filter checks:

```python
# Stage-1 Static Filter
if not (MIN_ENTRY_PRICE <= entry_price <= MAX_ENTRY_PRICE):
    logger.warning(f"[FILTER] Price {entry_price} out of range [{MIN_ENTRY_PRICE}-{MAX_ENTRY_PRICE}]")
    return False

if vwap_premium < MIN_VWAP_PREMIUM:
    logger.warning(f"[FILTER] VWAP premium {vwap_premium:.2%} below minimum {MIN_VWAP_PREMIUM:.2%}")
    return False

# Stage-2 Dynamic Filter
if not (MIN_SL_PERCENT <= sl_percent <= MAX_SL_PERCENT):
    logger.warning(f"[FILTER] SL% {sl_percent:.2%} out of range [{MIN_SL_PERCENT:.2%}-{MAX_SL_PERCENT:.2%}]")
    return False

# All filters passed - proceed with order
```

### Rule 13: Order Cancellation on Disqualification

If a strike gets disqualified (SL% exceeds maximum), cancel its pending order:

```python
if sl_percent > MAX_SL_PERCENT:
    if order_id in pending_orders:
        cancel_order(order_id)
        logger.info(f"[CANCEL] {symbol} disqualified (SL% {sl_percent:.1%} > {MAX_SL_PERCENT:.1%})")
```

## Reconciliation Rules

### Rule 14: Daily Position Reconciliation

Every 60 seconds, sync with broker:

```python
internal_positions = get_internal_positions()
broker_positions = get_broker_positionbook()

if internal_positions != broker_positions:
    logger.warning("[RECONCILE] Position mismatch detected")
    # Trust broker, update internal state
    update_internal_to_match_broker()
```

### Rule 15: Order Status Polling

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

### Rule 16: Log All Critical Events

Every action must be logged:

```python
# Order placed (SL = stop-limit order for entry)
logger.info("[ORDER] Placing SL for 26000CE @ trigger=129.95 limit=126.95 qty=650")

# Order filled
logger.info("[FILL] Entry 26000CE @ 129.95, placing exit SL @ trigger=141 limit=144")

# Position closed
logger.info("[EXIT] Position 26000CE closed: Entry=129.95 Exit=135 PnL=+3375 R=+0.5")

# Daily summary
logger.info("[SUMMARY] Day: +2.5R (5 trades, 3 winners, 2 losers)")
```

### Rule 17: Structured Error Logging

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

### Rule 18: Prevent Runaway Orders

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

### Rule 19: Shutdown Gracefully

On system shutdown:

```python
def shutdown():
    logger.info("[SHUTDOWN] Initiating graceful shutdown...")

    # Cancel all pending orders
    cancel_all_pending_orders()

    # Close all positions (REQUIRED - MIS is intraday only)
    close_all_positions()

    # Save state to database
    save_state_to_db()

    # Close WebSocket
    close_websocket()

    logger.info("[SHUTDOWN] Complete")
```

**Note:** This is an intraday MIS trading system. Positions cannot be held overnight - they must be squared off before market close (3:15 PM IST) or during shutdown.

## Terminal Output Rules

### Rule 20: NO Emojis in Terminal Output

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

## EC2/Docker Production Safety Rules

### Rule 21: Pre-Deployment Checks

Before deploying to EC2 production:

```bash
# 1. Verify local tests pass
python -m baseline_v1_live.check_system

# 2. Ensure code is committed and pushed
git status  # Should be clean
git push origin feature/docker-ec2-fixes

# 3. SSH and pull latest
ssh -i "D:/aws_key/openalgo-key.pem" ubuntu@13.233.211.15
cd ~/nifty_options_agent
git pull origin feature/docker-ec2-fixes
```

### Rule 22: Container Health Monitoring

**Check container status regularly:**

```bash
# All containers should show "Up"
docker-compose ps

# Check for restart loops
docker-compose ps | grep "Restarting"

# Monitor resource usage
docker stats --no-stream
```

**Alert conditions:**
- Container status is not "Up"
- Container has restarted more than 3 times
- Memory usage > 80%
- Disk usage > 90% (`df -h`)

### Rule 23: EC2 Deployment Safety

**Never deploy during market hours (9:15 AM - 3:30 PM IST):**

```python
from datetime import datetime, time
import pytz

IST = pytz.timezone('Asia/Kolkata')
now = datetime.now(IST).time()

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

if MARKET_OPEN <= now <= MARKET_CLOSE:
    print("WARNING: Market is open. Deployment not recommended.")
    print("Deploy before 9:15 AM or after 3:30 PM IST.")
```

**Safe deployment windows:**
- Before market: 6:00 AM - 9:00 AM IST
- After market: 3:45 PM - 11:59 PM IST
- Weekends: Anytime

### Rule 24: Three-Way Sync Discipline

**Always maintain sync between Laptop ↔ GitHub ↔ EC2:**

```bash
# Before making changes on EC2
git status
git pull origin feature/docker-ec2-fixes

# After making changes on EC2
git add .
git commit -m "Fix from EC2"
git push origin feature/docker-ec2-fixes

# On laptop - pull EC2 changes
git pull origin feature/docker-ec2-fixes
```

**Critical Rules:**
1. Never force push (`git push --force`)
2. Always pull before making changes
3. Commit EC2 changes immediately
4. Resolve conflicts properly (don't overwrite)

### Rule 25: Production Rollback Plan

**If deployment fails or system misbehaves:**

```bash
# 1. Stop trading immediately
docker-compose stop trading_agent

# 2. Check logs for errors
docker-compose logs --tail=100 trading_agent

# 3. Rollback to previous commit
git log --oneline -5  # Find last working commit
git checkout <commit-hash> -- baseline_v1_live/

# 4. Rebuild and restart
docker-compose up -d --build trading_agent

# 5. Verify system health
docker-compose logs -f trading_agent
```

### Rule 26: Sensitive Data Protection

**Never commit to git:**
- `.env` files (API keys, credentials)
- `*.pem` files (SSH keys)
- `live_state.db` (trading state)
- Broker credentials

**Verify before commit:**
```bash
git status
# Check for sensitive files in "Untracked files"
# Add to .gitignore if found
```

---

## Summary of Non-Negotiables

| Rule | Constraint | Enforcement |
|------|-----------|------------|
| **Paper Trading** | Default PAPER_TRADING=true | Check before every order |
| **Max Positions** | Configurable (default: 5 total, 3 CE, 3 PE) | Check in config.py |
| **Daily Target** | Configurable DAILY_TARGET_R (default: +5R) | Auto-exit at threshold |
| **Daily Stop Loss** | Configurable DAILY_STOP_R (default: -5R) | Auto-exit at threshold |
| **Market Close** | Exit all at 3:15 PM IST | Force close at time |
| **Position Sizing** | R-based formula + configurable cap | Primary: R-based, Secondary: MAX_LOTS_PER_POSITION |
| **Data Quality** | Coverage ≥90%, no stale > 30s | Monitor every 60s |
| **Order Validation** | All filters pass | Reject if any fail |
| **No Duplicates** | One order per symbol | Check before placing |
| **Reconciliation** | Sync every 60 seconds | Trust broker as truth |
| **Intraday Only** | No overnight positions | MIS product, close by 3:15 PM |
| **NO Emojis** | ASCII only in terminal output | Avoid Unicode characters |
| **EC2 Deployment** | Never during market hours | Deploy before 9:15 AM or after 3:30 PM |
| **Three-Way Sync** | Laptop ↔ GitHub ↔ EC2 | Always pull before changes |

## Testing Checklist

### Local Testing

Before going live, verify:

- [ ] PAPER_TRADING=true by default (.env file)
- [ ] Position limits enforced (check config.py values)
- [ ] Daily target exits at configured DAILY_TARGET_R (automatic)
- [ ] Daily stop-loss exits at configured DAILY_STOP_R (automatic)
- [ ] Force close at 3:15 PM IST (automatic)
- [ ] R-based position sizing working (verify calculations)
- [ ] Data quality monitoring (heartbeat logs good)
- [ ] Reconciliation running (every 60s in logs)
- [ ] All critical events logged
- [ ] Error handling graceful (system doesn't crash)
- [ ] Shutdown procedure tested

### EC2 Production Testing

Before going live on EC2:

- [ ] All local tests passing
- [ ] Code pushed to GitHub
- [ ] EC2 pulled latest code
- [ ] Docker containers built successfully
- [ ] All containers showing "Up" status
- [ ] OpenAlgo dashboard accessible
- [ ] Broker connected in dashboard
- [ ] WebSocket receiving ticks (check logs)
- [ ] Paper trading mode tested on EC2
- [ ] Deployment done outside market hours
- [ ] Rollback plan documented and tested
- [ ] .env file backed up (not in git)
