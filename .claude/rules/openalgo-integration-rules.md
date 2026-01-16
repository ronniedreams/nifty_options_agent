---
paths: order_manager.py, data_pipeline.py, position_tracker.py, baseline_v1_live.py
---

# OpenAlgo Integration Rules

## Overview

OpenAlgo is the broker integration layer that provides a unified API across 24+ Indian brokers. This trading system uses OpenAlgo for:
- Order placement and management (SL orders for both entry and exit)
- Real-time WebSocket data (quotes, ticks)
- Position tracking and reconciliation
- Account/margin information

## OpenAlgo Server Setup

### Starting OpenAlgo

**Before starting the trading system, OpenAlgo must be running:**

```bash
# Terminal 1: Start OpenAlgo
cd D:\marketcalls\openalgo
python app.py
```

**OpenAlgo runs on:**
- **Dashboard**: http://127.0.0.1:5000
- **API Base**: http://127.0.0.1:5000/api/v1/
- **WebSocket**: ws://127.0.0.1:8765 (or configured proxy)

### Broker Login

Before trading:
1. Go to http://127.0.0.1:5000
2. Login with broker credentials (configured in OpenAlgo)
3. Verify "Status: Connected"
4. Check available margin/balance

**DO NOT proceed if broker not connected!**

## Order Placement API

### Endpoint: Place Order (Entry)

```
POST http://127.0.0.1:5000/api/v1/orders/place
```

**Request Format (SL Order for Entry):**
```python
# Entry uses SL (stop-limit) orders
# trigger_price = swing_low - tick_size (e.g., 130.00 - 0.05 = 129.95)
# limit_price = trigger_price - 3 (e.g., 129.95 - 3 = 126.95)

order = {
    "strategy": "baseline_v1",           # Strategy name
    "symbol": "NIFTY30DEC2526000CE",   # Option symbol
    "action": "SELL",                   # SELL (short) for entry
    "exchange": "NFO",                  # Always NFO for options
    "price_type": "SL",                # Stop-Limit order
    "trigger_price": 129.95,           # Trigger at swing_low - tick
    "price": 126.95,                   # Limit price (trigger - 3)
    "quantity": 650,                   # Total quantity (lots × LOT_SIZE)
    "product": "MIS"                   # MIS (intraday) only
}
```

**Response:**
```json
{
    "status": "success",
    "order_id": "ABC123456",
    "timestamp": "2026-01-02 10:15:30"
}
```

**Error Responses:**
```json
{
    "status": "error",
    "error": "Insufficient margin",
    "code": "MARGIN_ERROR"
}
```

### Order Status Types

| Status | Meaning | Action |
|--------|---------|--------|
| OPEN | Order placed, waiting to fill | Monitor for fill |
| COMPLETE | Order filled successfully | Place SL order immediately |
| REJECTED | Broker rejected order | Log error, try next candidate |
| CANCELLED | Order cancelled (by system or user) | Remove from pending |
| PENDING | Order in transit to broker | Wait, check status later |

### Polling Order Status

**Every 10 seconds, check all pending orders:**

```python
ORDERBOOK_POLL_INTERVAL = 10  # seconds

for order_id, order_info in pending_orders.items():
    response = client.orderbook(
        strategy="baseline_v1",
        order_id=order_id
    )

    if response['status'] == 'COMPLETE':
        handle_order_fill(order_id)
    elif response['status'] == 'REJECTED':
        handle_order_rejection(order_id)
```

## Smart Order (SL-L) API

### Endpoint: Place Stop-Loss Order

```
POST http://127.0.0.1:5000/api/v1/smartorders/place
```

**Request Format:**
```python
sl_order = {
    "strategy": "baseline_v1",
    "symbol": "NIFTY30DEC2526000CE",
    "action": "BUY",                   # BUY (cover short)
    "exchange": "NFO",
    "price_type": "SL-L",             # Stop-Loss Limit
    "trigger_price": 141.00,          # Trigger at highest_high + 1
    "price": 144.00,                  # Limit price (trigger + 3 Rs buffer)
    "quantity": 650,
    "product": "MIS"
}
```

**Key Differences from LIMIT Order:**
- `price_type`: "SL-L" (Stop-Loss Limit), not "LIMIT"
- `trigger_price`: Price at which order becomes active
- `price`: Limit price once triggered (usually trigger_price + buffer)

**Why SL-L instead of SL-M?**
- SL-M (market) can have extreme slippage in fast markets
- SL-L (limit) gives price control with buffer
- Example: Order activates at 141, but won't fill above 144 (3 Rs buffer)

### Smart Order Status Check

```python
response = client.smartorderbook(
    strategy="baseline_v1",
    order_id=sl_order_id
)

if response['status'] == 'TRIGGERED':
    # SL order is now active (converted to LIMIT)
    pass
elif response['status'] == 'COMPLETE':
    # Position closed at SL
    handle_sl_hit(order_id)
```

## WebSocket Data Feed

### Connection

**WebSocket is typically proxied through OpenAlgo:**

```python
# OpenAlgo proxy WebSocket
WS_URL = "ws://127.0.0.1:8765"  # Configured in OpenAlgo

# Or direct to broker (depends on OpenAlgo setup)
WS_URL = "wss://broker-ws.example.com/quote"
```

### Authentication

**WebSocket subscription format (broker-specific):**

```python
# Example: Angel One format
subscribe_message = {
    "mode": "quote",
    "exchange": "NFO",
    "token": "22216062",  # Token for NIFTY30DEC2526000CE
}

websocket.send(json.dumps(subscribe_message))
```

**Token lookup:**
- Use OpenAlgo's search API to get token for symbol
- Store token mapping in memory or cache

```python
response = client.search_symbol(
    exchange="NFO",
    symbol="NIFTY30DEC2526000CE"
)
token = response['token']
```

### Tick Data Format

**Incoming tick from WebSocket:**

```json
{
    "exchange": "NFO",
    "token": "22216062",
    "symbol": "NIFTY30DEC2526000CE",
    "timestamp": "2026-01-02 10:15:30.123",
    "bid": 129.50,
    "ask": 129.55,
    "ltp": 129.52,
    "volume": 150,
    "open": 128.50,
    "high": 130.20,
    "low": 128.00,
    "close": 129.52
}
```

### Handling Connection Loss

**When WebSocket disconnects:**

```python
def on_websocket_close():
    logger.warning("[WEBSOCKET] Connection lost")

    # Start reconnection with exponential backoff
    retry_count = 0
    max_retries = 10
    backoff = 2  # seconds

    while retry_count < max_retries:
        try:
            reconnect_websocket()
            logger.info("[WEBSOCKET] Reconnected")
            return
        except Exception as e:
            retry_count += 1
            wait_time = backoff ** retry_count
            logger.warning(f"[WEBSOCKET] Retry {retry_count}/{max_retries} in {wait_time}s")
            time.sleep(wait_time)

    logger.critical("[WEBSOCKET] Failed to reconnect after 10 attempts")
    alert_user("WebSocket connection lost - data unavailable")
```

## Position Book API

### Get Current Positions

```
GET http://127.0.0.1:5000/api/v1/positions
```

**Response:**
```json
{
    "status": "success",
    "positions": [
        {
            "symbol": "NIFTY30DEC2526000CE",
            "exchange": "NFO",
            "quantity": 650,
            "entry_price": 129.95,
            "ltp": 135.50,
            "pnl": 3,575,
            "pnl_percentage": 2.75,
            "product": "MIS"
        }
    ]
}
```

### Reconciliation Check

**Every 60 seconds, sync with broker:**

```python
def reconcile_positions():
    """Verify internal positions match broker's positionbook"""

    # Get internal positions
    internal = get_internal_positions()

    # Get broker positions
    broker_response = client.positions()
    broker = {pos['symbol']: pos for pos in broker_response['positions']}

    # Check for mismatches
    for symbol, pos in internal.items():
        if symbol not in broker:
            logger.error(f"[RECONCILE] Position {symbol} missing in broker!")
            # Trust broker as source of truth
            delete_internal_position(symbol)

        broker_pos = broker[symbol]
        if pos['quantity'] != broker_pos['quantity']:
            logger.warning(f"[RECONCILE] Qty mismatch {symbol}: internal={pos['qty']} broker={broker_pos['qty']}")
            # Update internal to match broker
            update_internal_quantity(symbol, broker_pos['quantity'])
```

## Margin & Risk Limits

### Get Account Details

```
GET http://127.0.0.1:5000/api/v1/account
```

**Response:**
```json
{
    "status": "success",
    "cash": 500000,
    "equity": 10000000,
    "used_margin": 125000,
    "available_margin": 375000,
    "margin_percentage": 25.0
}
```

### Margin Check Before Order

**Before placing any order, verify margin:**

```python
account = client.get_account()
required_margin = calculate_margin_requirement(symbol, quantity)

if account['available_margin'] < required_margin:
    logger.warning(f"[MARGIN] Insufficient: {account['available_margin']} < {required_margin}")
    return False

# Proceed with order placement
```

## Error Handling

### Common OpenAlgo Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `BROKER_DISCONNECTED` | Broker login expired | Re-login in OpenAlgo dashboard |
| `SYMBOL_NOT_FOUND` | Invalid symbol format | Verify symbol against OpenAlgo search |
| `INSUFFICIENT_MARGIN` | Not enough margin for trade | Check account, reduce position size |
| `INVALID_QUANTITY` | Qty not multiple of LOT_SIZE | Use `quantity = lots × LOT_SIZE` |
| `PRICE_OUT_OF_RANGE` | Limit price unrealistic | Check for typos in price |
| `DUPLICATE_ORDER` | Order ID already exists | Use unique strategy + timestamp |
| `MARKET_CLOSED` | Market not open | Check market hours (9:15-3:30 PM IST) |

### Retry Logic

**3-retry pattern for all broker calls:**

```python
def place_order_with_retry(order_params, max_retries=3):
    """Place order with automatic retry on failure"""

    for attempt in range(1, max_retries + 1):
        try:
            response = client.placeorder(order_params)
            if response['status'] == 'success':
                return response
        except Exception as e:
            logger.warning(f"[ORDER] Attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                time.sleep(2)  # Wait 2 seconds before retry

    logger.error(f"[ORDER] Failed after {max_retries} attempts")
    return None
```

## API Rate Limiting

### Rate Limits

- **Order placement**: 10 orders/minute per strategy
- **Data requests**: 100 requests/minute
- **WebSocket**: No limit (but connection pooling recommended)

### Backoff Strategy

```python
from time import sleep

def rate_limited_api_call(func, *args, **kwargs):
    """Execute API call with rate limit backoff"""

    try:
        return func(*args, **kwargs)
    except RateLimitError as e:
        wait_time = e.retry_after  # Seconds to wait
        logger.warning(f"[RATELIMIT] Waiting {wait_time}s before retry")
        sleep(wait_time)
        return func(*args, **kwargs)
```

## Testing & Paper Trading

### Paper Trading Mode

**OpenAlgo supports sandbox/paper trading:**

```python
# In OpenAlgo config, set broker to "PAPER" mode
# Orders will be simulated, not sent to real broker
```

### Testing Checklist

Before going live:

- [ ] OpenAlgo server starts successfully
- [ ] Broker connects (Status: Connected in dashboard)
- [ ] Can place LIMIT order (enters, checks for fills)
- [ ] Can place SL-L order (triggers correctly)
- [ ] WebSocket receives ticks in real-time
- [ ] Position reconciliation syncs correctly
- [ ] Error handling works (network failure, margin error, etc.)
- [ ] Rate limiting doesn't block legitimate orders

## Common Integration Issues

### Issue 1: Orders Not Placing
**Symptom:** Order endpoint called but no response

**Debug:**
1. Is OpenAlgo running? Check http://127.0.0.1:5000
2. Is broker connected? Check dashboard Status
3. Check available margin: `client.get_account()`
4. Verify symbol exists: `client.search_symbol(symbol)`
5. Check logs: `D:\marketcalls\openalgo\logs/`

### Issue 2: WebSocket Ticks Stopping
**Symptom:** Data pipeline receives ticks, then stops

**Debug:**
1. Check WebSocket connection status
2. Verify subscription tokens are valid
3. Check OpenAlgo logs for connection errors
4. Restart WebSocket: Close and reconnect
5. Check data coverage: `[HEARTBEAT] Data: 22/22`

### Issue 3: Position Mismatch
**Symptom:** Internal positions differ from broker

**Debug:**
1. Force reconciliation: `client.positions()`
2. Check broker dashboard for actual positions
3. Trust broker as source of truth
4. Update internal state to match
5. Log mismatch for investigation

### Issue 4: Order Rejected Silently
**Symptom:** Order placed, but immediately rejected

**Debug:**
1. Check order status: `client.orderbook(order_id)`
2. Look for error message in response
3. Verify all order parameters (symbol, qty, price)
4. Check logs for OpenAlgo errors
5. Validate with paper trading first

## Performance Optimization

### Connection Pooling

```python
# Use persistent HTTP connection for API calls
import httpx

client = httpx.Client(
    base_url="http://127.0.0.1:5000/api/v1",
    timeout=30.0,
    limits=httpx.Limits(max_connections=10)
)
```

### Batch Operations

```python
# When possible, use batch endpoints instead of individual calls
# Example: If OpenAlgo supports batch order placement

batch_orders = [
    {"symbol": "NIFTY...CE", "action": "SELL", ...},
    {"symbol": "NIFTY...PE", "action": "SELL", ...}
]
response = client.batch_placeorder(batch_orders)
```

### Caching

```python
# Cache symbol tokens to avoid repeated search calls
symbol_cache = {}

def get_token(symbol):
    if symbol in symbol_cache:
        return symbol_cache[symbol]

    response = client.search_symbol(symbol=symbol)
    token = response['token']
    symbol_cache[symbol] = token  # Cache for future use
    return token
```

## OpenAlgo Dashboard Usage

### Live Monitoring

**OpenAlgo Dashboard** (http://127.0.0.1:5000) provides:

1. **Orders Tab**: All orders placed, status, fills
2. **Positions Tab**: Current positions, P&L, margin usage
3. **Trades Tab**: Completed trades, entry/exit prices
4. **Logs Tab**: System logs for debugging
5. **Settings Tab**: Broker config, API keys, strategy settings

### Troubleshooting via Dashboard

- Check "Broker Status" (must be Connected)
- View "Recent Orders" for order status
- Monitor "Available Margin" (must be > 0)
- Check "Logs" for error messages
- Verify "Strategy" is registered

## Documentation References

For detailed OpenAlgo documentation:

| Topic | Doc Location |
|-------|--------------|
| **API Reference** | `docs/OPTIONSORDER_API.md` |
| **WebSocket** | `docs/QUOTE_WEBSOCKET_EXAMPLE.md` |
| **Python SDK** | `docs/python_sdk.md` |
| **Order Modes** | `docs/API_ORDER_MODE.md` |
| **Options Symbols** | `docs/OPTIONSYMBOL_API.md` |
| **Broker Setup** | `docs/broker_factory.md` |
| **Rate Limiting** | `docs/rate_limiting.md` |
| **Sandbox Mode** | `docs/SANDBOX_AND_TELEGRAM.md` |

## Safety Rules for OpenAlgo Integration

### Non-Negotiable

1. **Always verify broker connection** before placing orders
2. **Check available margin** before each order
3. **Use paper trading first** (at least 1 week)
4. **Reconcile positions every 60 seconds** (trust broker)
5. **Implement 3-retry logic** for all API calls
6. **Monitor heartbeat** for data quality (Coverage ≥ 90%)
7. **Force close at 3:15 PM IST** (market close)
8. **Log all order events** with timestamps and order IDs
9. **Alert on connection loss** (WebSocket, broker disconnection)
10. **Never hardcode API keys** (use .env or secure vault)

## Validation Checklist

Before deploying to production:

- [ ] OpenAlgo server running and accessible
- [ ] Broker credentials configured and connected
- [ ] HTTP API responding to requests
- [ ] WebSocket receiving ticks in real-time
- [ ] Order placement working (paper mode)
- [ ] SL-L orders placing correctly
- [ ] Position reconciliation syncing
- [ ] Error handling catching all common errors
- [ ] Rate limiting not blocking legitimate traffic
- [ ] Logs capturing all events for audit trail
