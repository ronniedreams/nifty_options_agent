# OpenAlgo v2 Migration Assessment & Recommendations

**Status**: Ready for Implementation (Scheduled: Week of 2026-02-02)
**Prepared**: 2026-01-26
**Estimated Duration**: 3 weeks (phased approach)

---

## Executive Summary

**Recommendation: PROCEED WITH MIGRATION** (with caution and phased approach)

OpenAlgo v2 represents a major architectural overhaul (React SPA frontend, multi-database system, new features), but the **REST and WebSocket APIs appear backward compatible** for your use case. The migration complexity is **MEDIUM**, with manageable risks if properly tested.

---

## Decision Matrix

| Factor | v1 (Current) | v2 (New) | Impact |
|--------|-------------|----------|--------|
| **API Stability** | Stable, well-tested | Appears compatible, needs validation | LOW |
| **Frontend** | Flask/Jinja2 | React 19 SPA | NONE (you use API only) |
| **Python Version** | 3.11+ | 3.12+ required | MEDIUM (verify EC2) |
| **Database** | Single SQLite | 5 databases | LOW (migration script provided) |
| **New Features** | N/A | Historify, Sandbox, API Playground | HIGH (useful for development) |
| **Community Support** | Stable | Active development | HIGH (future-proof) |

---

## Pros of Migrating to v2

### 1. **Future-Proofing**
- v1 will likely receive minimal updates
- v2 is the active development branch (212 commits since v1)
- Bug fixes and broker updates will focus on v2

### 2. **Better Testing Tools**
- **Sandbox Mode**: Isolated Rs. 1 Crore virtual capital (better than PAPER_TRADING flag)
- **API Playground**: Built-in Bruno-style API tester (debug WebSocket subscriptions easily)
- **Historify Module**: DuckDB-backed historical data with 1m-1d timeframes (could replace your parquet files)

### 3. **Performance Improvements**
- React frontend faster (code splitting, lazy loading)
- Multi-database architecture reduces lock contention
- ZeroMQ message bus for normalized data (optional, port 5555)

### 4. **Infrastructure Enhancements**
- Separate logs database (easier debugging)
- Latency monitoring database (performance tracking)
- Better error handling with structured JSON responses

### 5. **Active Community**
- GitHub Discussions active (735+ discussions)
- Documentation updated (docs.openalgo.in)
- Rapid issue resolution (recent commits show daily activity)

---

## Cons of Migrating to v2

### 1. **Testing Overhead**
- **HIGH**: Minimum 2-3 weeks of testing required
- Must validate all integration points (order placement, WebSocket, position sync)
- Paper trading mandatory before live deployment

### 2. **Python Version Upgrade**
- **CRITICAL**: Python 3.12+ required (your current setup uses 3.11+)
- EC2 Docker container needs base image update
- Potential compatibility issues with other dependencies

### 3. **Potential API Changes**
- **MEDIUM RISK**: Documentation shows both `price_type` and `pricetype` (inconsistency)
- Your code uses `price_type="SL"` - may need adjustment
- Orderbook response field names might change (`order_status` vs `status`)

### 4. **Database Migration**
- Must run migration script (`migrate_all.py`)
- Backup required before upgrade (openalgo.db is 51 MB)
- Feed token column added (auto-migration, but test carefully)

### 5. **Deployment Downtime**
- Must deploy outside market hours (after 3:30 PM IST)
- Nginx configuration may need updates (SSL cert refresh)
- EC2 production system at risk if rollback needed

### 6. **Unknown Unknowns**
- v2 just released (January 22, 2026) - early adoption risks
- Broker-specific quirks may exist (Zerodha integration tested?)
- Community hasn't reported all edge cases yet

---

## Critical Risk Areas

### 🔴 HIGH RISK

| Area | Current v1 Behavior | Potential v2 Change | Mitigation |
|------|-------------------|---------------------|------------|
| **SL Order Placement** | `price_type="SL"` with trigger/limit | Docs show `pricetype` (inconsistency) | Test both parameter names, update if needed |
| **WebSocket Subscription** | `subscribe_quote(instruments)` | May change with ZeroMQ integration | Monitor first ticks, validate structure |
| **Order Fill Detection** | Uses `order_status` field | May rename to `status` | Check orderbook response, update parsing |

### 🟡 MEDIUM RISK

| Area | Mitigation |
|------|------------|
| **Historical Data Backfill** | Test `history()` API with v2, validate DataFrame structure |
| **Position Reconciliation** | Verify `positionbook()` response fields unchanged |
| **Python 3.12 Compatibility** | Test all dependencies (pandas, pytz, websocket-client) |

### 🟢 LOW RISK

| Area | Notes |
|------|-------|
| **Authentication** | API key mechanism likely unchanged |
| **Paper Trading** | Sandbox mode is enhanced version of Analyzer mode |
| **Error Handling** | JSON error format appears consistent |

---

## Migration Steps (Phased Approach)

### Phase 1: Local Testing (Week 1)

**Goal**: Validate v2 compatibility on Windows laptop (paper trading)

#### Step 1.1: Backup Current State
```bash
# Backup OpenAlgo v1 database
cd D:\marketcalls\openalgo
copy openalgo.db openalgo_v1_backup.db

# Backup trading system .env
cd D:\nifty_options_agent
copy baseline_v1_live\.env baseline_v1_live\.env.backup
```

#### Step 1.2: Install UV Package Manager
```powershell
pip install uv
```

#### Step 1.3: Upgrade OpenAlgo to v2
```bash
# In D:\marketcalls\openalgo
git fetch origin
git checkout main  # or v2 branch if separate
git pull

# Check Python version (must be 3.12+)
python --version

# Copy sample environment file
copy .sample.env .env

# Run migration script
uv run migrate_all.py

# Start OpenAlgo v2
uv run app.py
```

#### Step 1.4: Verify OpenAlgo Dashboard
- Clear browser cache (Ctrl+Shift+Delete)
- Open http://127.0.0.1:5000
- Should see React-based UI (faster loading)
- Login with Zerodha credentials
- Verify "Status: Connected" in dashboard

#### Step 1.5: Update Trading System Dependencies
```bash
cd D:\nifty_options_agent

# Upgrade OpenAlgo SDK to v2
pip install --upgrade openalgo

# Check installed version
pip show openalgo
```

#### Step 1.6: Test API Endpoints
```bash
# Run system check
python -m baseline_v1_live.check_system
```

**Expected Output**:
```
✓ OpenAlgo API accessible
✓ API key valid
✓ Broker connected
✓ WebSocket functional
✓ Historical data available
```

**If Errors**:
- Check API endpoint changes in v2 docs
- Update parameter names (price_type vs pricetype)
- Verify orderbook field names (order_status vs status)

#### Step 1.7: Test WebSocket Feed
```bash
# Start trading system in paper mode
PAPER_TRADING=true python -m baseline_v1_live.baseline_v1_live --expiry 30JAN26 --atm 23500
```

**Monitor Logs**:
```
[HEARTBEAT] Positions: 0 | Data: 82/82 | Coverage: 100.0% | Stale: 0
[TICK] NIFTY30JAN2623500CE: 245.50 (LTP)
```

**Validation Checklist**:
- [ ] WebSocket connects successfully
- [ ] Ticks received for all subscribed symbols
- [ ] Data coverage 100% (no stale symbols)
- [ ] Reconnection works after manual disconnect

#### Step 1.8: Test Order Placement (Paper Mode)
**Wait for swing detection or manually trigger order**

**Monitor Logs**:
```
[ORDER-PLACE] NIFTY30JAN2623500CE: SELL SL @ trigger=129.95, limit=126.95, qty=650
[ORDER-PLACED] Order ID: 240126000001, Status: PENDING
[ORDER-FILL] Order 240126000001 filled @ 127.30 (qty: 650)
[SL-PLACE] Exit SL @ trigger=160.00, limit=163.00
```

**Validation Checklist**:
- [ ] SL entry orders place successfully
- [ ] Order status polling detects fills
- [ ] Exit SL orders place after entry fills
- [ ] Position tracking updates correctly
- [ ] Telegram notifications sent

#### Step 1.9: Run for 3-5 Trading Days
- Monitor daily (9:15 AM - 3:30 PM IST)
- Check for any API errors
- Verify order lifecycle (place → fill → exit)
- Compare with v1 behavior (if running in parallel)

**Success Criteria**:
- Zero API errors over 5 days
- All order types work (SL entry, SL exit, MARKET exit)
- Position reconciliation accurate (no mismatches)
- Data coverage consistently 100%
- System handles market volatility without crashes

---

### Phase 2: EC2 Staging Deployment (Week 2)

**Goal**: Deploy v2 on EC2 in parallel with v1 (blue-green deployment)

#### Step 2.1: Verify Python 3.12 on EC2
```bash
# SSH into EC2
ssh -i "D:/aws_key/openalgo-key.pem" ubuntu@13.233.211.15

# Check Python version in Docker
docker run --rm python:3.12-bullseye python --version
# Should output: Python 3.12.x
```

#### Step 2.2: Backup EC2 State
```bash
# On EC2
cd ~/nifty_options_agent

# Backup databases
docker cp openalgo:/app/db/openalgo.db ./backups/openalgo_v1_$(date +%Y%m%d).db

# Backup Docker volumes
docker volume create openalgo_data_backup
docker run --rm -v openalgo_data:/from -v openalgo_data_backup:/to alpine sh -c "cd /from && cp -av . /to"

# Commit current state
git add -A
git commit -m "Backup before v2 migration"
git push origin feature/docker-ec2-fixes
```

#### Step 2.3: Deploy v2 OpenAlgo
```bash
# Pull latest code (ensure v2 branch merged)
cd ~/nifty_options_agent/openalgo-zerodha/openalgo
git pull origin main

# Run migration script
uv run migrate_all.py

# Rebuild Docker image (includes Python 3.12 update)
cd ~/nifty_options_agent
docker-compose build openalgo

# Restart OpenAlgo container
docker-compose up -d openalgo

# Verify container health
docker-compose ps openalgo
# Should show "Up" status

# Check logs for errors
docker-compose logs -f openalgo
```

#### Step 2.4: Update Trading Agent
```bash
# Update requirements.txt with v2 SDK
cd ~/nifty_options_agent
# Edit requirements.txt: openalgo==2.x.x

# Rebuild trading agent container
docker-compose build trading_agent

# Restart in paper mode
docker-compose up -d trading_agent

# Monitor logs
docker-compose logs -f trading_agent
```

#### Step 2.5: Test from EC2
```bash
# Test API connectivity from trading agent container
docker exec -it trading_agent python -m baseline_v1_live.check_system
```

#### Step 2.6: Verify Dashboard Access
- Open https://openalgo.ronniedreams.in
- Clear browser cache
- Login with Basic Auth (admin / your-dashboard-password)
- Verify React UI loads
- Check broker connection status

#### Step 2.7: Run Paper Trading for 1 Week
- Monitor daily (remote access via dashboard)
- Compare logs with local v2 testing
- Verify Telegram notifications arrive
- Check position reconciliation every hour

**Success Criteria**:
- System runs unattended for 5 consecutive days
- No container restarts due to crashes
- WebSocket connection stable (no frequent reconnects)
- Order flows identical to local v2 testing

---

### Phase 3: Production Cutover (Week 3)

**Goal**: Switch to live trading with v2 (outside market hours)

#### Step 3.1: Pre-Cutover Checklist
- [ ] v2 tested locally for 5+ days (Phase 1 complete)
- [ ] v2 tested on EC2 for 5+ days (Phase 2 complete)
- [ ] Zero critical errors in logs
- [ ] Position reconciliation 100% accurate
- [ ] Rollback plan documented
- [ ] Market closed (after 3:30 PM IST)

#### Step 3.2: Enable Live Trading
```bash
# On EC2
cd ~/nifty_options_agent

# Edit .env file
nano baseline_v1_live/.env
# Change: PAPER_TRADING=false

# Restart trading agent
docker-compose restart trading_agent

# Monitor logs closely
docker-compose logs -f trading_agent
```

#### Step 3.3: First Live Session Monitoring
**Day 1 (Full Attention Required)**:
- 9:00 AM: Start monitoring logs 15 minutes before market opens
- 9:15 AM: Market opens - verify WebSocket ticks flowing
- 9:30 AM: Check first swing detection (if any)
- 10:00 AM: Verify order placement if entry triggers
- 11:00 AM: Check position reconciliation (every hour)
- 3:15 PM: Verify force exit logic (if positions open)
- 3:30 PM: Market closes - review daily summary

**Critical Alerts to Watch**:
- Order placement failures
- Fill detection delays (should be <10 seconds)
- Position mismatches (broker vs system)
- WebSocket disconnects

#### Step 3.4: Week 1 Live Trading
- Monitor daily P&L vs v1 baseline (if historical data available)
- Compare R-multiple calculations
- Verify daily limits (±5R exits)
- Check Telegram notifications

**Success Criteria**:
- System trades autonomously without manual intervention
- All orders execute as designed (no API errors)
- Position tracking accurate (verified against broker dashboard)
- Risk management enforced (daily limits, max positions)

---

### Phase 4: Rollback Plan (If Critical Issues)

**Trigger Conditions** (rollback immediately if any occur):
1. **Order API failures** >10% of attempts
2. **WebSocket disconnects** >5 times per session
3. **Position mismatches** >2 in a single day
4. **Incorrect order types** placed (e.g., MARKET instead of SL)
5. **Risk limits breached** (daily stop bypassed)

**Rollback Steps**:
```bash
# On EC2
cd ~/nifty_options_agent

# Stop containers
docker-compose down

# Restore v1 code
git checkout <pre-v2-commit-hash>

# Restore v1 database
docker cp ./backups/openalgo_v1_YYYYMMDD.db openalgo:/app/db/openalgo.db

# Or restore volume backup
docker run --rm -v openalgo_data:/to -v openalgo_data_backup:/from alpine sh -c "cd /from && cp -av . /to"

# Rebuild with v1
docker-compose build
docker-compose up -d

# Verify v1 running
docker-compose ps
docker-compose logs -f trading_agent
```

**Expected Rollback Time**: <15 minutes

---

## Code Changes Required

### 1. **order_manager.py** (if API parameter names change)

**Location**: Lines 832-860, 943-962, 1027-1063

**Potential Change**:
```python
# v1 code:
response = client.placeorder(
    strategy="baseline_v1_live",
    symbol="NIFTY30JAN2623500CE",
    action="SELL",
    exchange="NFO",
    price_type="SL",  # ← May need to change
    trigger_price=129.95,
    price=126.95,
    quantity=650,
    product="MIS"
)

# v2 code (if docs are correct):
response = client.placeorder(
    ...,
    pricetype="SL",  # ← Changed from price_type
    triggerprice=129.95,  # ← May be camelCase now
    ...
)
```

**Testing**:
```bash
# Place test order in paper mode
# If 400 error: "Unknown parameter 'price_type'" → Update parameter names
```

### 2. **order_manager.py** (if orderbook field names change)

**Location**: Lines 652-658, 1027-1028

**Potential Change**:
```python
# v1 code:
status = order.get('order_status', '').lower()  # ← OpenAlgo-specific field

# v2 code (if normalized):
status = order.get('status', '').lower()  # ← Standard field
```

**Testing**:
```bash
# Check orderbook response structure
response = client.orderbook()
print(json.dumps(response, indent=2))
# Look for 'order_status' vs 'status' field
```

### 3. **data_pipeline.py** (if WebSocket message format changes)

**Location**: Lines 710-734, 995-1153

**Current Tick Structure**:
```python
{
    "symbol": "NIFTY30JAN2623500CE",
    "data": {
        "ltp": 245.50,
        "high": 250.00,
        "low": 240.00,
        "volume": 12500,
        "timestamp": "2024-12-20 10:15:30"
    }
}
```

**v2 May Add**:
```python
{
    ...,
    "data": {
        ...,
        "feed_token": "12345",  # ← New field
        "broker": "zerodha"      # ← Broker identifier
    }
}
```

**Testing**:
```bash
# Log raw WebSocket messages
# Add in data_pipeline.py callback function:
logger.debug(f"[WS-RAW] {json.dumps(message)}")
```

### 4. **requirements.txt**

**Change**:
```
# v1
openalgo==1.0.45

# v2
openalgo>=2.0.0  # Use ">=" to auto-upgrade patch versions
```

### 5. **docker-compose.yaml** (if base image needs update)

**Location**: openalgo service build section

**Potential Change**:
```yaml
# Current (if using Python 3.11)
FROM python:3.11-bullseye

# v2 required
FROM python:3.12-bullseye
```

**Testing**:
```bash
# Check Dockerfile in openalgo-zerodha/openalgo/Dockerfile
# Should already be 3.12-bullseye if pulling latest v2 code
```

---

## Testing Checklist

### Before Migration (Current v1 Baseline)
- [ ] Document current system performance metrics:
  - Average orders per day: ___
  - Order success rate: ___
  - WebSocket uptime: ___
  - Position reconciliation accuracy: ___
  - Average R-multiple: ___

### After v2 Migration (Compare with v1)
- [ ] Order placement success rate matches v1 (>95%)
- [ ] WebSocket uptime matches v1 (>99%)
- [ ] Position reconciliation accuracy matches v1 (100%)
- [ ] Daily P&L calculations match v1 within ±2%
- [ ] Risk limits enforced (daily ±5R exits)
- [ ] Telegram notifications delivered within 5 seconds

### Edge Cases to Test
- [ ] **Order Cancellation**: Cancel pending SL orders when disqualified
- [ ] **Partial Fills**: Handle partial fills correctly (use filled_qty from broker)
- [ ] **Order Rejection**: Retry logic works (3 attempts with 2s delay)
- [ ] **WebSocket Reconnection**: Reconnects within 30s, backfills missed bars
- [ ] **Position Mismatch**: Reconciliation detects and logs discrepancies
- [ ] **Daily Exit**: Force close at 3:15 PM works regardless of market conditions
- [ ] **Daily Limits**: System exits all positions at ±5R (or configured limits)
- [ ] **Swing Update**: Swing low updates trigger order modification (not cancellation)

### Performance Benchmarks
- [ ] Order placement latency: <500ms (from signal to order placed)
- [ ] Order fill detection latency: <10s (from fill to system acknowledgment)
- [ ] WebSocket tick latency: <2s (from exchange to system)
- [ ] Historical backfill time: <30s for full day (9:15 AM to current)

---

## Final Recommendation

### ✅ **PROCEED WITH MIGRATION** if:
1. You can dedicate **2-3 weeks for testing** (1 week local, 1 week EC2 staging, 1 week live monitoring)
2. Python 3.12+ is available on both laptop and EC2 (verify Docker base images)
3. You're willing to **monitor closely** during first live session (Day 1: full attention required)
4. System is currently **stable in v1** (no critical bugs that need immediate fixes)
5. You have **rollback plan confidence** (can restore v1 within 15 minutes)

### ⛔ **DEFER MIGRATION** if:
1. System is in **active production with tight deadlines** (can't afford testing time)
2. Python 3.12 upgrade is **complex** (many dependency conflicts)
3. v1 has **critical bugs** that need fixing first (focus on stability before migration)
4. Market conditions are **highly volatile** (wait for calmer period)
5. You're **unavailable** for close monitoring during initial live session

### 🎯 **Conservative Approach** (Recommended):
- **Month 1**: Upgrade local to v2, test extensively in paper mode
- **Month 2**: Upgrade EC2 to v2, run paper trading in parallel with v1 (if possible)
- **Month 3**: Cutover to live trading with v2, monitor daily for first week

### ⚡ **Aggressive Approach** (Not Recommended):
- Upgrade both local and EC2 simultaneously
- Risk: No working baseline to compare if issues arise
- Only consider if v2 has critical features you need immediately

---

## Risk Mitigation Summary

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **API Parameter Changes** | MEDIUM | HIGH | Test order placement in paper mode, update code if needed |
| **WebSocket Protocol Changes** | LOW | HIGH | Monitor first ticks, validate structure, log raw messages |
| **Orderbook Field Renames** | MEDIUM | HIGH | Check response structure, update parsing logic |
| **Python 3.12 Incompatibility** | LOW | MEDIUM | Test all dependencies locally first |
| **Database Migration Failure** | LOW | HIGH | Backup before migration, test restore process |
| **Deployment Downtime** | MEDIUM | MEDIUM | Deploy outside market hours, prepare rollback |
| **Early Adoption Bugs** | MEDIUM | MEDIUM | Join GitHub Discussions, monitor issue tracker |

---

## Success Metrics

After 1 month of v2 live trading, evaluate:

| Metric | Target | Acceptable | Unacceptable |
|--------|--------|-----------|--------------|
| **Order Success Rate** | >98% | >95% | <95% |
| **WebSocket Uptime** | >99.5% | >99% | <99% |
| **Position Accuracy** | 100% | 100% | <100% |
| **System Crashes** | 0/month | 1/month | >1/month |
| **API Errors** | <5/day | <10/day | >10/day |
| **Manual Interventions** | 0/week | 1/week | >1/week |

If **any metric falls into "Unacceptable"** range for 3 consecutive days → **Rollback to v1 immediately**.

---

## Additional Resources

- **OpenAlgo v2 Discussions**: https://github.com/marketcalls/openalgo/discussions/735
- **Documentation**: https://docs.openalgo.in
- **API Reference**: https://docs.openalgo.in/api-documentation/v1
- **WebSocket API**: https://docs.openalgo.in/api-documentation/v1/websockets
- **Upgrade Guide**: https://docs.openalgo.in/installation-guidelines/getting-started/upgrade

---

## Next Steps (When Ready to Start)

1. **Create migration branch**:
   ```bash
   git checkout -b feature/openalgo-v2-migration
   ```

2. **Start Phase 1 (Local Testing)**:
   - Backup current state
   - Upgrade OpenAlgo locally
   - Test for 5+ trading days

3. **Document findings**:
   - Any API changes discovered
   - Code modifications made
   - Issues encountered and resolved

4. **Proceed to Phase 2** (EC2 staging) only after Phase 1 success

---

**Prepared by**: Claude Code Agent
**Date**: 2026-01-26
**Status**: Ready for implementation (scheduled Week of 2026-02-02)
**Estimated Timeline**: 3 weeks (phased approach)
