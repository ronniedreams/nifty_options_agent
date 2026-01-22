---
paths: swing_detector.py, continuous_filter.py
---

# Swing Detection & Filtration Rules

## Swing Detection Theory

### The Watch-Based System

Each bar gets watch counters that track when future bars confirm it was a turning point:

- **low_watch**: Counter incremented when a future bar has HIGHER HIGH + HIGHER CLOSE
- **high_watch**: Counter incremented when a future bar has LOWER LOW + LOWER CLOSE
- **Trigger**: When counter reaches 2, the swing is confirmed

### The Alternating Pattern (Non-Negotiable)

Valid swing sequence: `High → Low → High → Low → High → Low`

- After a swing LOW, the next swing MUST be HIGH
- After a swing HIGH, the next swing MUST be LOW
- Reject any swing that violates this pattern

### Swing Updates (Same Direction)

Before a new alternating swing forms, if a NEW EXTREME appears, UPDATE the existing swing:

**For Swing Lows:**
- Current swing LOW @ 80
- Price drops to 75 (new lower low) before any HIGH
- Action: UPDATE swing low from 80 → 75
- Reason: 75 is the true extreme, not 80

**For Swing Highs:**
- Current swing HIGH @ 100
- Price rallies to 105 (new higher high) before any LOW
- Action: UPDATE swing high from 100 → 105
- Reason: 105 is the true extreme, not 100

**Invalid Update (Reject):**
- Current swing LOW @ 80
- Price drops to 82 (not lower than 80)
- Action: REJECT - not a new extreme

### Window Behavior

- **First swing**: From start of data to current bar
- **Subsequent swings**: From bar AFTER last swing to current bar

This ensures we only look at relevant price action.

## Multi-Symbol Context

Swing detection operates **independently per symbol**. Each option strike (e.g., NIFTY06JAN2624000CE, NIFTY06JAN2624100CE) has its own:
- Swing history (alternating highs and lows)
- Watch counters (low_watch, high_watch)
- Candidates in swing_candidates pool

This allows the system to track multiple potential entry points across different strikes simultaneously.

## Proactive Order Flow

**Key Concept:** Orders are placed BEFORE swing breaks, not after.

```
1. Swing LOW detected and qualified → Place SL order (entry)
   - Trigger: swing_low - tick_size (e.g., 130.00 - 0.05 = 129.95)
   - Limit: trigger - 3 Rs buffer (e.g., 129.95 - 3 = 126.95)
   - Order sits dormant until price drops to trigger

2. Price drops to trigger → Entry order FILLS → Position opened

3. IMMEDIATELY after fill → Place exit SL order
   - Trigger: highest_high + 1 Rs (e.g., 142.00 + 1 = 143.00)
   - Limit: trigger + 3 Rs buffer (e.g., 143.00 + 3 = 146.00)
   - This protects the position from adverse moves
```

**Why Proactive?**
- No latency between swing break and order placement
- Better fill prices (limit order vs market chase)
- Order ready and waiting at exchange

## Strike Filtration Pipeline

### Stage 1: Static Filter (Run Once)

Applied immediately when swing forms. Never re-evaluated.

**Filters (all configurable in config.py):**
1. **Price Range**: `MIN_ENTRY_PRICE ≤ Swing Low ≤ MAX_ENTRY_PRICE` (configurable, default: 100-300)
2. **VWAP Premium**: `((Swing Low - VWAP) / VWAP) ≥ MIN_VWAP_PREMIUM` (configurable, default: 4%)

**Key Points:**
- VWAP is frozen at swing formation time (immutable)
- Price range check eliminates thinly traded or expensive options
- VWAP premium ensures entry is above "normal value"
- Pass → Add to `swing_candidates` dict
- Fail → Log rejection reason, discard swing

### Stage 2: Dynamic Filter (Run Every Bar/Tick)

Applied continuously to all swings in `swing_candidates` pool.

**Filter (configurable in config.py):**
- **SL% Range**: `MIN_SL_PERCENT ≤ SL% ≤ MAX_SL_PERCENT` (configurable, default: 2-10%)

**SL% Calculation:**
```
Highest High = Maximum high price since swing formation (including current tick)
Entry Price = Swing low price
SL Price = Highest High + 1 Rs (buffer for slippage)
SL% = (SL Price - Entry Price) / Entry Price × 100
```

**Why Dynamic:**
- Highest High updates every bar (price keeps moving)
- SL% can change from PASS → FAIL as highest_high increases
- Example: Bar 1: SL%=4.6% ✓ → Bar 3: SL%=12.3% ❌ (exceeds 10%)

**Real-Time Evaluation (CRITICAL):**
- **Highest high tracking**: Includes current bar's high, which updates with each tick as new highs are made
- **Filter evaluation**: Tick-wise - add/remove candidates as soon as they qualify/disqualify
- **Why tick-wise matters**: Order placed immediately when strike qualifies in Stage 3, so we need real-time SL%

```
Example:
Bar 10: Swing LOW forms @ 130
Bar 11: High = 135 (highest_high = 135, SL% = 4.6%)
Bar 12: High = 138 (highest_high = 138, SL% = 6.9%)
Bar 13: Current bar, high so far = 142 (highest_high = 142, SL% = 10.0%)
        ↑ If tick pushes high to 144 → highest_high = 144 → SL% = 11.5% → DISQUALIFIED immediately
```

**Action:**
- Pass → Add to `qualified_candidates` list (mutable, refreshed every bar)
- Fail → Remove from qualified pool, log rejection

### Stage 3: Tie-Breaker (Best Strike Selection)

When multiple strikes pass all filters for same option type (CE or PE), select ONE.

**Rule 1: SL Points Closest to 10 Rs (Primary)**
```
Target: 10 points (optimized for R_VALUE - configurable in config.py)
sl_distance = abs(sl_points - 10)

Example:
Strike A: SL=8 points → distance=2
Strike B: SL=12 points → distance=2
Strike C: SL=9 points → distance=1 ← WINNER (closest to 10)
```

**Rule 2: Strike Multiple of 100 (Secondary Tie-Breaker)**
```
If multiple strikes have same SL distance, prefer strikes that are multiples of 100

Why: Round strikes (24000, 24100, 24200) have better liquidity than odd strikes (24050, 24150)

Strike Extraction from Symbol:
  Symbol: NIFTY06JAN2626200CE
  Format: NIFTY + DDMMMYY + STRIKE + CE/PE
  Strike: 26200 (extract numeric portion before CE/PE)
  Check: 26200 % 100 == 0 → True (multiple of 100)

Example (both distance=1):
Strike 24050CE: Entry=145, 24050 % 100 = 50 (not multiple)
Strike 24100CE: Entry=142, 24100 % 100 = 0 ← WINNER (multiple of 100)
```

**Rule 3: Highest Entry Price (Final Tie-Breaker)**
```
If multiple strikes have same SL distance AND both are multiples of 100, prefer higher premium

Example (both distance=1, both multiples of 100):
Strike 24100CE: Entry=145 ← WINNER (higher premium)
Strike 24200CE: Entry=120
```

**Output:**
- Store best strike per option type in `current_best` dict
- Mark as eligible for order placement

## Data Structures

### swing_candidates (Dict)
```python
{
    'NIFTY06JAN2626200CE': {
        'symbol': 'NIFTY06JAN2626200CE',
        'swing_low': 130.50,
        'timestamp': datetime(2026, 1, 1, 10, 15),
        'vwap': 125.00,
        'vwap_premium_pct': 4.4,
        'option_type': 'CE',
        'index': 75  # Bar index when swing formed
    }
}
```
**Purpose:** All swings that passed static filters (price range + VWAP premium).
**Immutability:** Once added, never re-evaluated on static filters.
**Removal:** Only if swing breaks, replaced by new swing, or daily reset.

### qualified_candidates (List)
```python
[
    {
        'symbol': 'NIFTY06JAN2626200CE',
        'swing_low': 130.50,
        'highest_high': 142.30,
        'sl_price': 143.30,  # highest_high + 1 Rs buffer
        'sl_points': 12.80,  # sl_price - swing_low
        'sl_percent': 0.098,  # sl_points / swing_low
        'vwap_premium_pct': 4.4,
    },
]
```
**Purpose:** Swings from swing_candidates that currently pass dynamic SL% filter.
**Mutability:** Refreshed every bar/tick.
**Update Frequency:** Real-time, not batched.

### current_best (Dict)
```python
{
    'CE': {
        'symbol': 'NIFTY06JAN2626200CE',
        'swing_low': 130.50,
        'highest_high': 142.30,
        'sl_points': 12.80,
        'sl_percent': 0.098,
    },
    'PE': None  # or PE candidate object
}
```
**Purpose:** Single best strike per option type (CE/PE) selected from qualified_candidates.
**Eligibility:** This is the strike eligible for order placement.

## Filter Rejection Tracking

Log every rejection with reason:

```python
{
    'timestamp': '2026-01-01T10:15:00',
    'symbol': 'NIFTY06JAN2626300CE',
    'swing_low': 145.00,
    'rejection_reason': 'vwap_premium_low',
    'detail': 'VWAP premium 2.1% < 4.0% threshold'
}
```

**Valid Rejection Reasons:**
1. `price_low`: Entry < MIN_ENTRY_PRICE (static)
2. `price_high`: Entry > MAX_ENTRY_PRICE (static)
3. `vwap_premium_low`: Premium < MIN_VWAP_PREMIUM (static)
4. `sl_percent_low`: SL% < MIN_SL_PERCENT (dynamic, mutable)
5. `sl_percent_high`: SL% > MAX_SL_PERCENT (dynamic, mutable)
6. `no_data`: Missing OHLC/VWAP data

## Swing Invalidation Rules

**Once swing_low price is breached, that swing is "dead"** - regardless of whether we had an order placed.

### Scenario 1: Swing Break WITH Order (Qualified Strike)
```
Swing: 26200CE @ 130 (was best qualified → SL order placed)
Price drops below 130 → Order triggers and fills → Position entered

Action:
1. Position opened - swing served its purpose
2. Remove from swing_candidates
3. Remove from qualified_candidates
4. Place exit SL order immediately
5. Swing data retained for reference (next swing detection)
```

### Scenario 2: Swing Break WITHOUT Order (Not Qualified)
```
Swing: 26200CE @ 130 (was in pool but NOT the best qualified → no order)
Price drops below 130 → Entry opportunity passed

Action:
1. Mark swing as "dead" - no longer considered for qualification
2. Keep in swing_candidates pool (for reference purposes)
3. Will NOT appear in qualified_candidates anymore
4. Swing data retained for next swing detection
5. Log: "[SWING] 26200CE swing broken without order - opportunity passed"
```

### Scenario 3: New Swing Replaces Old
```
Old swing: 26200CE @ 130 (in pool)
New swing LOW forms: 26200CE @ 125 (lower low)

Action:
1. Old swing replaced by new swing
2. New swing evaluated through filter pipeline
3. Old swing data discarded
```

### Summary Table

| Scenario | In Pool? | Considered for Qualification? | Notes |
|----------|----------|-------------------------------|-------|
| Swing break + order filled | Removed | No | Position entered |
| Swing break + no order | Stays (dead) | **No** | Opportunity passed |
| New swing replaces old | Old removed | No | Replaced by new |
| SL% out of range | Stays | No (not in qualified) | May re-qualify later |
| Different strike becomes best | Stays | Yes | Still evaluated each tick |

## Common Gotchas

### Gotcha 1: Stale Highest High
- **Issue**: Using highest_high from previous bar instead of including current tick
- **Fix**: Always include current tick when calculating highest_high
- **Impact**: Wrong SL%, premature disqualifications

### Gotcha 2: VWAP Doesn't Update
- **Issue**: VWAP frozen at swing formation, but never recalculated with new data
- **Fix**: VWAP is immutable by design (correct behavior)
- **Note**: This is intentional - we want VWAP at formation time, not current

### Gotcha 3: Stale Current Bar High
- **Issue**: Not updating current bar's high with each tick
- **Fix**: Track current bar's high in real-time (updates with each tick if new high made)
- **Impact**: SL% won't reflect true risk mid-bar, may miss disqualification moment

### Gotcha 4: Missing Swing Updates
- **Issue**: Not updating swing when new extreme forms
- **Fix**: Check for new lows (if swing low) and new highs (if swing high) every bar
- **Impact**: Entering at wrong levels, wrong risk calculations

### Gotcha 5: Alternating Pattern Violation
- **Issue**: Creating same-direction swings (two LOWs or two HIGHs in a row)
- **Fix**: Check last_swing_type before accepting new swing
- **Impact**: Invalid swing sequence, wrong entry/exit logic

## Validation Checkpoints

**When swing detected:**
- [ ] Alternating pattern maintained (opposite of last swing type)
- [ ] Pass static filter (price range + VWAP premium) if first swing
- [ ] Check for swing update opportunity (new extreme same direction)
- [ ] Log swing with timestamp, price, VWAP, premium %

**When evaluating for order placement:**
- [ ] Swing in swing_candidates pool (passed static filters)
- [ ] SL% calculated including current tick
- [ ] SL% within MIN_SL_PERCENT to MAX_SL_PERCENT range
- [ ] Highest high updated to current bar's high
- [ ] Tie-breaker applied if multiple candidates

**Before order placement:**
- [ ] current_best contains selected strike
- [ ] Strike passes all three filter stages
- [ ] No duplicate order already pending for this strike
- [ ] Position availability check passed

## Evaluation Frequency Summary

| Component | Frequency | Reason |
|-----------|-----------|--------|
| **Swing detection** (watch counters) | Bar close | Needs complete OHLC bars |
| **Highest high tracking** | Every tick | Current bar's high updates with new highs |
| **SL% calculation** | Every tick | Depends on highest_high |
| **Filter evaluation** | Every tick | Order placed immediately on qualification |
| **Order status polling** | Every 10 seconds | Broker API rate limits |
| **Position reconciliation** | Every 60 seconds | Sync with broker |

## Performance Optimization

- Cache swing_candidates to avoid re-filtering every bar
- Only recalculate SL% for swings in swing_candidates pool
- Use index-based lookup for highest_high window (avoid full scan)
- Log filter rejections periodically (not every tick) to reduce I/O
- Monitor swing detection accuracy through live logs
