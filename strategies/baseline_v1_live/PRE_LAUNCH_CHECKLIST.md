# Pre-Launch Checklist for Baseline V1 Live Trading

**Date**: December 23, 2025  
**System**: Baseline V1 - Continuous Filtering Architecture  
**Capital**: ₹1 Crore (Paper Trading → Live)

---

## Phase 1: Environment Setup ✅

### 1.1 OpenAlgo Configuration
- [ ] OpenAlgo running at http://127.0.0.1:5000
- [ ] WebSocket proxy running at ws://127.0.0.1:8765
- [ ] Broker connected and authenticated
- [ ] Paper trading mode enabled initially (`PAPER_TRADING=true`)

### 1.2 Environment Variables (.env)
```bash
# Verify these settings
OPENALGO_API_KEY=<your_api_key>
OPENALGO_HOST=http://127.0.0.1:5000
OPENALGO_WS_URL=ws://127.0.0.1:8765
PAPER_TRADING=true  # Start with paper trading!
DRY_RUN=false
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<your_token>
TELEGRAM_CHAT_ID=<your_chat_id>
```

- [ ] API key validated
- [ ] All required variables set
- [ ] Telegram bot tested

### 1.3 Python Environment
- [ ] Virtual environment activated
- [ ] All dependencies installed: `pip install -r requirements.txt`
- [ ] OpenAlgo SDK version verified: `pip show openalgo`

---

## Phase 2: System Validation (30 mins) ⚠️

### 2.1 Run System Checks
```powershell
cd d:\nifty_options_agent
python -m baseline_v1_live.check_system
```

**Expected Output**:
- ✅ OpenAlgo connection successful
- ✅ Broker authenticated
- ✅ WebSocket connection stable
- ✅ Margin available (≥ ₹2 Lakh)
- ✅ Database initialized

### 2.2 Run Test Scripts
```powershell
# Test swing detection
python test_simple_flow.py

# Expected: Swing detected, proactive order placed
```

- [ ] Swing detection working
- [ ] VWAP calculation correct
- [ ] Order trigger logic verified

### 2.3 Validate Configuration
```powershell
# Check current expiry and ATM
# Example: 26DEC24, ATM = round(NIFTY spot to nearest 50/100)
```

- [ ] Expiry date confirmed (Thursday)
- [ ] ATM strike validated (check NIFTY spot at 9:00 AM)
- [ ] ±10 strike range covers expected price movement

---

## Phase 3: Paper Trading Dry Run (1 Full Day) 🧪

### 3.1 Pre-Market (9:00 AM)
```powershell
# Start system with paper trading
cd d:\nifty_options_agent
python -m baseline_v1_live.baseline_v1_live --expiry 26DEC24 --atm 18000
```

**Monitor**:
- [ ] System starts without errors
- [ ] 22 option symbols subscribed (±10 strikes)
- [ ] Historical data loaded (last 390 bars)
- [ ] WebSocket connected

### 3.2 During Market Hours (9:15 AM - 3:30 PM)

**Every Hour Check**:
- [ ] Swings being detected
- [ ] Candidates added to filter
- [ ] Best CE/PE updated continuously
- [ ] Orders placed when strike qualifies (all filters pass)
- [ ] Orders modified when different strike qualifies
- [ ] No crashes or exceptions

**Log Files to Monitor**:
```
logs/baseline_v1_live_YYYYMMDD.log  # Main log
live_state.db                        # SQLite state
```

### 3.3 Critical Events to Test

#### Scenario 1: Swing Break Entry
- [ ] Swing detected and added to candidates
- [ ] Strike qualifies through all filters (price, VWAP, SL%)
- [ ] Limit order placed IMMEDIATELY @ swing_low - 0.05
- [ ] Price breaks swing
- [ ] Order fills
- [ ] SL order placed immediately
- [ ] Position tracked in SQLite

#### Scenario 2: Order Modification
- [ ] Different strike qualifies (better SL points)
- [ ] Old order cancelled
- [ ] New order placed for new symbol
- [ ] Log confirms modification

#### Scenario 3: Position Exit
- [ ] SL hit → position closed
- [ ] R-multiple calculated correctly
- [ ] Cumulative R updated
- [ ] Telegram notification sent

#### Scenario 4: Daily Exit
- [ ] +5R reached → all positions exited
- [ ] OR -5R reached → all positions exited
- [ ] OR 3:15 PM → all positions exited
- [ ] System stops taking new entries

### 3.4 End of Day (3:30 PM)
- [ ] All positions squared off (if any open)
- [ ] Daily summary logged
- [ ] SQLite database has complete records
- [ ] No pending orders left

**Review Metrics**:
```sql
-- Check trades in SQLite
SELECT * FROM positions WHERE date = 'today';
SELECT * FROM orders WHERE date = 'today';
SELECT * FROM daily_summary WHERE date = 'today';
```

---

## Phase 4: Multi-Day Paper Testing (3 Days) 📊

### Day 1 Results
- Total Swings Detected: __
- Candidates Qualified: __
- Entries Taken: __
- Exits (SL / Target / EOD): __
- Cumulative R: __
- Issues Found: __

### Day 2 Results
- Total Swings Detected: __
- Candidates Qualified: __
- Entries Taken: __
- Exits (SL / Target / EOD): __
- Cumulative R: __
- Issues Found: __

### Day 3 Results
- Total Swings Detected: __
- Candidates Qualified: __
- Entries Taken: __
- Exits (SL / Target / EOD): __
- Cumulative R: __
- Issues Found: __

**3-Day Validation Checklist**:
- [ ] System ran without crashes all 3 days
- [ ] Order placement/modification working
- [ ] SL orders triggering correctly
- [ ] Position sizing accurate
- [ ] R-multiple accounting correct
- [ ] Daily exits working (±5R or 3:15 PM)
- [ ] Telegram notifications reliable

---

## Phase 5: Edge Case Testing (1 Day) 🧩

### 5.1 No Swing Breaks (Trending Market)
- [ ] System waits patiently
- [ ] No orders placed
- [ ] No errors or crashes
- [ ] Continuous monitoring continues

### 5.2 Many Rapid Swings (Choppy Market)
- [ ] Multiple candidates tracked
- [ ] Best CE/PE updated frequently
- [ ] Orders modified correctly
- [ ] No duplicate orders

### 5.3 Max Positions Hit
- [ ] 5 total positions opened
- [ ] 3 CE + 2 PE (or vice versa)
- [ ] New orders cancelled with "max positions" reason
- [ ] System waits for exit before new entry

### 5.4 Mid-Day Restart
- [ ] Stop system at 11:00 AM
- [ ] Restart with same expiry/ATM
- [ ] Historical data loads correctly
- [ ] Existing positions recovered (if any)
- [ ] System continues normally

### 5.5 WebSocket Disconnection
- [ ] Disconnect WebSocket manually
- [ ] System detects disconnection
- [ ] Reconnection logic triggers
- [ ] Data flow resumes
- [ ] No data loss

---

## Phase 6: Final Pre-Live Checks (1 Day) 🚦

### 6.1 Broker Validation
- [ ] Check margin requirements (≥ ₹10 Lakh for 5 positions)
- [ ] Verify order types supported (LIMIT, SL-L)
- [ ] Confirm lot sizes (NIFTY = 65)
- [ ] Test manual order placement via OpenAlgo

### 6.2 Risk Parameters Review
```python
# From config.py
TOTAL_CAPITAL = 10000000      # ₹1 Crore ✓
R_VALUE = 6500                # ₹6,500 per R ✓
MAX_POSITIONS = 5             # Max 5 positions ✓
MAX_LOTS_PER_POSITION = 10    # Max 10 lots ✓
DAILY_TARGET_R = 5.0          # +5R exit ✓
DAILY_STOP_R = -5.0           # -5R stop ✓
```

- [ ] All values validated
- [ ] Position sizing tested
- [ ] Daily limits confirmed

### 6.3 Monitoring Setup
- [ ] Dashboard accessible (if available)
- [ ] Telegram notifications working
- [ ] Log files rotating properly
- [ ] SQLite database backed up

---

## Phase 7: LIVE Deployment 🚀

### 7.1 Pre-Market Checklist (9:00 AM)
- [ ] **Set PAPER_TRADING=false in .env**
- [ ] Verify broker balance ≥ ₹10 Lakh
- [ ] Confirm today's expiry and ATM strike
- [ ] Run `check_system.py` one final time
- [ ] Backup previous day's database

### 7.2 Launch Command
```powershell
cd d:\nifty_options_agent
python -m baseline_v1_live.baseline_v1_live --expiry <DATE> --atm <STRIKE>

# Example:
python -m baseline_v1_live.baseline_v1_live --expiry 26DEC24 --atm 18000
```

### 7.3 First Hour Monitoring (9:15 - 10:15 AM)
- [ ] System running smoothly
- [ ] Data flowing correctly
- [ ] First swing detected (if market provides)
- [ ] Order placement tested (if opportunity)
- [ ] No unexpected errors

### 7.4 Continuous Monitoring
- Monitor logs in real-time
- Watch Telegram notifications
- Check positions via OpenAlgo dashboard
- Be ready to intervene if needed

### 7.5 Emergency Stop
If critical issues occur:
```powershell
# Press Ctrl+C in terminal
# OR close terminal window
# Then manually close all positions via OpenAlgo
```

---

## Critical Numbers Reference 📋

### Position Sizing Example
```
Entry Price: ₹180
SL Price: ₹190
SL Points: 10
R_VALUE: ₹6,500

Lots = ₹6,500 / (10 × 65) = 10 lots
Quantity = 10 × 65 = 650 shares
Margin = 10 lots × ₹2 Lakh = ₹20 Lakh
```

### Daily Exit Triggers
- **+5R**: +₹32,500 (exit all, stop trading)
- **-5R**: -₹32,500 (exit all, stop trading)
- **3:15 PM**: Force exit all positions

### Strike Selection Criteria
1. Price: 100-300 Rs
2. VWAP Premium: ≥4%
3. SL%: 2-10%
4. Tie-breaker: SL points closest to 10

---

## Sign-Off 📝

**Paper Trading Completed**:
- Start Date: __________
- End Date: __________
- Total Days: __________
- Total Trades: __________
- Win Rate: __________
- Average R: __________
- Issues Resolved: __________

**Approved for Live Trading**:
- [ ] All tests passed
- [ ] No critical bugs
- [ ] Risk parameters validated
- [ ] Emergency procedures documented

**Signed**: ________________  
**Date**: ________________

---

## Emergency Contacts 🆘

- **OpenAlgo Support**: [GitHub Issues](https://github.com/marketcalls/openalgo)
- **Broker Support**: <broker_phone>
- **Telegram Bot**: @<your_bot_username>

---

**⚠️ CRITICAL REMINDER**: Always start with paper trading. Never skip testing phases. Real capital at risk.
