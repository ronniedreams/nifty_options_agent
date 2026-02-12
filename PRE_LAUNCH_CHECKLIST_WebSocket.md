# Pre-Launch Checklist - WebSocket Spot Detection
**Branch:** draft/debugging
**Date:** 2026-02-04 (Tuesday)
**Market Open:** 9:15 AM IST
**Testing Window:** 9:15 AM - 9:30 AM (first 15 minutes)

---

## Before Market Open (9:00 AM - 9:14 AM)

### 1. System Preparation

**Terminal 1: OpenAlgo**
```bash
cd D:\nifty_options_agent\openalgo-zerodha\openalgo
python app.py
```
- [ ] OpenAlgo running on http://127.0.0.1:5000
- [ ] Login to Zerodha broker
- [ ] Status shows "Connected"
- [ ] WebSocket on ws://127.0.0.1:8765

**Terminal 2: Verify Branch**
```bash
cd D:\nifty_options_agent
git status
# Should show: On branch draft/debugging
```
- [ ] Confirmed on `draft/debugging` branch
- [ ] No uncommitted changes (clean working tree)

### 2. Quick Test (Optional - 5 min before market)
```bash
python -m baseline_v1_live.test_websocket_spot
```
- [ ] WebSocket connection: PASS
- [ ] Subscription: PASS
- [ ] API fallback: PASS

---

## At Market Open (9:15 AM - 9:16 AM)

### 3. Start Strategy with Auto Mode

**Option A: Paper Trading (Recommended for first test)**
```bash
# Ensure PAPER_TRADING=true in .env
python -m baseline_v1_live.baseline_v1_live --auto
```

**Option B: Live Trading (if confident)**
```bash
# Ensure PAPER_TRADING=false in .env
python -m baseline_v1_live.baseline_v1_live --auto
```

### 4. Watch for Auto-Detection Logs

**Expected Output (WebSocket Success):**
```
[AUTO] Auto-detection mode enabled (WebSocket + API fallback)
[AUTO] Connecting to WebSocket for spot price...
[AUTO] Subscribing to NIFTY spot: Nifty 50
[AUTO] Attempting WebSocket-based spot price detection...
[AUTO] Waiting Xs for 9:16 AM candle close...  ← Before 9:16
[AUTO] NIFTY Spot from WebSocket (9:16 close): 25XXX.XX  ← SUCCESS!
[AUTO] Calculated ATM: 25XXX.XX -> 25XXX
[AUTO] Found XX expiries
[AUTO] Nearest expiry: 06-FEB-26 (Thursday, 06 February 2026)
[AUTO] Detected ATM: 25XXX, Expiry: 06FEB26
[AUTO] Cleaning up temporary WebSocket connection...
```

**Alternative Output (API Fallback - Still OK):**
```
[AUTO] WebSocket LTP not available, will try API fallback
[AUTO] Using API fallback for spot price...
[AUTO] NIFTY Spot from API: 25XXX.XX  ← Fallback used, but works
```

### 5. Monitor First 15 Minutes (9:15 AM - 9:30 AM)

**Check Points:**
- [ ] Auto-detection completed (ATM and Expiry detected)
- [ ] Data pipeline receiving ticks (check logs for tick updates)
- [ ] Swing detection working (check for [SWING] logs)
- [ ] Filter evaluation running (check for [FILTER] logs)
- [ ] No errors or exceptions in logs
- [ ] Memory usage stable (< 500 MB)
- [ ] WebSocket connection stable (no disconnects)

**Key Metrics to Track:**
```
[HEARTBEAT] Positions: X | Data: XX/XX | Coverage: XX.X% | Stale: X
```
- [ ] Data coverage > 90%
- [ ] Stale data count = 0
- [ ] All subscribed symbols receiving data

---

## Success Criteria (9:30 AM Check)

### ✅ Test PASSED if:
1. **Auto-detection used WebSocket** (check logs for "NIFTY Spot from WebSocket")
2. **ATM strike detected correctly** (reasonable value, multiple of 100)
3. **Expiry detected correctly** (nearest weekly/monthly)
4. **System running without errors** for 15+ minutes
5. **Data pipeline healthy** (>90% coverage, 0 stale)
6. **Swing detection working** (if swings formed)

### ⚠️ Test ACCEPTABLE if:
1. **API fallback used** (WebSocket didn't have data fast enough)
2. **But system still works** (ATM/Expiry detected, no errors)
3. **Consider increasing wait time** in baseline_v1_live.py (line 1046: `time.sleep(5)`)

### ❌ Test FAILED if:
1. **Auto-detection crashed** (exception during startup)
2. **ATM/Expiry invalid** (wrong values, not multiple of 100)
3. **WebSocket errors** (connection failures, subscription errors)
4. **System unstable** (frequent errors, crashes)

---

## Post-Test Actions

### If Test PASSED ✅

**Merge to Main:**
```bash
# Stop the strategy (Ctrl+C)

# Verify no issues
git status

# Merge to main
git checkout main
git merge draft/debugging

# Tag the successful test
git tag websocket-spot-v1.0-$(date +%Y%m%d)

# Push to remote
git push origin main --tags

# Keep draft/debugging for future work
git checkout draft/debugging
```

**Document Results:**
```bash
# Add entry to CHANGELOG.md or create summary
echo "✅ WebSocket spot detection - Tested successfully on 2026-02-04" >> TEST_LOG.md
```

### If Test FAILED ❌

**Debug and Investigate:**
```bash
# Stop the strategy (Ctrl+C)

# Check logs for errors
tail -100 logs/baseline_v1_live.log

# Re-run test suite
python -m baseline_v1_live.test_websocket_spot

# If symbol issue, try variants:
# Edit baseline_v1_live.py line 1026:
# spot_symbol = "NIFTY"  # Try instead of "Nifty 50"
```

**Rollback if needed:**
```bash
# Stay on draft/debugging, don't merge
# Fix issues and test again tomorrow
```

---

## Debug Checklist (If Issues Occur)

### Issue: WebSocket Not Receiving Ticks
**Symptoms:**
```
[AUTO] WebSocket LTP not available, will try API fallback
```

**Solutions:**
1. Check broker is logged in (OpenAlgo dashboard)
2. Verify market is open (9:15 AM - 3:30 PM)
3. Increase wait time: `time.sleep(5)` → `time.sleep(10)`
4. Try different symbol name (see WEBSOCKET_SPOT_TESTING.md)

### Issue: Symbol Name Mismatch
**Symptoms:**
```
FAILED: Spot price is None
```

**Solutions:**
Try variants in baseline_v1_live.py (line 1026):
- `"Nifty 50"` (default)
- `"NIFTY"`
- `"NSE:NIFTY"`
- `"NIFTY 50"`

### Issue: Auto-Detection Crashes
**Symptoms:**
```
[AUTO] Auto-detection failed: <exception>
```

**Solutions:**
1. Check logs for full traceback
2. Verify OpenAlgo is running and logged in
3. Test with manual mode: `--expiry 06FEB26 --atm 25700`
4. Run test suite to isolate issue

---

## Emergency Fallback

If WebSocket approach has critical issues:

**Use Manual Mode:**
```bash
# Get current NIFTY spot manually (from broker/news)
# Example: NIFTY at 25,727.55 → ATM = 25,700

python -m baseline_v1_live.baseline_v1_live --expiry 06FEB26 --atm 25700
```

System will work normally, just without auto-detection.

---

## Important Reminders

1. **⏰ No Code Changes During Market Hours** (9:15 AM - 3:30 PM)
2. **📊 Monitor first 30 minutes closely**
3. **💾 Let system run for full session** (don't stop unless critical issue)
4. **📝 Take notes** on any unusual behavior
5. **🔍 Check logs after market close** for detailed analysis

---

## Contact Info

**Documentation:**
- Implementation: `WEBSOCKET_SPOT_TESTING.md`
- Test Suite: `baseline_v1_live/test_websocket_spot.py`
- Git SOP: `.claude/GIT_SOP.md`

**Expected Behavior:**
- Before 9:16 AM: Waits for 9:16 candle close
- After 9:16 AM: Uses current LTP immediately
- API fallback always available as backup

---

## Final Check (Right Before Launch)

- [ ] OpenAlgo running and logged in
- [ ] On `draft/debugging` branch
- [ ] PAPER_TRADING setting verified (.env)
- [ ] Terminal ready to launch strategy
- [ ] Logs directory writable
- [ ] System resources OK (CPU < 50%, RAM < 4GB free)
- [ ] Backup terminal ready for emergency stop (Ctrl+C)

**Good Luck! 🚀**

---

*Generated: 2026-02-03 17:50 IST*
*Branch: draft/debugging*
*Commit: 6160248*
