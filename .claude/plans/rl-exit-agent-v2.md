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
Strike selection:         SL closest to Rs.10        SL closest to Rs.20 (structural)
Entry filters:            VWAP>4%, SL 2-10%,         REMOVED — agent decides
                          price 100-300              (price 50-500 kept as liquidity gate)
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

### Why remove SL% and price filters as hard gates?
"I gave these by intuition. They might be specific to the 9-month training period and overfit. When price has fallen a lot and is significantly below VWAP, reversal risk is higher. But instead of blanket filtering, the agent can learn when these are actually dangerous vs when they're fine for a quick profit. The raw values become features — agent sees SL%=1.5% and learns from experience whether that's viable."

### The pyramiding problem (critical)
"V1's strict filters prevent cascading. After first entry, price moves in our favor but subsequent swing breaks fail the filter (VWAP dropped, price out of range). Left with 1 lonely position trying to reach +5R. Agent should pyramid 2-3 positions and collectively reach target faster."

### Why fixed position sizing?
"SL is always ~20 pts. Same lots every time = same 1R risk per trade. Good normalization, one less thing for agent to learn. Every trade is exactly 1R bet — like fixed chip size in poker."

### Why SL target changed from 10 to 20 pts?
"With SL=10: 10 lots needed for 1R, margin ~Rs.20L (at ~Rs.2L/lot). With SL=20: 5 lots for same 1R, margin ~Rs.10L. Half the margin, same R-return — ROI doubles. This change will also apply to V1 in future."

```
SL=10:  lots = 6500/(10×65) = 10 lots  → margin ~Rs.20,00,000 → ROI = 0.33% per R
SL=20:  lots = 6500/(20×65) =  5 lots  → margin ~Rs.10,00,000 → ROI = 0.65% per R
```

---

## Feature Set — FINALIZED

### Two-Model Architecture
The agent uses two separate models with different feature sets:
- **Entry Model**: 18 global features → ENTER or SKIP (at swing break events)
- **Exit Model**: 18 global features + 4 per-position features = 22 features → HOLD or EXIT (every bar, per position)

### Global Features (18) — shared across entry and exit decisions:

#### Market Context (per option — what the chart shows):
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

#### Volatility Regime (computed from NIFTY spot, shared across all decisions):
```
12. spot_volatility_ratio          — spot_avg_bar_range_5 / spot_avg_bar_range_50
                                     1.0 = normal day, 2.5+ = high vol (event day), 0.5 = dead day
                                     Computed once per bar on NIFTY spot, not per-strike
```

#### Session State (the game state):
```
13. cumulative_daily_pnl_R        — where am I in the game (realized P&L)
14. open_position_count           — how many active bets (0-5)
15. trades_taken_today            — session activity level
16. total_unrealized_pnl_R        — sum of all open positions' unrealized P&L in R terms
                                     +1.5 = pyramid is working, -0.8 = positions underwater
                                     Critical for pyramiding: agent knows if it's building on strength or chasing losses
```

#### Game Parameters (configurable, no retraining needed):
```
17. distance_to_target_R          — how far to win (target_R - cumulative_pnl)
18. distance_to_stop_R            — how far to loss limit (cumulative_pnl - stop_R)
```

### Per-Position Features (4) — exit decisions only:
```
 A. position_unrealized_pnl_R    — THIS position's P&L in R terms (+2.0R, -0.3R, etc.)
 B. bars_since_entry             — how long this position has been held
 C. pct_from_sl                  — distance of current price to this position's SL
                                    (far = safe, close = at risk)
 D. option_price_roc_5           — 5-bar rate of change of this position's option price
                                    -3% = strong momentum (HOLD), -0.3% = fading (consider EXIT)
                                    +1% = reversing (EXIT NOW)
                                    Embeds recent price trajectory into current observation
                                    (agent has no memory across bars — this compensates)
```

**Design principle:** All features are percentage-based or R-based. No absolute prices (vary across strikes). Agent sees VWAP as a feature but has no hardcoded threshold — learns its own relationship.

**What features tell the agent (trader's perspective):**
- Features 1-5: WHERE is price in today's story
- Features 6-7: HOW FAST did price get here (impulsive vs grinding)
- Features 8-10: Is this move BEGINNING, MIDWAY, or EXHAUSTED
- Feature 11: WHERE in the trading session are we
- Feature 12: Is today NORMAL or ABNORMAL volatility (regime context from NIFTY spot)
- Features 13-16: The GAME STATE (stack, bets placed, pyramid health)
- Features 17-18: DISTANCE to win/lose (goal-conditioned)
- Features A-D: THIS POSITION's state (for exit decisions only)

**How the agent detects "momentum fading" (exit intelligence):**
Standard RL agents see one snapshot per bar, no memory of previous bars.
Per-position feature D (option_price_roc_5) encodes recent trajectory:
```
roc_5 = -3.0%  → price falling fast → strong momentum → HOLD
roc_5 = -0.5%  → price barely falling → momentum fading → consider EXIT
roc_5 = +1.0%  → price rising → reversal underway → EXIT
```
Combined with avg_bar_range_pct_5 (candle sizes shrinking) and position_unrealized_pnl_R
(P&L plateauing), the agent has enough signal to detect fading momentum without memory.

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

## Agent Architecture — Two Models, Two-Layer Exit

### Entry Model (18 global features → target selection)

```
Input:   18 global features
Output:  SKIP or ENTER_0.3R / ENTER_0.5R / ENTER_0.8R / ENTER_1.0R / ENTER_1.5R / ENTER_2.0R
When:    Swing break + best strike change events
Network: 2-3 hidden layers, 64-128 neurons each (~50KB)
```

**Skill 1: Entry + Target Intelligence — "Should I trade, and at what target?"**
- Predicts the R-multiple target for THIS trade based on market context
- High VWAP premium + trending → higher target (1.5R, 2.0R)
- Low VWAP premium + high vol + late session → quick grab (0.3R, 0.5R)
- Close to daily target → just need a bit more (0.3R, 0.5R)
- On ENTER: places TP limit order at target price + SL order (structural)

**Skill 2: Pyramiding — "Should I stack positions?"**
- Same Entry Model, but session state features change the decision context
- `open_position_count > 0` + `total_unrealized_pnl_R > 0` → building on strength
- Agent learns: 2-3 positions cascading reach target faster (ballooning effect)
- Agent learns: adding when underwater = chasing losses (SKIP)
- Agent learns: adding when near target = unnecessary risk (SKIP)

### Session Review Model (18 global features → session-level exit)

```
Input:   18 global features (crucially: cumulative_pnl_R, total_unrealized_pnl_R,
         distance_to_target_R, open_position_count, minutes_since_open)
Output:  HOLD_ALL or EXIT_ALL
When:    Every 10 bars (~10 minutes) when positions are open
Network: 2-3 hidden layers, 64-128 neurons each (~50KB)
```

**Skill 3: Session Exit Intelligence — "Is the cumulative picture enough?"**
- Overrides individual TP orders when cumulative P&L warrants booking everything
- EXIT_ALL: cancel all TP limit orders → market exit all positions
- HOLD_ALL: let individual TP orders continue working

**When EXIT_ALL makes sense (agent learns these patterns):**
- Cumulative unrealized is attractive + momentum fading across all positions
- Close to daily target (distance_to_target_R < 1.0) → preserve gains
- Late in session + decent cumulative → don't risk giving it back
- Sharp reversal signal → get out before individual SLs eat into profits

### Two-Layer Exit System — FINALIZED

```
Layer 1: Individual Position Targets (automatic, proactive)
  → Entry fills → agent's predicted target_R → TP limit order placed
  → TP fills automatically at exact price (captures intra-bar moves)
  → SL fills automatically if trade goes wrong (-1R)
  → No per-bar checking needed — orders handle it

Layer 2: Session-Level Override (periodic review)
  → Every 10 bars, Session Review Model evaluates cumulative picture
  → HOLD_ALL: let individual TPs continue working
  → EXIT_ALL: cancel all TPs → market exit all positions → book cumulative profit

Result: Individual intelligence (right target per trade) + collective intelligence
        (override when cumulative picture says "enough")
```

### Why Two Layers, Not One

```
Individual TPs alone:
  ✗ Can't react to cumulative picture
  ✗ Position 1 at +0.9R, Position 2 at +0.7R, Position 3 at +0.4R = +2.0R total
    Individual TPs might be set at +1.0R each — none have filled yet
    But +2.0R combined is great — should book it before reversal

Session override alone:
  ✗ Can't set different targets per trade context
  ✗ Always exits at market price (misses optimal intra-bar prices)
  ✗ Would need per-bar checking (noisy, 360 decisions/day)

Both together:
  ✓ TPs capture optimal prices for individual trades
  ✓ Session override captures cumulative opportunities
  ✓ Only ~10 session review decisions per day (clean signal)
```

### Complete Position Lifecycle

```
9:30  Swing break → Entry Model: ENTER_1.0R (Position 1)
        → TP limit order at +1.0R price level
        → SL order at -1R (structural)

9:35  Swing break → Entry Model: ENTER_1.0R (Position 2)
        → TP limit order at +1.0R, SL at -1R

9:40  Session Review: HOLD_ALL (unrealized +0.4R, far from target)

9:42  Swing break → Entry Model: ENTER_0.5R (Position 3, pyramid)
        → TP limit order at +0.5R, SL at -1R

9:50  Session Review: HOLD_ALL (unrealized +1.2R, building nicely)

9:55  Position 3 TP FILLS automatically at +0.5R ✓

10:00 Session Review: HOLD_ALL (realized +0.5R, unrealized +1.8R)

10:10 Session Review: momentum fading, total +2.5R
        → EXIT_ALL
        → Cancel Pos 1 and Pos 2 TP orders
        → Market exit both → books remaining profit
        → Day total: +2.5R
```

### Training vs Live Parity — Perfect Match

```
Training (in Gym environment):
  Entry: agent picks target → simulate TP limit order
         Scan subsequent bars: if price reaches target → fill at exact target price
         If SL triggers first → fill at SL (-1R)
  Session Review: every 10 bars → if EXIT_ALL → simulate market exit at current close

Live:
  Entry: agent picks target → place real TP limit order via broker
         Order fills automatically when price hits target
         SL order fills automatically if price hits SL
  Session Review: every 10 bars → if EXIT_ALL → cancel TPs, place market orders

Both use limit orders at exact prices. No next-bar-open compromise.
No bar-delay slippage for individual exits. Session exits use market price (acceptable).
```

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
  - target_R and stop_R randomized per episode

  Entry Model (QR-DQN + PER):
    - SKIP or ENTER_targetR at swing events
    - Replay buffer seeded with Phase 0 BC transitions (kept permanently)
    - Distributional Q-values learn return uncertainty per target level

  Session Review Model (PPO):
    - HOLD_ALL or EXIT_ALL every 10 bars
    - Fine-tuned from Phase 0 BC actor with KL penalty (decaying)
    - Outputs action probabilities for confidence-based decisions

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

## DECISIONS (6 finalized, 1 blocked)

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

### 2. Decision Timing (Entry) — FINALIZED

**Entry decision trigger:** When the continuous filter selects a new best strike (not every bar).

```
SWING CONFIRMED (2-watch)
  │
  ├── Continuous filter picks best strike (SL closest to 20, mechanical)
  │   → Agent Decision Point: sees strike features → ENTER or SKIP
  │
  ├── Best strike changes (new strike becomes favorable)
  │   → Agent Decision Point: sees new strike features → ENTER or SKIP
  │   → If agent had previous order, cancel it first
  │
  └── SWING BREAKS
      → If agent has active order → fill
      → If agent skipped → nothing happens
```

**Key decisions made:**
- **Strike selection stays mechanical** — continuous filter picks SL-closest-to-20, not the agent
- **Agent only decides ENTER/SKIP** — binary decision on the presented strike, not which strike
- **No V1 filter thresholds in V2** — VWAP%, SL%, price are features the agent sees, not gates
- **Execution constraints kept** — price 50-500 (liquidity), minimum volume (market structure gates, not strategy)

**What's structural (kept from V1) vs what agent learns:**
```
STRUCTURAL (hard rules, not strategy):
  ├── Swing detection (watch-based, alternating)     → same as V1
  ├── Strike selection (SL closest to 20 pts)        → margin/ROI normalization
  ├── Position sizing (R_VALUE / SL × LOT_SIZE)      → consistent risk per trade
  ├── Price 50-500 gate                              → liquidity/execution constraint
  └── Proactive SL order mechanics                   → same as V1

AGENT LEARNS (no hardcoded thresholds):
  ├── VWAP premium %     → sees raw value, learns if/when it matters
  ├── SL %               → sees raw value, learns viable range
  ├── Option premium      → sees raw price, learns sweet spot
  ├── Market context      → momentum, time of day, session P&L
  └── ENTER / SKIP        → the actual decision
```

**Training = Live parity:**
```
Training:  Replay bars → SwingDetector → continuous_filter picks best strike
           → when best strike changes → create decision point → agent decides

Live:      Live bars → SwingDetector → continuous_filter picks best strike
           → when best strike changes → ask agent → place/cancel order

Same triggers, same features, same decision points. No distribution mismatch.
```

**Training data is adequate:**
- Training data = number of swings, not number of strikes
- Each swing = 1 decision point (on the SL-closest-to-20 strike)
- ~10 swings/day × 1,000 days = ~10,000 entry decisions
- Training only on SL≈20 strikes avoids distribution mismatch
  (agent never sees SL=10, so can't develop preference for unavailable SL ranges)

### Decision Timing (Exit) — FINALIZED

**Two-layer exit system (see "Agent Architecture" section above for full details):**

```
Layer 1: Individual TP limit orders (set at entry by Entry Model)
  → Automatic fill at exact target price. No per-bar checking.
  → Captures intra-bar moves. Training = Live (both use limit orders).

Layer 2: Session-level override (every 10 bars by Session Review Model)
  → HOLD_ALL or EXIT_ALL based on cumulative picture.
  → Overrides individual TPs when collective profit warrants booking everything.
```

**Key decisions made:**
- **NOT per-bar HOLD/EXIT** — replaced with predictive targets (limit orders)
- **Individual exits via TP limit orders** — agent predicts target R at entry, order fills automatically
- **Session exits via periodic review** — every 10 bars, binary HOLD_ALL/EXIT_ALL
- **Per-position features (A-D) still used** — but by Session Review Model context, not per-bar exit
- **Training = Live parity** — both use limit orders at exact prices, no bar-delay slippage

### 3. Multi-Position Management (Pyramiding) — FINALIZED

**The pyramiding vision (core strategy):**
The primary profit mechanism is NOT individual trades. It's pyramided positions moving together:
```
Position 1: Enter CE short at 150 → 1R risk, TP at +1.0R (limit at 130)
  CE falls... Position 1 unrealized: +0.65R
Position 2: New swing breaks → Enter CE short at 140 → 1R risk, TP at +1.0R (limit at 120)
  CE falls... Position 1: +1.6R, Position 2: +1R
Position 3: New swing breaks → Enter CE short at 125 → 1R risk, TP at +0.5R (limit at 115)
  CE falls... Position 1: +2.3R, Position 2: +1.6R, Position 3: +0.65R

COMBINED: +4.55R from 3R total risk = ballooning effect
```

**Why V1 can't pyramid:** After first entry, subsequent swing breaks fail filters (VWAP dropped,
price out of range). V1 sits with 1 lonely position. No ballooning.

**V2 solution:** No filter gates. Entry Model sees session state features:
- `open_position_count = 1` → "I have one working position"
- `total_unrealized_pnl_R = +0.5` → "My pyramid is profitable, keep building"
- `is_lower_low = 1` → "Still trending"
- Agent learns: adding when pyramid is profitable → bigger daily R

**Individual TPs + Session Override together enable flexible exit:**
```
Example: 3 positions, all running
  Position 3 TP fills at +0.5R (automatic, limit order)  ✓
  Positions 1 & 2 still running, TPs not hit yet
  Session Review: cumulative realized + unrealized = +2.5R
    → Momentum fading → EXIT_ALL
    → Cancel Pos 1 & 2 TPs → market exit → book remaining profit
```

**The complete pyramid lifecycle:**
```
PHASE 1 — BUILD (Entry Model):
  Swing breaks → Entry Model picks target_R and adds positions
  Each position gets: TP limit order (agent's target) + SL order (structural)
  Agent learns: add when pyramid is profitable, skip when underwater

PHASE 2 — HOLD (orders working):
  TP and SL orders sit in market. No per-bar decisions.
  Individual TPs fill automatically as targets are reached.
  Session Review every 10 bars: mostly HOLD_ALL while things are working.

PHASE 3 — HARVEST (Session Review override):
  Cumulative picture is attractive OR momentum fading.
  Session Review: EXIT_ALL → cancel remaining TPs → market exit all.
  Or: individual TPs fill naturally → positions close one by one.
```

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

**Decision points per episode:**
- ~5-15 swing breaks/day (Entry Model decisions)
- ~36 session reviews/day (Session Review Model, every 10 bars when positions open)
- ~20-50 decision points per episode
- 443 days → ~10K-22K decision points (OK for Phase 0 behavior cloning, marginal for RL)

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
- Entry Model: 18 features → 7 discrete actions (SKIP + 6 target levels)
- Session Review: 18 features → 2 actions (HOLD_ALL / EXIT_ALL)
- Both models are tiny (~50KB each)
- 1,010 episodes with ~150K decision points is solid for this problem size

### 5. Algorithm Choice — FINALIZED

**Split algorithm strategy: different algorithms for each model.**

#### Entry Model → QR-DQN + Prioritized Experience Replay (sb3-contrib)
```
Library:  sb3-contrib.QRDQN + PrioritizedReplayBuffer
Why:
  1. OFF-POLICY — replay buffer keeps Phase 0 BC data permanently.
     Agent keeps learning from V1's examples throughout Phase 1.
     PPO would discard BC data after first rollout batch.
  2. DISTRIBUTIONAL Q-learning — learns full return distribution, not just mean.
     Handles uncertainty: "will target 1.0R fill, or will SL hit?"
     QR-DQN achieved 2.17x PPO's score in sparse-reward benchmarks (2025 study).
  3. Q-VALUE = EXPECTED R — Q(state, ENTER_1.0R) directly answers
     "what R-multiple do I expect if I enter with target 1.0R here?"
  4. PER — oversamples rare trade-close transitions (high TD-error).
     Addresses sparse rewards (most bars = 0 reward).
```

#### Session Review Model → PPO (SB3 core)
```
Library:  stable_baselines3.PPO
Why:
  1. ENOUGH DATA — 36 decisions/day × 1,000 days = 36,000 decision points.
     PPO's on-policy data inefficiency is not a problem at this scale.
  2. BINARY ACTION — 2-action policy gradient is very clean and stable.
  3. CONFIDENCE OUTPUT — PPO outputs P(EXIT_ALL) = 0.85.
     Useful in live: only exit when confidence > threshold.
  4. STABILITY — PPO's clipping prevents flipping to always-EXIT or always-HOLD.
  5. SIMPLEST — session review is the easier model. Keep it simple.
```

#### Why not the same algorithm for both?
```
DQN for Entry (critical):
  Phase 0: 10K BC transitions stored in replay buffer
  Phase 1: Buffer still contains ALL BC data → V1's knowledge persists
  With only ~10K entry decision points, losing BC data is costly

PPO for Session Review (sufficient):
  36K decision points = enough for on-policy learning
  Action probabilities enable confidence-based exit in live
  Binary action = stable policy gradient, no overfitting risk
```

#### Algorithms evaluated and rejected:
```
A2C:           Dominated by PPO on all axes. No advantage. SKIP.
SAC-Discrete:  Not in SB3 core (unmerged PR). BC warmstart awkward
               (multi-network calibration problem). SKIP.
Rainbow DQN:   Best empirical performance but not in SB3.
               QR-DQN captures the most valuable component (distributional).
               Full Rainbow = future aspiration if we outgrow SB3.
DQN (vanilla): Works, but QR-DQN's distributional returns propagate
               sparse reward signal more effectively. Use vanilla DQN
               as fallback if QR-DQN proves complex.
```

#### Implementation sketch:
```python
# Entry Model
from sb3_contrib import QRDQN
entry_model = QRDQN(
    "MlpPolicy", entry_env,
    n_quantiles=50,
    policy_kwargs=dict(net_arch=[128, 64]),
    learning_rate=1e-4,
    buffer_size=100_000,
    learning_starts=1000,
    batch_size=64,
)

# Session Review Model
from stable_baselines3 import PPO
session_model = PPO(
    "MlpPolicy", session_env,
    policy_kwargs=dict(net_arch=[64, 32]),
    learning_rate=3e-4,
    n_steps=2048,
    clip_range=0.2,
    ent_coef=0.01,
)
```

### 6. Evaluation Metrics — BLOCKED (waiting on V1 backtest results)
- Cannot finalize until we know V1's actual performance
- V1's claimed ~1.12R/day is unverified — need real backtest numbers
- **Prerequisite:** Clean Dinesh data → build backtest engine → backtest V1 over 9-month period
- Once V1's actual daily R, win rate, drawdown are known → set V2 targets relative to those
- Evaluation metrics will be finalized after Implementation Step 1 (Backtest Engine)

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
1. Data Preparation (MUST DO FIRST)
   → Clean Dinesh data (Jan 2022 - Mar 2023, 29K CSVs)
   → Merge with rl_dataset_v2 (Feb - Nov 2023)
   → Output: one comprehensive file with 1-min OHLCV for all symbols + NIFTY spot

2. Backtest Engine (validate V1)
   → Answers: "Does V1 actually work? What are the real numbers?"
   → Uses same SwingDetector, same filters, same position sizing
   → Realistic slippage + brokerage
   → Output: V1's actual daily R, win rate, drawdown, pyramiding stats
   → These numbers become V2's evaluation targets

3. RL Environment (Gym interface)
   → Replay historical bars through SwingDetector
   → Agent receives features at each decision point
   → Simulated TP/SL limit orders with realistic slippage

4. Phase 0: Behavior Cloning (supervised pre-training)
   → Replay data through V1 logic, generate ENTER/SKIP labels + target R labels
   → Entry Model: pre-train QR-DQN Q-network, seed replay buffer with BC transitions
   → Session Review Model: pre-train PPO actor as binary classifier
   → Solves lazy agent problem — agent starts from a trading policy

5. Phase 1: RL Fine-Tuning (fixed game)
   → Entry Model: QR-DQN fine-tuning (BC data stays in replay buffer)
   → Session Review Model: PPO fine-tuning with decaying KL penalty
   → Goal-conditioned with randomized ±target/stop
   → Curriculum: start tight, widen over training

6. Shadow Mode (paper trade alongside V1)
   → V1 makes real decisions
   → Agent logs what it would do
   → Compare: daily R, win rate, drawdown against V1 backtest numbers

7. Live Deployment (after shadow validation)
   → Replace V1 filters + exit logic
   → Keep swing detection + SL placement unchanged
```

---

## Infrastructure

### Training Hardware
- **GPU:** NVIDIA RTX 4060 Ti 16GB (Zotac Twin Edge) — desktop
- **More than sufficient** — models are ~50KB each, 20K parameters total
- Training estimate: 1,000 episodes in 5-30 minutes per run
- Can run 100+ experiments in a single evening

### Training Stack
- **PyTorch** — model definition and training
- **stable-baselines3** — PPO for Session Review Model
- **sb3-contrib** — QR-DQN + PrioritizedReplayBuffer for Entry Model
- **Gymnasium** — environment interface (replays bars, simulates orders)
- **CUDA 12.x** — GPU acceleration

### Complexity Check
```
Models:          2 tiny networks (~50KB each, 2-3 layers, 64-128 neurons)
Decisions/day:   ~20-50 (not 360 — limit orders handle most exits)
Code to write:   ~900 lines total
  - Gym environment:     ~500 lines (replay bars, simulate TP/SL orders)
  - Entry model:         ~100 lines (small network + PPO wrapper)
  - Session review model: ~100 lines (even smaller network)
  - Training script:     ~200 lines (standard PyTorch + stable-baselines3)
```

---

## File Location
This document: `.claude/plans/rl-exit-agent-v2.md`
Previous ML roadmap: `C:\Users\rajat\.claude\plans\squishy-sparking-bubble.md`
Option chain collector plan: `C:\Users\rajat\.claude\plans\functional-yawning-ripple.md`
