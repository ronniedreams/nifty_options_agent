# Baseline V1 Live Trading

Live deployment of the **baseline_v1 swing-break options shorting strategy** using OpenAlgo for broker integration.

## Strategy Overview

**Performance (Backtest):**
- **Period:** Feb 27 - Nov 24, 2023 (184 trading days)
- **Cumulative R:** +206.38R
- **Average Daily R:** +1.12R/day
- **Win Rate:** ~65%
- **Max Drawdown:** ~15R

**Trading Logic:**
1. Monitor NIFTY options (±7 strikes from ATM) for swing low breaks
2. Apply entry filters:
   - Price: 100-300 INR
   - VWAP premium: >4%
   - Stop Loss: 2-10% (prefer ~10 points for position sizing)
3. Place **proactive limit orders** BEFORE swing breaks (swing_low - 1 tick)
4. On fill, immediately place **SL-L orders** (trigger at SL, limit +3 Rs)
5. Exit all positions at **±5R cumulative** or **3:15 PM EOD**

## Capital & Position Sizing

```
Total Capital:          ₹1 Crore
Margin per lot:         ₹2 Lakh
Max lots available:     50
Max positions:          5 (max 3 CE, max 3 PE)
Max lots per position:  10
NIFTY lot size:         65

R-VALUE:                ₹6,500 per position
Target SL:              ~10 points
Typical lot size:       10 lots (₹20L margin per position)
```

**Position Sizing Formula:**
```python
risk_per_unit = entry_price - sl_price
required_lots = (₹6,500 / risk_per_unit) / 65
final_lots = min(required_lots, 10)  # Cap at 10
```

## Installation

### Prerequisites

1. **OpenAlgo** installed and running (http://127.0.0.1:5000)
2. **Python 3.10+** with venv
3. **Broker Account** connected to OpenAlgo

### Setup

```powershell
# Navigate to options_agent directory
cd d:\marketcalls\options_agent

# Activate virtual environment (or create one)
..\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Create `.env` file in `options_agent/live/` directory:

```bash
# OpenAlgo Connection
OPENALGO_API_KEY=your_api_key_here
OPENALGO_HOST=http://127.0.0.1:5000
OPENALGO_WS_URL=ws://127.0.0.1:8765

# Trading Mode
PAPER_TRADING=true          # Set to 'false' for live trading
DRY_RUN=false               # Set to 'true' to log orders without placing

# Optional: Telegram Notifications
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Logging
VERBOSE=false
```

## Usage

### 1. Paper Trading (Recommended First)

Test with OpenAlgo **Analyzer Mode** (virtual ₹1 Cr capital):

```powershell
cd live
python baseline_v1_live.py --expiry 26DEC24 --atm 18000
```

**Parameters:**
- `--expiry`: Option expiry date (format: `DDMMMYY`, e.g., `26DEC24`)
- `--atm`: ATM strike price to center scanning (e.g., `18000`)

### 2. Live Trading

After successful paper trading:

1. Set `PAPER_TRADING=false` in `.env`
2. Verify broker connection in OpenAlgo
3. Run strategy:

```powershell
python baseline_v1_live.py --expiry 26DEC24 --atm 18000
```

### 3. Deploy to OpenAlgo Python Strategy Manager

**Option A: Via Web UI**

1. Navigate to http://127.0.0.1:5000/python
2. Click "Upload Strategy"
3. Upload `baseline_v1_live.py` and entire `live/` folder
4. Set environment variables in UI
5. Schedule: 9:15 AM - 3:15 PM IST
6. Click "Start Strategy"

**Option B: Manual Deployment**

```powershell
# Copy to OpenAlgo strategies folder
cp -r live/* ../openalgo/strategies/scripts/baseline_v1_live/

# Edit strategy_configs.json to add baseline_v1_live
```

## Architecture

```
live/
├── baseline_v1_live.py        # Main orchestrator
├── config.py                  # Configuration (capital, limits, filters)
├── data_pipeline.py           # WebSocket → 1-min bars + VWAP
├── swing_detector.py          # Swing low detection per option
├── strike_filter.py           # Entry filters + tie-breaker
├── order_manager.py           # Limit orders + SL orders
├── position_tracker.py        # R-multiple accounting
├── state_manager.py           # SQLite persistence
└── logs/                      # Trade logs, daily summaries
```

**Data Flow:**
```
WebSocket (port 8765)
    ↓
DataPipeline (aggregate to 1-min bars)
    ↓
MultiSwingDetector (detect breaks per option)
    ↓
StrikeFilter (apply filters, select best)
    ↓
OrderManager (place/modify limit orders)
    ↓
PositionTracker (track R, check ±5R exits)
    ↓
StateManager (persist to SQLite)
```

## Monitoring

### Logs

**Main Log:**
```
live/logs/baseline_v1_live_YYYYMMDD.log
```

**Trade Log (CSV):**
```
live/logs/baseline_v1_live_trades.csv
```

**Daily Summary (CSV):**
```
live/logs/baseline_v1_live_daily_summary.csv
```

### Real-Time Status

Monitor position summary:

```python
from live.position_tracker import PositionTracker

tracker = PositionTracker()
print(tracker.get_position_summary())
```

Output:
```python
{
    'total_positions': 3,
    'ce_positions': 2,
    'pe_positions': 1,
    'cumulative_R': +2.3,
    'total_pnl': 14950,
    'daily_exit_triggered': False
}
```

## Risk Management

### Position Limits
- ✅ Max 5 total positions enforced
- ✅ Max 3 CE, Max 3 PE enforced
- ✅ Skips new entries if limits reached

### Daily Exits
- ✅ **+5R Target:** Exits ALL positions, cancels ALL orders, stops trading
- ✅ **-5R Stop:** Exits ALL positions, cancels ALL orders, stops trading
- ✅ **3:15 PM EOD:** Force exit all positions (MIS auto-square at 3:20 PM)

### Order Safeguards
- ✅ **SL-L Orders:** Trigger at SL price, limit +3 Rs above (prevents runaway losses)
- ✅ **Position Reconciliation:** Checks broker positions every 60s
- ✅ **Fill Monitoring:** Polls orderbook every 10s
- ✅ **State Persistence:** Recovers from crashes via SQLite

### Data Quality
- ✅ **Stale Tick Detection:** Alerts if no data for >5 seconds
- ✅ **Minimum Ticks:** Requires 5+ ticks per 1-min bar
- ✅ **WebSocket Reconnection:** Auto-reconnects on disconnect

## Troubleshooting

### Issue: No swing breaks detected

**Cause:** Market not volatile enough OR strike range too narrow

**Solution:**
- Increase `STRIKE_SCAN_RANGE` in `config.py` (default: 7 strikes)
- Check data pipeline health: `pipeline.get_health_status()`

### Issue: Orders not placing

**Cause:** Invalid API key OR OpenAlgo not running

**Solution:**
```powershell
# Verify OpenAlgo is running
curl http://127.0.0.1:5000/api/v1/

# Check API key
echo $env:OPENALGO_API_KEY

# Test order placement
python -c "from openalgo import api; client = api(api_key='your_key', host='http://127.0.0.1:5000'); print(client.positionbook())"
```

### Issue: WebSocket disconnecting

**Cause:** Network issues OR broker API rate limits

**Solution:**
- Check OpenAlgo logs: `openalgo/log/`
- Reduce subscription count (narrow `STRIKE_SCAN_RANGE`)
- Increase `WEBSOCKET_RECONNECT_DELAY` in `config.py`

### Issue: Position count mismatch

**Cause:** SL hit but not detected OR broker RMS rejection

**Solution:**
- Run reconciliation manually:
```python
from live.position_tracker import PositionTracker
tracker = PositionTracker()
tracker.reconcile_with_broker()
```

## Performance Expectations

Based on backtest (Feb-Nov 2023):

| Metric | Expected Value |
|--------|----------------|
| **Daily R** | +1.12R/day (avg) |
| **Win Days** | ~65% |
| **Loss Days** | ~30% |
| **Flat Days** | ~5% |
| **Max Drawdown** | ~15R |
| **Monthly Return** | ~₹70,000 - ₹1,50,000 (on ₹1 Cr capital) |

**Note:** Live performance may differ due to:
- Slippage (1-3 ticks on market orders)
- Brokerage + taxes (~₹60 per lot)
- Market regime changes
- Data quality issues

## Safety Checklist Before Live Trading

- [ ] Successfully paper traded for 10+ days
- [ ] Average daily R close to backtest (+1.12R/day ±20%)
- [ ] No order placement failures
- [ ] WebSocket data stable (>95% coverage)
- [ ] Position reconciliation working
- [ ] ±5R exits triggering correctly
- [ ] Telegram notifications working (if enabled)
- [ ] Reviewed broker margin requirements (₹2L per lot)
- [ ] Capital sufficient for 5 positions (₹1 Cr recommended)
- [ ] Backup plan for internet/power outage

## Advanced Configuration

### Adjust Entry Filters

Edit `live/config.py`:

```python
MIN_ENTRY_PRICE = 80           # Lower bound (default: 100)
MAX_ENTRY_PRICE = 350          # Upper bound (default: 300)
MIN_VWAP_PREMIUM = 0.03        # 3% instead of 4%
TARGET_SL_POINTS = 12          # Prefer 12-point SLs instead of 10
```

### Adjust Position Sizing

```python
R_VALUE = 10000                # ₹10,000 per R (more aggressive)
MAX_LOTS_PER_POSITION = 5      # Reduce to 5 lots (conservative)
```

### Adjust Daily Targets

```python
DAILY_TARGET_R = 3.0           # Exit at +3R instead of +5R
DAILY_STOP_R = -3.0            # Stop at -3R instead of -5R
```

## Support

For issues related to:
- **Strategy Logic:** Check backtest code in `trading_agent.py`
- **OpenAlgo Integration:** https://github.com/marketcalls/openalgo
- **Broker API Issues:** Check OpenAlgo broker plugin logs

## License

Same as parent project.

## Disclaimer

**This is a live trading system. Use at your own risk.**

- Past performance does not guarantee future results
- Options trading involves substantial risk of loss
- Start with paper trading and small capital
- Monitor positions actively during market hours
- Have risk management protocols in place

**Recommended:** Start with 1-2 positions max for first 30 days, then scale up based on performance.
