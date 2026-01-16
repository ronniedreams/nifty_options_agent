# 📋 DAILY STARTUP CHECKLIST

## Before Market Opens (Before 9:15 AM)

### 1️⃣ Check OpenAlgo is Running
```powershell
# Open browser and verify
http://127.0.0.1:5000
```
- ✅ OpenAlgo dashboard accessible
- ✅ Broker logged in and connected

---

### 2️⃣ Gather Today's Parameters

**Current NIFTY Spot Price:**
- Check pre-market at 9:00 AM
- Round to nearest 50/100 strike
- Example: NIFTY at 24,180 → use `--atm 24200`

**Weekly Expiry Date:**
- NIFTY expires every Thursday
- Format: `DDMMMYY` (e.g., 26DEC24, 02JAN25)
- Check NSE calendar if unsure

---

### 3️⃣ Activate Virtual Environment
```powershell
cd D:\marketcalls
.\venv\Scripts\activate
cd options_agent\live
```

---

### 4️⃣ Run System Check (Optional but Recommended)
```powershell
python check_system.py
```
- ✅ All checks should pass
- ✅ Telegram notifications enabled
- ✅ Broker margin sufficient

---

### 5️⃣ Start Trading System

**Paper Trading Mode** (Analyzer - Virtual ₹1 Cr):
```powershell
python baseline_v1_live.py --expiry 26DEC24 --atm 24200
```

**Live Trading Mode** (Real Money - set `PAPER_TRADING=false` in .env):
```powershell
python baseline_v1_live.py --expiry 26DEC24 --atm 24200
```

Replace:
- `26DEC24` → Today's weekly expiry
- `24200` → Current NIFTY ATM (rounded)

---

## During Trading (9:15 AM - 3:15 PM)

### Monitor These:

**✅ Telegram Notifications**
- Trade entries with strike, price, SL, lots
- Trade exits with P&L in R-multiples
- Daily target (±5R) alerts

**✅ Terminal Output**
- Real-time tick updates
- Swing break detections
- Order fills and rejections
- Position updates

**✅ OpenAlgo Dashboard** (http://127.0.0.1:5000)
- Orderbook: Pending limit orders
- Positions: Active trades with P&L
- Funds: Available margin

---

## System Will Auto-Stop At:

1. **3:15 PM** - Force exit all positions, cancel all orders
2. **+5R Daily Profit** - Stop trading, exit all positions
3. **-5R Daily Loss** - Stop trading, exit all positions
4. **Error/Exception** - Safe shutdown, positions preserved

---

## Emergency Stop

**To stop the system manually:**
- Press `Ctrl+C` in terminal
- System will safely exit all positions and cancel orders

---

## Coverage Today

With `--atm 24200` and ±10 strikes:
- **CE Range**: 24200 to 24700 (11 strikes)
- **PE Range**: 23700 to 24200 (11 strikes)
- **Total**: 42 options monitored

---

## Key Reminders

⚠️ **Paper Trading First**: Run 10-20 days in Analyzer mode before live
⚠️ **Check Expiry**: Wrong expiry = wrong contracts!
⚠️ **ATM Accuracy**: Set at market open, don't change mid-day
⚠️ **Margin Check**: Ensure ₹10L+ available for 5 positions
⚠️ **Telegram Working**: Test before market opens

---

## Quick Commands Reference

```powershell
# Check system
python check_system.py

# Test Telegram
python -c "from telegram_notifier import TelegramNotifier; t = TelegramNotifier(); t.send_message('Test')"

# Start trading (paper mode)
python baseline_v1_live.py --expiry 26DEC24 --atm 24200
```

---

**Last Updated**: December 20, 2025
**Strategy**: Baseline V1 - Options Swing Break
**Capital**: ₹1 Crore | Max Positions: 5 | R-Value: ₹6,500
