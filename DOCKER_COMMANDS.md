# Docker Quick Reference - Baseline V1 Live

## Daily Commands

| Task | Command |
|------|---------|
| **Start system** | `docker compose up -d` |
| **Stop system** | `docker compose down` |
| **View logs** | `docker compose logs -f trading_agent` |
| **Restart after config change** | `docker compose restart trading_agent` |
| **Check status** | `docker compose ps` |

## Pre-Market (9:00 AM IST)

```bash
# 1. Update expiry & ATM
nano baseline_v1_live/.env

# 2. Restart agent
docker compose restart trading_agent

# 3. Verify startup
docker compose logs --tail=100 trading_agent
```

## Monitoring

```bash
# Live logs (follow mode)
docker compose logs -f trading_agent

# Last 100 lines
docker compose logs --tail=100 trading_agent

# Logs from last hour
docker compose logs --since 1h trading_agent

# Resource usage
docker stats

# Container health
docker compose ps
```

## Debugging

```bash
# Shell into container
docker compose exec trading_agent bash

# Check Python environment
docker compose exec trading_agent python --version

# Test OpenAlgo connection
docker compose exec trading_agent python -c "
from openalgo import api
client = api(api_key='YOUR_KEY', host='http://openalgo:5000')
print(client.funds())
"

# View current positions
docker compose exec trading_agent python -c "
from baseline_v1_live.state_manager import StateManager
sm = StateManager('/app/state/live_state.db')
import json
print(json.dumps(sm.load_positions(), indent=2))
"

# Check environment variables
docker compose exec trading_agent env | grep -E "PAPER|OPENALGO|EXPIRY|ATM"
```

## Data Management

```bash
# Backup state database
docker cp baseline_v1_live:/app/state/live_state.db ./backups/state_$(date +%Y%m%d_%H%M%S).db

# Restore state
docker cp ./backups/state_20260115_093000.db baseline_v1_live:/app/state/live_state.db
docker compose restart trading_agent

# Export today's logs
docker compose logs --since $(date -u +%Y-%m-%dT00:00:00Z) trading_agent > logs/trade_log_$(date +%Y%m%d).txt

# Clear old logs (creates new on restart)
docker compose exec trading_agent rm -rf /app/logs/*
docker compose restart trading_agent
```

## Deployment

```bash
# Pull latest code
git pull origin main

# Rebuild and restart
docker compose up -d --build

# Rebuild from scratch (clears cache)
docker compose build --no-cache
docker compose up -d

# Restart only trading agent (keeps OpenAlgo running)
docker compose restart trading_agent
```

## Emergency

```bash
# STOP TRADING IMMEDIATELY
docker compose stop trading_agent

# Stop everything
docker compose down

# Force kill (if unresponsive)
docker compose kill trading_agent

# View exit errors
docker compose logs --tail=200 trading_agent
```

## Maintenance

```bash
# Remove stopped containers
docker compose down

# Remove unused images (frees disk space)
docker image prune -a

# View disk usage
docker system df

# Clean everything (CAUTION: removes volumes)
docker system prune -a --volumes

# Update OpenAlgo image
docker compose pull openalgo
docker compose up -d openalgo
```

## Volume Management

```bash
# Data is in bind mounts under ./data/ (not named volumes)
ls -lh data/trading_state/
ls -lh data/openalgo_db/

# Backup (simple — data is on host filesystem)
cp -r data/ backups/data_backup_$(date +%Y%m%d_%H%M%S)/

# Restore
cp -r backups/data_backup_YYYYMMDD_HHMMSS/* data/
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Container won't start** | `docker compose logs trading_agent` |
| **OpenAlgo connection failed** | `curl http://localhost:5000/` → `docker compose restart openalgo` |
| **Database locked** | `docker compose stop trading_agent` → backup → restart |
| **Out of memory** | `docker stats` → add resource limits in docker-compose.yaml |
| **Disk full** | `docker system prune -a` |
| **Network issues** | `docker compose down && docker compose up -d` |

## Configuration Changes

| Change | Command |
|--------|---------|
| **Update expiry/ATM** | Edit `.env` → `docker compose restart trading_agent` |
| **Change PAPER_TRADING** | Edit `.env` → `docker compose restart trading_agent` |
| **Update code** | `git pull` → `docker compose up -d --build` |
| **Update OpenAlgo** | `docker compose pull openalgo` → `docker compose up -d openalgo` |

## Health Checks

```bash
# Check all services
docker compose ps

# Test OpenAlgo API
curl http://localhost:5000/

# Check WebSocket
curl http://localhost:8765/

# Verify monitor dashboard
curl http://localhost:8050/

# Container resource usage
docker stats --no-stream

# System disk space
df -h
```

## Logging Best Practices

```bash
# Set log rotation in docker-compose.yaml (already configured):
logging:
  driver: "json-file"
  options:
    max-size: "50m"
    max-file: "5"

# View log file locations
docker inspect baseline_v1_live | grep LogPath

# Follow logs with grep filter
docker compose logs -f trading_agent | grep -i "order\|position\|error"
```

## Performance Monitoring

```bash
# Real-time stats
docker stats

# Container inspect (detailed)
docker inspect baseline_v1_live

# Network inspect
docker network inspect trading_network

# Check SQLite database size
docker compose exec trading_agent ls -lh /app/state/live_state.db
```

## Quick Aliases (Add to ~/.bashrc)

```bash
# Add these to your EC2 ~/.bashrc for convenience
alias t-start='docker compose up -d'
alias t-stop='docker compose down'
alias t-logs='docker compose logs -f trading_agent'
alias t-restart='docker compose restart trading_agent'
alias t-status='docker compose ps'
alias t-backup='docker cp baseline_v1_live:/app/state/live_state.db ./backups/state_$(date +%Y%m%d_%H%M%S).db'
alias t-update='git pull && docker compose up -d --build'
```

Then: `source ~/.bashrc` and use `t-logs`, `t-restart`, etc.

---

**Most Common Daily Workflow:**

```bash
# Morning (9:00 AM)
nano baseline_v1_live/.env     # Update EXPIRY, ATM
docker compose restart trading_agent
docker compose logs -f trading_agent

# During Market
docker compose logs -f trading_agent | grep -E "ENTRY|EXIT|POSITION"

# Evening (3:30 PM)
docker compose logs --since 9h trading_agent > logs/trade_log_$(date +%Y%m%d).txt
docker cp baseline_v1_live:/app/state/live_state.db ./backups/state_$(date +%Y%m%d).db
```
