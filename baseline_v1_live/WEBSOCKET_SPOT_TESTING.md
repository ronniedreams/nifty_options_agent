# WebSocket Spot Price Testing Guide

## Overview

This guide explains how to test the new WebSocket-based NIFTY spot price detection for auto mode.

## What Was Changed

**Before:** Auto-detection made a separate API call to fetch NIFTY spot price.

**After:** Auto-detection uses WebSocket (already established) to fetch spot price, with API as fallback.

## Test Script

### Run the Test Suite

```bash
# Ensure OpenAlgo is running first
cd D:\nifty_options_agent\openalgo-zerodha\openalgo
python app.py

# In another terminal, run the test
cd D:\nifty_options_agent
python -m baseline_v1_live.test_websocket_spot
```

### What It Tests

1. **WebSocket Connection** - Verifies connection to OpenAlgo WebSocket
2. **NIFTY Spot Subscription** - Subscribes to "Nifty 50" symbol
3. **Spot Price Retrieval** - Gets current LTP from WebSocket
4. **Bar Retrieval** - Fetches latest completed 1-min bar
5. **Auto-Detection** - Tests WebSocket-based detection
6. **Full Flow** - Tests complete ATM + Expiry detection
7. **Symbol Variants** - Diagnostic test for different symbol formats

### Expected Output (Success)

```
================================================================================
  TEST 1: WebSocket Connection
================================================================================

10:15:30 | INFO | Creating DataPipeline instance...
10:15:31 | INFO | WebSocket connection established
10:15:31 | INFO | SUCCESS: WebSocket is connected

================================================================================
  TEST 2: NIFTY Spot Subscription
================================================================================

10:15:31 | INFO | Subscribing to NIFTY spot: Nifty 50
10:15:31 | INFO | Waiting 5 seconds for WebSocket data...
10:15:36 | INFO | SUCCESS: Nifty 50 is in subscribed symbols

================================================================================
  TEST 3: Spot Price Retrieval (Current LTP)
================================================================================

10:15:36 | INFO | SUCCESS: NIFTY Spot LTP: 24248.75
10:15:36 | INFO |   Type: <class 'float'>
10:15:36 | INFO |   Value: 24248.75
10:15:36 | INFO |   Spot price is within expected range (15000-30000)

================================================================================
  TEST 4: Bar Retrieval (Latest Completed Bar)
================================================================================

10:15:36 | INFO | SUCCESS: Retrieved latest bar
10:15:36 | INFO |   Timestamp: 2026-02-03 10:14
10:15:36 | INFO |   Open:  24245.00
10:15:36 | INFO |   High:  24250.00
10:15:36 | INFO |   Low:   24240.00
10:15:36 | INFO |   Close: 24248.75
10:15:36 | INFO |   Volume: 150
10:15:36 | INFO |   VWAP: 24246.25
10:15:36 | INFO |   Tick Count: 12

================================================================================
  TEST 5: Auto-Detection with WebSocket
================================================================================

10:15:36 | INFO | Testing WebSocket-based spot price fetch...
10:15:36 | INFO | Already past 9:16 AM, using current WebSocket LTP
10:15:36 | INFO | NIFTY Spot from WebSocket (current LTP): 24248.75
10:15:36 | INFO | SUCCESS: WebSocket spot price: 24248.75
10:15:36 | INFO |   Calculated ATM: 24248.75 -> 24200

================================================================================
  TEST 6: Full Auto-Detection Flow (ATM + Expiry)
================================================================================

10:15:36 | INFO | Running full auto-detection (may take a few seconds)...
10:15:36 | INFO | Already past 9:16 AM, using current WebSocket LTP
10:15:36 | INFO | NIFTY Spot from WebSocket (current LTP): 24248.75
10:15:38 | INFO | Found 12 expiries
10:15:38 | INFO | Nearest expiry: 06-FEB-26 (Thursday, 06 February 2026)
10:15:38 | INFO | SUCCESS: Auto-detection complete
10:15:38 | INFO |   ATM Strike: 24200
10:15:38 | INFO |   Expiry Date: 06FEB26

================================================================================
  TEST SUMMARY
================================================================================

10:15:40 | INFO |   ✓ Connection: PASS
10:15:40 | INFO |   ✓ Subscription: PASS
10:15:40 | INFO |   ✓ Spot Price: PASS
10:15:40 | INFO |   ✓ Bar Retrieval: PASS
10:15:40 | INFO |   ✓ Auto Detection: PASS
10:15:40 | INFO |   ✓ Full Flow: PASS

10:15:40 | INFO | TOTAL: 6/6 tests passed (100%)
10:15:40 | INFO | SUCCESS: All tests passed! WebSocket spot detection is working.
```

## Troubleshooting

### Issue 1: Spot Price is None

**Symptoms:**
```
FAILED: Spot price is None (no data received)
```

**Possible Causes:**
1. **WebSocket not receiving ticks** - Wait longer (increase sleep time)
2. **Symbol name mismatch** - Try different variants:
   - "Nifty 50" (Angel One)
   - "NIFTY" (Zerodha)
   - "NSE:NIFTY" (some brokers)
3. **Market closed** - Run during market hours (9:15 AM - 3:30 PM IST)

**Solution:**
Run Test 7 (Symbol Variants) to identify correct symbol format for your broker.

### Issue 2: WebSocket Connection Failed

**Symptoms:**
```
FAILED: WebSocket connection failed
```

**Possible Causes:**
1. OpenAlgo not running
2. Wrong WebSocket URL in config
3. Broker not logged in

**Solution:**
```bash
# Check OpenAlgo is running
curl http://127.0.0.1:5000

# Check broker connection in dashboard
# http://127.0.0.1:5000
# Status should show "Connected"
```

### Issue 3: No Bars Available

**Symptoms:**
```
No completed bar available yet
```

**Possible Causes:**
- System started mid-minute (bar not complete yet)
- No ticks received (market closed)

**Solution:**
- Wait for next minute boundary
- Check current bar exists (incomplete bar with latest LTP)

### Issue 4: API Fallback Used Instead of WebSocket

**Symptoms:**
```
[AUTO] WebSocket LTP not available, will try API fallback
[AUTO] Using API fallback for spot price...
[AUTO] NIFTY Spot from API: 24248.75
```

**This is NOT an error** - It means:
- WebSocket didn't have data yet (too fast startup)
- System correctly fell back to API
- Auto-detection still works

**To prefer WebSocket:**
- Increase wait time in baseline_v1_live.py (currently 3 seconds)
- Or restart test after WebSocket has been running a while

## Testing Auto Mode in Practice

After test suite passes, try real auto mode:

```bash
# Before 9:15 AM (will wait for market open)
python -m baseline_v1_live.baseline_v1_live --auto

# After 9:16 AM (uses current LTP)
python -m baseline_v1_live.baseline_v1_live --auto
```

### Expected Auto Mode Output

```
[AUTO] Auto-detection mode enabled (WebSocket + API fallback)
[AUTO] Connecting to WebSocket for spot price...
[AUTO] Subscribing to NIFTY spot: Nifty 50
[AUTO] Attempting WebSocket-based spot price detection...
[AUTO] Already past 9:16 AM, using current WebSocket LTP
[AUTO] NIFTY Spot from WebSocket (current LTP): 24248.75
[AUTO] Calculated ATM: 24248.75 -> 24200
[AUTO] Found 12 expiries
[AUTO] Nearest expiry: 06-FEB-26 (Thursday, 06 February 2026)
[AUTO] Auto-detection complete: ATM=24200, Expiry=06FEB26
[AUTO] Cleaning up temporary WebSocket connection...
[AUTO] Detected ATM: 24200, Expiry: 06FEB26
```

## Verifying Symbol Name

Different brokers use different symbol formats for NIFTY spot. To find yours:

```python
# Quick test (in Python REPL)
from openalgo import api

client = api(api_key="YOUR_KEY", host="http://127.0.0.1:5000")

# Search for NIFTY
result = client.searchscrip(exchange="NSE", searchtext="NIFTY")
print(result)

# Look for the spot symbol (not futures/options)
# Common formats:
# - "Nifty 50"     (Angel One, ICICI)
# - "NIFTY"        (Zerodha)
# - "NSE:NIFTY"    (Some brokers)
```

Then update the symbol in code:
- `baseline_v1_live.py` line 1026: `spot_symbol = "YOUR_SYMBOL"`
- `test_websocket_spot.py` uses "Nifty 50" by default

## Performance Comparison

| Method | Latency | Reliability | Data Source |
|--------|---------|-------------|-------------|
| **WebSocket (New)** | ~0.1s | High (always connected) | Real-time ticks |
| **API (Fallback)** | ~1-2s | Medium (HTTP request) | On-demand query |

**Why WebSocket is Better:**
- Already connected (no extra network call)
- Real-time data available
- Consistent with rest of system architecture
- API still available as robust fallback

## Edge Cases Handled

1. **System starts before 9:16 AM** → Waits for 9:16 candle close
2. **System starts after 9:16 AM** → Uses current LTP immediately
3. **WebSocket has no data** → Falls back to API
4. **Market closed** → API returns last known price
5. **Connection lost** → Auto-reconnect (existing feature)

## Next Steps

1. Run test suite: `python -m baseline_v1_live.test_websocket_spot`
2. Verify all tests pass
3. Try auto mode: `python -m baseline_v1_live.baseline_v1_live --auto`
4. Monitor logs for `[AUTO]` tags to see which method was used
5. If using different broker, update spot symbol name

## Summary

**Goal:** Eliminate separate API call for spot price by using WebSocket.

**Implementation:** WebSocket-first with API fallback.

**Benefit:** Faster, more reliable, architecturally consistent.

**Status:** Implemented and ready for testing.
