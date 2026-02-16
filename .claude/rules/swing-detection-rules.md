---
paths: swing_detector.py, continuous_filter.py
---

# Swing Detection & Filtration Rules

## Watch-Based Swing Detection

Each bar gets watch counters tracking when future bars confirm it as a turning point:
- **low_watch**: Incremented when future bar has HIGHER HIGH + HIGHER CLOSE
- **high_watch**: Incremented when future bar has LOWER LOW + LOWER CLOSE
- **Trigger**: Counter reaches 2 → swing confirmed

## Alternating Pattern (Non-Negotiable)
Valid sequence: `High → Low → High → Low`
- After swing LOW, next swing must be HIGH
- After swing HIGH, next swing must be LOW
- Reject any swing violating this pattern

## Swing Updates (Same Direction)
Before an alternating swing forms, if a new extreme appears AND gets 2-watch confirmation → UPDATE the existing swing.
- Updates require the same 2-watch confirmation as initial swings (not immediate)
- Example: swing LOW @ 80, price drops to 75 → wait 2 HH+HC confirmations → update to 75
- Reject non-extreme updates (e.g., price at 82 can't update a LOW at 80)

## Multi-Symbol Independence
Swing detection operates **independently per symbol**. Each strike has its own history, watch counters, and candidates.

## Strike Filtration Pipeline

### Only Swing LOWs Are Processed
`continuous_filter.py` only accepts `swing_type == 'Low'`. Swing highs are used only by `swing_detector.py` internally for the alternating pattern.

### Stage 1: Static Filter (Run Once at Swing Formation)
1. **Price range**: `MIN_ENTRY_PRICE (100) ≤ swing_low ≤ MAX_ENTRY_PRICE (300)`
2. **VWAP premium**: `(swing_low − VWAP) / VWAP ≥ MIN_VWAP_PREMIUM (4%)`

- VWAP is **frozen at swing formation** (immutable for the swing's lifetime)
- Pass → added to `stage1_swings_by_type` dict (`{'CE': [...], 'PE': [...]}`)
- If new swing for same symbol fails Stage 1 → old swing removed too (invalidated)

### Stage 2: Dynamic Filter (Every Bar/Tick)
```
SL price = highest_high_since_swing + 1 Rs
SL pts   = sl_price − swing_low
SL%      = sl_pts / swing_low
```
- Pass range: `MIN_SL_PERCENT (2%) ≤ SL% ≤ MAX_SL_PERCENT (10%)`
- `highest_high` includes current bar's real-time high (updates with each tick)
- Qualified candidates → `qualified[option_type]` list, refreshed every bar

### Stage 3: Tie-Breaker (Best Strike Selection)
When multiple strikes qualify for same option type, select ONE using:
1. **SL points closest to TARGET_SL_POINTS (10)** — primary (`score = abs(sl_pts − 10)`)
2. **Round strike preferred** (strike % 100 == 0) — secondary
3. **Highest entry price** — final tiebreaker

```python
# In continuous_filter.py line ~483
best = min(qualified[option_type], key=lambda x: (x['score'], not x['is_round_strike'], -x['entry_price']))
```

Result stored in `self.current_best = {'CE': candidate_dict or None, 'PE': candidate_dict or None}`

## Data Structures

### swing_candidates (ContinuousFilterEngine)
```python
{symbol: {'price': swing_low, 'timestamp': dt, 'vwap': float, 'option_type': 'CE'/'PE', 'index': int}}
```
Passed Stage 1 price filter. One entry per symbol (newer swing replaces older).

### stage1_swings_by_type (ContinuousFilterEngine)
```python
{'CE': [swing_info, ...], 'PE': [swing_info, ...]}
```
Passed both price AND VWAP filters. Multiple symbols per type possible.

### current_best (ContinuousFilterEngine)
```python
{'CE': enriched_candidate_dict_or_None, 'PE': enriched_candidate_dict_or_None}
```
Single best strike per type eligible for order placement. Includes: `swing_low`, `sl_price`, `sl_points`, `sl_percent`, `lots`, `quantity`, `actual_R`, `vwap_premium`, `is_round_strike`.

## Swing Invalidation

| Scenario | Action |
|----------|--------|
| Price breaks swing_low + order filled | Remove from all pools; position entered |
| Price breaks swing_low + no order | Mark `broke_in_history=True`; removed from qualified |
| New swing for same symbol | Old swing removed; new swing evaluated |
| SL% drifts out of range | Stays in stage1 pool; removed from qualified until re-qualifies |

## Evaluation Frequency

| Component | Frequency |
|-----------|-----------|
| Swing detection (watch counters) | Bar close only |
| Highest high tracking | Every tick (real-time) |
| SL% calculation | Every tick |
| Order trigger decision | Every tick |
| Order status polling | Every 5 seconds |
| Position reconciliation | Every 60 seconds |

## Critical Gotchas
- **Stale highest high**: Always include current bar's real-time high in highest_high, not just closed bars
- **VWAP immutability**: VWAP is fixed at swing formation; never recalculate for existing swings
- **Updates need 2-watch**: Swing updates are NOT immediate; require same 2-watch confirmation
- **Only LOWs to filter**: Don't pass swing HIGHs to `ContinuousFilterEngine.add_swing_candidate()`
