# Options Trading Agent - NIFTY Swing-Break Strategy

## ⚡ Quick Context (30 seconds)

**What:** Automated trading system for NIFTY index options using swing-break strategy
**How:** Detect swing lows → Apply 2-stage filters → Place proactive SL (stop-limit) orders BEFORE breaks
**Risk:** Configurable R_VALUE (default Rs.6,500 per R), daily targets configurable (default +/-5R)
**Broker:** OpenAlgo (Local: http://127.0.0.1:5000 | EC2: https://openalgo.ronniedreams.in)
**Mode:** Paper trading by default (PAPER_TRADING=true in .env)

---

## 📋 Architecture at a Glance (3 minutes)

```
1. DATA PIPELINE (data_pipeline.py)
   WebSocket ticks → 1-min OHLCV bars + VWAP calculation

2. SWING DETECTION (swing_detector.py)
   Watch-based system: bars confirm past turning points
   Output: swing_candidates dict (all detected swings)

3. STRIKE FILTRATION (continuous_filter.py)
   Stage-1: Static filters (price range 100-300 Rs, VWAP ≥4%)
   Stage-2: Dynamic filter (SL% 2-10%, recalculated every bar)
   Stage-3: Tie-breaker (select best strike per option type)
   Output: current_best dict (qualified strikes for CE and PE)

4. ORDER EXECUTION (order_manager.py)
   Proactive placement: SL orders BEFORE swing breaks (trigger: swing_low - tick, limit: trigger - 3)
   Position sizing: R-based formula (R_VALUE configurable, default ₹6,500)
   Exit SL: SL orders at highest_high + 1 (trigger), +3 Rs buffer (limit)

5. POSITION TRACKING (position_tracker.py)
   Monitor active positions, calculate R-multiples
   Daily exit at +/-5R or 3:15 PM force close
```

---

## Architecture: Continuous Filtering (Proactive Order Management)

**Key Innovation:** Orders are placed BEFORE swing breaks, not after.

```
Flow:
1. Swing LOW detected → Added to candidates (static filter: 100-300 Rs)
2. EVERY BAR: Evaluate ALL candidates (dynamic filters: VWAP 4%+, SL 2-10%)
3. Track best CE and best PE separately
4. Best strike qualifies → Place SL order (trigger: swing_low - tick, limit: trigger - 3)
5. Price drops to trigger → Order activates → Fills at limit price (no slippage)
```

---

## File Structure (baseline_v1_live/)

### Core Trading System
| File | Purpose | Lines |
|------|---------|-------|
| `baseline_v1_live.py` | Main orchestrator, entry point | ~530 |
| `config.py` | All configuration parameters | ~180 |
| `data_pipeline.py` | WebSocket → 1-min OHLCV bars + VWAP | ~500 |
| `swing_detector.py` | Multi-symbol swing low/high detection | ~350 |
| `continuous_filter.py` | Two-stage filtering engine | ~300 |
| `order_manager.py` | Proactive SL orders for entry + exit | ~680 |
| `position_tracker.py` | R-multiple accounting | ~350 |
| `state_manager.py` | SQLite persistence | ~280 |
| `telegram_notifier.py` | Trade notifications | ~150 |

### Utilities
| File | Purpose |
|------|---------|
| `check_system.py` | Pre-flight validation |
| `strike_filter.py` | Strike selection helpers |
| `monitor_dashboard/` | Streamlit monitoring dashboard |

### Configuration
| File | Purpose |
|------|---------|
| `.env` | API keys, trading mode (PAPER_TRADING=true/false) |
| `config.py` | Position sizing, filters, timing |

---

## Key Configuration (config.py)

All values below are **configurable** in config.py:

```python
# Capital & Position Sizing
TOTAL_CAPITAL = 10000000      # Rs.1 Crore
R_VALUE = 6500                # Rs.6,500 per R (configurable risk per trade)
MAX_POSITIONS = 5             # Max concurrent positions (configurable)
MAX_LOTS_PER_POSITION = 15    # Safety cap on lots (configurable, R-based sizing is primary)
LOT_SIZE = 65                 # NIFTY lot size

# Entry Filters (all configurable)
MIN_ENTRY_PRICE = 100         # Minimum option price
MAX_ENTRY_PRICE = 300         # Maximum option price
MIN_VWAP_PREMIUM = 0.04       # 4% above VWAP required
MIN_SL_PERCENT = 0.02         # 2% minimum SL
MAX_SL_PERCENT = 0.10         # 10% maximum SL

# Daily Exits (configurable)
DAILY_TARGET_R = 5.0          # Exit all at +5R (configurable)
DAILY_STOP_R = -5.0           # Exit all at -5R (configurable)
FORCE_EXIT_TIME = time(15, 15) # Force exit at 3:15 PM
```

---

## Swing Detection Logic

The swing detector uses a **watch-based system** - a confirmation-voting mechanism where future bars validate past turning points.

### Core Concept

A **swing** is a significant turning point where trends change:
- **Swing Low**: A local minimum - price makes a low, then moves higher
- **Swing High**: A local maximum - price makes a high, then moves lower

### Watch-Based Confirmation System

Each bar in history gets **watch counters** that increment when future bars confirm it might be a turning point:

**For Swing LOW Detection:**
- A bar's `low_watch` counter increments when a future bar shows:
  1. **Higher High** AND
  2. **Higher Close**
- Logic: If price makes higher highs/closes vs a past bar, that bar was likely a low point

**For Swing HIGH Detection:**
- A bar's `high_watch` counter increments when a future bar shows:
  1. **Lower Low** AND
  2. **Lower Close**
- Logic: If price makes lower lows/closes vs a past bar, that bar was likely a high point

### Trigger Rule

When any bar's watch counter reaches **2**:
- **For low_watch = 2**: Find the **lowest low** in the window → Mark as **SWING LOW**
- **For high_watch = 2**: Find the **highest high** in the window → Mark as **SWING HIGH**

**Why "2"?** One confirmation could be noise. Two confirmations validate the turning point.

### Alternating Pattern

Valid swings must alternate:
```
High → Low → High → Low → High → Low
```

After a swing low, the next swing must be a high. After a high, the next must be a low.

### Swing Updates (Same Direction)

If a **new extreme** forms BEFORE the next alternating swing:
- **Swing LOW @ 80** detected
- Before any HIGH, price drops to **75** (new lower low)
- **Action**: UPDATE the swing low from 80 → 75
- **Reason**: 75 is the true extreme, not the premature 80

This ensures we always track the TRUE extremes, not intermediate levels.

### Window Behavior

- **First swing**: From start of data to current bar
- **Subsequent swings**: From bar AFTER last swing to current bar

This keeps the analysis focused on current wave formation only.

**Validation:** Watch-based system validated through historical testing

---

## Strike Filtration Logic

Strike filtration is the process of evaluating swing candidates to determine which options qualify for order placement. This happens **AFTER swing detection** but **BEFORE order execution**.

The system uses a **two-stage approach**: Static filters (run once) and Dynamic filters (run continuously).

### Stage 1: Static Filter (Run Once)

Applied **immediately when swing forms**, never rechecked.

**Criteria:**

1. **Price Range**
   - Rule: `MIN_ENTRY_PRICE ≤ Entry Price ≤ MAX_ENTRY_PRICE`
   - Entry Price = Swing Low (the option premium at swing formation)
   - Default: 100-300 Rs range

2. **VWAP Premium**
   - Rule: `VWAP Premium ≥ MIN_VWAP_PREMIUM` (4% by default)
   - Formula: `((Entry - VWAP) / VWAP) × 100`
   - VWAP is frozen at swing formation time (immutable)

**When Applied:**
```
Swing forms → Check price range → Check VWAP premium
    ↓
Pass both → Add to swing_candidates (static pool)
Fail either → Log rejection, discard swing
```

### Stage 2: Dynamic Filter (Tick-Level Evaluation)

Applied **every tick** to all candidates in `swing_candidates`. Current bar's high updates with each tick.

**SL% Filter (Truly Dynamic):**
- Rule: `MIN_SL_PERCENT ≤ SL% ≤ MAX_SL_PERCENT` (2-10% by default, configurable)
- Formula:
  ```
  Highest High = Maximum high since swing formation (includes current bar's high, updates each tick)
  Entry Price = Swing low
  SL Price = Highest High + 1 Rs (buffer for slippage)
  SL% = (SL Price - Entry Price) / Entry Price × 100
  ```

**Why SL% is Dynamic:**
- Highest High updates every tick (current bar's high tracked in real-time)
- SL% can change from passing → failing mid-bar
- Example: Bar starts SL%=8% ✓ → Tick pushes high up → SL%=11% ❌ (disqualified immediately)

**Evaluation Frequency:**
- **Swing detection**: On bar close (needs complete OHLC)
- **Filter evaluation**: Every tick (real-time SL% accuracy)
- Order placed immediately when strike qualifies

### Stage 3: Tie-Breaker (Best Strike Selection)

When **multiple strikes pass all filters** for the same option type (CE or PE), select ONE using:

**Rule 1: SL Points Closest to 10 Rs (Primary)**
- Target: 10 points (optimized for R_VALUE, configurable)
- Formula: `sl_distance = abs(sl_points - 10)`
- Example: 8pts (distance=2), 12pts (distance=2), 9pts (distance=1) ← **BEST**

**Rule 2: Strike Multiple of 100 (Secondary Tie-Breaker)**
- If multiple strikes have same SL distance, prefer strikes that are multiples of 100
- Why: Round strikes (24000, 24100, 24200) have better liquidity than odd strikes (24050, 24150)
- Strike extraction from symbol: `NIFTY06JAN2626200CE` → Strike = 26200 → 26200 % 100 == 0 ✓

**Rule 3: Highest Entry Price (Final Tie-Breaker)**
- If still tied (same SL distance AND both multiples of 100), prefer higher premium

### Filter State Tracking

**swing_candidates (dict):**
- All swings that passed static filters (price range + VWAP premium)
- Never re-evaluated after insertion
- Only swings in this pool are eligible for dynamic SL% filtering

**qualified_candidates (list):**
- Swings from `swing_candidates` that currently pass dynamic SL% filter
- Refreshed every tick/bar (mutable)

**current_best (dict):**
- The single best strike per option type (CE/PE) selected from qualified_candidates
- Eligible for order placement
- Updated every evaluation cycle

### Filter Flow Example

**Time: 10:15 AM - New Swing Detected**
```
Swing: NIFTY06JAN2626250CE @ 125.00 (swing low)
VWAP: 118.00

Step 1: Static Filter
- Check: 100 ≤ 125 ≤ 300 ✓ PASS
- Premium: (125-118)/118 = 5.93% ✓ PASS (≥4%)
- Action: Add to swing_candidates

Step 2: Dynamic Filter (10:15 AM)
- Highest High: 130.00
- SL Price: 131 (includes +1 Rs buffer)
- SL%: (131-125)/125 = 4.8% ✓ PASS (2-10%)
- Action: Add to qualified_candidates

Step 3: Tie-Breaker
- Only one CE qualified → Automatically best
```

**Evolution Over Time:**
- 10:20 AM: SL% = 8.8% ✓ Still qualified
- 10:30 AM: SL% = 12.0% ❌ DISQUALIFIED (exceeds 10%)

---

## Order Flow

### Core Concept: Proactive vs Reactive

**Reactive Approach (Legacy):**
```
1. Swing low breaks (price < swing_low)
2. System detects break
3. Place MARKET order
4. Get filled at current price (slippage!)
```

**Proactive Approach (Current - What We Do):**
```
1. Swing low detected and qualified
2. Place SL order BEFORE break:
   - Trigger: swing_low - tick_size (e.g., swing_low - 0.05)
   - Limit: trigger - 3 Rs (buffer for fill)
3. Order sits dormant until trigger hit
4. When price drops to trigger → Order activates as limit order
5. Fills at limit price with no slippage!
```

### Order Placement Trigger

Orders are placed only when a strike passes all three filter stages:

1. **Stage-1 (Static)**: Price range + VWAP premium ✓
2. **Stage-2 (Dynamic)**: SL% within limits ✓
3. **Stage-3 (Tie-Breaker)**: Selected as best strike per option type ✓

Plus position availability checks (max positions, no duplicate orders)

### Entry Order: SL (Stop-Limit)

```python
# When strike qualifies:
tick_size = 0.05
trigger_price = swing_low - tick_size      # Trigger just below swing
limit_price = trigger_price - 3            # 3 Rs buffer for fill

order = client.placeorder(
    strategy="baseline_v1",
    symbol="NIFTY30DEC2526000CE",
    action="SELL",              # Short the option
    exchange="NFO",
    price_type="SL",            # Stop-Limit order
    trigger_price=trigger_price,
    price=limit_price,
    quantity=lots * LOT_SIZE,
    product="MIS"               # Intraday
)
```

**Why SL (Stop-Limit), not regular LIMIT?**
- Order sits dormant until trigger price is hit
- Prevents fills if price never reaches our entry level
- When triggered, becomes a limit order with price control
- Better entry timing aligned with swing break

**Price Calculation:**
- `trigger_price = swing_low - 0.05` (1 tick below swing)
- `limit_price = trigger_price - 3` (3 Rs buffer below trigger)
- When price drops to trigger → Order activates → Fills at limit or better

### Position Sizing

Formula based on R_VALUE:

```
Risk per unit = Entry Price - SL Price
Required lots = R_VALUE / (Risk per unit × LOT_SIZE)
Final lots = min(Required lots, MAX_LOTS_PER_POSITION)
Final quantity = Final lots × LOT_SIZE

Example:
Entry: 150 Rs
SL: 160 Rs
Risk per unit: 10 Rs
Required lots: 6500 / (10 × 65) = 10 lots
Quantity: 10 × 65 = 650 shares
```

### Exit Stop Loss Order: SL (Placed on Fill)

```python
# SL placed IMMEDIATELY when entry fills:
trigger_price = highest_high + 1    # +1 Rs buffer above highest high
limit_price = trigger_price + 3     # +3 Rs buffer for fill

sl_order = client.placeorder(
    strategy="baseline_v1",
    symbol="NIFTY30DEC2526000CE",
    action="BUY",              # Close the short
    exchange="NFO",
    price_type="SL",           # Stop-Limit order
    trigger_price=trigger_price,
    price=limit_price,
    quantity=lots * LOT_SIZE,
    product="MIS"
)
```

**Why SL (Stop-Limit) instead of Market?**
- Market orders can have extreme slippage in fast markets
- SL with 3 Rs buffer ensures reasonable fill
- Better control over exit price

**+1 Rs Buffer in Trigger Calculation:**
- Accounts for tick-level slippage during volatile moves
- Prevents premature exits at exact highest high level
- Ensures SL triggers reliably

### Order Lifecycle States

```
NO_ORDER → ORDER_PLACED → ORDER_FILLED → POSITION_ACTIVE → EXITED
   ↓           ↓              ↓               ↓             ↓
REJECTED   CANCELLED      SL_HIT         CLOSED        LOGGED
```

**State Transitions:**

1. **NO_ORDER → ORDER_PLACED**
   - Trigger: Strike passes all filters
   - Action: Place SL order (trigger: swing_low - tick, limit: trigger - 3)

2. **ORDER_PLACED → ORDER_FILLED**
   - Trigger: Order status = COMPLETE (checked every 10 seconds)
   - Action: Create position, place SL-L order immediately

3. **ORDER_PLACED → CANCELLED**
   - Triggers:
     - Disqualification (SL% exceeds MAX_SL_PERCENT)
     - Different strike becomes best (tie-breaker selects new winner)
     - Daily limits hit (DAILY_TARGET_R or DAILY_STOP_R reached)
     - Market close approaching (FORCE_EXIT_TIME)
     - System shutdown
   - **NOT a cancellation reason**: Swing breaking - that's the ENTRY TRIGGER!

4. **POSITION_ACTIVE → EXITED**
   - Triggers: SL hit, target hit, or force exit
   - Action: Log trade with R-multiple

### Order Modification Rules

**When to Modify (keep same symbol):**
- Same symbol remains best candidate
- Swing low gets updated (e.g., 80 → 75)
- Modify order trigger/limit to: new_swing_low - tick, trigger - 3

**When to Cancel and Replace (different symbol):**
- Different strike becomes best
- Cancel old symbol's order
- Place new order for new symbol

### Important Rules

**Rule 1: Keep Orders Once Placed**
- Don't cancel just because price moves away from swing
- Only cancel if disqualified (SL% out of range) or better strike available

**Rule 2: One Order Per Option Type**
- Maximum one pending CE order
- Maximum one pending PE order
- Cancel old if new best strike selected

**Rule 3: Proactive Placement**
- Place orders BEFORE swing breaks
- Orders wait in market (not reactive after break)

---

## Running the System

### Start OpenAlgo First
```powershell
cd D:\marketcalls\openalgo
python app.py
```

### Run System Check
```powershell
cd D:\nifty_options_agent
python -m baseline_v1_live.check_system
```

### Start Trading (Paper Mode)
```powershell
cd D:\nifty_options_agent
python -m baseline_v1_live.baseline_v1_live --expiry 30JAN25 --atm 23500
```

### Go Live (Change .env)
```bash
# Edit baseline_v1_live/.env
PAPER_TRADING=false
```

---

## EC2 Deployment (Production)

### Infrastructure
- **EC2 Instance**: Ubuntu 22.04 on AWS
- **Elastic IP**: 13.233.211.15 (static)
- **Domain**: ronniedreams.in
- **SSL**: Let's Encrypt (auto-renews)

### URLs (Password Protected)
| Service | URL |
|---------|-----|
| OpenAlgo Dashboard | https://openalgo.ronniedreams.in |
| Monitor Dashboard | https://monitor.ronniedreams.in |

**Basic Auth Credentials:**
- Username: `admin`
- Password: `your-dashboard-password`

### SSH Access
```bash
ssh -i "D:/aws_key/openalgo-key.pem" ubuntu@13.233.211.15
```

### Deploy Updates to EC2
After pushing changes to GitHub:
```bash
# SSH into EC2
ssh -i "D:/aws_key/openalgo-key.pem" ubuntu@13.233.211.15

# Run deploy script
cd ~/nifty_options_agent
./deploy.sh
```

### Manual Docker Commands on EC2
```bash
cd ~/nifty_options_agent

# View container status
docker-compose ps

# View logs
docker-compose logs -f trading_agent
docker-compose logs -f openalgo

# Restart all services
docker-compose down && docker-compose up -d

# Restart single service
docker-compose restart trading_agent
```

### Directory Structure on EC2
```
~/nifty_options_agent/
├── openalgo-zerodha/openalgo/   # OpenAlgo broker integration
├── baseline_v1_live/            # Trading strategy
├── docker-compose.yaml          # Container orchestration
├── deploy.sh                    # Deployment script
└── .env                         # Environment variables
```

### Nginx Configuration
Location: `/etc/nginx/sites-available/ronniedreams.in`

To modify basic auth password:
```bash
sudo htpasswd /etc/nginx/.htpasswd admin
sudo systemctl reload nginx
```

### SSL Certificate Renewal
Certbot auto-renews. Manual renewal:
```bash
sudo certbot renew
```

### Three-Way Sync Workflow (IMPORTANT for Claude Code)

**Environment Setup:**
- **Laptop** (Windows): Local development, uses HTTPS for GitHub
- **GitHub**: Central repository (feature/docker-ec2-fixes branch)
- **EC2**: Production server, uses SSH for GitHub

**Laptop → GitHub → EC2 (Most Common):**
```bash
# 1. On laptop - commit and push
git add . && git commit -m "message" && git push origin feature/docker-ec2-fixes

# 2. On EC2 - pull and deploy
ssh -i "D:/aws_key/openalgo-key.pem" ubuntu@13.233.211.15
cd ~/nifty_options_agent && ./deploy.sh
```

**EC2 → GitHub → Laptop:**
```bash
# 1. On EC2 - commit and push
cd ~/nifty_options_agent
git add . && git commit -m "message" && git push origin feature/docker-ec2-fixes

# 2. On laptop - pull
git pull origin feature/docker-ec2-fixes
```

**CRITICAL RULES for Claude Code:**
1. **Always check git status** on both laptop and EC2 before making changes
2. **Never force push** - always resolve conflicts properly
3. **EC2 is production** - test changes locally first when possible
4. **After any code change on EC2**, commit and push to keep all three in sync
5. **docker-compose.yaml** has EC2-specific paths - don't change volume mounts without testing
6. **SSH key for EC2→GitHub**: `~/.ssh/github_key` (already configured)

---

## Common Tasks

### Check Why No Trades
Look at filter summary logs:
```
[FILTER-SUMMARY] 8 candidates, 0 qualified. Rejections: VWAP<4%=5, SL<2%=0
```
- `VWAP<4%`: Swing low price is below VWAP (need price 4%+ ABOVE VWAP)
- `SL<2%` or `SL>10%`: Stop loss percentage outside acceptable range

### Modify Entry Filters
Edit `config.py`:
```python
MIN_VWAP_PREMIUM = 0.04  # Reduce to 0.02 for more trades
```

### Check Swing Detection
The swing detector uses watch-based confirmation. If issues:
1. Check logs for `[SWING]` tags
2. Verify alternating pattern (High → Low → High → Low)

### Debug Data Pipeline
```python
# Check coverage in heartbeat logs:
[HEARTBEAT] Positions: 0 | Data: 22/22 | Coverage: 100.0% | Stale: 0
```

---

## Database Schema (live_state.db)

```sql
-- Positions table
positions (symbol, entry_price, quantity, sl_price, entry_time, status, pnl, r_multiple)

-- Orders table
orders (order_id, symbol, order_type, price, quantity, status, timestamp)

-- Daily summary
daily_summary (date, total_trades, winning_trades, cumulative_r, pnl)

-- Swing detection log
swing_log (symbol, swing_type, price, timestamp, vwap)
```

---

## Important Patterns

### Time Handling
Always use IST:
```python
import pytz
IST = pytz.timezone('Asia/Kolkata')
now = datetime.now(IST)
```

### Symbol Format
```python
# Format: NIFTY[DDMMMYY][STRIKE][CE/PE]
symbol = f"NIFTY{expiry}{strike}CE"
# Example: NIFTY30DEC2526000CE
```

### Error Handling
- All broker calls have 3-retry logic with 2-second delay
- WebSocket auto-reconnects on disconnect
- State persists to SQLite (crash recovery)

### Logging
```python
import logging
logger = logging.getLogger(__name__)
logger.info("[TAG] Message")  # Use tags like [SWING], [ORDER], [FILL]
```

---

## Safety Rules

All limits below are **configurable** in config.py:

1. **Never skip Analyzer Mode** - Always test with PAPER_TRADING=true first
2. **Position limits enforced** - MAX_POSITIONS (default 5), MAX_CE_POSITIONS (default 3), MAX_PE_POSITIONS (default 3)
3. **Daily stops** - Auto-exit at DAILY_TARGET_R/DAILY_STOP_R (default +/-5R)
4. **Force exit** - All positions closed at FORCE_EXIT_TIME (default 3:15 PM)
5. **Reconciliation** - Positions synced with broker every 60 seconds
6. **R-based position sizing** - Primary sizing via R_VALUE formula, MAX_LOTS_PER_POSITION as safety cap

---

## Historical Data

Located in `data/`:

| File | Purpose |
|------|---------|
| `rl_dataset_v2.parquet` | Historical NIFTY options data |
| `rl_dataset_v2_with_spot.parquet` | Historical data with spot prices |

Note: Backtest files are maintained separately. This folder focuses on live trading only.

---

## Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| No ticks received | Check OpenAlgo WebSocket, broker login |
| All candidates rejected | Check VWAP filter (price must be 4%+ above VWAP) |
| Orders not placing | Verify API key, check order_manager logs |
| Swings not detecting | Compare with backtest logic, check bar formation |
| Position mismatch | Check reconciliation logs, verify broker dashboard |

---

## Related Projects

- **OpenAlgo** (`D:\marketcalls\openalgo`) - Broker integration platform (must be running for live trading)

---

## Contact Points

- OpenAlgo Dashboard: http://127.0.0.1:5000
- WebSocket Proxy: ws://127.0.0.1:8765
- Telegram Bot: Configured in .env (TELEGRAM_BOT_TOKEN)

---

## 📚 Deep Dive Theory Documents

For comprehensive understanding, refer to parent documentation:

| Document | Focus | Key Topics |
|----------|-------|-----------|
| **SWING_DETECTION_THEORY.md** | Swing identification mechanism | Watch-based confirmation, alternating patterns, swing updates, window behavior |
| **STRIKE_FILTRATION_THEORY.md** | Multi-stage filter pipeline | Static filters, dynamic filters, tie-breaker rules, pool state tracking |
| **ORDER_EXECUTION_THEORY.md** | Order placement and lifecycle | Proactive vs reactive, position sizing, order states, modification rules |

---

## 🎯 Modular Rules (for Claude Code context)

See `.claude/rules/` for path-specific rules:

- **trading-rules.md** → For baseline_v1_live.py, order_manager.py, position_tracker.py
- **swing-detection-rules.md** → For swing_detector.py, continuous_filter.py
- **data-pipeline-rules.md** → For data_pipeline.py
- **openalgo-integration-rules.md** → For all OpenAlgo API/WebSocket integration
- **safety-rules.md** → Critical constraints and validations

Each rule file includes:
- Path-specific conditions (paths: ...)
- Detailed implementation rules
- Common gotchas and edge cases
- Validation checkpoints

### OpenAlgo Integration (NEW!)

Comprehensive guide for broker API integration:
- Order placement (SL orders for entry and exit)
- WebSocket data feed management
- Position reconciliation
- Error handling & retry logic
- Rate limiting & backoff
- Testing & paper trading

See **openalgo-integration-rules.md** for complete details.

---

## Code Change Guidelines

**When modifying core files:**
- `order_manager.py`
- `position_tracker.py`
- `baseline_v1_live.py`
- `continuous_filter.py`
- `swing_detector.py`
- `data_pipeline.py`

**Best Practices:**
1. Make minimal, focused changes
2. Don't refactor unrelated code
3. Keep changes small and testable
4. Verify system runs without errors after changes
5. Test in paper trading mode first

**Verification Steps:**
```bash
cd D:\nifty_options_agent

# 1. Run system check
python -m baseline_v1_live.check_system

# 2. Start in paper mode and verify no errors
python -m baseline_v1_live.baseline_v1_live --expiry 30JAN25 --atm 23500
```

Note: Automated tests will be added in future iterations.
