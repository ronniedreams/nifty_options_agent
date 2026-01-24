# Sub-Agents Complete Reference Guide

## Quick Reference Card

### Skills (Slash Commands)
| Skill | Use When |
|-------|----------|
| `/trading-strategy` | Swing detection issues, filter problems, tie-breaker questions |
| `/order-execution` | Order cancelled, position sizing, daily limits, SL placement |
| `/broker-integration` | WebSocket issues, ticks stopped, API errors, connection problems |
| `/state-management` | Crash recovery, database queries, schema changes |
| `/monitoring-alerts` | Dashboard issues, Telegram notifications, health monitoring |
| `/infrastructure` | Deployment, Docker, config changes, git sync |
| `/code-reviewer` | Review code before commit for safety/quality |
| `/integration-checker` | Check how changes affect other modules |
| `/test-runner` | Run tests, validate system, check config |
| `/e2e-workflow` | Validate complete trading pipeline |
| `/pre-commit` | Full quality check workflow before commit |

### Subagents (Autonomous)
Use `Task` tool with `subagent_type` parameter:
- `trading-strategy-agent` - Swing/filter investigation
- `order-execution-agent` - Order/position analysis
- `broker-integration-agent` - API/WebSocket debugging
- `state-management-agent` - Database operations
- `monitoring-alerts-agent` - Dashboard/alerts work
- `infrastructure-agent` - Config/deployment tasks
- `code-reviewer-agent` - Automated code review
- `integration-checker-agent` - Impact analysis
- `test-runner-agent` - Test execution
- `e2e-workflow-agent` - Pipeline validation

---

## Overview

The NIFTY Options Trading System uses **specialized sub-agents** to handle different functional domains. This architecture:
- Reduces context clutter in main conversations
- Enables focused expertise per domain
- Allows parallel investigation of issues
- Provides consistent approaches to common problems

---

## Agent Types: Skills vs Subagents

### Skills (Slash Commands)
**What:** Interactive mode where you stay in the conversation with specialized context loaded.

**How to invoke:** Type `/skill-name` (e.g., `/trading-strategy`)

**When to use:**
- You want to interactively explore an issue
- You need to ask follow-up questions
- The task requires back-and-forth discussion

**Location:** `.claude/skills/<skill-name>/SKILL.md`

### Subagents (Autonomous)
**What:** Autonomous delegation where the agent works independently and returns results.

**How to invoke:** Via Task tool with `subagent_type` parameter

**When to use:**
- The task is well-defined and self-contained
- You want parallel investigation
- You need research done in the background

**Location:** `.claude/agents/`

**Built-in subagents:** `Explore`, `Plan`, `Bash`, `general-purpose`
**Custom subagents:** Our domain-specific agents like `trading-strategy-agent`

---

## Domain Agents (6)

These agents handle the core functional areas of the trading system.

---

### Agent 1: Trading Strategy

**Skill:** `/trading-strategy`
**Subagent:** `trading-strategy-agent`

#### Responsibility
Swing detection and strike filtration - the core trading logic that determines WHAT to trade.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| `baseline_v1_live/SWING_DETECTION_THEORY.md` | Watch counters, alternating patterns, swing updates, window behavior |
| `baseline_v1_live/STRIKE_FILTRATION_THEORY.md` | Static/dynamic filters, tie-breakers, pool states |

#### Secondary Context
| File | What It Covers |
|------|----------------|
| `.claude/rules/swing-detection-rules.md` | Implementation rules and gotchas |

#### Files Owned
| File | Lines | Purpose |
|------|-------|---------|
| `baseline_v1_live/swing_detector.py` | ~735 | Watch-based swing detection |
| `baseline_v1_live/continuous_filter.py` | ~761 | Three-stage filter pipeline |
| `baseline_v1_live/strike_filter.py` | ~322 | Tie-breaker utilities |

#### Key Domain Knowledge

**Swing Detection:**
- Watch counters: `low_watch` increments on HH+HC, `high_watch` on LL+LC
- Trigger at counter = 2, find extreme in window
- Alternating pattern: High → Low → High → Low
- Swing updates: Same-direction extreme replaces existing swing ONLY after 2-watch confirmation (not immediate)

**Filter Pipeline:**
- **Static (run once)**: Price range 100-300, VWAP premium >= 4%
- **Dynamic (every tick)**: SL% within 2-10%
- **Tie-breaker**: SL closest to 10pts → Multiple of 100 → Highest premium

**Pool States:**
- `swing_candidates`: Passed static filter (immutable pool)
- `qualified_candidates`: Passed dynamic filter (mutable, refreshed each tick)
- `current_best`: Selected by tie-breaker (one per CE/PE)

#### Common Use Cases

| User Says | What Agent Does |
|-----------|-----------------|
| "Why isn't my swing low being detected?" | Check watch counter values, HH+HC conditions, alternating pattern |
| "A candidate was disqualified but I don't understand why" | Trace through filter stages, identify which filter rejected |
| "The tie-breaker is selecting the wrong strike" | Calculate SL distance, check multiple-of-100, compare premiums |
| "Add a new filter criteria" | Identify correct stage, implement filter, update pools |
| "Debug watch counter logic" | Trace bar-by-bar watch counter updates |

#### Example Investigation

```
User: "Why is NIFTY24000CE not being selected as best?"

Agent Investigation:
1. Check if in swing_candidates (passed static filter)
2. Check current SL% (within 2-10%?)
3. If qualified, calculate tie-breaker score:
   - SL points = highest_high + 1 - swing_low
   - Distance from 10 = abs(sl_points - 10)
4. Compare with other qualified candidates
5. Report: "26000CE has SL distance 1, 24000CE has SL distance 3 → 26000CE wins"
```

---

### Agent 2: Order Execution

**Skill:** `/order-execution`
**Subagent:** `order-execution-agent`

#### Responsibility
Proactive order placement, order lifecycle, position tracking, and R-multiple calculations.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| `baseline_v1_live/ORDER_EXECUTION_THEORY.md` | Proactive SL orders, position sizing, lifecycle states |

#### Secondary Context
| File | What It Covers |
|------|----------------|
| `.claude/rules/trading-rules.md` | Trading logic patterns, order types |

#### Files Owned
| File | Lines | Purpose |
|------|-------|---------|
| `baseline_v1_live/order_manager.py` | ~1,215 | Order placement and lifecycle |
| `baseline_v1_live/position_tracker.py` | ~577 | Position tracking and R-multiples |

#### Key Domain Knowledge

**Proactive Order Placement:**
- Orders placed BEFORE swing breaks, not after
- Entry SL order: `trigger = swing_low - tick_size`, `limit = trigger - 3`
- Exit SL order: `trigger = highest_high + 1`, `limit = trigger + 3`

**Position Sizing (R-Based):**
```python
risk_per_unit = entry_price - sl_price
required_lots = R_VALUE / (risk_per_unit * LOT_SIZE)
final_lots = min(required_lots, MAX_LOTS_PER_POSITION)
quantity = final_lots * LOT_SIZE
```

**Order Lifecycle:**
```
NO_ORDER → ORDER_PLACED → ORDER_FILLED → POSITION_ACTIVE → EXITED
                ↓              ↓               ↓
           CANCELLED       SL_HIT          CLOSED
```

**Cancellation Rules (When to Cancel):**
- Strike disqualified (SL% out of range)
- Different strike becomes best (tie-breaker)
- Daily limits hit (+5R or -5R)
- Market close approaching (3:15 PM)
- **NOT cancelled when swing breaks** - that's the entry trigger!

**Daily Limits:**
- Exit all at DAILY_TARGET_R (default +5R)
- Exit all at DAILY_STOP_R (default -5R)
- Force exit at FORCE_EXIT_TIME (3:15 PM IST)

#### Common Use Cases

| User Says | What Agent Does |
|-----------|-----------------|
| "Why did my order get cancelled?" | Check disqualification, tie-breaker, daily limits |
| "SL order wasn't placed after entry fill" | Verify fill detection, position creation, SL calculation |
| "Position sizing calculation seems wrong" | Trace R-based formula, verify prices |
| "Daily +5R exit isn't triggering" | Check cumulative R calculation, daily limit config |
| "Add trailing stop loss functionality" | Design SL update logic, implement in position_tracker |

#### Example Investigation

```
User: "Why did my 26000CE order get cancelled at 10:45?"

Agent Investigation:
1. Check order log at 10:45
2. Look for disqualification (SL% exceeded 10%?)
3. Look for tie-breaker change (different strike became best?)
4. Check daily limits (was +5R or -5R hit?)
5. Report: "At 10:45, highest_high rose to 165, making SL% = 12.4% > 10%. Order cancelled due to disqualification."
```

---

### Agent 3: Broker Integration

**Skill:** `/broker-integration`
**Subagent:** `broker-integration-agent`

#### Responsibility
OpenAlgo API interactions, WebSocket data feed, position reconciliation, and connection management.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| `.claude/rules/openalgo-integration-rules.md` | Complete API reference, endpoints, error handling |

#### Secondary Context
| File | What It Covers |
|------|----------------|
| `.claude/rules/data-pipeline-rules.md` | Data handling, tick processing |

#### Files Owned
| File | Lines | Purpose |
|------|-------|---------|
| `baseline_v1_live/data_pipeline.py` | ~1,343 | WebSocket → OHLCV bars + VWAP |

#### Key Domain Knowledge

**OpenAlgo Environments:**
| Aspect | Local | EC2 |
|--------|-------|-----|
| Dashboard | http://127.0.0.1:5000 | https://openalgo.ronniedreams.in |
| API Base | http://127.0.0.1:5000/api/v1/ | http://openalgo:5000/api/v1/ (Docker) |
| WebSocket | ws://127.0.0.1:8765 | ws://openalgo:8765 (Docker) |

**Order API:**
```python
order = {
    "strategy": "baseline_v1",
    "symbol": "NIFTY30DEC2526000CE",
    "action": "SELL",
    "exchange": "NFO",
    "price_type": "SL",
    "trigger_price": 129.95,
    "price": 126.95,
    "quantity": 650,
    "product": "MIS"
}
```

**Error Handling:**
- 3 retries with 2-second delay
- Exponential backoff for rate limits
- Auto-reconnect on WebSocket disconnect

**Position Reconciliation:**
- Sync with broker every 60 seconds
- Trust broker as source of truth
- Update internal state on mismatch

#### Common Use Cases

| User Says | What Agent Does |
|-----------|-----------------|
| "WebSocket connection keeps dropping" | Check network, OpenAlgo status, reconnect logic |
| "Ticks stopped flowing" | Verify connection, subscription, market hours |
| "Order placement returns error" | Check API key, parameters, margin, trading hours |
| "Position reconciliation mismatch" | Compare local vs broker, identify discrepancy |
| "Switch from local to EC2 OpenAlgo" | Update config, verify Docker networking |

#### Example Investigation

```
User: "WebSocket stopped receiving ticks at 11:30"

Agent Investigation:
1. Check WebSocket connection status in logs
2. Look for disconnect messages
3. Check OpenAlgo container status (if EC2)
4. Verify subscription tokens are valid
5. Check data coverage in heartbeat: [HEARTBEAT] Data: 22/22 | Coverage: 100%
6. Report: "WebSocket disconnected at 11:28:45, reconnect failed due to OpenAlgo container restart. Reconnected at 11:32:10 after container recovery."
```

---

### Agent 4: State Management

**Skill:** `/state-management`
**Subagent:** `state-management-agent`

#### Responsibility
SQLite database operations, crash recovery, schema management, and query optimization.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| `.claude/rules/trading-rules.md` | State Persistence section |
| `.claude/CLAUDE.md` | Database Schema section |

#### Files Owned
| File | Lines | Purpose |
|------|-------|---------|
| `baseline_v1_live/state_manager.py` | ~932 | All database operations |
| `baseline_v1_live/live_state.db` | - | SQLite database |

#### Key Domain Knowledge

**Database Schema:**
```sql
-- Positions table
positions (symbol, entry_price, quantity, sl_price, entry_time, status, pnl, r_multiple)

-- Orders table
orders (order_id, symbol, order_type, price, quantity, status, timestamp)

-- Daily summary
daily_summary (date, total_trades, winning_trades, cumulative_r, pnl)

-- Swing detection log
swing_log (symbol, swing_type, price, timestamp, vwap)
```

**Persistence Rules:**
- Save after every position creation
- Save after every position modification
- Save after every order placement/cancellation
- Save before system shutdown
- Use transactions for multi-row updates

**Crash Recovery:**
1. Load positions from database on startup
2. Reconcile with broker positions
3. Restore pending orders
4. Resume from last state

#### Common Use Cases

| User Says | What Agent Does |
|-----------|-----------------|
| "System crashed - how to recover state?" | Check database, load positions, reconcile with broker |
| "Add a new database table" | Design schema, add migration, update StateManager |
| "Query today's swing history" | Write SQL or StateManager method |
| "Database migration for schema change" | Backup, create migration, apply, verify |
| "Optimize queries for dashboard" | Identify slow queries, add indexes, optimize |

#### Example Query

```sql
-- Get today's trades with R-multiples
SELECT symbol, entry_price, exit_price, r_multiple, pnl
FROM positions
WHERE DATE(entry_time) = DATE('now', 'localtime')
  AND status = 'CLOSED'
ORDER BY entry_time;
```

---

### Agent 5: Monitoring Alerts

**Skill:** `/monitoring-alerts`
**Subagent:** `monitoring-alerts-agent`

#### Responsibility
Streamlit dashboard, Telegram notifications, health monitoring, and visualization.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| `baseline_v1_live/TELEGRAM_SETUP.md` | Bot setup, notification types |

#### Secondary Context
| File | What It Covers |
|------|----------------|
| `.claude/rules/safety-rules.md` | Alert thresholds |

#### Files Owned
| File | Lines | Purpose |
|------|-------|---------|
| `baseline_v1_live/telegram_notifier.py` | ~402 | Telegram alerts |
| `baseline_v1_live/monitor_dashboard/` | ~930 total | Streamlit dashboard |

#### Key Domain Knowledge

**Telegram Notification Types:**
- Trade entry alerts (symbol, entry, SL, lots, risk)
- Exit alerts with R-multiple
- Daily summary
- Error alerts
- System health updates

**Alert Format:**
```
TRADE ENTRY
Symbol: NIFTY30DEC2526000CE
Entry: 148.00
SL: 156.00
Lots: 10
Risk: 1R (6,500)
```

**Dashboard Components:**
- Position summary (active positions, P&L)
- Daily P&L chart
- Swing detection log
- Order history
- System health status (WebSocket, coverage, stale)

**Health Monitoring:**
- WebSocket connection status
- Data coverage percentage
- Stale symbols count
- Order placement success rate
- Position reconciliation status

#### Common Use Cases

| User Says | What Agent Does |
|-----------|-----------------|
| "Add a new Telegram notification" | Design format, add to notifier, add trigger |
| "Dashboard showing outdated data" | Check refresh, database queries, caching |
| "Create new chart for swing history" | Query swing_log, create Plotly/Altair chart |
| "Style improvements for Streamlit" | Update CSS, layout, theming |
| "Add heartbeat monitoring" | Design format, add to main loop, create widget |

---

### Agent 6: Infrastructure

**Skill:** `/infrastructure`
**Subagent:** `infrastructure-agent`

#### Responsibility
Configuration management, Docker containers, EC2 deployment, and three-way git sync.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| `baseline_v1_live/DAILY_STARTUP.md` | Daily operational procedures |
| `baseline_v1_live/PRE_LAUNCH_CHECKLIST.md` | Pre-flight validation |

#### Secondary Context
| File | What It Covers |
|------|----------------|
| `.claude/rules/openalgo-integration-rules.md` | EC2/Docker sections |
| `.claude/rules/safety-rules.md` | Deployment safety |

#### Files Owned
| File | Lines | Purpose |
|------|-------|---------|
| `baseline_v1_live/config.py` | ~202 | All configuration parameters |
| `baseline_v1_live/check_system.py` | ~233 | Pre-flight validation |
| `docker-compose.yaml` | - | Container orchestration |
| `deploy.sh` | - | Deployment script |
| `Dockerfile` | - | Container build spec |

#### Key Domain Knowledge

**Configuration Parameters:**
```python
# Capital & Position Sizing
R_VALUE = 6500                # Risk per trade
MAX_POSITIONS = 5             # Total concurrent
MAX_LOTS_PER_POSITION = 15    # Safety cap

# Entry Filters
MIN_ENTRY_PRICE = 100
MAX_ENTRY_PRICE = 300
MIN_VWAP_PREMIUM = 0.04       # 4%
MIN_SL_PERCENT = 0.02         # 2%
MAX_SL_PERCENT = 0.10         # 10%

# Daily Limits
DAILY_TARGET_R = 5.0
DAILY_STOP_R = -5.0
FORCE_EXIT_TIME = time(15, 15)
```

**EC2 Infrastructure:**
- Instance: Ubuntu 22.04 on AWS
- Elastic IP: 13.233.211.15
- Domain: ronniedreams.in
- SSL: Let's Encrypt

**Three-Way Sync:**
```
Laptop (Windows) ↔ GitHub ↔ EC2 (Ubuntu)
```

**Docker Commands:**
```bash
docker-compose ps                    # Status
docker-compose logs -f trading_agent # Logs
docker-compose restart trading_agent # Restart
docker-compose down && docker-compose up -d  # Full restart
```

#### Common Use Cases

| User Says | What Agent Does |
|-----------|-----------------|
| "Deploy to EC2" | SSH, pull code, run deploy.sh |
| "Docker container won't start" | Check logs, volumes, ports, .env |
| "Add new configuration parameter" | Add to config.py, .env.example, update docs |
| "Troubleshoot three-way git sync" | Check status on both ends, resolve conflicts |
| "Update SSL certificates" | Run certbot renew, reload nginx |

---

## Quality Agents (4)

These agents ensure code quality, safety, and proper testing.

---

### Agent 7: Code Reviewer

**Skill:** `/code-reviewer`
**Subagent:** `code-reviewer-agent`

#### Responsibility
Review code changes for quality, safety rule violations, pattern consistency, and potential bugs.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| `.claude/rules/safety-rules.md` | Non-negotiable safety constraints |
| `.claude/rules/trading-rules.md` | Trading logic patterns |
| All theory files | Logic verification |

#### What It Checks

**1. Safety Rule Violations (Critical)**
- No hardcoded trading values (use config.py)
- Position limits enforced
- Daily limits enforced
- Paper trading flag checked
- Input validation present

**2. Pattern Consistency**
- IST timezone used correctly
- Symbol format correct (NIFTY{expiry}{strike}CE/PE)
- Logging format with tags ([SWING], [ORDER], etc.)
- Error handling with 3-retry logic
- Database access via state_manager

**3. Potential Bugs**
- Watch counter logic matches theory
- Alternating pattern enforced
- Filter pools used correctly
- Order lifecycle states correct
- Position sizing formula correct

**4. Over-Engineering**
- Unnecessary abstractions
- Extra features not requested
- Premature optimization
- Backwards compatibility hacks

**5. Style**
- No emojis in terminal output
- Concise log messages
- No excessive comments

#### Review Output Format

```
[CODE REVIEW SUMMARY]
File: baseline_v1_live/order_manager.py
Lines Changed: 45-67

[CRITICAL ISSUES] (0)
None found

[WARNINGS] (2)
- Line 52: Hardcoded retry count (3) - use config parameter
- Line 58: Missing type hint on return value

[STYLE NOTES] (1)
- Line 61: Consider shorter variable name

[VERDICT]
APPROVED with minor suggestions
```

---

### Agent 8: Integration Checker

**Skill:** `/integration-checker`
**Subagent:** `integration-checker-agent`

#### Responsibility
Analyze how changes in one module affect others, ensuring cross-module consistency.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| `.claude/CLAUDE.md` | Full architecture understanding |

#### What It Checks

**1. Module Dependencies**
- What does this file import?
- What modules import this file?
- What data structures are shared?

**2. Interface Contracts**
- Function signature changes (breaking?)
- Data structure changes (dict keys, class attributes)
- Constant/enum value changes

**3. Data Flow**
- Input format unchanged?
- Output format unchanged?
- New race conditions?

**4. State Management**
- swing_candidates usage
- qualified_candidates usage
- current_best usage
- positions/orders consistency

**5. Configuration Impact**
- Which modules use changed parameter?
- Default values need updating?
- .env changes needed?

#### Module Dependency Graph

```
config.py (Base)
    │
    ├── data_pipeline.py
    │       └── swing_detector.py
    │               └── continuous_filter.py
    │                       └── order_manager.py
    │                               ├── position_tracker.py
    │                               └── state_manager.py
    │
    ├── telegram_notifier.py
    └── monitor_dashboard/
```

#### Impact Analysis Output Format

```
[INTEGRATION ANALYSIS]
Changed File: swing_detector.py
Changed Function: detect_swings()

[DEPENDENCY MAP]
Imports: config (L5), data_pipeline (L8)
Imported by: continuous_filter (L12), baseline_v1_live (L45)

[AFFECTED MODULES]
1. continuous_filter.py
   - Uses: get_swing_candidates()
   - Impact: Return format changed
   - Action: Update swing dict access pattern
   - Risk: HIGH

[VERDICT]
CAUTION - continuous_filter.py needs update
```

---

### Agent 9: Test Runner

**Skill:** `/test-runner`
**Subagent:** `test-runner-agent`

#### Responsibility
Write tests, run system validation, and verify behavior before deployment.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| Theory files | Expected behavior |
| `baseline_v1_live/check_system.py` | Validation patterns |

#### Capabilities

**1. System Validation**
```bash
python -m baseline_v1_live.check_system
```
Validates: OpenAlgo connectivity, API key, WebSocket, database, config

**2. Unit Test Writing**
```python
def test_swing_detection_watch_counter():
    """Test watch counter increments on HH+HC"""
    detector = SwingDetector()
    bars = [
        {'high': 100, 'close': 98},
        {'high': 105, 'close': 102},  # HH + HC
    ]
    detector.process_bars(bars)
    assert detector.get_low_watch(0) == 1
```

**3. Configuration Validation**
```python
def validate_config():
    assert R_VALUE > 0
    assert MAX_POSITIONS > 0
    assert 0 < MIN_SL_PERCENT < MAX_SL_PERCENT < 1
```

**4. Database Validation**
```python
def test_database_schema():
    required_tables = ['positions', 'orders', 'daily_summary', 'swing_log']
    existing = get_table_names()
    for table in required_tables:
        assert table in existing
```

#### Test Categories

| Category | What It Tests |
|----------|---------------|
| Critical Path | Tick → Bar → Swing → Filter → Order → Position → Exit |
| Edge Cases | Watch counter = 2, SL% at boundaries, dual trigger |
| Safety | Position limits, daily limits, force exit, paper trading |

#### Output Format

```
[TEST RESULTS]
Module: swing_detector.py
Tests Run: 12
Passed: 11
Failed: 1

[FAILED TESTS]
- test_dual_trigger_edge_case
  Expected: swing_type == 'LOW'
  Actual: swing_type == 'HIGH'

[RECOMMENDATIONS]
1. Fix dual trigger logic in swing_detector.py:234
```

---

### Agent 10: E2E Workflow

**Skill:** `/e2e-workflow`
**Subagent:** `e2e-workflow-agent`

#### Responsibility
Validate complete trading workflows from data ingestion to order execution.

#### Primary Context (Read First)
| File | What It Covers |
|------|----------------|
| All theory files | Full pipeline understanding |
| `.claude/CLAUDE.md` | Architecture section |

#### Pipeline Checkpoints

```
1. DATA PIPELINE
   Tick → OHLCV Bar + VWAP

2. SWING DETECTION
   Bar → Watch Counters → Swing Point

3. STATIC FILTER
   Swing → Price/VWAP Check → swing_candidates

4. DYNAMIC FILTER
   Candidate → SL% Check → qualified_candidates

5. TIE-BREAKER
   Qualified → Best Selection → current_best

6. ORDER PLACEMENT
   Best → SL Order → Pending Order

7. ORDER FILL
   Trigger Hit → Fill → Position + Exit SL

8. POSITION TRACKING
   Position → R-Multiple → Daily Summary
```

#### Validation at Each Checkpoint

| Checkpoint | What to Verify |
|------------|----------------|
| Tick → Bar | OHLC correct, VWAP calculated, volume accumulated |
| Bar → Swing | Watch counters increment, trigger at 2, extreme found |
| Swing → Static | Price 100-300, VWAP premium >= 4% |
| Static → Dynamic | SL% within 2-10%, recalculated each tick |
| Dynamic → Best | SL distance, multiple-of-100, premium comparison |
| Best → Order | Trigger = swing_low - tick, limit = trigger - 3 |
| Order → Position | Position created, exit SL placed |
| Position → R | R-multiple calculated, daily limits checked |

#### Failure Mode Analysis

| Failure | What to Check |
|---------|---------------|
| No swing detection | Watch counters, HH+HC conditions |
| All candidates rejected | Price range, VWAP premium |
| No qualified candidates | SL% within 2-10% |
| Order not placed | Position limits, daily limits |
| Order not filling | Trigger price, order type |

#### Output Format

```
[E2E WORKFLOW VALIDATION]
Flow: Tick → Order Placement
Status: VALIDATED

[CHECKPOINT RESULTS]
1. Tick → Bar: PASS
2. Bar → Swing: PASS
3. Swing → Static: PASS
4. Static → Dynamic: PASS
5. Dynamic → Best: PASS
6. Best → Order: PASS

[OVERALL]
Pipeline integrity: VERIFIED
```

---

## Workflow Skill

### Pre-Commit Workflow

**Skill:** `/pre-commit`

#### Purpose
Orchestrate quality checks before committing code changes.

#### Workflow Steps

```
1. IDENTIFY CHANGES
   └─ git diff --name-only

2. CODE REVIEW (code-reviewer)
   ├─ Safety rule violations
   ├─ Pattern consistency
   └─ Potential bugs

3. INTEGRATION CHECK (integration-checker)
   ├─ Affected modules
   ├─ Interface contracts
   └─ Data flow consistency

4. SYSTEM VALIDATION (test-runner)
   ├─ python -m baseline_v1_live.check_system
   └─ Configuration verification

5. E2E VERIFICATION (e2e-workflow) [if trading logic]
   ├─ Trace affected workflows
   └─ Validate pipeline integrity

6. REPORT & COMMIT
   ├─ Summarize findings
   └─ Commit if all checks pass
```

#### Verdict Categories

| Verdict | Meaning | Action |
|---------|---------|--------|
| READY TO COMMIT | All checks passed | Proceed with commit |
| WARNINGS | Minor issues, not critical | Proceed, note for future |
| BLOCKERS | Critical issues found | Must fix before commit |

#### Domain-Specific Checks

| Files Changed | Extra Checks |
|---------------|--------------|
| swing_detector.py, continuous_filter.py | Verify watch counter logic, filter pipeline, E2E workflow |
| order_manager.py, position_tracker.py | Verify order logic, position sizing, R-multiples |
| data_pipeline.py | Test WebSocket, tick aggregation, VWAP |
| state_manager.py | Database schema, migration, CRUD operations |
| config.py, Docker files | Config validation, Docker build, deployment |

---

## Cross-Agent Workflows

### New Trade Entry Flow

```
trading-strategy → order-execution → broker-integration → state-management → monitoring-alerts
     │                  │                  │                   │                  │
  Qualifies         Places SL          Sends to           Persists          Sends
  candidate         entry order        OpenAlgo           position          Telegram
```

### Debugging Failed Trade

```
Step 1: trading-strategy
        └─ Did swing qualify? Check filters.

Step 2: order-execution
        └─ Was order placed? Check order lifecycle.

Step 3: broker-integration
        └─ Did order reach broker? Check API logs.

Step 4: state-management
        └─ What does order history show?
```

### Pre-Deployment Validation

```
Step 1: code-reviewer
        └─ Check all changed files

Step 2: integration-checker
        └─ Analyze module impact

Step 3: test-runner
        └─ Run system validation

Step 4: e2e-workflow (if trading logic)
        └─ Validate pipeline

Step 5: infrastructure
        └─ Deploy to EC2
```

---

## Quick Reference Table

| User Intent | Agent | Skill | Subagent |
|-------------|-------|-------|------------|
| Swing not detecting | Trading Strategy | `/trading-strategy` | `trading-strategy-agent` |
| Candidate disqualified | Trading Strategy | `/trading-strategy` | `trading-strategy-agent` |
| Tie-breaker wrong | Trading Strategy | `/trading-strategy` | `trading-strategy-agent` |
| Order cancelled | Order Execution | `/order-execution` | `order-execution-agent` |
| Position sizing wrong | Order Execution | `/order-execution` | `order-execution-agent` |
| Daily limit not triggering | Order Execution | `/order-execution` | `order-execution-agent` |
| WebSocket dropping | Broker Integration | `/broker-integration` | `broker-integration-agent` |
| Ticks stopped | Broker Integration | `/broker-integration` | `broker-integration-agent` |
| Order API error | Broker Integration | `/broker-integration` | `broker-integration-agent` |
| Crash recovery | State Management | `/state-management` | `state-management-agent` |
| Database query | State Management | `/state-management` | `state-management-agent` |
| Schema migration | State Management | `/state-management` | `state-management-agent` |
| Dashboard issue | Monitoring Alerts | `/monitoring-alerts` | `monitoring-alerts-agent` |
| Add Telegram alert | Monitoring Alerts | `/monitoring-alerts` | `monitoring-alerts-agent` |
| Deploy to EC2 | Infrastructure | `/infrastructure` | `infrastructure-agent` |
| Docker issue | Infrastructure | `/infrastructure` | `infrastructure-agent` |
| Config change | Infrastructure | `/infrastructure` | `infrastructure-agent` |
| Review code | Code Reviewer | `/code-reviewer` | `code-reviewer-agent` |
| Check impact | Integration Checker | `/integration-checker` | `integration-checker-agent` |
| Run tests | Test Runner | `/test-runner` | `test-runner-agent` |
| Validate pipeline | E2E Workflow | `/e2e-workflow` | `e2e-workflow-agent` |
| Pre-commit checks | Pre-Commit | `/pre-commit` | - |

---

## File Locations

```
.claude/
├── skills/                          # Slash command definitions (each in subdirectory)
│   ├── trading-strategy/
│   │   └── SKILL.md
│   ├── order-execution/
│   │   └── SKILL.md
│   ├── broker-integration/
│   │   └── SKILL.md
│   ├── state-management/
│   │   └── SKILL.md
│   ├── monitoring-alerts/
│   │   └── SKILL.md
│   ├── infrastructure/
│   │   └── SKILL.md
│   ├── code-reviewer/
│   │   └── SKILL.md
│   ├── integration-checker/
│   │   └── SKILL.md
│   ├── test-runner/
│   │   └── SKILL.md
│   ├── e2e-workflow/
│   │   └── SKILL.md
│   └── pre-commit/
│       └── SKILL.md
│
├── agents/                          # Task agent definitions
│   ├── trading-strategy-agent.md
│   ├── order-execution-agent.md
│   ├── broker-integration-agent.md
│   ├── state-management-agent.md
│   ├── monitoring-alerts-agent.md
│   ├── infrastructure-agent.md
│   ├── code-reviewer-agent.md
│   ├── integration-checker-agent.md
│   ├── test-runner-agent.md
│   └── e2e-workflow-agent.md
│
├── rules/                           # Context rules per domain
│   ├── trading-rules.md
│   ├── swing-detection-rules.md
│   ├── data-pipeline-rules.md
│   ├── openalgo-integration-rules.md
│   └── safety-rules.md
│
├── CLAUDE.md                        # Main project documentation
└── SUB_AGENTS_REFERENCE.md          # This file
```

---

## Skill Frontmatter Format

Each skill requires this YAML frontmatter:
```yaml
---
name: skill-name          # Lowercase, hyphenated (becomes /slash-command)
description: Brief description of what the skill does
---
```

**Important:** Skills must be in `<skill-name>/SKILL.md` format to be discovered by Claude Code.
