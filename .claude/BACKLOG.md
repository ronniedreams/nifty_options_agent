# Backlog — NIFTY Options Agent

Tracks bugs, operational issues, feature ideas, and enhancements in a single unified log.

**Type:** `Issue` | `Idea`

**Status values:**
- Issues: `Open` | `Investigating` | `Fix In Progress` | `Fixed` | `Closed (Won't Fix)` | `Monitoring`
- Ideas: `Idea` | `Scoping` | `Planned` | `In Progress` | `Implemented` | `Rejected`

**Priority:** `P1 — High` | `P2 — Medium` | `P3 — Low`

**ID format:** `ISS-XXX` for issues | `IDEA-XXX` for ideas

---

## Active Backlog

| ID | Type | Date | Title | Area | Priority | Status | Description |
|----|------|------|-------|------|----------|--------|-------------|
| ISS-007 | Issue | 2026-02-16 | Mobile app HTTP 401 on EC2 | EC2 reverse proxy / nginx | P2 — Medium | Open | Mobile app not connecting to EC2 — returns HTTP 401. Likely Basic Auth or session issue with the EC2 reverse proxy. |
| ISS-009 | Issue | 2026-02-20 | Angel One WebSocket lost after failback | `data_pipeline.py` | P2 — Medium | Fixed | Root cause identified (2026-02-23): After failback to Zerodha, `reconnect()` clears `last_zerodha_tick_time` before `_failback_to_zerodha()` restores it into `last_tick_time` — result is empty dict. Monitor immediately sees 0% coverage, tries re-failover, but Angel One backup reference is stale. Angel One container was healthy all day — the bug is in `data_pipeline.py` failback state management. Also: misleading "Angel One backup not available" log when already on Angel One. |
| IDEA-001 | Idea | 2026-02-20 | Order Change Reason in Telegram Notifications | `telegram_notifier.py`, `baseline_v1_live.py`, `continuous_filter.py` | P2 — Medium | Implemented | Telegram best-strike notifications now show selection reason (tie-breaker criteria) and replaced symbol. Changes: `continuous_filter.py` adds `selection_reason` + `num_qualified` to candidate dict; `baseline_v1_live.py` passes `previous_symbol` on replacements; `telegram_notifier.py` displays "Replaces:" and "Selected:" lines. |
| IDEA-002 | Idea | 2026-02-23 | Fix Android Toggle EC2 Button for Market Hours on Weekends | AWS Lambda toggle function | P2 — Medium | Implemented | Added `is_weekend = ist_now.weekday() >= 5` check to Lambda. Toggle now allowed anytime on Sat/Sun; weekdays still blocked 8:30 AM–4:30 PM IST. Deployed 2026-02-23. |
| IDEA-003 | Idea | 2026-02-23 | Skip Historify Cron Job on Weekends | Historify cron job / EC2 | P2 — Medium | Implemented | Already done — EC2 crontab uses `* * 1-5` (Mon-Fri only). Verified 2026-02-23. |
| ISS-011 | Issue | 2026-02-23 | EC2 started after market hours enters futile wait loop | `baseline_v1_live.py`, `auto_detector.py` | P2 — Medium | Open | If EC2 instance is started after market hours (post 3:30 PM or weekends), the trading agent still launches and attempts to connect/auto-detect — but there will be no WebSocket data. It should detect that market is closed and skip starting the strategy entirely (exit cleanly or sleep until next market open). |

---

## Template

```
| ISS-XXX | Issue | YYYY-MM-DD | <short title> | <module(s)> | P1/P2/P3 | Open | <description> |
| IDEA-XXX | Idea | YYYY-MM-DD | <short title> | <module(s)> | P1/P2/P3 | Idea | <description> |
```

---

## Closed / Implemented / Rejected

| ID | Type | Date Closed | Resolution |
|----|------|-------------|------------|
| ISS-001 | Issue | 2026-02-10 | Fixed wrong kwarg `orderid` → `order_id` in `cancelorder()` calls |
| ISS-002 | Issue | 2026-02-10 | Fixed BarData attribute access pattern |
| ISS-003 | Issue | 2026-02-10 | Stripped all emojis from log-reachable code paths |
| ISS-004 | Issue | 2026-02-14 | Graceful degradation + automated login implemented |
| ISS-005 | Issue | 2026-02-20 | Angel One container `/app/logs` dir missing after rebuild — workaround confirmed stable, permanent fix in `start.sh` |
| ISS-006 | Issue | 2026-02-20 | Positions and Orders tabs in monitor dashboard not showing live data — fixed |
| ISS-008 | Issue | 2026-02-23 | Fixed: `reconnect()` now saves `last_bar_timestamp` into `_saved_bar_timestamps` before clearing; `backfill_missed_bars()` reads saved copy first — no more silent bar gaps |
| ISS-010 | Issue | 2026-02-23 | Fixed `:ro` volume mount breaking SQLite WAL + race condition returning empty DataFrame if DB missing |
| IDEA-002 | Idea | 2026-02-23 | Added weekend bypass to AWS Lambda toggle — `is_weekend` check allows toggling on Sat/Sun |
| ISS-009 | Issue | 2026-02-23 | Root cause: failback clears tick timestamps → 0% coverage → Angel One backup lost. Angel One container was healthy — bug in `data_pipeline.py` state management |
| IDEA-001 | Idea | 2026-02-23 | Telegram best-strike notifications now show selection reason + replaced symbol |
| IDEA-003 | Idea | 2026-02-23 | Already implemented — EC2 crontab uses `1-5` day-of-week field (Mon-Fri only) |
