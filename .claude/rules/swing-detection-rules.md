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

## Strike Filtration Pipeline

### Stage 1: Static Filter (Run Once)

Applied immediately when swing forms. Never re-evaluated.

**Filters:**
1. **Price Range**: `MIN_ENTRY_PRICE ≤ Swing Low ≤ MAX_ENTRY_PRICE` (default: 100-300)
2. **VWAP Premium**: `((Swing Low - VWAP) / VWAP) ≥ MIN_VWAP_PREMIUM` (default: 4%)

**Key Points:**
- VWAP is frozen at swing formation time (immutable)
- Price range check eliminates thinly traded or expensive options
- VWAP premium ensures entry is above "normal value"
- Pass → Add to `swing_candidates` dict
- Fail → Log rejection reason, discard swing

### Stage 2: Dynamic Filter (Run Every Bar/Tick)

Applied continuously to all swings in `swing_candidates` pool.

**Filter:**
- **SL% Range**: `MIN_SL_PERCENT ≤ SL% ≤ MAX_SL_PERCENT` (default: 2-10%)

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
- Evaluate EVERY tick (not batched every 10 seconds)
- Ensures SL% reflects true risk at order placement moment
- If excluded current bar, SL% will be artificially tight (WRONG)

**Action:**
- Pass → Add to `qualified_candidates` list (mutable, refreshed every bar)
- Fail → Remove from qualified pool, log rejection

### Stage 3: Tie-Breaker (Best Strike Selection)

When multiple strikes pass all filters for same option type (CE or PE), select ONE.

**Rule 1: SL Points Closest to 10 Rs (Primary)**
```
Target: 10 points (optimized for R_VALUE = ₹6,500)
sl_distance = abs(sl_points - 10)

Example:
Strike A: SL=8 points → distance=2
Strike B: SL=12 points → distance=2
Strike C: SL=9 points → distance=1 ← WINNER (closest to 10)
```

**Rule 2: Highest Entry Price (Tie-Breaker)**
```
If multiple strikes have same SL distance, prefer higher premium

Example (both distance=1):
Strike A: Entry=145 ← WINNER (higher premium)
Strike B: Entry=120
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

## Swing Break Detection

Swings are removed from all pools when price breaks below swing_low:

```python
Swing: 26200CE @ 130
Current bar low: 128 (< 130)

Action:
1. Mark swing as broken
2. Remove from swing_candidates
3. Remove from qualified_candidates (if present)
4. Cancel order (if pending)
5. Log swing break event
```

## Common Gotchas

### Gotcha 1: Stale Highest High
- **Issue**: Using highest_high from previous bar instead of including current tick
- **Fix**: Always include current tick when calculating highest_high
- **Impact**: Wrong SL%, premature disqualifications

### Gotcha 2: VWAP Doesn't Update
- **Issue**: VWAP frozen at swing formation, but never recalculated with new data
- **Fix**: VWAP is immutable by design (correct behavior)
- **Note**: This is intentional - we want VWAP at formation time, not current

### Gotcha 3: Batch Evaluation
- **Issue**: Only evaluating filters every 10 seconds instead of every tick
- **Fix**: Evaluate every tick for real-time SL% accuracy
- **Impact**: SL% will be artificially tight if current bar excluded

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

## Performance Optimization

- Cache swing_candidates to avoid re-filtering every bar
- Only recalculate SL% for swings in qualified_candidates
- Use index-based lookup for highest_high window (avoid full scan)
- Log filter rejections periodically (not every bar) to reduce I/O
- Monitor swing detection accuracy through live logs
