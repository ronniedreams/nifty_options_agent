# OpenAlgo v2 Migration Checklist

**Status**: Not Started
**Started**: ___________
**Completed**: ___________

Refer to `openalgo_v2_migration_plan.md` for detailed instructions.

---

## Pre-Migration Baseline

- [ ] Document current v1 metrics:
  - [ ] Average orders per day: ___
  - [ ] Order success rate: ___
  - [ ] WebSocket uptime: ___
  - [ ] Position reconciliation accuracy: ___
  - [ ] Average R-multiple: ___

---

## Phase 1: Local Testing (Week 1)

**Started**: ___________
**Completed**: ___________

### Backup and Setup
- [ ] Backup OpenAlgo v1 database (openalgo.db → openalgo_v1_backup.db)
- [ ] Backup trading system .env (baseline_v1_live/.env → .env.backup)
- [ ] Install UV package manager (`pip install uv`)

### OpenAlgo v2 Upgrade
- [ ] Navigate to D:\marketcalls\openalgo
- [ ] Git fetch and pull latest (`git checkout main && git pull`)
- [ ] Verify Python 3.12+ installed (`python --version`)
- [ ] Copy .sample.env to .env
- [ ] Run migration script (`uv run migrate_all.py`)
- [ ] Start OpenAlgo v2 (`uv run app.py`)
- [ ] Verify React UI at http://127.0.0.1:5000
- [ ] Clear browser cache and test dashboard
- [ ] Login with Zerodha credentials
- [ ] Verify "Status: Connected" in dashboard

### Trading System Update
- [ ] Upgrade OpenAlgo SDK (`pip install --upgrade openalgo`)
- [ ] Verify SDK version (`pip show openalgo`)
- [ ] Run system check (`python -m baseline_v1_live.check_system`)
- [ ] Document any API errors or warnings

### WebSocket Testing
- [ ] Start trading system in paper mode
- [ ] Verify WebSocket connects successfully
- [ ] Check ticks received for all symbols
- [ ] Verify data coverage 100%
- [ ] Test reconnection after manual disconnect

### Order Testing (Paper Mode)
- [ ] Wait for swing detection or manually trigger
- [ ] Verify SL entry orders place successfully
- [ ] Verify order status polling detects fills
- [ ] Verify exit SL orders place after entry fills
- [ ] Verify position tracking updates correctly
- [ ] Verify Telegram notifications sent

### Extended Testing (3-5 Days)
- [ ] Day 1 testing complete
- [ ] Day 2 testing complete
- [ ] Day 3 testing complete
- [ ] Day 4 testing complete
- [ ] Day 5 testing complete
- [ ] Zero API errors observed
- [ ] All order types working (SL entry, SL exit, MARKET)
- [ ] Position reconciliation accurate
- [ ] Data coverage consistently 100%

### Code Changes (If Needed)
- [ ] API parameter changes identified: ___________
- [ ] Code modifications made: ___________
- [ ] Changes tested and verified

**Phase 1 Sign-Off**: ___________

---

## Phase 2: EC2 Staging Deployment (Week 2)

**Started**: ___________
**Completed**: ___________

### Pre-Deployment
- [ ] Verify Python 3.12 in Docker on EC2
- [ ] SSH into EC2 (13.233.211.15)
- [ ] Backup EC2 openalgo.db
- [ ] Backup Docker volumes
- [ ] Commit and push current state to GitHub

### OpenAlgo v2 Deployment
- [ ] Pull latest code on EC2
- [ ] Run migration script on EC2
- [ ] Rebuild OpenAlgo Docker image
- [ ] Restart OpenAlgo container
- [ ] Verify container health (`docker-compose ps`)
- [ ] Check logs for errors

### Trading Agent Update
- [ ] Update requirements.txt with v2 SDK
- [ ] Rebuild trading agent container
- [ ] Restart in paper mode
- [ ] Test API connectivity from container
- [ ] Run system check from container

### Dashboard Verification
- [ ] Access https://openalgo.ronniedreams.in
- [ ] Clear browser cache
- [ ] Login with Basic Auth
- [ ] Verify React UI loads
- [ ] Check broker connection status

### Extended Testing (5 Days)
- [ ] Day 1 monitoring complete
- [ ] Day 2 monitoring complete
- [ ] Day 3 monitoring complete
- [ ] Day 4 monitoring complete
- [ ] Day 5 monitoring complete
- [ ] No container restarts from crashes
- [ ] WebSocket connection stable
- [ ] Order flows match local v2 testing

**Phase 2 Sign-Off**: ___________

---

## Phase 3: Production Cutover (Week 3)

**Started**: ___________
**Completed**: ___________

### Pre-Cutover Verification
- [ ] Phase 1 completed successfully
- [ ] Phase 2 completed successfully
- [ ] Zero critical errors in logs
- [ ] Position reconciliation 100% accurate
- [ ] Rollback plan reviewed and ready
- [ ] Market closed (after 3:30 PM IST)

### Live Trading Enablement
- [ ] Edit .env on EC2 (PAPER_TRADING=false)
- [ ] Restart trading agent
- [ ] Verify configuration in logs

### Day 1 Live Monitoring
- [ ] 9:00 AM - Start monitoring
- [ ] 9:15 AM - Verify WebSocket ticks
- [ ] 9:30 AM - Check swing detection
- [ ] 10:00 AM - Verify order placement (if triggered)
- [ ] 11:00 AM - Position reconciliation check
- [ ] 12:00 PM - Midday status check
- [ ] 1:00 PM - Position reconciliation check
- [ ] 2:00 PM - Position reconciliation check
- [ ] 3:15 PM - Verify force exit logic
- [ ] 3:30 PM - Review daily summary

### Week 1 Live Trading
- [ ] Day 1 complete (detailed monitoring)
- [ ] Day 2 complete
- [ ] Day 3 complete
- [ ] Day 4 complete
- [ ] Day 5 complete
- [ ] System trades autonomously
- [ ] No API errors
- [ ] Position tracking accurate
- [ ] Risk management enforced

**Phase 3 Sign-Off**: ___________

---

## Post-Migration Validation

### Performance Comparison (v1 vs v2)
- [ ] Order success rate: v1=___ v2=___ (target: match within 2%)
- [ ] WebSocket uptime: v1=___ v2=___ (target: match or exceed)
- [ ] Position accuracy: v1=___ v2=___ (target: 100%)
- [ ] Daily P&L variance: ___ (target: within ±2%)
- [ ] Risk limits enforced correctly
- [ ] Telegram notifications working

### Edge Case Testing
- [ ] Order cancellation when disqualified
- [ ] Partial fills handled correctly
- [ ] Order rejection retry logic works
- [ ] WebSocket reconnection works
- [ ] Position mismatch detection works
- [ ] Daily exit at 3:15 PM works
- [ ] Daily limits (±5R) enforced
- [ ] Swing update triggers order modification

### Performance Benchmarks
- [ ] Order placement latency: ___ (target: <500ms)
- [ ] Order fill detection: ___ (target: <10s)
- [ ] WebSocket tick latency: ___ (target: <2s)
- [ ] Historical backfill time: ___ (target: <30s)

---

## Success Metrics (After 1 Month)

| Metric | Target | Actual | Pass/Fail |
|--------|--------|--------|-----------|
| Order Success Rate | >98% | ___ | ___ |
| WebSocket Uptime | >99.5% | ___ | ___ |
| Position Accuracy | 100% | ___ | ___ |
| System Crashes | 0/month | ___ | ___ |
| API Errors | <5/day | ___ | ___ |
| Manual Interventions | 0/week | ___ | ___ |

**Overall Migration Status**: ___________

---

## Issues Encountered

| Date | Phase | Issue | Resolution | Impact |
|------|-------|-------|------------|--------|
| ___ | ___ | ___ | ___ | ___ |
| ___ | ___ | ___ | ___ | ___ |
| ___ | ___ | ___ | ___ | ___ |

---

## Rollback Executed

- [ ] N/A - Migration successful
- [ ] Rollback performed on: ___________
  - Trigger condition: ___________
  - Rollback time: ___________
  - System restored: Yes / No
  - Lessons learned: ___________

---

## Final Notes

**Migration Successful**: Yes / No
**Recommendation for Future**: ___________
**Key Learnings**: ___________
