# Issue Tracker — NIFTY Options Agent

Tracks observed bugs, anomalies, and operational issues during live/paper trading sessions.

**Status values:** `Open` | `Investigating` | `Fix In Progress` | `Fixed` | `Closed (Won't Fix)` | `Monitoring`

---

## Issue Log

| ID | Date Observed | Description | Affected Module(s) | Status | Notes |
|----|---------------|-------------|-------------------|--------|-------|
| ISS-001 | 2026-02-10 | Duplicate orders accumulating — 66-lot naked positions on EC2 (paper mode). Root cause: `cancelorder(orderid=...)` wrong kwarg raised TypeError silently, memory cleared anyway, stale orders accumulated. | `order_manager.py` | Fixed | Fixed in `stable-2026-02-10`: changed to `cancelorder(order_id=...)` in 3 places; Case 1 now checks cancel result before clearing |
| ISS-002 | 2026-02-10 | `BarData.get()` used on dataclass — AttributeError at runtime in `continuous_filter.py`. | `continuous_filter.py` | Fixed | Changed `.get()` to `.high if .high is not None` pattern |
| ISS-003 | 2026-02-10 | Emojis in `emergency_market_exit()` and `should_halt_trading()` caused UnicodeEncodeError crash on Windows CMD. | `order_manager.py` | Fixed | Stripped all emojis; ASCII-only log messages |
| ISS-004 | 2026-02-14 | EC2 8:30 AM auto-start failed silently — Zerodha tokens expired overnight, container entered infinite restart loop with no Telegram alert, strategy never ran. | `auto_detector.py`, `baseline_v1_live.py` | Fixed | Implemented graceful degradation: quick retries → wait mode → periodic Telegram updates |
| ISS-007 | 2026-02-16 | Mobile app not connecting to EC2 — returns HTTP 401. | EC2 reverse proxy / nginx | Open | Likely Basic Auth or session issue; pending debug (Task #7) |
| ISS-008 | 2026-02-20 | `reconnect()` clears `last_bar_timestamp` before calling `backfill_missed_bars()` — backfill skips all symbols on 3+ min reconnect, leaving silent bar gap in history. | `data_pipeline.py` | Open | Tier 4 candidate. Lower priority now that Angel One failover reduces long outage risk. Fix: save timestamps before clearing, or fallback to `self.bars[symbol][-1].timestamp` in backfill |
| ISS-009 | 2026-02-20 | Angel One WebSocket dropped ~50 min after successful login on EC2. Login confirmed at 8:32 AM; at 9:22 AM received Telegram alert "Angel One not connected, running on Zerodha only". System fell back to Zerodha-only mode as expected, but Angel One feed was lost for the entire trading session. Root cause unknown — possible causes: Angel One session token expiry (~1hr TTL), WebSocket idle timeout, OpenAlgo Angel One container crash, or Angel One broker-side disconnection. | `openalgo-angelone` (Docker), `data_pipeline.py` | Open | Failover to Zerodha worked correctly. But Angel One was lost for the entire day — no auto-reconnect. Need to check: (1) Angel One container logs around 9:22 AM; (2) whether the Angel One OpenAlgo session token has a short TTL requiring re-auth; (3) whether `data_pipeline.py` attempts Angel One reconnect or just stays on Zerodha once switched. |
| ISS-010 | 2026-02-20 | Monitor dashboard showing `pandas.errors.DatabaseError: unable to open database file` at 09:21 AM IST on EC2. Error thrown in `/app/db.py` line 22 during `pd.read_sql()` for query `SELECT * FROM daily_state ORDER BY updated_at DESC LIMIT 1`. Dashboard container (`trading_monitor`) cannot open the SQLite `live_state.db` file. Root cause likely: DB file path mismatch between trading agent (`/app/state/live_state.db`) and dashboard container, or bind mount for `./data/trading_state` not correctly set up for `trading_monitor` container, or DB file did not exist yet (trading agent not fully started at 09:21 AM). | `monitor_dashboard/` (Docker), `docker-compose.yaml` | Open | Error appears in Streamlit dashboard UI. Traceback: `/app/app.py:50` → `read_df(q.DAILY_STATE)` → `/app/db.py:22` → `pd.read_sql()`. Check: (1) `docker-compose.yaml` bind mount for `trading_monitor`; (2) DB path in dashboard's `db.py`; (3) whether trading agent was fully started when dashboard tried to read. Related to ISS-006 (Positions/Orders tab not showing data). |

---

## Issue Template

To add a new issue, append a row to the table above:

```
| ISS-XXX | YYYY-MM-DD | <one-line description> | <module(s)> | Open | <any initial notes> |
```

---

## Recently Closed

| ID | Date Closed | Resolution Summary |
|----|-------------|-------------------|
| ISS-001 | 2026-02-10 | Fixed wrong kwarg `orderid` → `order_id` in `cancelorder()` calls |
| ISS-002 | 2026-02-10 | Fixed BarData attribute access pattern |
| ISS-003 | 2026-02-10 | Stripped all emojis from log-reachable code paths |
| ISS-004 | 2026-02-14 | Graceful degradation + automated login implemented |
| ISS-005 | 2026-02-20 | Angel One container `/app/logs` dir missing after rebuild — workaround confirmed stable, permanent fix applied in `start.sh` |
| ISS-006 | 2026-02-20 | Positions and Orders tabs in monitor dashboard not showing live data |
