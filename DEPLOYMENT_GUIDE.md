# Deployment Guide: Failure Handling & Notification Management

## Pre-Deployment Testing ✅

All tests passed successfully:

### Unit Tests
- ✅ Database schema validation
- ✅ Notification throttling logic
- ✅ Operational state transitions
- ✅ Configuration validation
- ✅ File structure validation
- ✅ Code structure validation

### Integration Tests
- ✅ OpenAlgo down scenario
- ✅ Throttle window behavior
- ✅ System recovery flow
- ✅ Permanent error handling
- ✅ Error aggregation
- ✅ Graceful shutdown (<10s)
- ✅ Database performance

## Deployment Steps

### 1. Local Testing (Optional)

If you have the full environment set up locally:

```bash
# Install dependencies
cd D:\nifty_options_agent
pip install -r requirements.txt

# Test with OpenAlgo running
python -m baseline_v1_live.baseline_v1_live --expiry 30JAN26 --atm 23500

# Test graceful shutdown (Ctrl+C)
# Expected: Shutdown in <10s, final notification
```

### 2. Deploy to EC2

```bash
# 1. SSH into EC2
ssh -i "D:/aws_key/openalgo-key.pem" ubuntu@13.233.211.15

# 2. Navigate to project directory
cd ~/nifty_options_agent

# 3. Pull latest code from GitHub
git fetch origin
git checkout fix/crash-loop-notifications
git pull origin fix/crash-loop-notifications

# 4. Review changes
git log --oneline -3
git diff feature/order-flow-update..fix/crash-loop-notifications --stat

# 5. Deploy (rebuild containers and restart)
./deploy.sh
```

### 3. Verification on EC2

```bash
# Monitor logs in real-time
docker-compose logs -f trading_agent

# Check operational state
docker exec -it trading_agent sqlite3 /app/baseline_v1_live/live_state.db \
    "SELECT * FROM operational_state;"

# Check for errors
docker exec -it trading_agent sqlite3 /app/baseline_v1_live/live_state.db \
    "SELECT * FROM error_notifications_log ORDER BY last_occurrence DESC LIMIT 10;"

# Check container status
docker-compose ps
```

## Testing Scenarios on EC2

### Scenario 1: OpenAlgo Down

**Test:**
```bash
# Stop OpenAlgo container
docker-compose stop openalgo

# Watch trading agent logs
docker-compose logs -f trading_agent
```

**Expected Behavior:**
1. Health checks fail after 3 retries
2. System enters WAITING state
3. Telegram notification sent (once)
4. System checks every 5 minutes
5. No crash loop

**Verify:**
```bash
# Check operational state
docker exec -it trading_agent sqlite3 /app/baseline_v1_live/live_state.db \
    "SELECT current_state, error_reason FROM operational_state;"
```

**Recovery:**
```bash
# Restart OpenAlgo
docker-compose start openalgo

# Watch trading agent - should recover automatically
docker-compose logs -f trading_agent
```

**Expected:**
- System detects OpenAlgo is back
- Health checks pass
- Transitions to ACTIVE state
- Recovery notification sent

### Scenario 2: Broker Not Logged In

**Test:**
1. Ensure OpenAlgo is running
2. Make sure Zerodha session is expired (don't login)
3. Start trading agent

**Expected Behavior:**
1. Health check: OpenAlgo connectivity ✓
2. Health check: Broker login ✗
3. System enters ERROR state (permanent)
4. Notification sent once
5. System idle (no crash loop)

**Verify:**
```bash
docker exec -it trading_agent sqlite3 /app/baseline_v1_live/live_state.db \
    "SELECT current_state, error_reason FROM operational_state;"
```

### Scenario 3: Graceful Shutdown

**Test:**
```bash
# Stop trading agent
docker-compose stop trading_agent
```

**Expected Behavior:**
1. SIGTERM received
2. Cancel all orders
3. Close all positions
4. Save state to database
5. Send final notification
6. Complete in <10 seconds

**Verify:**
```bash
# Check if shutdown was graceful (no force kill)
docker-compose logs trading_agent | grep -i shutdown
```

### Scenario 4: Notification Throttling

**Test:**
1. Trigger same error multiple times (e.g., restart OpenAlgo repeatedly)

**Expected Behavior:**
1. First error: Notification sent
2. Same error within 1 hour: Logged but no notification
3. Error count increments
4. Notification count stays at 1

**Verify:**
```bash
docker exec -it trading_agent sqlite3 /app/baseline_v1_live/live_state.db \
    "SELECT error_type, occurrence_count, notification_count FROM error_notifications_log;"
```

## Rollback Plan

If deployment fails:

```bash
# 1. Stop containers
docker-compose down

# 2. Rollback code
git checkout feature/order-flow-update

# 3. Rebuild and restart
docker-compose build
docker-compose up -d

# 4. Verify
docker-compose ps
docker-compose logs -f trading_agent
```

## Monitoring Commands

```bash
# View all logs
docker-compose logs -f

# View only trading agent
docker-compose logs -f trading_agent

# View only OpenAlgo
docker-compose logs -f openalgo

# Check container health
docker-compose ps

# Check resource usage
docker stats

# Follow logs with grep filter
docker-compose logs -f trading_agent | grep -E "\[HEALTH-CHECK\]|\[NOTIFICATION\]|\[STATE\]"
```

## Database Queries

```bash
# Enter SQLite shell
docker exec -it trading_agent sqlite3 /app/baseline_v1_live/live_state.db

# Useful queries:

# 1. Current operational state
SELECT * FROM operational_state;

# 2. Recent errors
SELECT error_type, error_message, occurrence_count, notification_count, is_resolved
FROM error_notifications_log
ORDER BY last_occurrence DESC
LIMIT 10;

# 3. Unresolved errors
SELECT error_type, error_message, occurrence_count
FROM error_notifications_log
WHERE is_resolved = 0;

# 4. Error notification history
SELECT error_type, COUNT(*) as total_occurrences, SUM(notification_count) as total_notifications
FROM error_notifications_log
GROUP BY error_type;

# Exit SQLite
.quit
```

## Configuration Tuning

All limits are configurable in `baseline_v1_live/config.py`:

```python
# Retry behavior
MAX_STARTUP_RETRIES = 3              # Increase for flaky connections
STARTUP_RETRY_DELAY_BASE = 30        # Seconds between retries

# Notification throttling
NOTIFICATION_THROTTLE_STARTUP = 3600 # Decrease for more frequent alerts
NOTIFICATION_THROTTLE_WEBSOCKET = 3600
NOTIFICATION_THROTTLE_BROKER = 1800

# Waiting mode
WAITING_MODE_CHECK_INTERVAL = 300    # Decrease to check more frequently
WAITING_MODE_SEND_HOURLY_STATUS = True  # Disable to reduce spam

# Shutdown
SHUTDOWN_TIMEOUT = 9                 # Max shutdown time
```

After changing config:
```bash
docker-compose restart trading_agent
```

## Success Criteria

Deployment is successful if:

✅ System starts without errors
✅ Health checks pass and system enters ACTIVE state
✅ OpenAlgo down scenario: System enters WAITING mode (no crash)
✅ Broker not logged in: System enters ERROR state (no crash)
✅ Notifications sent only once per error type per hour
✅ Graceful shutdown completes in <10 seconds
✅ Database queries return expected state

## Troubleshooting

### Issue: Container won't start

```bash
# Check logs
docker-compose logs trading_agent

# Check for import errors
docker-compose run --rm trading_agent python -c "from baseline_v1_live.notification_manager import NotificationManager; print('OK')"
```

### Issue: Database migrations failed

```bash
# Backup database
docker exec trading_agent cp /app/baseline_v1_live/live_state.db /app/baseline_v1_live/live_state.db.backup

# Delete and recreate
docker exec -it trading_agent rm /app/baseline_v1_live/live_state.db
docker-compose restart trading_agent
```

### Issue: Notifications not being sent

```bash
# Check Telegram configuration
docker exec trading_agent env | grep TELEGRAM

# Test notification manually
docker exec -it trading_agent python -c "
from baseline_v1_live.telegram_notifier import get_notifier
notifier = get_notifier()
notifier.send_message('Test notification from EC2')
"
```

### Issue: Health checks failing incorrectly

```bash
# Check OpenAlgo connectivity from container
docker exec trading_agent curl http://openalgo:5000/

# Check broker login status
docker exec trading_agent curl -X POST \
    -H "Authorization: Bearer $OPENALGO_API_KEY" \
    http://openalgo:5000/api/v1/funds
```

## Next Steps After Deployment

1. **Monitor for 24 hours** - Watch logs, check notifications
2. **Verify crash loop is fixed** - OpenAlgo restarts should not cause spam
3. **Test recovery scenarios** - Manually stop/start OpenAlgo
4. **Review notification log** - Check throttling is working
5. **Performance check** - Ensure no slowdown from new features
6. **Merge to main** - Once stable, merge fix/crash-loop-notifications

## Support

If issues persist:
1. Check GitHub issues: https://github.com/ronniedreams/nifty_options_agent/issues
2. Review logs: `docker-compose logs -f trading_agent`
3. Check operational state in database
4. Contact: Provide logs + database state + error description
