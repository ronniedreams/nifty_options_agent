---
paths: data_pipeline.py
---

# Data Pipeline Rules

## Overview
```
WebSocket Ticks → 1-min OHLCV Bars + VWAP → Swing Detector + Filter Engine + Position Tracker
```
Primary: Zerodha (port 8765) | Backup: Angel One (port 8766) with auto-failover.

## Bar Aggregation
- **Interval**: 60-second bars aligned to minute boundaries
- **MIN_TICKS_PER_BAR**: 5 (bars with fewer ticks discarded)
- **OHLCV**: open=first LTP, high=max LTP, low=min LTP, close=last LTP, volume=sum
- Empty minutes: skip (don't create empty bars)
- MAX_BARS_PER_SYMBOL = 400 (covers full ~6h session)

## VWAP Calculation
- Calculated **on bar close** (not on every tick)
- Cumulative from session start (9:15 AM IST)
- Formula: `Typical Price = (H + L + C) / 3; VWAP = cumsum(TP × vol) / cumsum(vol)`
- Reset daily at market open
- **VWAP at swing formation is frozen** — never updated after the swing forms

## Current Bar High (Real-Time)
- Current bar's high updates with **every tick** if LTP > current high
- Filter engine uses this real-time high for SL% calculation
- Distinct from VWAP (which only updates on bar close)

## Dual-Broker Failover

### State Variables
```python
active_source = 'zerodha'        # Which feed processes ticks into bars
is_failover_active = False
last_zerodha_tick_time = {}      # Per-symbol (always tracked regardless of active_source)
zerodha_continuous_tick_start    # When Zerodha resumed (for switchback timer)
```

### Failover Triggers → Switch to Angel One
- Zerodha ticks stale > FAILOVER_NO_TICK_THRESHOLD (15s), OR
- Zerodha WebSocket disconnects

### Switchback Triggers → Return to Zerodha
- Zerodha reconnects AND stable ticks for > FAILOVER_SWITCHBACK_THRESHOLD (10s)
- On switchback: clear `last_zerodha_tick_time` to prevent stale re-failover

### Tick Routing (Critical)
- `_on_quote_update_zerodha()`: **Always** updates `last_zerodha_tick_time`; only calls `_process_tick()` if `active_source == 'zerodha'`
- `_on_quote_update_angelone()`: Only calls `_process_tick()` if `active_source == 'angelone'`
- `_process_tick()`: Source-agnostic bar aggregation

### Thread Safety
- `active_source` and `angelone_is_connected` must be read/written inside `self.lock` (RLock)
- Never read `active_source` outside the lock

### Disabling Failover
If `ANGELONE_OPENALGO_API_KEY` is empty → failover silently disabled, Zerodha only.

## WebSocket URLs

| Environment | Zerodha | Angel One |
|-------------|---------|-----------|
| Local | ws://127.0.0.1:8765 | ws://127.0.0.1:8766 |
| EC2 Docker | ws://openalgo:8765 | ws://openalgo_angelone:8766 |

## Connection Management
- Auto-reconnect with exponential backoff on disconnect
- WEBSOCKET_MAX_RECONNECT_ATTEMPTS = 5
- WEBSOCKET_RECONNECT_DELAY = 5s base
- Log all disconnects immediately

## Data Watchdog (Auto-Shutdown)
- Check data freshness every 30s (DATA_FRESHNESS_CHECK_INTERVAL)
- MIN_DATA_COVERAGE_THRESHOLD = 50% (shutdown if <50% symbols have fresh data)
- STALE_DATA_TIMEOUT = 30s (shutdown if no fresh data for 30s)
- MAX_BAR_AGE_SECONDS = 120 (shutdown if last bar >2 min old)

## Heartbeat Log (every 60s)
```
[HEARTBEAT] Positions: 0 | Data: 82/82 | Coverage: 100.0% | Stale: 0
```

## Critical Gotchas
- **VWAP vs current bar high**: VWAP = bar-close cumulative. Current bar high = tick-level real-time. Don't confuse.
- **Empty minutes**: Skip (don't create bars), not fill with zeros
- **Timezone**: All bar keys in IST — using UTC causes bar alignment issues
- **MIN_TICKS_PER_BAR**: A bar with fewer than 5 ticks is invalid and should not be emitted
- **Angel One port**: Internal WS port is **8766** (not 8765) — already fixed in docker-compose.yaml
