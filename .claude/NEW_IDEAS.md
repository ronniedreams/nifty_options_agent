# New Ideas — NIFTY Options Agent

Tracks feature ideas, enhancements, and architectural improvements to consider for future implementation.

**Status values:** `Idea` | `Scoping` | `Planned` | `In Progress` | `Implemented` | `Rejected`

**Priority:** `P1 — High` | `P2 — Medium` | `P3 — Low`

---

## Ideas Log

| ID | Date Added | Title | Area | Priority | Status | Description |
|----|------------|-------|------|----------|--------|-------------|
| IDEA-001 | 2026-02-20 | Order Change Reason in Telegram Notifications | `telegram_notifier.py`, `order_manager.py`, `continuous_filter.py` | P2 — Medium | Idea | When a new entry order is placed or an existing pending order is replaced/updated, the Telegram notification should explain: (1) why the previous order was cancelled (e.g. SL% moved out of range, better candidate found, swing invalidated, daily stop hit), and (2) why the new order was selected (e.g. closest SL to 10pts, round strike, highest premium among qualified). Requires passing a cancellation reason string through the order lifecycle and a selection reason string from the tie-breaker logic in `continuous_filter.py` to the notifier. |

---

## Idea Template

To add a new idea, append a row to the table above:

```
| IDEA-XXX | YYYY-MM-DD | <short title> | <module(s)> | P1/P2/P3 | Idea | <description> |
```

---

## Implemented Ideas

| ID | Date Implemented | Summary |
|----|-----------------|---------|
| — | — | — |

---

## Rejected Ideas

| ID | Date Rejected | Reason |
|----|--------------|--------|
| — | — | — |
