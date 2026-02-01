# Strike Filtration Theory - Two-Stage Filter System

## Overview

Strike filtration is the process of evaluating swing candidates to determine which options qualify for order placement. This happens AFTER swing detection but BEFORE order execution.

The system uses a **two-stage approach**: Static filters (run once) and Dynamic filters (run continuously).

## Core Philosophy

Not every swing low is worth trading. We need filters to eliminate lot of them

## The Two-Stage Filter Pipeline


```
Swing Detected
    ↓
[STAGE 1: Static Filter] (Run ONCE when swing forms)
    ↓
Price Filter (MIN_ENTRY_PRICE-MAX_ENTRY_PRICE)
    ↓
VWAP Filter (≥MIN_VWAP_PREMIUM) - IMMUTABLE
    ↓
Pass → swing_candidates
Fail → Rejected (never rechecked)
    ↓
[STAGE 2: Dynamic Filter] (Run EVERY TICK - real-time)
    ↓
SL% Filter (MIN_SL_PERCENT-MAX_SL_PERCENT) - MUTABLE
    ↓
Pass → qualified_candidates
    ↓
[STAGE 3: Tie-Breaker]
    ↓
Best Strike Selection (SL closest to 10 → Multiple of 100 → Highest premium)
    ↓
Order Placement Trigger
```

## Stage 1: Static Filter (Run Once)

Applied **immediately when swing forms**, never rechecked.

### Criteria:

1. **Price Range:**
   - Rule: MIN_ENTRY_PRICE ≤ Entry Price ≤ MAX_ENTRY_PRICE
   - Entry Price = Swing Low (the option premium at swing formation)

2. **VWAP Premium:**
   - Rule: VWAP Premium ≥ MIN_VWAP_PREMIUM
   - VWAP at swing formation = swing_info['vwap']
   - VWAP premium % = ((Entry - VWAP) / VWAP) × 100
   - MIN_VWAP_PREMIUM = config.MIN_VWAP_PREMIUM

### When Applied

```
Swing detector calls: _on_swing_detected(swing_info)
    ↓
Check: MIN_ENTRY_PRICE ≤ swing_info['price'] ≤ MAX_ENTRY_PRICE
    ↓
Check: VWAP Premium ≥ MIN_VWAP_PREMIUM
    ↓
Pass: Add to swing_candidates dict
Fail: Log rejection, discard swing
```

### Immutability

Once a swing passes static filter:
- It stays in `swing_candidates` until:
  - Swing breaks (price drops below swing_low)
  - New swing for same symbol replaces it
  - Daily reset at market open

Static filter is NEVER re-evaluated.

## Stage 2: Dynamic Filter (Run Every Tick)

Applied **every tick** to all candidates in `swing_candidates`. Current bar's high updates with each tick, so SL% is evaluated in real-time.

Example candidates pool:
```python
swing_candidates = {
    'NIFTY...26200CE': {'swing_low': 130, 'vwap_premium': 5.2%},
    'NIFTY...26250CE': {'swing_low': 95, 'vwap_premium': 4.8%},
    'NIFTY...26150PE': {'swing_low': 140, 'vwap_premium': 6.1%}
}
```

### SL% Filter (Truly Dynamic)

**Rule:** MIN_SL_PERCENT ≤ SL% ≤ MAX_SL_PERCENT

**Formula:**
```
Highest High = Maximum high since swing formation
Entry Price = Swing low
SL Price = Highest High + 1 Rs (buffer for slippage)
SL Points = SL Price - Entry Price
SL% = (SL Points / Entry Price) × 100
```

**Why +1 Rs Buffer?**
- Accounts for tick-level slippage during volatile moves
- Ensures SL triggers reliably without premature exits
- Protects against price whipsaws at exact highest high level
- Example: Entry 130, Highest High 140 → SL at 141 (11 points instead of 10)

**Requirement:** MIN_SL_PERCENT ≤ SL% ≤ MAX_SL_PERCENT (configurable, default 2% to 10%)

### Dynamic Re-evaluation


**Important Note:**
For live trading, the SL% and swing break logic must be evaluated in real time on every tick (not just every 10 seconds or on bar close). This ensures that if the current bar makes a new high and then immediately breaks the swing low, the SL% reflects the true risk at the moment of order placement. Excluding the current bar or using a slower evaluation interval can result in orders being placed with an incorrect (too tight) stop-loss, creating a mismatch between theoretical and actual risk.

**Real-Time Evaluation Pattern:**

```python
on every tick:
    for swing in swing_candidates:
        # Get latest highest high since swing (including current tick)
        highest_high = get_highest_high_since_swing(swing)
        # Calculate SL with +1 Rs buffer
        sl_price = highest_high + 1
        sl_points = sl_price - swing_low
        sl_percent = sl_points / swing_low
        # Check range
        if sl_percent < MIN_SL_PERCENT or sl_percent > MAX_SL_PERCENT:
            # Disqualified - remove from candidates
            continue
        # Still qualified - add to current cycle's qualified list
        qualified_candidates.append(swing)
```

### Why SL% is MUTABLE

Unlike VWAP, SL% changes every bar because:

**Highest High Updates:**
```
Bar 1 after swing: High = 135, Swing = 130, SL Price = 136, SL% = 4.6%
Bar 2 after swing: High = 138, Swing = 130, SL Price = 139, SL% = 6.9%
Bar 3 after swing: High = 145, Swing = 130, SL Price = 146, SL% = 12.3% ❌ FAIL (>10%)
```

**Note:** SL Price includes +1 Rs buffer (SL Price = Highest High + 1)

**As highest_high grows:**
- SL% increases
- Can change from passing → failing
- Swing gets disqualified when SL% exceeds MAX_SL_PERCENT

## Stage 3: Tie-Breaker (Best Strike Selection)

When **multiple strikes pass all filters**, select ONE per option type (CE/PE).

### Tie-Breaker Rules (in order)

**Rule 1: SL Points Closest to 10 (Primary)**

Target: 10 points (based on R_VALUE, configurable)

```python
sl_distance = abs(sl_points - 10)

# Example:
Strike A: SL = 8 points → distance = 2
Strike B: SL = 12 points → distance = 2
Strike C: SL = 9 points → distance = 1 ✓ BEST

# Note: SL points includes +1 Rs buffer
# E.g., Entry=130, Highest High=139 → SL Price=140 → SL Points=10
```

**Why prefer 10 points?**

R_VALUE (configurable, default Rs.6,500) optimized for:
- 10 points × 10 lots × 65 qty = Rs.6,500

Strikes with ~10-point SL give:
- Optimal position sizing
- Maximum capital efficiency
- Balanced risk/reward

**Rule 2: Strike Multiple of 100 (Secondary Tie-Breaker)**

```python
# If same SL distance, prefer strikes that are multiples of 100
# Why: Round strikes (24000, 24100, 24200) have better liquidity

# Strike extraction from symbol:
# NIFTY06JAN2626200CE → Strike = 26200 → 26200 % 100 == 0 ✓

# Example (both distance=1):
Strike 24050CE: Entry=145, 24050 % 100 = 50 (not multiple)
Strike 24100CE: Entry=142, 24100 % 100 = 0 ✓ BETTER (multiple of 100)
```

**Rule 3: Highest Entry Price (Final Tie-Breaker)**

```python
# If still tied (same SL distance AND both multiples of 100), choose higher premium
Strike A: SL distance = 2, multiple of 100, Entry = 125 ✓ BETTER
Strike B: SL distance = 2, multiple of 100, Entry = 120
```


### Tie-Breaker Example

```
CE Candidates after filters:
1. 26200CE: SL=11pts, Entry=145 → distance=1, 26200 % 100 = 0 ✓
2. 26250CE: SL=9pts, Entry=130  → distance=1, 26250 % 100 = 50 ✗
3. 26300CE: SL=7pts, Entry=115  → distance=3

Step 1: Check SL distance (Rule 1)
- 26200CE and 26250CE tied (distance=1)
- 26300CE eliminated (distance=3)

Step 2: Check multiple of 100 (Rule 2)
- 26200CE: 26200 % 100 = 0 ✓ WINNER (multiple of 100)
- 26250CE: 26250 % 100 = 50 (not multiple)

Best CE = 26200CE

(Rule 3 not needed - resolved by Rule 2)
```

## Filter State Tracking

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
        # ... other fields
    }
}
```

**Purpose:** Holds all swings that passed static filters (price range and VWAP premium) at swing formation. Only swings in this pool are eligible for dynamic SL% filtering and order selection. Swings are removed if broken, replaced, or at daily reset.

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
        # ... other fields
    },
    # ... more qualified swings
]
```

**Purpose:** Contains swings from swing_candidates that currently pass the dynamic SL% filter (real-time tick evaluation). This pool is refreshed every tick/bar.

### current_best (Dict)
```python
{
    'CE': {
        'symbol': 'NIFTY06JAN2626200CE',
        'swing_low': 130.50,
        'highest_high': 142.30,
        'sl_price': 143.30,  # highest_high + 1 Rs buffer
        'sl_points': 12.80,  # sl_price - swing_low
        'sl_percent': 0.098,  # sl_points / swing_low
        'vwap_premium_pct': 4.4,
        # ... tie-breaker scores
    },
    'PE': None  # No qualified PE this cycle
}
```

**Purpose:** The single best strike per option type (CE/PE) selected from qualified_candidates using tie-breaker rules. This is the strike eligible for order placement in the current evaluation cycle.

## Filter Flow Example

### Scenario: New Swing Detected

```
Time: 10:15 AM
Swing: NIFTY06JAN2626250CE
Price: 125.00 (swing low)
VWAP: 118.00
```

**Step 1: Static Filter**
```
Check: 100 ≤ 125 ≤ 300 ✓ PASS
Action: Add to swing_candidates
```

**Step 2A: VWAP Filter**
```
Premium: (125 - 118) / 118 = 5.93% ✓ PASS (≥4%)
Action: Add to vwap_qualified_swings['CE']
```

**Step 2B: SL% Filter (First Evaluation at 10:15)**
```
Highest High: 130.00 (just after swing)
SL Price: 130 + 1 = 131 (includes +1 Rs buffer)
SL Points: 131 - 125 = 6
SL%: 6 / 125 = 4.8% ✓ PASS (2-10%)
Action: Add to qualified_candidates for tie-breaker
```

**Step 3: Tie-Breaker**
```
Only one CE qualified → Automatically best
Best CE: 26250CE
Action: Mark as best candidate for execution decision
```

### Evolution Over Time

**10:20 AM (5 minutes later):**
```
Highest High: 135.00 (price rallied)
SL Price: 135 + 1 = 136
SL Points: 136 - 125 = 11
SL%: 11 / 125 = 8.8% ✓ STILL PASS
Status: Still qualified, remains in pool
```

**10:30 AM (15 minutes later):**
```
Highest High: 139.00 (continued rally)
SL Price: 139 + 1 = 140
SL Points: 140 - 125 = 15
SL%: 15 / 125 = 12.0% ❌ FAIL (>10%)
Status: DISQUALIFIED
Action: Remove from pool
```

## Swing Replacement Rule

When a new swing for the SAME symbol is detected:

### If New Swing Passes VWAP:
```
Old swing: 26250PE @ 120 (VWAP premium 5%)
New swing: 26250PE @ 115 (VWAP premium 6%)

Action:
1. Remove old swing (120) from pool
2. Add new swing (115) to pool
```

### If New Swing Fails VWAP:
```
Old swing: 26250PE @ 120 (VWAP premium 5%, in pool)
New swing: 26250PE @ 118 (VWAP premium 2% ❌)

Action:
1. New swing rejected (fails VWAP)
2. Old swing STAYS in pool (unaffected)
```

**Why this matters:**

Prevents losing good swings when new weaker swings form for same symbol.

## Swing Break Behavior

**CRITICAL: Swing breaking is the ENTRY TRIGGER, not a cancellation event!**

### Scenario 1: Swing Break WITH Order (Qualified Strike)
```
Swing: 26200CE @ 130 (was best qualified → SL order placed)
Price drops below 130 → SL order TRIGGERS and FILLS → Position opened!

Action:
1. Order fills - swing served its purpose
2. Remove from swing_candidates (entry complete)
3. Place exit SL order immediately
4. Log entry event
```

### Scenario 2: Swing Break WITHOUT Order (Not Qualified)
```
Swing: 26200CE @ 130 (in pool but NOT the best qualified → no order placed)
Price drops below 130 → Entry opportunity passed

Action:
1. Mark swing as "broken" - no longer considered for qualification
2. Remove from swing_candidates
3. Log: "[SWING] 26200CE swing broken without order - opportunity passed"
```

### Scenario 3: Swing Break While Disqualified
```
Swing: 26200CE @ 130 (was qualified, but SL% exceeded MAX_SL_PERCENT → order cancelled)
Price drops below 130 → Entry opportunity passed (correctly avoided high-risk entry)

Action:
1. Swing already removed from qualified pool (disqualified earlier)
2. Remove from swing_candidates if still there
3. Log swing break event
```

**Key Principle:** If an SL order is pending for a swing, swing break = ORDER FILLS, not cancel!

## Filter Rejection Logging

Track WHY swings get rejected for analysis:

### Rejection Reasons

1. **price_low**: Entry < MIN_ENTRY_PRICE (static)
2. **price_high**: Entry > MAX_ENTRY_PRICE (static)
3. **vwap_premium_low**: Premium < MIN_VWAP_PREMIUM (static)
4. **sl_percent_low**: SL% < MIN_SL_PERCENT (dynamic, mutable)
5. **sl_percent_high**: SL% > MAX_SL_PERCENT (dynamic, mutable)
6. **no_data**: Missing OHLC/VWAP data

### Logging Example
```
{
    'timestamp': '2026-01-01T10:15:00',
    'symbol': 'NIFTY06JAN2626300CE',
    'swing_low': 145.00,
    'vwap_premium_pct': 2.1,
    'rejection_reason': 'vwap_premium_low',
    'detail': 'VWAP premium 2.1% < 4.0% threshold'
}
```

Saved to `filter_rejections` table for dashboard visibility.

## Dashboard Integration


### Dashboard Panels

**Stage-1 (Static Filters) Candidates:**
- All strikes that have passed the static filters (price range and VWAP premium) and are currently tracked in the `swing_candidates` pool.
- These swings have not yet broken and are eligible for dynamic SL% evaluation.

**Stage-2 (Dynamic Filters) Candidates:**
- All strikes from `swing_candidates` that currently pass the dynamic SL% filter (real-time tick evaluation).
- This is the current set of candidates that meet all risk criteria at this moment.

**Stage-3 (Final Qualifiers):**
- The single best CE and best PE selected from Stage-2 candidates using the tie-breaker logic (SL points closest to target, then highest entry price).
- These are the final qualifiers for execution decision.

**Recent Rejections:**
- Shows swings that were detected but failed one or more filters (static or dynamic) at the time of evaluation.
- Includes:
    - Swings rejected by static filters (price < MIN_ENTRY_PRICE, price > MAX_ENTRY_PRICE, VWAP premium < MIN_VWAP_PREMIUM) at swing formation.
    - Swings removed from pools due to failing dynamic SL% filter (SL% < MIN_SL_PERCENT or SL% > MAX_SL_PERCENT) during real-time evaluation.
    - Swings rejected due to missing data (OHLC/VWAP).
- Each entry lists symbol, swing low, rejection reason (with config variable), and details about which filter was not met.

#### Example Panel Layout

Stage-1 (Static Filters) Candidates:
```
Symbol         | Swing Low | VWAP   | VWAP% | Time
-------------- | --------- | ------ | ----- | -----
NIFTY...6200CE | 130.5     | 125.0  | 4.4%  | 10:15
...            | ...       | ...    | ...   | ...
```

Stage-2 (Dynamic Filters) Candidates:
```
Symbol         | Swing Low | High   | SL Pts | VWAP% | SL%  | Status
-------------- | --------- | ------ | ------ | ----- | ---- | ---------
NIFTY...6200CE | 130.5     | 142.3  | 11.8   | 4.4%  | 9.0% | Qualified
...            | ...       | ...    | ...    | ...   | ...  | ...
```

Stage-3 (Final Qualifiers):
```
Option | Symbol         | Swing Low | High   | SL Pts | VWAP% | SL%  | Status
CE     | NIFTY...6200CE| 130.5     | 142.3  | 11.8   | 4.4%  | 9.0% | Final
PE     | None          | -         | -      | -      | -     | -    | No Candidate
```

Recent Rejections:
```
Symbol         | Swing Low | VWAP   | VWAP% | Rejection Reason      | Detail
-------------- | --------- | ------ | ----- | ---------------------|-----------------------------
NIFTY...6250CE | 120.0     | 118.0  | 1.7%  | vwap_premium_low      | VWAP premium 1.7% < MIN_VWAP_PREMIUM
NIFTY...6300CE | 115.0     | 125.0  | -8.0% | price_low             | Entry < MIN_ENTRY_PRICE
NIFTY...6200CE | 130.5     | 125.0  | 4.4%  | sl_percent_high       | SL% 11.2% > MAX_SL_PERCENT
...            | ...       | ...    | ...   | ...                   | ...
```

### Filter Summary

Tracks filter effectiveness:
```
Total Swings Detected: 45
Static Filter Pass: 38 (84%)
VWAP Filter Pass: 22 (58% of static pass)
SL% Filter Pass: 15 (68% of VWAP pass)
Best Strike Selected: 2 (1 CE, 1 PE)
```

## Summary

The filtration system ensures:

1. **Quality Swings**: Only trade setups with good fundamentals
2. **Risk Control**: SL% range keeps risk consistent (configurable MIN/MAX_SL_PERCENT)
3. **Momentum Confirmation**: VWAP filter validates strength (static, immutable)
4. **Optimal Selection**: Tie-breaker finds best strike (SL closest to 10 → Multiple of 100 → Highest premium)
5. **Dynamic Adaptation**: SL% updates every tick as market moves
6. **Static Context**: VWAP frozen at formation time
7. **Entry Trigger**: Swing break = order fills (not cancellation!)

**Two-Stage Philosophy:**

- **Static filter**: Eliminates unusable strikes immediately (run once at swing formation)
- **Dynamic filter**: Validates ongoing viability (run every tick for real-time SL% accuracy)

**Multi-Swing Tracking:**

- Keep multiple qualified swings in pool
- Apply tie-breaker only at order placement
- Allows swings to "mature" into qualification over time
