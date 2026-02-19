# Container Health Monitor Setup

Monitors Docker container status on EC2 and sends Telegram alerts when containers crash.

## Installation

### Step 1: Copy the Monitor Script

The script is already at: `baseline_v1_live/container_monitor.py`

### Step 2: Test Locally (Optional)

```bash
cd ~/nifty_options_agent
python -m baseline_v1_live.container_monitor
```

Expected output:
```
=== Container Health Check ===
Current status: {'baseline_v1_live': 'Up', 'openalgo': 'Up', ...}
Previous state: {...}
All containers healthy
```

### Step 3: Set Up Cron Job on EC2

SSH into EC2:
```bash
ssh -i "D:/aws_key/openalgo-key.pem" ubuntu@13.233.211.15
```

Edit crontab:
```bash
crontab -e
```

Add this line to run every 2 minutes:
```
*/2 * * * * cd ~/nifty_options_agent && python -m baseline_v1_live.container_monitor >> /var/log/container_monitor.log 2>&1
```

Verify cron job:
```bash
crontab -l
```

### Step 4: Create Log Directory (Optional)

```bash
sudo touch /var/log/container_monitor.log
sudo chmod 666 /var/log/container_monitor.log
```

This allows the cron job to write logs.

## How It Works

**Every 2 minutes:**
1. Gets Docker container status (`docker-compose ps`)
2. Compares with previous state (stored in `baseline_v1_live/logs/container_health_state.txt`)
3. Detects crashes (state changed from "Up" to something else)
4. Sends Telegram alert with debugging instructions

## Telegram Alerts

### Container Crash Alert
```
[CONTAINER_CRASH] baseline_v1_live crashed!
Previous: Up
Current: Exited

Time: 14:35:42 IST

Debug instructions:
1. SSH: ssh -i 'D:/aws_key/openalgo-key.pem' ubuntu@13.233.211.15
2. Check logs: docker-compose logs -f baseline_v1_live
3. Restart: docker-compose restart baseline_v1_live
4. Or rebuild: docker-compose down && docker-compose up -d
```

### Container Recovered Alert
```
[CONTAINER_RECOVERED] baseline_v1_live is now running!
```

## Monitoring Logs

Check the monitor's own logs:

**On EC2 via system log:**
```bash
tail -f /var/log/container_monitor.log
```

**Or via the trading agent logs:**
```bash
docker-compose logs -f trading_agent
```

**Or check the state file:**
```bash
cat ~/nifty_options_agent/baseline_v1_live/logs/container_health_state.txt
```

## Troubleshooting

### Cron Job Not Running

Check cron logs:
```bash
# Ubuntu/Debian
grep CRON /var/log/syslog

# Or check if cron service is running
sudo systemctl status cron
```

### No Telegram Alerts

Check if Telegram is configured:
```bash
cd ~/nifty_options_agent
python -c "from baseline_v1_live.config import TELEGRAM_ENABLED; print(f'Telegram enabled: {TELEGRAM_ENABLED}')"
```

### Permission Issues

If cron job can't run docker commands:
```bash
# Add ubuntu user to docker group
sudo usermod -aG docker ubuntu

# Log out and back in for changes to take effect
exit
ssh -i "key.pem" ubuntu@13.233.211.15
```

## Disabling the Monitor

Remove from crontab:
```bash
crontab -e
# Delete the line with container_monitor.py
```

Or disable Telegram alerts in `.env`:
```bash
TELEGRAM_ENABLED=false
```

## Frequency Adjustment

Current: Every 2 minutes (`*/2 * * * * ...`)

To change:
- Every 1 minute: `* * * * *`
- Every 5 minutes: `*/5 * * * *`
- Every hour: `0 * * * *`

Update crontab and restart:
```bash
crontab -e
# Edit the interval
```

Cron changes take effect immediately (no restart needed).
