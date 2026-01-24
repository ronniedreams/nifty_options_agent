---
name: pre-commit
description: Quality checks workflow before committing code changes
---

# Pre-Commit Workflow

## Your Role
You orchestrate the pre-commit quality checks for the NIFTY options trading system. You coordinate multiple validation agents to ensure code quality and safety before any commit.

## Workflow Overview

```
+------------------------------------------------------------------+
|                    PRE-COMMIT WORKFLOW                            |
+------------------------------------------------------------------+
|                                                                   |
|  1. IDENTIFY CHANGES                                              |
|     +-- git diff --name-only                                      |
|                                                                   |
|  2. CODE REVIEW (code-reviewer)                                   |
|     +-- Safety rule violations                                    |
|     +-- Pattern consistency                                       |
|     +-- Potential bugs                                            |
|                                                                   |
|  3. INTEGRATION CHECK (integration-checker)                       |
|     +-- Affected modules                                          |
|     +-- Interface contracts                                       |
|     +-- Data flow consistency                                     |
|                                                                   |
|  4. SYSTEM VALIDATION (test-runner)                               |
|     +-- python -m baseline_v1_live.check_system                   |
|     +-- Configuration verification                                |
|                                                                   |
|  5. E2E VERIFICATION (e2e-workflow) [if trading logic]            |
|     +-- Trace affected workflows                                  |
|     +-- Validate pipeline integrity                               |
|                                                                   |
|  6. REPORT & COMMIT                                               |
|     +-- Summarize findings                                        |
|     +-- Commit if all checks pass                                 |
|                                                                   |
+------------------------------------------------------------------+
```

## Step-by-Step Execution

### Step 1: Identify Changed Files

Run git diff to see what's changed:
```bash
git diff --name-only
git diff --name-only --staged
```

Categorize files by domain:
| File Pattern | Domain | Agent |
|--------------|--------|-------|
| `swing_detector.py`, `continuous_filter.py`, `strike_filter.py` | Trading Strategy | trading-strategy |
| `order_manager.py`, `position_tracker.py` | Order Execution | order-execution |
| `data_pipeline.py` | Broker Integration | broker-integration |
| `state_manager.py` | State Management | state-management |
| `telegram_notifier.py`, `monitor_dashboard/` | Monitoring | monitoring-alerts |
| `config.py`, `docker-compose.yaml`, `Dockerfile` | Infrastructure | infrastructure |

### Step 2: Code Review

Invoke `/code-reviewer` skill for each changed file:

**Check for**:
- Safety rule violations
  - Hardcoded trading values
  - Missing position limit checks
  - Missing paper trading checks
- Pattern consistency
  - IST timezone usage
  - Symbol format
  - Logging format with tags
  - Error handling with retries
- Potential bugs
  - Logic inconsistent with theory
  - Edge cases not handled
  - State transition errors

### Step 3: Integration Check

Invoke `/integration-checker` skill:

**Analyze**:
- Module dependencies affected by changes
- Interface contracts (function signatures, data structures)
- Data flow disruptions
- State management consistency
- Configuration parameter impacts

### Step 4: System Validation

Invoke `/test-runner` skill:

**Run**:
```bash
python -m baseline_v1_live.check_system
```

**Verify**:
- OpenAlgo connectivity
- API key validity
- WebSocket connection
- Database integrity
- Configuration parameters

### Step 5: E2E Workflow Check (Conditional)

**Only if changes affect trading logic** (swing detection, filtering, orders):

Invoke `/e2e-workflow` skill:

**Validate**:
- Tick -> Bar aggregation
- Bar -> Swing detection
- Swing -> Filter qualification
- Qualification -> Order placement
- Fill -> Position creation
- SL hit -> Position exit

### Step 6: Report & Commit

**Report Format**:
```
[PRE-COMMIT REPORT]
Files Changed: 3
+-- baseline_v1_live/swing_detector.py
+-- baseline_v1_live/continuous_filter.py
+-- baseline_v1_live/config.py

[CODE REVIEW]
Status: PASSED with warnings
+-- swing_detector.py: 2 warnings
|   +-- Line 234: Consider type hint
|   +-- Line 256: Long line (82 chars)
+-- continuous_filter.py: CLEAN
+-- config.py: CLEAN

[INTEGRATION CHECK]
Status: PASSED
+-- Affected modules: 2
|   +-- order_manager.py (uses swing_detector)
|   +-- baseline_v1_live.py (uses continuous_filter)
+-- No breaking changes detected

[SYSTEM VALIDATION]
Status: PASSED
+-- OpenAlgo: Connected
+-- WebSocket: OK
+-- Database: Valid
+-- Config: Valid

[E2E WORKFLOW]
Status: PASSED
+-- Swing detection: Verified
+-- Filter pipeline: Verified
+-- Order flow: Verified

[OVERALL VERDICT]
READY TO COMMIT

[COMMIT MESSAGE SUGGESTION]
Fix swing detection edge case and update filter logic

- Handle dual trigger scenario in swing_detector.py
- Add window boundary check in continuous_filter.py
- Update MAX_SL_PERCENT to 0.12 in config.py
```

## Verdict Categories

### READY TO COMMIT
All checks passed. Proceed with:
```bash
git add <files>
git commit -m "<message>

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

### WARNINGS (Non-Blocking)
Minor issues found but not critical:
- Style suggestions
- Type hint recommendations
- Long lines
- Minor refactoring opportunities

Decision: Proceed with commit, note for future cleanup

### BLOCKERS (Must Fix)
Critical issues found:
- Safety rule violations
- Breaking interface changes
- Failed system validation
- Logic inconsistent with theory

Decision: Fix issues before committing

## Domain-Specific Checks

### Trading Strategy Changes
When `swing_detector.py`, `continuous_filter.py`, `strike_filter.py` modified:
1. Verify watch counter logic matches SWING_DETECTION_THEORY.md
2. Verify filter logic matches STRIKE_FILTRATION_THEORY.md
3. Run E2E workflow validation
4. Check alternating pattern enforcement
5. Verify pool state transitions

### Order Execution Changes
When `order_manager.py`, `position_tracker.py` modified:
1. Verify order logic matches ORDER_EXECUTION_THEORY.md
2. Check position sizing calculation
3. Verify SL order placement
4. Check R-multiple calculation
5. Verify daily limit enforcement

### Broker Integration Changes
When `data_pipeline.py` or OpenAlgo code modified:
1. Test WebSocket connectivity
2. Verify tick aggregation
3. Check VWAP calculation
4. Verify order API calls
5. Test error handling/retries

### State Management Changes
When `state_manager.py` modified:
1. Verify database schema
2. Check migration if schema changed
3. Test CRUD operations
4. Verify crash recovery
5. Check query performance

### Infrastructure Changes
When `config.py`, Docker files, deploy scripts modified:
1. Validate all config parameters
2. Test Docker build
3. Verify environment variables
4. Check EC2 deployment path
5. Test three-way sync

## Quick Commands

```bash
# Check what's changed
git status
git diff --name-only

# Run system check
python -m baseline_v1_live.check_system

# Stage specific files
git add baseline_v1_live/swing_detector.py
git add baseline_v1_live/config.py

# Commit with proper format
git commit -m "$(cat <<'EOF'
Fix swing detection edge case

- Handle dual trigger scenario
- Add window boundary check

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"

# View commit
git log -1
```

## Handling Failures

### System Check Failed
```bash
# Check logs
python -m baseline_v1_live.check_system 2>&1 | grep ERROR

# Common fixes:
# - OpenAlgo not running: Start OpenAlgo first
# - Database locked: Kill other processes
# - Config error: Check .env file
```

### Integration Check Failed
- Review the impact analysis
- Update affected modules
- Re-run integration check

### E2E Workflow Failed
- Identify failing checkpoint
- Review theory docs for correct behavior
- Fix logic to match theory
- Re-run workflow validation
