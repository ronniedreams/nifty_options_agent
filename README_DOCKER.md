# Baseline V1 Live Trading System - Docker Edition

Production-ready Docker deployment for systematic options trading on NIFTY.

## Quick Start

### 1. Setup Environment

```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/nifty_options_agent.git
cd nifty_options_agent

# Create .env from sample
cp .env.sample baseline_v1_live/.env

# Edit with your OpenAlgo credentials
nano baseline_v1_live/.env
```

### 2. Start System

```bash
# Build and start all services
docker compose up -d

# View logs
docker compose logs -f trading_agent
```

### 3. Monitor

- **Logs**: `docker compose logs -f trading_agent`
- **Dashboard**: http://localhost:8050
- **OpenAlgo**: http://localhost:5000

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Docker Network (trading_network)              │
│                                                 │
│  ┌──────────────┐      ┌──────────────────┐   │
│  │  OpenAlgo    │◄─────┤ Trading Agent    │   │
│  │  (Port 5000) │      │ baseline_v1_live │   │
│  │  (Port 8765) │      └──────────────────┘   │
│  └──────────────┘               │              │
│                                  │              │
│                          ┌───────▼────────┐    │
│                          │  Monitor       │    │
│                          │  Dashboard     │    │
│                          │  (Port 8050)   │    │
│                          └────────────────┘    │
└─────────────────────────────────────────────────┘
        │                 │              │
        ▼                 ▼              ▼
   Bind mounts:          Bind mounts:         Bind mounts:
   ./data/openalgo_db    ./data/trading_state  logs/
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| **openalgo** | 5000 | Unified broker API layer |
| **openalgo** | 8765 | WebSocket proxy for live data |
| **trading_agent** | - | Core trading logic (swing + filters) |
| **monitor** | 8050 | Real-time dashboard (Dash/Plotly) |

## Configuration

### Environment Variables (.env)

```bash
# Required
OPENALGO_API_KEY=your_api_key_here
EXPIRY=30JAN26                    # Weekly expiry
ATM=23500                         # Current NIFTY ATM

# Safety
PAPER_TRADING=true                # KEEP true for testing!

# Risk Parameters
TOTAL_CAPITAL=10000000            # 1 Crore
R_VALUE=6500                      # Risk per position
MAX_POSITIONS=5
MAX_LOTS_PER_POSITION=10

# Optional
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### Runtime Parameters

Override via docker-compose:

```bash
# Set expiry and ATM dynamically
EXPIRY=06FEB26 ATM=23700 docker compose up -d trading_agent
```

## Daily Workflow

### Pre-Market (9:00 AM IST)

1. **Update Parameters**:
   ```bash
   nano baseline_v1_live/.env
   # Update EXPIRY and ATM
   ```

2. **Restart Agent**:
   ```bash
   docker compose restart trading_agent
   ```

3. **Verify Health**:
   ```bash
   docker compose logs --tail=100 trading_agent
   ```

### During Market Hours

```bash
# Live monitoring
docker compose logs -f trading_agent

# Check positions
docker compose exec trading_agent python -c "
from baseline_v1_live.state_manager import StateManager
sm = StateManager('/app/state/live_state.db')
print(sm.load_positions())
"
```

### Post-Market (3:30 PM IST)

System auto-exits at 3:15 PM. Review logs:

```bash
# Export today's logs
docker compose logs trading_agent --since $(date -u +%Y-%m-%dT00:00:00Z) > logs/trade_log_$(date +%Y%m%d).txt
```

## Commands Reference

### Service Control

```bash
# Start all
docker compose up -d

# Stop all
docker compose down

# Restart specific service
docker compose restart trading_agent

# Rebuild after code changes
docker compose up -d --build
```

### Monitoring

```bash
# View logs (all services)
docker compose logs -f

# View logs (specific service)
docker compose logs -f trading_agent

# Last 100 lines
docker compose logs --tail=100 trading_agent

# Check resource usage
docker stats

# Health status
docker compose ps
```

### Debugging

```bash
# Shell into container
docker compose exec trading_agent bash

# Check Python environment
docker compose exec trading_agent python --version

# Test OpenAlgo connectivity
docker compose exec trading_agent python -c "
from openalgo import api
client = api(api_key='YOUR_KEY', host='http://openalgo:5000')
print(client.funds())
"

# Inspect state database
docker compose exec trading_agent python -c "
import sqlite3
conn = sqlite3.connect('/app/state/live_state.db')
cursor = conn.cursor()
cursor.execute('SELECT * FROM positions')
print(cursor.fetchall())
"
```

### Data Management

```bash
# Backup state
docker cp baseline_v1_live:/app/state/live_state.db ./backups/state_$(date +%Y%m%d).db

# Restore state
docker cp ./backups/state_20260115.db baseline_v1_live:/app/state/live_state.db
docker compose restart trading_agent

# Export logs
docker cp baseline_v1_live:/app/logs ./logs_backup

# Clear old logs (restart creates new)
docker compose stop trading_agent
docker compose exec trading_agent rm -rf /app/logs/*
docker compose start trading_agent
```

## Deployment Checklist

### EC2 Ubuntu Setup

- [ ] Docker installed (`docker --version`)
- [ ] Docker Compose installed (`docker compose version`)
- [ ] User added to docker group (`sudo usermod -aG docker $USER`)
- [ ] UFW firewall configured (block port 5000 externally)
- [ ] SSH key-based auth enabled
- [ ] Swap space configured (4GB minimum)

### Pre-Launch

- [ ] `.env` configured with real API key
- [ ] `PAPER_TRADING=true` verified
- [ ] Expiry and ATM parameters set correctly
- [ ] Telegram notifications tested
- [ ] Monitor dashboard accessible
- [ ] 1 week of paper trading completed successfully
- [ ] Daily logs reviewed

### Go-Live

- [ ] Change `PAPER_TRADING=false` in `.env`
- [ ] Restart: `docker compose restart trading_agent`
- [ ] Monitor first hour closely
- [ ] Verify orders hitting broker correctly
- [ ] Check position sizing calculations
- [ ] Confirm SL orders placed immediately

## Troubleshooting

### Agent won't start

```bash
# Check logs
docker compose logs trading_agent

# Verify env file
docker compose exec trading_agent cat baseline_v1_live/.env

# Rebuild from scratch (safe — data is in bind mounts under ./data/)
docker compose down
docker compose build --no-cache
docker compose up -d
```

### OpenAlgo connection failed

```bash
# Check OpenAlgo health
curl http://localhost:5000/

# Test from agent container
docker compose exec trading_agent curl http://openalgo:5000/

# Restart OpenAlgo
docker compose restart openalgo
```

### State database locked

```bash
# Stop agent
docker compose stop trading_agent

# Backup current state
docker cp baseline_v1_live:/app/state/live_state.db ./backup.db

# Remove lock files
docker compose exec trading_agent rm -f /app/state/live_state.db-wal /app/state/live_state.db-shm

# Restart
docker compose start trading_agent
```

### Memory issues

```bash
# Check usage
docker stats

# Add resource limits in docker-compose.yaml:
services:
  trading_agent:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
```

## Security

### Protect Secrets

- ✅ `.env` excluded from git (check `.gitignore`)
- ✅ Never commit API keys
- ✅ Use read-only volume mount for `.env`
- ✅ Restrict OpenAlgo to localhost in production

### Network Isolation

```bash
# Production: Bind OpenAlgo to localhost only
# In docker-compose.yaml:
ports:
  - "127.0.0.1:5000:5000"  # Not externally accessible
```

### Regular Backups

```bash
# Cron job for daily backups
0 16 * * * docker cp baseline_v1_live:/app/state/live_state.db /backups/state_$(date +\%Y\%m\%d).db
```

## GitHub Workflow

### Push Changes

```bash
git status  # Ensure .env not listed
git add .
git commit -m "Your change description"
git push origin main
```

### Deploy to EC2

```bash
# SSH into EC2
ssh ubuntu@YOUR_EC2_IP

# Pull latest
cd ~/nifty_options_agent
git pull origin main

# Rebuild and restart
docker compose up -d --build

# Verify
docker compose logs -f trading_agent
```

## Performance Tips

1. **Use SSD storage** for SQLite database (IOPS critical)
2. **Set swap** to 2x RAM to prevent OOM kills
3. **Enable log rotation** (already configured in docker-compose)
4. **Monitor disk usage**: `df -h` and `docker system df`
5. **Prune old images weekly**: `docker image prune -a`

## Support & Maintenance

- **Logs location**: `./logs/` (host) and `/app/logs` (container)
- **State location**: `./data/trading_state/` (bind mount on host)
- **Config location**: `./baseline_v1_live/.env`
- **Dashboard**: http://localhost:8050 (monitor only - no trading controls)

## License

See LICENSE file in repository.
