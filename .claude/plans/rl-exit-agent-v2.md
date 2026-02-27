# RL Exit & Session Management Agent — V2 Design Document

## Vision

Replace V1's hardcoded filters and passive exit strategy with an RL agent that learns to play the trading session as a game. The agent decides WHEN to enter, WHEN to exit each trade for profit, and manages multiple positions to reach the daily target efficiently.

**Core insight:** V1's filters (VWAP>4%, price 100-300, SL 2-10%) are too rigid and prevent pyramiding. V1 has no individual trade profit-booking — trades only exit on SL hit or session-level +5R/-5R. The RL agent fills both gaps.

---

## V1 vs RL Agent — What Changes

```
                          V1 (current)              RL Agent (new)
                          ────────────              ──────────────
Swing detection:          SwingDetector              SwingDetector (SAME)
Strike selection:         SL closest to Rs.10        SL closest to Rs.10 (SAME)
Entry filters:            VWAP>4%, SL 2-10%,         REMOVED — agent decides
                          price 100-300
Enter or skip:            Filters decide              Agent decides
SL placement:             highest_high + 1           highest_high + 1 (SAME)
Position sizing:          R-based formula (1R)        R-based formula 1R (SAME)
Exit individual trade:    Only on SL hit              Agent decides (HOLD/EXIT)
Exit session:             +5R / -5R / 3:15 PM        Configurable target (SAME structure)
Max positions:            5 (3 CE, 3 PE)             5 (SAME)
```

---

## Why This Approach (User's Trading Rationale)

### Why remove VWAP filter?
User (6-7 yrs derivative trading): "Price above VWAP has room for reward when shorting, but that doesn't mean below-VWAP trades don't give reward — they might give smaller reward. Agent can decide to enter these and book at lower R like +0.5R or +1R."

### Why remove SL% and price filters?
"I gave these by intuition. When price has fallen a lot and is significantly below VWAP, reversal risk is higher. But instead of blanket filtering, the agent can learn when these are actually dangerous vs when they're fine for a quick profit."

### The pyramiding problem (critical)
"V1's strict filters prevent cascading. After first entry, price moves in our favor but subsequent swing breaks fail the filter (VWAP dropped, price out of range). Left with 1 lonely position trying to reach +5R. Agent should pyramid 2-3 positions and collectively reach target faster."

### Why fixed position sizing?
"SL is always ~10 pts. Same lots every time = same 1R risk per trade. Good normalization, one less thing for agent to learn. Every trade is exactly 1R bet — like fixed chip size in poker."

---

## Feature Set (16 features) — FINALIZED

### Market Context (per swing low — what the chart shows):
```
 1. vwap_premium_pct              — price vs VWAP (no threshold, agent sees raw value)
 2. sl_pct                        — stop loss as % of price
 3. pct_from_day_high             — how far price has fallen from today's high
 4. pct_from_day_low              — how close to today's low (exhaustion signal)
 5. pct_diff_swing_low_vs_prev_high — magnitude of the recent swing move
 6. bars_since_prev_swing_high    — move speed (3 bars = impulsive, 20 bars = grinding)
 7. avg_bar_range_pct_5           — recent volatility (big vs small candles)
 8. swing_low_count_today         — 1st swing vs 5th swing (early opportunity vs noise)
 9. is_lower_low                  — 1 = trending (lower low), 0 = potential reversal (higher low)
10. day_range_pct                 — today's range as % of price (narrow = early, wide = exhausted)
11. minutes_since_open            — time of day context
```

### Session State (the game state):
```
12. cumulative_daily_pnl_R        — where am I in the game
13. open_position_count           — how many active bets
14. trades_taken_today            — session activity level
```

### Game Parameters (configurable, no retraining needed):
```
15. distance_to_target_R          — how far to win (target_R - cumulative_pnl)
16. distance_to_stop_R            — how far to loss limit (cumulative_pnl - stop_R)
```

**Design principle:** All features are percentage-based or R-based. No absolute prices (vary across strikes). Agent sees VWAP as a feature but has no hardcoded threshold — learns its own relationship.

**What features tell the agent (trader's perspective):**
- Features 1-5: WHERE is price in today's story
- Features 6-7: HOW FAST did price get here (impulsive vs grinding)
- Features 8-10: Is this move BEGINNING, MIDWAY, or EXHAUSTED
- Feature 11: WHERE in the trading session are we
- Features 12-14: The GAME STATE (stack, bets placed)
- Features 15-16: DISTANCE to win/lose (goal-conditioned)

---

## Daily Limits — Goal-Conditioned (FINALIZED)

### Problem with hardcoded ±5R:
If trained with fixed +5R/-5R, the agent has never seen states beyond those boundaries. Changing to +7R/-7R later requires retraining.

### Solution: Goal-conditioned RL
Pass target_R and stop_R as input features. During training, randomize:
```
Each training episode:
  target_R  = random from [3.0, 4.0, 5.0, 6.0, 7.0]
  stop_R    = random from [-3.0, -4.0, -5.0, -6.0, -7.0]
```

Agent learns generalized policy: "how to play toward ANY target."
Key insight: `distance_to_target` matters, not the absolute number.

### At deployment (no retraining):
```python
config.TARGET_R = 5.0   # Conservative
config.STOP_R = -5.0

config.TARGET_R = 7.0   # Aggressive
config.STOP_R = -3.0    # Tighter stop
```

---

## Agent's Three Skills

### 1. Entry Intelligence — "Should I take this trade?"
- Not based on hardcoded thresholds
- Context-dependent: cumulative P&L, positions held, VWAP, time of day
- Learns: low-VWAP trades fine for quick +0.5R bookings
- Learns: below-VWAP risky but sometimes worth it for recovery
- Learns: close to target → be selective; behind on day → be aggressive

### 2. Exit Intelligence — "Should I book this trade now?"
- High VWAP premium trade running well → hold for bigger R
- Low VWAP premium trade at +0.8R → book it, this is its ceiling
- At +4R cumulative with +0.6R unrealized → book it, almost home
- At -2R with +1.5R unrealized → let it run, need bigger win

### 3. Pyramiding — "Should I stack positions?"
- After first entry, subsequent swing breaks aren't filtered out
- Agent learns: 2-3 positions cascading reach target faster
- Agent learns: adding position when already near target = unnecessary risk

---

## Training Approach — Curriculum Learning (FINALIZED)

### Phase 0: Behavior cloning from V1 (supervised pre-training)
```
Rules:
  - Replay historical bars through V1's SwingDetector + filters
  - Generate labels: ENTER/SKIP at swing breaks, HOLD/EXIT per bar for open positions
  - Train agent policy via supervised learning (cross-entropy loss)
  - Agent learns "what V1 would do" as starting point
  - Much more data-efficient than RL (supervised, not trial-and-error)
  - Solves lazy agent problem: agent starts from a trading policy, not blank slate

  Analogous to AlphaGo: imitation learning from human games → RL self-play to surpass.
```

### Phase 1: Fixed game (RL fine-tuning from Phase 0 policy)
```
Rules:
  - Session: 9:16 AM to 3:15 PM
  - Each trade risks exactly 1R
  - Episode ends: reach target_R, hit stop_R, or 3:15 PM
  - Actions: ENTER/SKIP + HOLD/EXIT per position
  - target_R and stop_R randomized per episode

  Clear win/lose conditions. Bounded game. Fast convergence.
```

### Phase 2: Flexible ceiling (only after Phase 1 converges)
```
Changes:
  - Keep stop_R as hard floor (safety)
  - Add STOP_TRADING action — agent can stop early
  - Remove target_R ceiling — agent decides when enough is enough
  - Reward: maximize total R across MONTH of sessions
```

### Phase 3: Maybe never needed
If Phase 1 agent is profitable, Phase 2 may be unnecessary.

### Research backing:
- AlphaZero, Pluribus, Atari DQN all use externally imposed termination rules
- Fixed-horizon episodes converge 1-2 orders of magnitude faster than variable
- JMLR Curriculum Learning survey: "starting simple leads to faster convergence AND better final performance"
- QF-TraderNet: learned exits outperform fixed TP/SL for individual trades (supports our per-trade exit learning)

---

## OPEN DECISIONS (still to finalize)

### 1. Reward Function — FINALIZED

```
Per-trade close:    reward = realized_R_multiple   (e.g., +1.5, -1.0, +0.3)
Per-bar hold:       reward = 0                     (no holding penalty)
Skip swing break:   reward = 0                     (free to skip)
Session-end:        reward = -0.5 if trades_taken == 0 else 0  (lazy agent safety net)
```

**Decisions made:**
- **Hybrid approach** — per-trade is primary signal, session-end only penalizes zero-trade days
- **No session-end bonus** for hitting target — per-trade rewards already sum to daily R (avoids double-counting)
- **No holding penalty** — 3:15 PM force-exit + SL already bound downside; adding time cost risks premature exits
- **No skip penalty** — skipping must be genuinely free; penalizing skips would push agent into marginal trades
- **No extra SL penalty** — SL hit = -1R by design (fixed sizing), no need for additional penalty
- **Brokerage/slippage** — not modeled in reward; handled separately in execution simulation

**Lazy agent problem & fix:**
Risk: agent learns skip-everything policy (0R > losing). Especially during early training when random actions cause losses.
Solution: **Behavior cloning warmstart (Phase 0)**
- Pre-train policy by imitating V1 decisions (supervised learning)
- V1 enters → label ENTER, V1 skips → label SKIP, V1 holds → HOLD
- Agent starts from "trade like V1" and RL fine-tunes to be selectively better
- Never discovers "never trade" because starting point already trades
- Session-end -0.5R penalty is just a safety net, not the primary fix

### 2. Decision Timing
- Entry: when swing breaks (obvious)
- Exit: every 1-min bar close? Less frequent?
- How often does agent evaluate open positions?

### 3. Multi-Position Management
- 3 positions open + new swing breaks: one decision or separate?
- How does agent express "exit position 2 but hold position 1"?

### 4. Training Data Strategy — FINALIZED

**Current inventory:**
```
Source                  Period                  Trading Days   Status
──────────────────────  ──────────────────────  ────────────   ──────
Dinesh data             Jan 2022 - Mar 2023     ~295 days      Have
rl_dataset_v2           Feb 2023 - Nov 2023     ~190 days      Have
  (overlap Feb-Mar 23)                          ~42 days
Unique total            Jan 2022 - Nov 2023     ~443 days      Have
GAP                     Nov 2023 - Feb 2026     ~567 days      MISSING
Historify (ongoing)     Feb 2026 →              ~1 day/day     Collecting
```

**Decision points per episode multiply effective data:**
- ~5-15 swing breaks/day (entry decisions) + ~360 bar closes × open positions (exit decisions)
- ~50-200 decision points per episode
- 443 days → ~50K-90K decision points (marginal for RL, OK for Phase 0 behavior cloning)

**Data acquisition priority:**
```
Priority 1 (MUST): Fill gap Nov 2023 - Feb 2026 (27 months, ~567 days)
  → Doubles training data to ~1,010 days (~100K-200K decision points)
  → Covers recent regimes: 2024 election volatility, NIFTY 25000+ breakout, 2025 correction
  → Source: True Data or similar (Rs.5K-15K)

Priority 2 (NICE TO HAVE): 2020-2021 data (~500 days)
  → COVID crash + recovery = extreme volatility regime (tail events)
  → Total: ~1,500 days (~200K-300K decision points) — comfortable for RL
  → Source: same vendor, historical add-on

Priority 3 (NOT NEEDED): Pre-2020
  → Different lot sizes, lower liquidity, diminishing returns
```

**Data format required per symbol per minute:**
- timestamp, open, high, low, close, volume
- Symbols: NIFTY spot + full weekly option chain (all CE + PE strikes, typically ATM ± 1000 pts)
- Nice to have: Open Interest (OI) — potential future feature

**Why 1,010 days is enough:**
- Phase 0 (behavior cloning) is supervised learning — very data-efficient, 443 days workable
- Phase 1 (RL) starts from pre-trained policy — needs less data than from-scratch RL
- State space is small (16 features) and action space is small (~4 discrete actions)
- 1,010 episodes with ~150K decision points is solid for this problem size

### 5. Algorithm Choice
- DQN vs PPO vs A2C
- Discrete action space

### 6. Evaluation Metrics
- What proves agent > V1?

---

## Existing Data Available

### Current inventory:
- **Source 1:** `data/rl_dataset_v2_with_spot.csv` — Feb-Nov 2023 (9 months, ~6.3M bars)
- **Source 2:** Dinesh data — Jan 2022-Mar 2023 (14 months, 29K CSVs)
- **Combined (unique):** ~21 months / ~443 trading days
- **GAP:** Nov 2023 to Feb 2026 (27 months, ~567 days — **MUST acquire**)
- **Ongoing:** Historify collecting daily from Feb 2026

### Data acquisition plan:
- **Buy gap data (Priority 1):** Nov 2023 - Feb 2026 from True Data or similar vendor (Rs.5K-15K)
- **Buy 2020-2021 (Priority 2):** COVID-era data for extreme regime coverage
- **Format:** 1-min OHLCV for NIFTY spot + full weekly option chain (all strikes CE+PE)
- See "Training Data Strategy" in Open Decisions above for full analysis

### For backtesting V1 (prerequisite):
Build backtest engine using actual SwingDetector + filter code to validate V1's claimed ~1.12R/day before investing in RL.

---

## Implementation Sequence

```
1. Backtest Engine (validate V1 — MUST DO FIRST)
   → Answers: "Does V1 actually work?"
   → Uses same SwingDetector, same filters
   → Realistic slippage + brokerage

2. RL Environment (Gym interface)
   → Replay historical bars through SwingDetector
   → Agent receives features at each decision point
   → Simulated execution with slippage

3. Phase 0: Behavior Cloning (supervised pre-training)
   → Replay data through V1 logic, generate ENTER/SKIP/HOLD/EXIT labels
   → Train agent to imitate V1 via supervised learning
   → Solves lazy agent problem — agent starts from a trading policy

4. Phase 1: RL Fine-Tuning (fixed game)
   → Start from Phase 0 pre-trained policy
   → Goal-conditioned with randomized ±target/stop
   → Curriculum: start tight, widen over training

5. Shadow Mode (paper trade alongside V1)
   → V1 makes real decisions
   → Agent logs what it would do
   → Compare: daily R, win rate, drawdown

6. Live Deployment (after shadow validation)
   → Replace V1 filters + exit logic
   → Keep swing detection + SL placement unchanged
```

---

## File Location
This document: `.claude/plans/rl-exit-agent-v2.md`
Previous ML roadmap: `C:\Users\rajat\.claude\plans\squishy-sparking-bubble.md`
Option chain collector plan: `C:\Users\rajat\.claude\plans\functional-yawning-ripple.md`
