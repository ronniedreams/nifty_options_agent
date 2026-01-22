# Swing Detection Theory - Watch-Based System

## What is a Swing?

A **swing** is a significant turning point in price action where the trend changes direction:

- **Swing Low**: A local minimum - price makes a low, then moves higher
- **Swing High**: A local maximum - price makes a high, then moves lower

Think of it like ocean waves:
- The **trough** (lowest point) = Swing Low
- The **crest** (highest point) = Swing High

## The Alternating Pattern

Valid swings must alternate between highs and lows:

```
High → Low → High → Low → High → Low
```

**Core Rule**: After a swing low, the next swing must be a swing high. After a swing high, the next swing must be a swing low.

### Exception: Swing Updates (Same Direction)

However, if a **new extreme** is found BEFORE the next alternating swing forms, we UPDATE the existing swing:

**For Swing Lows:**
- Swing LOW @ 80 detected
- Before a swing HIGH forms, price drops to new lower low @ 75
- **Action**: UPDATE the swing low from 80 → 75
- **Reason**: 75 is the true low point, not 80

**For Swing Highs:**
- Swing HIGH @ 100 detected  
- Before a swing LOW forms, price rallies to new higher high @ 105
- **Action**: UPDATE the swing high from 100 → 105
- **Reason**: 105 is the true high point, not 100

### Why Allow Updates?

In real trading, we care about the TRUE extremes:
- **For entries**: We want to enter at the actual swing low break, not a premature level
- **For stops**: We want stop-loss above the actual highest high, not an intermediate level
- **For accuracy**: Premature swings lock in wrong levels and cause bad trades

**Example of the Problem:**
```
Swing LOW @ 80 detected
Price drops to 75 (new lower low)
System rejects 75 (strict alternating rule)
Entry triggers at 80 break
But true low was 75!
Result: Entered 5 points too early, wrong risk calculation
```

By allowing updates, we track the TRUE turning point even if it takes multiple bars to fully form.

## The Watch-Based Detection Method

### Core Concept: "Watching" Bars

Instead of looking for completed patterns, we **watch** individual bars to see if price behavior confirms they were turning points.

Each bar in history gets assigned **watch counters**:
- **Low Watch Counter**: Counts how many times subsequent bars confirm this might be a swing low
- **High Watch Counter**: Counts how many times subsequent bars confirm this might be a swing high

### How Watch Counters Work

#### For Swing LOW Detection:

A bar gets its **low watch counter incremented** when a future bar shows:
1. **Higher High** than that bar, AND
2. **Higher Close** than that bar

**Logic**: If price is making higher highs and higher closes compared to a past bar, that past bar might have been a low point.

**Example**:
```
Bar 5: High=100, Low=95, Close=97
Bar 10: High=105, Low=99, Close=103

Bar 10 has HIGHER high (105 > 100) AND HIGHER close (103 > 97)
→ Increment Bar 5's low_watch_counter
```

#### For Swing HIGH Detection:

A bar gets its **high watch counter incremented** when a future bar shows:
1. **Lower Low** than that bar, AND
2. **Lower Close** than that bar

**Logic**: If price is making lower lows and lower closes compared to a past bar, that past bar might have been a high point.

**Example**:
```
Bar 8: High=110, Low=105, Close=108
Bar 12: High=102, Low=98, Close=100

Bar 12 has LOWER low (98 < 105) AND LOWER close (100 < 108)
→ Increment Bar 8's high_watch_counter
```

### The Trigger: Counter Reaches 2

When any bar's watch counter reaches **2**, it triggers swing detection:

**For low_watch = 2:**
- We know price confirmed TWICE that this area was a low point
- Find the **lowest LOW** in the window from that area to now
- Mark it as a **SWING LOW**

**For high_watch = 2:**
- We know price confirmed TWICE that this area was a high point
- Find the **highest HIGH** in the window from that area to now
- Mark it as a **SWING HIGH**

### Why "2" Confirmations?

One confirmation could be noise. Two confirmations validate that price truly reversed at that point. It's a balance between:
- **Too few (1)**: Too many false swings from noise
- **Too many (3+)**: Too much lag, miss real turning points

### Step 5: Enforce Alternating Pattern (with Updates)

Before finalizing the swing:
- Check if it matches the required pattern (high after low, low after high)
- **If it's the SAME type as the last swing:**
  - Check if it's a NEW extreme (lower low or higher high)
  - If YES → **UPDATE the existing swing** to the new extreme
  - If NO → Reject the swing
- If it's the opposite type → Accept as new alternating swing
### Step 1: New Bar Arrives

When a new 1-minute bar completes, the detector activates.

### Step 2: Compare Against All Previous Bars

The new bar is compared against **every previous bar** (or all bars after the last swing):

- Check if new bar has HH+HC (higher high, higher close) → increment low_watch for those bars
- Check if new bar has LL+LC (lower low, lower close) → increment high_watch for those bars

**Which Bars' Counters Do We Check?**

This is critical for correctness:

**Current bar (index i) does:**
1. Compares itself against previous bars (j = last_swing_idx + 1 to i - 1)
2. Increments their counters (low_watch[j] or high_watch[j])

**We check counters of:**
- Bars in range: [last_swing_idx + 1, i - 1]
- NOT the current bar itself (i)
- NOT bars before last swing (≤ last_swing_idx)

**Example:**
```
Last swing: Bar 5
Current bar: Bar 10

Check counters for: Bar 6, 7, 8, 9 only
Don't check: Bar 0-5 (before/at last swing), Bar 10 (current bar)

When Bar 7's counter reaches 2:
→ Triggers swing detection
→ Find extreme in window [Bar 6 to Bar 10]
```

**Why not check current bar's counter?**
- Current bar just arrived - it can't have a counter yet
- Counters belong to PREVIOUS bars being "watched"
- Current bar is the one DOING the watching (incrementing others' counters)

**Why not check bars before last swing?**
- They're in the previous wave (already confirmed swing there)
- Only care about price action AFTER the last turning point
- Keeps window focused on current wave formation

### Step 3: Check for Trigger

After updating counters, check if ANY bar's counter reached 2:
- If yes → Find the swing point in that window
- If no → Wait for next bar

**Dual Trigger Resolution (Critical for Large Range Bars):**

When a single bar triggers BOTH low_watch=2 AND high_watch=2:

1. **Check last swing type** to determine what's required next
2. **Choose the swing that alternates**, ignore the other trigger
3. **Create only ONE swing per bar**

**Example - Both Triggers Fire:**
```
Last swing: LOW @ 120
Bar arrives: High=141, Low=129
Both low_watch and high_watch reach 2!

Decision:
- Last swing = LOW
- Required next = HIGH
- Action: Create SWING HIGH @ 141
- Ignore: The low_watch trigger
```

**Why This Matters:**

Large range bars (e.g., 12-point range from Low=129 to High=141) can:
- Create HH+HC vs many previous bars (triggers low_watch)
- Create LL+LC vs many previous bars (triggers high_watch)
- Both counters hit 2 simultaneously

Without dual trigger resolution, the system would attempt to create BOTH swings from the same bar - violating the alternating pattern and creating invalid swing sequences.

**Resolution Rule:**
- If `last_swing_type == 'low'` → Only process high_watch triggers (create HIGH)
- If `last_swing_type == 'high'` → Only process low_watch triggers (create LOW)
- If no last swing (first swing) → First trigger to fire wins

### Step 4: Create the Swing

Once triggered:
1. Determine the window (from last swing to current bar, or from start if no swing yet)
2. Find the extreme point (lowest low for swing low, highest high for swing high)
3. Mark that bar as the swing
4. **Reset all watch counters** (start fresh for next swing)

### Step 5: Enforce Alternating Pattern

Before finalizing the swing:
- Check if it matches the required pattern (high after low, low after high)
- If it violates the pattern → **reject the swing**
- If it's valid → accept and update last swing type

## Window Behavior

### For the First Swing (No Previous Swing):

**Window**: From the very first bar to the current bar

The first swing can be either a LOW or a HIGH - whichever condition triggers first wins.

### For Subsequent Swings (After First Swing):

**Window**: From the bar AFTER the last swing to the current bar

This ensures we're only looking at price action that happened AFTER the previous turning point.

**Example**:
```
Bar 0-4: Initial price action
Bar 5: First swing LOW detected
Bar 6-10: Price movement after swing low
Bar 11: Next swing (must be HIGH) detected in window [Bar 6 to Bar 11]
```

## Real-World Example

Let's trace through a sequence:

```
Bar 0: O=100, H=102, L=98, C=101
Bar 1: O=101, H=103, L=99, C=100
Bar 2: O=100, H=101, L=97, C=98   ← Potential low
Bar 3: O=98, H=100, L=96, C=99
Bar 4: O=99, H=104, L=98, C=103   ← HH+HC vs Bar 2
```

**At Bar 4**:
- Compare vs Bar 0: H=104>102 ✓, C=103>101 ✓ → Bar 0 low_watch = 1
- Compare vs Bar 1: H=104>103 ✓, C=103>100 ✓ → Bar 1 low_watch = 1
- Compare vs Bar 2: H=104>101 ✓, C=103>98 ✓ → Bar 2 low_watch = 1
- Compare vs Bar 3: H=104>100 ✓, C=103>99 ✓ → Bar 3 low_watch = 1

No trigger yet (all counters = 1).

```
Bar 5: O=103, H=106, L=102, C=105  ← HH+HC again
```

**At Bar 5**:
- Compare vs Bar 2: H=106>101 ✓, C=105>98 ✓ → Bar 2 low_watch = **2** ← TRIGGER!

**Action**:
- Window: Bar 0 to Bar 5
- Find lowest low in window: Bar 2 (Low=97) or Bar 3 (Low=96)?
- Bar 3 has lowest low (96)
- **Create SWING LOW at Bar 3, price 96**

Now looking for SWING HIGH (must alternate).

```
Bar 6: O=105, H=107, L=103, C=104
Bar 7: O=104, H=105, L=100, C=101  ← LL+LC vs Bar 6
```

**At Bar 7**:
- Compare vs Bar 6: L=100<103 ✓, C=101<104 ✓ → Bar 6 high_watch = 1

```
Bar 8: O=101, H=102, L=98, C=99    ← LL+LC vs Bar 6 again
```

**At Bar 8**:
- Compare vs Bar 6: L=98<103 ✓, C=99<104 ✓ → Bar 6 high_watch = **2** ← TRIGGER!

**Action**:
- Window: Bar 4 to Bar 8 (after last swing at Bar 3)
- Find highest high in window: Bar 5 (H=106) or Bar 6 (H=107)?
- Bar 6 has highest high (107)
- **Create SWING HIGH at Bar 6, price 107**
# Swing Update Example:

Let's see what happens if a lower low forms:

```
Bar 9: O=99, H=100, L=94, C=95    ← New LOWER low (94 < 96)
Bar 10: O=95, H=98, L=93, C=97    ← Even lower (93)
```

**At Bar 9**:
- Last swing: LOW @ Bar 3 (price 96)
- New swing detected: LOW @ 94
- Same type (LOW after LOW) → Check if new extreme
- 94 < 96 ✓ → **UPDATE swing low** from Bar 3 to Bar 9 (96 → 94)

**At Bar 10**:
- Last swing: LOW @ Bar 9 (price 94)
- New swing detected: LOW @ 93
- Same type (LOW after LOW) → Check if new extreme
- 93 < 94 ✓ → **UPDATE swing low** from Bar 9 to Bar 10 (94 → 93)

Now we have the TRUE low at 93, not the premature 96.

```
Bar 11: O=97, H=108, L=96, C=107  ← Rally
Bar 12: O=107, H=110, L=105, C=109 ← LL+LC triggers high_watch
```

**At Bar 12**:
- Last swing: LOW @ Bar 10 (price 93)
- New swing: HIGH (different type) → Valid alternating swing ✓
- **Create SWING HIGH** at highest point in window

**Mitigation**: With swing updates enabled, if a lower low forms later and DOES get confirmed, it will update to capture the true extreme.

Final pattern: **LOW(93) → HIGH(110)** - captures true extremes!

## Why This Method Works

The watch-based system with adaptive updates:

1. Every bar is a potential swing point
2. Future bars "vote" whether past bars were turning points
3. When 2 votes are cast (counter = 2), the swing is confirmed
4. The actual swing point is the extreme in that confirmed area
5. Swings must alternate to maintain the wave pattern
6. **NEW**: If a new extreme forms before alternation, UPDATE the existing swing
7. This ensures we track TRUE extremes, not premature turning points

It's like looking backwards and saying: "Now that I see what happened AFTER that bar, I can confirm it was a turning point - or wait, an even better extreme just formed, let me update!"

### The Update Rule in Practice

**Swing Low Update**:
- Have: Swing LOW @ 80
- Detect: New swing LOW @ 75 (before any HIGH)
- Check: Is 75 < 80? YES
- **Action**: Replace 80 with 75 as the swing low

**Swing High Update**:
- Have: Swing HIGH @ 100
- Detect: New swing HIGH @ 105 (before any LOW)
- Check: Is 105 > 100? YES
- **Action**: Replace 100 with 105 as the swing high

**Invalid Update (Rejected)**:
- Have: Swing LOW @ 80
- Detect: New swing LOW @ 82 (before any HIGH)
- Check: Is 82 < 80? NO
- **Action**: Reject (not a new extreme, just noise)

This ensures every swing represents the ACTUAL extreme in that wave, making trading decisions more accurate.

## Limitations

1. **Lag**: Swing is confirmed AFTER price has already moved away
2. **Complexity**: Multiple counters to track, can be hard to debug
3. **Parameter sensitivity**: The "2 confirmations" rule is hardcoded
4. **Can miss swings**: If price doesn't create the specific HH+HC or LL+LC pattern

## Common Issues

### Issue 1: Multiple Swings from One Bar

If the watch counter logic triggers multiple times in one bar processing cycle, it could create multiple swings from a single bar arrival.

**Fix**: 
1. Check last swing type to determine what's required (high or low)
2. Only process the counter that creates the alternating swing
3. Ensure the function returns after creating ONE swing per bar

**Example**: Bar with High=141, Low=129 after a swing low:
- CORRECT: Create only swing HIGH @ 141
- WRONG: Create both swing HIGH @ 141 AND swing LOW @ 129

### Issue 2: Missing True Extremes

If a true low (like 119.50) doesn't get enough HH+HC confirmations (count doesn't reach 2), it won't be detected as a swing.

**Why**: Subsequent price action might not create the specific pattern required.

### Issue 3: Detecting Minor Pullbacks

Small retracements can trigger watch counters if they meet the HH+HC or LL+LC conditions twice.

**Result**: Too many swings on minor price fluctuations.

## Multi-Symbol Context

Swing detection operates **independently per symbol**. Each option strike (e.g., NIFTY06JAN2624000CE, NIFTY06JAN2624100CE) has its own:

- Swing detector instance
- Watch counters (low_watch, high_watch)
- Swing history (alternating highs and lows)
- Last swing state

This allows the system to track potential entry points across multiple strikes simultaneously. The `MultiSwingDetector` class manages 42+ individual detectors (21 CE + 21 PE strikes around ATM).

## Evaluation Frequency

**Swing detection happens on BAR CLOSE, not every tick.**

| Component | Frequency | Reason |
|-----------|-----------|--------|
| Swing detection (watch counters) | Bar close | Needs complete OHLC bars for comparison |
| Highest high tracking | Every tick | Current bar's high updates with new highs |
| Filter evaluation (SL%) | Every tick | Real-time risk assessment |

**Why bar-level for swings?**
- Watch counters compare OHLC values between bars
- Incomplete bars would give false signals
- Confirmed bars provide stable reference points

## What Happens When Swing Breaks

**CRITICAL: Swing breaking is the ENTRY TRIGGER for qualified strikes!**

When price drops below swing_low:
- If SL order is pending → Order TRIGGERS and FILLS → Position opened
- If no order (not qualified) → Opportunity passed, swing marked as "broken"
- Swing is removed from candidates pool (entry complete or missed)

See STRIKE_FILTRATION_THEORY.md for details on swing break behavior.

## Summary

The watch-based system treats swing detection as a **confirmation game**:

1. Every bar is a potential swing point
2. Future bars "vote" whether past bars were turning points
3. When 2 votes are cast (counter = 2), the swing is confirmed
4. The actual swing point is the extreme in that confirmed area
5. Swings must alternate to maintain the wave pattern
6. **Swing updates**: If a new extreme forms before alternation, UPDATE the existing swing
7. This ensures we track TRUE extremes, not premature turning points

It's like looking backwards and saying: "Now that I see what happened AFTER that bar, I can confirm it was a turning point - or wait, an even better extreme just formed, let me update!"
