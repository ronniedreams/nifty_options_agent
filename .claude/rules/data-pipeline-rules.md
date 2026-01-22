---
paths: data_pipeline.py
---

# Data Pipeline Rules

## Overview

The data pipeline aggregates real-time tick data from WebSocket into 1-minute OHLCV candles and calculates VWAP continuously.

```
WebSocket Ticks → Tick Buffer → 1-Min Bar Aggregation → VWAP Calculation → Downstream Systems
```

## WebSocket Management

### Connection Handling

- **Initial Connection**: Establish WebSocket connection to broker at startup
- **Auto-Reconnect**: Implement exponential backoff on disconnect
- **Heartbeat**: Validate connection every 30 seconds (or as per broker spec)
- **Graceful Shutdown**: Close connection properly on system exit

### Error Handling

- **Connection Lost**: Log error, trigger auto-reconnect with backoff
- **Message Loss**: Log but don't crash; continue with next message
- **Timeout**: Reconnect if no messages received for 60 seconds
- **Never Silently Fail**: Always log connection issues for debugging

## Tick Processing

### Tick Receipt

Each tick contains: `symbol, timestamp, bid, ask, volume, ltp`

- **Validation**: Check timestamp is within expected range (not stale/future)
- **Deduplication**: Skip duplicate ticks (same symbol, timestamp)
- **Symbol Mapping**: Verify symbol format matches expected NIFTY options format
- **Data Quality**: Check bid < ask, volume > 0

### Use LTP (Last Traded Price)

- Use LTP as the price for aggregation
- Not bid/ask midpoint (avoid execution slippage in calculations)
- LTP reflects actual execution price

## 1-Minute Bar Aggregation

### Bar Formation Rules

Each bar is 1 calendar minute:
- **Start**: Minute boundary (HH:00:00)
- **End**: Next minute boundary (HH:01:00)
- **Window**: Exactly 60 seconds

### OHLCV Calculation

From all ticks in the minute window:

```python
open = first_tick_ltp      # First tick LTP in the minute
high = max(all_tick_ltps)  # Highest LTP in the minute
low = min(all_tick_ltps)   # Lowest LTP in the minute
close = last_tick_ltp      # Last tick LTP in the minute
volume = sum(all_volumes)  # Total volume in the minute
```

### Bar Completion

Only emit bar when:
1. Minute boundary crossed (next minute started)
2. At least one tick received in the minute
3. All OHLCV values are valid:
   - Not None
   - high >= max(open, close)
   - low <= min(open, close)
   - volume > 0

### Handling No-Tick Minutes

If no ticks received in a minute:
- **Skip the minute** (don't create empty bar)
- **Log warning**: `[DATA] No ticks for NIFTY...CE in minute HH:MM`
- **Continue processing**: Don't crash, wait for next tick

## VWAP Calculation

### What is VWAP

Volume-Weighted Average Price: average price weighted by volume traded.

### When to Calculate

- **On bar completion** (when 1-minute bar closes, not on every tick)
- **Cumulative** from start of trading day (9:15 AM IST)
- **Per symbol** (separate VWAP for each option strike)

### Formula

```
Typical Price = (High + Low + Close) / 3  # Uses completed bar's OHLC
Cumulative PV = Sum of (Typical Price × Volume) from start of day
Cumulative Volume = Total volume from start of day
VWAP = Cumulative PV / Cumulative Volume
```

### Implementation Details

**For each symbol, maintain:**
```python
session_vwap_data = {
    'symbol': 'NIFTY30DEC2526000CE',
    'cum_pv': 0.0,           # Cumulative (Typical Price × Volume)
    'cum_vol': 0,            # Cumulative volume
}
```

**On bar completion:**
```python
# When bar completes (minute boundary crossed)
typical_price = (bar.high + bar.low + bar.close) / 3
cum_pv += typical_price * bar.volume
cum_vol += bar.volume

if cum_vol > 0:
    bar.vwap = cum_pv / cum_vol
else:
    bar.vwap = typical_price
```

**Reset Daily:**
- At market open (9:15 AM IST), reset VWAP for all symbols
- Clear cumulative PV and volume
- Start fresh calculation

**Note:** VWAP is frozen at swing formation time for filter evaluation. Once a swing forms, its VWAP doesn't change (immutable).

### Include in Bar Data

When emitting bar, include VWAP:
```python
bar = {
    'symbol': symbol,
    'timestamp': bar_time,
    'open': open,
    'high': high,
    'low': low,
    'close': close,
    'volume': volume,
    'vwap': vwap  # Include VWAP in every bar
}
```

## Multi-Symbol Handling

### Symbol Coverage

Maintain bars for all NIFTY options being traded:
- CE strikes: 26000CE, 26050CE, 26100CE, etc.
- PE strikes: 26000PE, 26050PE, 26100PE, etc.

### Per-Symbol State

Each symbol maintains independent:
- Tick buffer (for current minute)
- VWAP cumulative state
- Bar history

### Bar Emission

When a bar completes for any symbol:
- Emit bar with full OHLCV + VWAP
- Send downstream to swing detector and filter engine
- Log bar formation: `[BAR] NIFTY30DEC2526000CE: O=130 H=132 L=129 C=131 V=1000 VWAP=130.5`

## Data Quality Monitoring

### Heartbeat Signal

Emit heartbeat every 60 seconds showing data health:

```
[HEARTBEAT] Positions: 0 | Data: 22/22 | Coverage: 100.0% | Stale: 0
```

**Metrics:**
- **Data**: Current symbols / Expected symbols (e.g., 22/22)
- **Coverage**: % of symbols with recent ticks (100% = good, <90% = warning)
- **Stale**: Number of symbols with no ticks in last minute

### Alert Conditions

**Warning Level:**
- Coverage < 100% → Some symbols falling behind
- Stale > 0 → At least one symbol has no recent data

**Critical Level:**
- Coverage < 90% → Significant data gaps
- Connection loss → WebSocket disconnected

## Bar History Management

### Retention

- Keep last 100 bars per symbol in memory
- Discard older bars (they're in database for historical analysis)

### Access Pattern

Swing detector needs:
- Last 50 bars for swing detection (watch counter evaluation)
- Latest bar completed (current bar for processing)

Provide methods:
```python
get_bars(symbol, count=50)  # Get last N bars
get_latest_bar(symbol)      # Get most recent bar
```

## Integration Points

### Input: WebSocket

Receive ticks in format: `(symbol, timestamp, bid, ask, ltp, volume)`

### Output: Downstream Systems

Emit bars to:
1. **Swing Detector** → Processes bar on bar close, detects swings
2. **Filter Engine** → Recalculates SL% using current bar's high (updates with each tick)
3. **Position Tracker** → Updates live prices

**Current Bar High Tracking:**
- Current bar's high updates with each tick (if LTP > current high)
- Filter engine uses this real-time high for SL% calculation
- Ensures SL% reflects true risk at any moment, not just at bar close

## Common Gotchas

### Gotcha 1: VWAP vs Current Bar High Confusion
- **Issue**: Confusing VWAP calculation with current bar high tracking
- **Clarification**:
  - VWAP: Calculated on bar close (cumulative, uses completed bars)
  - Current bar high: Updates with each tick (used for SL% calculation)
- **Impact**: VWAP is frozen at swing formation; current bar high is real-time

### Gotcha 2: Empty Minutes
- **Issue**: Creating bars with zero volume (no ticks in minute)
- **Fix**: Skip minutes with no ticks
- **Impact**: Wrong swing detection on empty bars

### Gotcha 3: Timezone Mismatch
- **Issue**: Using UTC timestamps instead of IST
- **Fix**: Convert all timestamps to IST for bar key
- **Impact**: Bar alignment issues, market open mismatch

### Gotcha 4: Stale Data
- **Issue**: Not monitoring data freshness
- **Fix**: Implement heartbeat and coverage tracking
- **Impact**: Silent failures when data stops flowing

### Gotcha 5: Wrong Typical Price
- **Issue**: Using close price instead of (H+L+C)/3 for VWAP
- **Fix**: Always use Typical Price = (high + low + close) / 3
- **Impact**: Inaccurate VWAP, filter calculation errors

### Gotcha 6: Symbol Format
- **Issue**: Inconsistent symbol naming across components
- **Fix**: Normalize all symbols to NIFTY[DDMMMYY][STRIKE][CE/PE]
- **Impact**: Data lookup failures, missing bars

## Validation Checkpoints

**On tick receipt:**
- [ ] Symbol in expected format (NIFTY...CE/PE)
- [ ] Timestamp within 1-hour window (not stale)
- [ ] LTP > 0 and bid < ask
- [ ] Volume > 0

**On bar completion:**
- [ ] OHLCV values valid (not None)
- [ ] high >= max(open, close) and low <= min(open, close)
- [ ] volume > 0
- [ ] VWAP calculated and non-zero
- [ ] Timestamp is exact minute boundary

**On data quality check:**
- [ ] Coverage >= 90% (warn if <100%)
- [ ] Heartbeat emitted every 60 seconds
- [ ] Stale count = 0 (all symbols have recent data)
- [ ] No duplicate bar emissions

## Performance Optimization

- Use dict-based symbol lookup (O(1) access)
- Limit bar history in memory to 100 bars per symbol
- Batch bar emissions (emit once per minute)
- Calculate VWAP incrementally (don't recalculate from scratch)
- Log data quality metrics periodically (not every bar)

## EC2/Docker Environment

### WebSocket URL Differences

| Environment | WebSocket URL |
|-------------|---------------|
| Local (Laptop) | ws://127.0.0.1:8765 |
| EC2 (Docker) | ws://openalgo:8765 (internal network) |

### Environment-Aware Configuration

```python
import os

def get_websocket_url():
    if os.environ.get('DOCKER_ENV'):
        return "ws://openalgo:8765"  # Docker service name
    return "ws://127.0.0.1:8765"     # Local development
```

### Troubleshooting Data Issues on EC2

```bash
# Check if data pipeline is receiving ticks
docker-compose logs -f trading_agent | grep "\[TICK\]"

# Check bar formation
docker-compose logs -f trading_agent | grep "\[BAR\]"

# Check heartbeat and coverage
docker-compose logs -f trading_agent | grep "\[HEARTBEAT\]"

# Verify WebSocket connection
docker-compose logs openalgo | grep -i websocket
```
