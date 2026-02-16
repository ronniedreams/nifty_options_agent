---
paths: order_manager.py, data_pipeline.py, position_tracker.py, baseline_v1_live.py
---

# OpenAlgo Integration Rules

## Environments

| Aspect | Local (Laptop) | EC2 (Docker) |
|--------|----------------|--------------|
| Zerodha OpenAlgo | http://127.0.0.1:5000 | http://openalgo:5000 |
| Angel One OpenAlgo | http://127.0.0.1:5001 | http://openalgo_angelone:5000 |
| Zerodha WS | ws://127.0.0.1:8765 | ws://openalgo:8765 |
| Angel One WS | ws://127.0.0.1:8766 | ws://openalgo_angelone:8766 |
| EC2 Dashboard | — | https://openalgo.ronniedreams.in (admin/your-dashboard-password) |
| Monitor | http://localhost:8050 | https://monitor.ronniedreams.in |

## Docker Services (docker-compose.yaml)
- `openalgo` — Zerodha OpenAlgo, ports 5000+8765, volume `openalgo_data:/app/db`
- `openalgo_angelone` — Angel One OpenAlgo, ports 5001+8766, volume `openalgo_angelone_data:/app/db`
- `baseline_v1_live` — Trading agent, depends on both, volume `trading_state:/app/state`
- `trading_monitor` — Streamlit dashboard, port 8050, read-only access to `trading_state`

**Named volumes (NEVER delete with `-v`):** `openalgo_data`, `openalgo_angelone_data`, `trading_state`, `openalgo_logs`, `openalgo_angelone_logs`

## OpenAlgo Python SDK (openalgo package)

### Critical kwarg — cancelorder
```python
# CORRECT
client.cancelorder(order_id="ABC123")
# WRONG — silently raises TypeError
client.cancelorder(orderid="ABC123")
```

### Order Placement
```python
# Entry: LIMIT order (proactive — placed before swing breaks)
client.placeorder(
    strategy="baseline_v1_live",
    symbol="NIFTY20FEB2624000CE",
    action="SELL",
    exchange="NFO",
    price_type="LIMIT",
    price=swing_low - tick_size,  # 1 tick below swing low
    quantity=quantity,
    product="MIS"
)

# Exit SL: SL order (placed immediately after entry fills)
client.placeorder(
    strategy="baseline_v1_live",
    symbol="NIFTY20FEB2624000CE",
    action="BUY",
    exchange="NFO",
    price_type="SL",
    trigger_price=highest_high + 1,
    price=highest_high + 4,       # trigger + 3 buffer
    quantity=quantity,
    product="MIS"
)
```

### Orderbook Poll
```python
response = client.orderbook(strategy="baseline_v1_live")
# Returns list of all strategy orders with status: OPEN, COMPLETE, REJECTED, CANCELLED
```
Poll every **5 seconds** (ORDERBOOK_POLL_INTERVAL). Orderbook may return a list or dict — check type before iterating.

### Position Book
```python
response = client.positionbook(strategy="baseline_v1_live")
```
Reconcile every 60 seconds. Trust broker as source of truth.

## API Analyzer (Best Debug Tool)
OpenAlgo dashboard → **API Analyzer** tab logs every API call with full request/response. Persists across restarts (stored in named volume). Use for order debugging before looking at Python logs.

## Error Handling
- 3-retry with 2s delay for all broker calls
- After 3 failures: log error and skip (don't crash)
- Log attempt number: `[ORDER] Attempt 1/3 ...`

## Common Error Codes

| Error | Cause | Fix |
|-------|-------|-----|
| `BROKER_DISCONNECTED` | Zerodha token expired | Re-login in OpenAlgo dashboard |
| `SYMBOL_NOT_FOUND` | Wrong symbol format | Check NIFTY[DDMMMYY][STRIKE][CE/PE] |
| `INSUFFICIENT_MARGIN` | Not enough margin | Check account, reduce size |
| `INVALID_QUANTITY` | Qty not multiple of LOT_SIZE | qty = lots × 65 |

## Automated Login (login_handler.py)
- Only for paper trading (AUTOMATED_LOGIN=true in .env)
- Sequence: OpenAlgo login → Zerodha TOTP → Angel One TOTP
- Max 20 retries × 5s = 100s wait for EC2 cold boot
- Auth failures abort immediately (no retry); connection failures retry

## Broker Daily Login (Manual)
Sessions expire daily. Before 9:15 AM:
1. https://openalgo.ronniedreams.in → log in with Zerodha (TOTP 2FA)
2. Angel One OpenAlgo (port 5001 locally) → log in with Angel One
3. Restart trading agent: `docker-compose restart baseline_v1_live`

## Container Health
```bash
# Check status
docker-compose ps

# Angel One logs directory fix (after any rebuild)
docker exec -u root openalgo_angelone mkdir -p /app/logs && docker exec -u root openalgo_angelone chmod 777 /app/logs

# View logs
docker-compose logs -f baseline_v1_live
docker-compose logs -f openalgo

# Restart trading agent only
docker-compose stop baseline_v1_live && docker-compose rm -f baseline_v1_live && docker-compose up -d baseline_v1_live
```

## Safety Rules
1. Verify broker connection before placing orders
2. Use paper trading first (PAPER_TRADING=true)
3. Reconcile positions every 60s (trust broker)
4. Monitor heartbeat for data quality
5. Never hardcode API keys (use .env)
6. **Never `docker-compose down -v`** — deletes all named volumes including Historify DuckDB and trading state
