# Options Trading Agent - NIFTY Swing-Break Strategy

## ⚡ Quick Context

**What:** Automated trading system for NIFTY index options using swing-break strategy
**How:** Detect swing lows → Apply 2-stage filters → Place proactive SL (stop-limit) orders BEFORE breaks
**Risk:** Configurable R_VALUE (default Rs.6,500 per R), daily targets configurable (default +/-5R)
**Mode:** Paper trading by default (PAPER_TRADING=true in .env)

### Broker Environments

| Environment | Broker | EC2 IP | Domain | Branch |
|-------------|--------|--------|--------|--------|
| **Production** | Zerodha | 13.233.211.15 | ronniedreams.in | `main` |
| **Sandbox** | Definedge | 13.201.203.56 | somiljain.in | `deploy/ec2-definedge` |

**Local Development:** OpenAlgo at http://127.0.0.1:5000 (any broker)

---

## 🚨 Known Issues & Persistent Fixes (READ ON EVERY STARTUP)

### Angel One Container — `logs/` Directory Missing After Rebuild
**Fix (run after any rebuild):**
```bash
docker exec -u root openalgo_angelone mkdir -p /app/logs && docker exec -u root openalgo_angelone chmod 777 /app/logs
```
**Permanent fix location:** `openalgo-angelone/openalgo/start.sh` (gitignored, can't commit).

---

### Angel One WebSocket Port — Internal Port is 8766 (not 8765)
**Status:** Already fixed in `docker-compose.yaml` (`8766:8766` and `ws://openalgo_angelone:8766`). No action needed unless docker-compose is reset.

---

### Container Health Monitor (EC2 cron job)
Sends Telegram alert if any Docker container crashes. Set up once on EC2:
```bash
crontab -e
# Add: */2 * * * * cd ~/nifty_options_agent && python -m baseline_v1_live.container_monitor >> /var/log/container_monitor.log 2>&1
```
Also set `EC2_HOST=ubuntu@13.233.211.15` in `.env` for SSH instructions in alerts.
See `CONTAINER_MONITOR_SETUP.md` for full instructions.

---

### Daily Login Required (All Brokers)

**Sessions expire daily.** Login before 9:15 AM:

| EC2 | Broker | URL | Notes |
|-----|--------|-----|-------|
| ronniedreams.in | Zerodha | https://openalgo.ronniedreams.in | Auto-login may work |
| ronniedreams.in | Angel One | Port 5001 | Backup feed |
| somiljain.in | Definedge | https://openalgo.somiljain.in | **MANUAL login required** (auto-login fails for WebSocket) |

**After login, restart trading agent:**
```bash
cd ~/nifty_options_agent && docker compose restart trading_agent
```

**Verify WebSocket connected:**
```bash
docker logs baseline_v1_live 2>&1 | grep "WebSocket connectivity: OK"
```

---

## 📝 Pending Tasks

| # | Task | Notes |
|---|------|-------|
| 2 | ~~Upgrade Zerodha OpenAlgo to v2.0.0.0~~ | Done — EC2 running v2.0.0.0 (git pull in openalgo-zerodha/openalgo + docker rebuild; sync worker preserved) |
| 3 | Verify Zerodha WebSocket ATP matches Kite VWAP | Critical — Stage-1 VWAP filter depends on this |
| 4 | Check if Angel One WebSocket provides VWAP/ATP values | If absent, need fallback strategy |

**Completed:**
- ~~Task 1~~: cancel-verify non-list orderbook — fixed (`order_manager.py`, type check + string check before iterating)
- ~~Task 5~~: stale `all_swings_log` on restart — fixed (`baseline_v1_live.py` always resets dashboard data on startup; `state_manager.py` uses `INSERT OR IGNORE`)

---

## 🔧 Development & Git Workflow

**MANDATORY SOP:** See `.claude/GIT_SOP.md`

**Branch Structure:** `main` → Production | `feature/X` → Development | `draft/X` → Pre-market test

**Quick Workflow:**
```bash
git checkout main && git pull && git checkout -b feature/my-feature
# develop...
git checkout main && git checkout -b draft/my-feature && git merge feature/my-feature
git tag pre-market-YYYYMMDD-my-feature && git push --tags
# After market: merge to main (good) or delete branch (bad)
```

**Hard Rules:**
- ❌ Never deploy to EC2 during market hours (9:15 AM - 3:30 PM)
- ✅ Local code changes/debugging allowed during market hours
- ❌ Never test directly on main | ❌ Never delete tags | ❌ Never deploy draft to EC2

---

## 📋 Architecture at a Glance

```
1. DATA PIPELINE (data_pipeline.py)
   Dual WebSocket → 1-min OHLCV bars + VWAP
   Primary: Zerodha (ws://127.0.0.1:8765) | Backup: Angel One (ws://127.0.0.1:8766)
   Auto-failover: Zerodha stale >15s → switch to Angel One; switchback when Zerodha recovers

2. SWING DETECTION (swing_detector.py)
   Watch-based system (2-bar confirmation) → swing_candidates dict
   Alternating High→Low→High pattern enforced. See SWING_DETECTION_THEORY.md

3. STRIKE FILTRATION (continuous_filter.py)
   Stage-1: Static (price 100-300 Rs, VWAP ≥4%) — run once at swing formation
   Stage-2: Dynamic (SL% 2-10%) — recalculated every tick
   Stage-3: Tie-breaker (SL pts closest to 10, round strike, highest premium)
   See STRIKE_FILTRATION_THEORY.md

4. ORDER EXECUTION (order_manager.py)
   Proactive SL orders BEFORE swing breaks: trigger=swing_low-tick, limit=trigger-3
   Exit SL on fill: trigger=highest_high+1, limit=trigger+3
   Position sizing: R_VALUE / (risk_per_unit × LOT_SIZE). See ORDER_EXECUTION_THEORY.md

5. POSITION TRACKING (position_tracker.py)
   Monitor active positions, calculate R-multiples
   Daily exit at +/-5R or 3:15 PM force close
```

---

## File Structure (baseline_v1_live/)

| File | Purpose |
|------|---------|
| `baseline_v1_live.py` | Main orchestrator (~530 lines) |
| `config.py` | All configuration parameters (~180 lines) |
| `data_pipeline.py` | WebSocket → 1-min OHLCV bars + VWAP (~500 lines) |
| `swing_detector.py` | Multi-symbol swing detection (~350 lines) |
| `continuous_filter.py` | Two-stage filtering engine (~300 lines) |
| `order_manager.py` | Proactive SL orders entry + exit (~680 lines) |
| `position_tracker.py` | R-multiple accounting (~350 lines) |
| `state_manager.py` | SQLite persistence (~280 lines) |
| `telegram_notifier.py` | Trade notifications (~150 lines) |
| `check_system.py` | Pre-flight validation |
| `auto_detector.py` | ATM + expiry auto-detection with graceful degradation (~370 lines) |
| `login_handler.py` | Automated TOTP login for Zerodha + Angel One (~240 lines) |
| `container_monitor.py` | Docker container health monitor — run via cron on EC2 (~210 lines) |
| `monitor_dashboard/` | Streamlit monitoring dashboard |

---

## Key Configuration (config.py)

```python
TOTAL_CAPITAL = 10000000      # Rs.1 Crore
R_VALUE = 6500                # Rs.6,500 per R
MAX_POSITIONS = 5             # Max concurrent (also MAX_CE_POSITIONS=3, MAX_PE_POSITIONS=3)
MAX_LOTS_PER_POSITION = 15    # Safety cap
LOT_SIZE = 65                 # NIFTY lot size

MIN_ENTRY_PRICE = 100         # Option price range
MAX_ENTRY_PRICE = 300
MIN_VWAP_PREMIUM = 0.04       # 4% above VWAP required
MIN_SL_PERCENT = 0.02         # SL range 2-10%
MAX_SL_PERCENT = 0.10

DAILY_TARGET_R = 5.0          # Exit at +5R
DAILY_STOP_R = -5.0           # Exit at -5R
FORCE_EXIT_TIME = time(15, 15)

# Angel One Backup Feed (.env)
ANGELONE_OPENALGO_API_KEY = ''  # Empty = disable failover
ANGELONE_HOST = 'http://127.0.0.1:5001'
ANGELONE_WS_URL = 'ws://127.0.0.1:8766'
FAILOVER_NO_TICK_THRESHOLD = 15       # Seconds before switching
FAILOVER_SWITCHBACK_THRESHOLD = 10    # Seconds stable before switchback

# Automated Login (paper trading only — set in .env)
AUTOMATED_LOGIN = False               # Enable TOTP-based auto login at startup
OPENALGO_USERNAME = ''                # OpenAlgo dashboard username
OPENALGO_PASSWORD = ''                # OpenAlgo dashboard password
ZERODHA_TOTP_SECRET = ''             # Zerodha TOTP secret (base32)
ANGELONE_TOTP_SECRET = ''            # Angel One TOTP secret (base32)
# Also needed in .env: ZERODHA_USER_ID, ZERODHA_PASSWORD, ANGELONE_USER_ID, ANGELONE_PASSWORD
```

---

## Running the System

```powershell
# Start Zerodha OpenAlgo (Primary)
cd D:\nifty_options_agent\openalgo-zerodha\openalgo && python app.py

# Start Angel One OpenAlgo (Backup, optional)
cd D:\nifty_options_agent\openalgo-angelone\openalgo && python app.py

# System check
python -m baseline_v1_live.check_system

# Start trading (paper mode, manual expiry/ATM)
python -m baseline_v1_live.baseline_v1_live --expiry 30JAN25 --atm 23500

# Start trading (auto mode — auto-detect ATM + expiry, auto-login if AUTOMATED_LOGIN=true)
python -m baseline_v1_live.baseline_v1_live --auto
```

---

## EC2 Deployment (Production - Zerodha)

- **EC2**: Ubuntu 22.04 | **IP**: 13.233.211.15 | **Domain**: ronniedreams.in
- **SSH**: `ssh -i "D:/aws_key/openalgo-key.pem" ubuntu@13.233.211.15`
- **Deploy**: `cd ~/nifty_options_agent && ./deploy.sh`
- **Basic Auth**: admin / Trading@2026
- **Branch**: `main`

| Service | URL |
|---------|-----|
| OpenAlgo Dashboard | https://openalgo.ronniedreams.in |
| Monitor Dashboard | https://monitor.ronniedreams.in |

**Docker commands:**
```bash
docker-compose ps
docker-compose logs -f trading_agent
docker-compose restart trading_agent
docker-compose down && docker-compose up -d
```

**Three-Way Sync (Laptop → GitHub → EC2):**
```bash
# Laptop: git add . && git commit -m "msg" && git push origin <branch>
# EC2:    cd ~/nifty_options_agent && ./deploy.sh
```
- Never force push | EC2 is production — test locally first
- SSH key for EC2→GitHub: `~/.ssh/github_key`

---

## EC2 Deployment (Sandbox - Definedge)

- **EC2**: Ubuntu 22.04 | **IP**: 13.201.203.56 | **Domain**: somiljain.in
- **Instance ID**: `i-08625be9058db8058`
- **SSH**: `ssh -i ~/Downloads/openalgo2.pem ubuntu@13.201.203.56`
- **Branch**: `deploy/ec2-definedge`

| Service | URL |
|---------|-----|
| OpenAlgo Dashboard | https://openalgo.somiljain.in |
| Monitor Dashboard | https://trading.somiljain.in |

### EC2 Auto Start/Stop (Weekdays Only)

| Time (IST) | Action | Method |
|------------|--------|--------|
| **8:45 AM** | EC2 starts | AWS Lambda `StartEc2` via EventBridge |
| **3:15 PM** | WebSocket alerts silenced | Code in `data_pipeline.py` |
| **3:30 PM** | EC2 stops + Telegram alert | Cron on EC2 |

**Lambda function** (`StartEc2`):
```python
import boto3

def lambda_handler(event, context):
    ec2 = boto3.client('ec2', region_name='ap-south-1')
    instance_id = 'i-08625be9058db8058'
    ec2.start_instances(InstanceIds=[instance_id])
    print(f'Started instance {instance_id}')
    return {'statusCode': 200, 'body': 'Started'}
```

**EventBridge Schedule**: `StartTradingEC2-Schedule`
- Cron: `cron(15 3 ? * MON-FRI *)` (3:15 UTC = 8:45 AM IST)

**EC2 Shutdown Cron** (`/home/ubuntu/shutdown_after_market.sh`):
- Cron: `0 10 * * 1-5` (10:00 UTC = 3:30 PM IST)
- Sends Telegram notification before shutdown

### Definedge Daily Login (MANUAL REQUIRED)

⚠️ **Auto-login does NOT work reliably for Definedge WebSocket.** The TOTP login establishes dashboard access but the broker WebSocket token often fails authentication.

**Daily Morning Routine:**
1. EC2 auto-starts at 8:45 AM IST (Lambda)
2. **MANUAL**: Go to https://openalgo.somiljain.in
3. **MANUAL**: Login with Definedge credentials (User ID + Password + TOTP)
4. **MANUAL**: Restart trading agent:
   ```bash
   ssh -i ~/Downloads/openalgo2.pem ubuntu@13.201.203.56
   cd ~/nifty_options_agent && docker compose restart trading_agent
   ```
5. Verify WebSocket connected in logs:
   ```bash
   docker logs baseline_v1_live 2>&1 | grep -i "health-check"
   ```

**Why manual login is required:**
- Definedge WebSocket requires valid `susertoken` from broker session
- Auto-login via OpenAlgo web form gets dashboard access but WebSocket auth fails with `{'t': 'ck', 's': 'NOT_OK'}`
- Manual login through the web UI establishes proper broker session

### Definedge-Specific Issues

| Issue | Symptom | Solution |
|-------|---------|----------|
| WebSocket auth failed | `Status: NOT_OK` in OpenAlgo logs | Manual login at https://openalgo.somiljain.in |
| Orderbook returns dict | `type=dict, value={'orders': [...]}` | Fixed in `order_manager.py` - extracts list from dict |
| Trading agent starts before OpenAlgo ready | WebSocket connection refused | Restart trading agent after OpenAlgo is healthy |

### Definedge Configuration (.env)

```bash
AUTOMATED_LOGIN=true  # Attempts auto-login but WebSocket may still fail
DEFINEDGE_USER_ID=1142509
DEFINEDGE_PASSWORD=****
DEFINEDGE_TOTP_SECRET=****
```

---

## Database Schema (live_state.db)

```sql
positions (symbol, entry_price, quantity, sl_price, entry_time, status, pnl, r_multiple)
orders (order_id, symbol, order_type, price, quantity, status, timestamp)
daily_summary (date, total_trades, winning_trades, cumulative_r, pnl)
swing_log (symbol, swing_type, price, timestamp, vwap)
```
WAL mode enabled. Do NOT clear positions/orders/daily_state on restart (crash recovery).

---

## Crash Recovery

On startup: loads positions/orders/daily_state from DB → reconciles with live broker orderbook.

**Order reconciliation:**
- DB PENDING + Broker COMPLETE → process fill, place SL
- DB PENDING + Broker OPEN → keep
- DB PENDING + Broker REJECTED → clean up
- DB SL_ACTIVE + Broker MISSING → re-place SL, send CRITICAL Telegram alert

---

## Important Patterns

```python
# Time: always IST
IST = pytz.timezone('Asia/Kolkata'); now = datetime.now(IST)

# Symbol format: NIFTY[DDMMMYY][STRIKE][CE/PE]
symbol = f"NIFTY{expiry}{strike}CE"  # e.g. NIFTY30DEC2526000CE

# Logging
logger.info("[TAG] Message")  # Tags: [SWING], [ORDER], [FILL], [RECOVERY]
```
- Broker calls: 3-retry with 2s delay | WebSocket: auto-reconnects | State: SQLite WAL

---

## Safety Rules

1. Always test with PAPER_TRADING=true first
2. Position limits: MAX_POSITIONS=5, MAX_CE/PE_POSITIONS=3
3. Daily stops: auto-exit at DAILY_TARGET_R/DAILY_STOP_R (+/-5R)
4. Force exit all at FORCE_EXIT_TIME (3:15 PM)
5. Positions synced with broker every 60 seconds
6. R-based sizing is primary; MAX_LOTS_PER_POSITION is safety cap

---

## Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| No ticks | Check OpenAlgo WebSocket, broker login |
| All candidates rejected | Check VWAP filter (price must be 4%+ above VWAP) |
| Orders not placing | Verify API key, check order_manager logs |
| Swings not detecting | Check `[SWING]` logs, verify alternating pattern |
| Position mismatch | Check reconciliation logs |
| Failover not triggering | Check Angel One at port 5001, verify ANGELONE_OPENALGO_API_KEY |
| Stuck on Angel One | Check Zerodha reconnection logs; `last_zerodha_tick_time` must update |
| **Definedge WebSocket NOT_OK** | Manual login required at https://openalgo.somiljain.in |
| **Definedge orderbook dict error** | Fixed - `order_manager.py` handles dict format |
| **EC2 not starting** | Check Lambda `StartEc2` in AWS Console (ap-south-1) |

**Filter debug:**
```
[FILTER-SUMMARY] 8 candidates, 0 qualified. Rejections: VWAP<4%=5, SL<2%=0
```

**Definedge WebSocket debug:**
```bash
docker logs openalgo 2>&1 | grep -i "websocket auth"
# Should see: "Status: OK" (not "NOT_OK")
```

---

## 🔍 Debugging Order Issues — Log Sources

When investigating order problems, check these sources **in order**:

### 1. OpenAlgo API Analyzer (BEST for order debugging)
The OpenAlgo dashboard has a built-in **API Analyzer** that logs every API call (place order, cancel, orderbook, etc.) with full request/response details. **This persists across EC2 restarts** because it's stored in a named Docker volume (`openalgo_data:/app/db`).

- **Local:** http://127.0.0.1:5000 → API Analyzer tab
- **EC2:** https://openalgo.ronniedreams.in → API Analyzer tab (admin / Trading@2026)

Use this to see: exact order payloads, broker responses, cancel confirmations, fill timestamps.

### 2. Trading Agent Python Logs (ephemeral — lost on container restart)
- **Live (container running):** `docker logs baseline_v1_live 2>&1 | grep <pattern>`
- **On disk (persisted since fix):** `~/nifty_options_agent/baseline_v1_live/logs/baseline_v1_live_YYYYMMDD.log`
- Note: Logs were NOT persisted before the docker-compose volume fix (Feb 13, 2026). Logs prior to that date may be unavailable.

### 3. SQLite Database
- Located at `~/nifty_options_agent/baseline_v1_live/live_state.db` (host) or `/app/state/live_state.db` (container via named volume — persists)
- Tables: `pending_orders`, `trade_log`, `positions`, `order_triggers`
- Query via Python inside container: `docker exec baseline_v1_live python3 -c "import sqlite3; ..."`

---

## 📚 Theory Documents

| Document | Topics |
|----------|--------|
| `SWING_DETECTION_THEORY.md` | Watch-based confirmation, alternating patterns, swing updates |
| `STRIKE_FILTRATION_THEORY.md` | Static/dynamic filters, tie-breaker rules, pool state |
| `ORDER_EXECUTION_THEORY.md` | Proactive placement, position sizing, order lifecycle |

## 🎯 Modular Rules (`.claude/rules/`)

- `trading-rules.md` → baseline_v1_live.py, order_manager.py, position_tracker.py
- `swing-detection-rules.md` → swing_detector.py, continuous_filter.py
- `data-pipeline-rules.md` → data_pipeline.py
- `openalgo-integration-rules.md` → all OpenAlgo API/WebSocket integration
- `safety-rules.md` → critical constraints and validations

---

## Sub-Agents

**Reference:** `.claude/SUB_AGENTS_REFERENCE.md`

| Agent | Skill | Intent |
|-------|-------|--------|
| Trading Strategy | `/trading-strategy` | Swing detection, filtration, tie-breakers |
| Order Execution | `/order-execution` | Orders, positions, R-multiples |
| Broker Integration | `/broker-integration` | OpenAlgo API, WebSocket |
| State Management | `/state-management` | Database, persistence, crash recovery |
| Monitoring Alerts | `/monitoring-alerts` | Dashboard, Telegram |
| Infrastructure | `/infrastructure` | Config, Docker, EC2 |
| Code Reviewer | `/code-reviewer` | Safety, patterns, bugs |
| Integration Checker | `/integration-checker` | Cross-module impact |
| Test Runner | `/test-runner` | Testing, validation |
| E2E Workflow | `/e2e-workflow` | Pipeline validation |
| Pre-Commit | `/pre-commit` | Quality checks before commit |

---

## Code Change Guidelines

**Core files requiring extra care:** `order_manager.py`, `position_tracker.py`, `baseline_v1_live.py`, `continuous_filter.py`, `swing_detector.py`, `data_pipeline.py`

**Best practices:** Minimal focused changes | No unrelated refactoring | Paper mode first

**⚠️ MANDATORY Commit Workflow (never skip):**
1. Show code changes to user, explain what/why → wait for feedback
2. Ask permission to run `/pre-commit` checks → wait for approval
3. Run `/pre-commit` → fix any issues
4. Ask permission to commit with message preview → wait for explicit yes
5. Commit only after explicit approval

**Verification:**
```bash
python -m baseline_v1_live.check_system
python -m baseline_v1_live.baseline_v1_live --expiry 30JAN25 --atm 23500
```
