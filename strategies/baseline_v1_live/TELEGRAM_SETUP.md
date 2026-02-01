# Telegram Setup Guide

Get real-time trade notifications on Telegram!

## Step 1: Create Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` command
3. Choose a name (e.g., "My Trading Bot")
4. Choose a username (e.g., "mytradingbot123_bot")
5. Copy the **bot token** (looks like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

## Step 2: Get Your Chat ID

1. Search for **@userinfobot** on Telegram
2. Send `/start` command
3. Copy your **ID** number (e.g., `123456789`)

## Step 3: Configure in .env

Edit your `.env` file:

```bash
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz  # From BotFather
TELEGRAM_CHAT_ID=123456789  # From userinfobot
```

## Step 4: Start the Bot

1. Search for your bot username (e.g., `@mytradingbot123_bot`)
2. Send `/start` to the bot
3. Bot is now ready to send you messages!

## Step 5: Test

Run the test script:

```powershell
cd baseline_v1_live
python -c "from telegram_notifier import TelegramNotifier; t = TelegramNotifier(); t.send_message('Test message')"
```

You should receive a test message on Telegram!

## What Notifications You'll Get

### 🟢 Trade Entry
```
Symbol: NIFTY26DEC2418000CE
Entry: ₹250.50
SL: ₹260.50 (10.0 pts)
Qty: 650 (10 lots)
Risk: ₹6,500 (1R)
```

### 🔴 Trade Exit
```
Symbol: NIFTY26DEC2418000CE
Entry: ₹250.50
Exit: ₹245.00

P&L: ₹3,575 (+0.55R)
Reason: Profit Target
```

### 🎯 Daily Target Hit
```
Cumulative R: +5.20R
Total P&L: ₹33,800
Trades: 6
Reason: +5R_TARGET

All positions closed.
Trading stopped for the day.
```

### 📊 Daily Summary (EOD)
```
Date: 20 Dec 2024

Cumulative R: +2.50R
Total P&L: ₹16,250
Trades: 4

Trading session ended.
```

## Customization

Edit `config.py` to control which notifications you receive:

```python
# Notification Events
NOTIFY_ON_TRADE_ENTRY = True   # Entry notifications
NOTIFY_ON_TRADE_EXIT = True    # Exit notifications
NOTIFY_ON_DAILY_TARGET = True  # Daily target hit (DAILY_TARGET_R/DAILY_STOP_R, configurable)
NOTIFY_ON_ERROR = True         # Error alerts
```

**Note:** R_VALUE, DAILY_TARGET_R, and DAILY_STOP_R are configurable in config.py. The notification amounts will reflect your configured values.

## Troubleshooting

### Not receiving messages?

1. **Check bot is started:** Send `/start` to your bot
2. **Verify token:** Make sure bot token is correct (no spaces)
3. **Verify chat ID:** Make sure it's YOUR chat ID (not someone else's)
4. **Check logs:** Look for "Telegram message sent" in logs

### Getting "Forbidden" error?

- You haven't sent `/start` to the bot yet
- Send `/start` to your bot first

### Getting "Unauthorized" error?

- Bot token is incorrect
- Copy the FULL token from BotFather (including everything after the colon)

## Privacy & Security

- ✅ Bot can ONLY send messages (cannot read your messages)
- ✅ Bot is private to you (only you can receive messages)
- ✅ Token is stored locally in .env (not shared)
- ❌ Never share your bot token publicly

## Advanced: Group Notifications

Want to send alerts to a group?

1. Create a Telegram group
2. Add your bot to the group
3. Make the bot an admin
4. Get group chat ID:
   - Add @getidsbot to the group
   - Copy the group ID (starts with -)
5. Use group ID in TELEGRAM_CHAT_ID

Now all admins in the group will receive alerts!

## EC2/Docker Deployment

When running on EC2 with Docker, Telegram notifications work the same way:

### Configuration

The `.env` file on EC2 should contain the same Telegram settings:

```bash
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### Verify on EC2

```bash
# SSH into EC2
ssh -i "path/to/key.pem" ubuntu@13.233.211.15

# Check if Telegram is configured
docker-compose exec trading_agent env | grep TELEGRAM

# View Telegram-related logs
docker-compose logs trading_agent | grep -i telegram
```

### Troubleshooting on EC2

- **No notifications received**: Check that `.env` is mounted correctly in docker-compose.yaml
- **Connection errors**: EC2 security group must allow outbound HTTPS (port 443) to api.telegram.org
- **Test from container**:
  ```bash
  docker-compose exec trading_agent python -c "from telegram_notifier import TelegramNotifier; t = TelegramNotifier(); t.send_message('EC2 test')"
  ```
