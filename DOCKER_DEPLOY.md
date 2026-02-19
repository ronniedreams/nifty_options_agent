# Docker Deployment Guide for Baseline V1 Live

## Prerequisites on EC2 Ubuntu Instance

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
newgrp docker

# Install Docker Compose
sudo apt install docker-compose-plugin -y

# Verify installation
docker --version
docker compose version
```

## Initial Setup

### 1. Clone Repository

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/nifty_options_agent.git
cd nifty_options_agent
```

### 2. Configure Environment

```bash
# Copy sample env file
cp .env.sample baseline_v1_live/.env

# Edit with your actual credentials
nano baseline_v1_live/.env
```

**Critical Variables to Update:**
- `OPENALGO_API_KEY` - Your OpenAlgo API key
- `PAPER_TRADING` - Keep as `true` for initial testing
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` - For notifications
- `EXPIRY` - Weekly expiry (format: DDMMMYY, e.g., 30JAN26)
- `ATM` - Current NIFTY ATM strike

### 3. Create Required Directories

```bash
mkdir -p logs state
chmod 755 logs state
```

## Running the System

### Start Everything

```bash
# Build and start all services
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f trading_agent
```

### Stop Everything

```bash
docker compose down
```

### Restart After Code Changes

```bash
# Pull latest code
git pull origin main

# Rebuild and restart
docker compose up -d --build
```

## Daily Operations

### Pre-Market Checklist (9:00 AM IST)

1. **Update Expiry & ATM in `.env`:**
   ```bash
   nano baseline_v1_live/.env
   # Update EXPIRY=30JAN26
   # Update ATM=23500
   ```

2. **Restart with new parameters:**
   ```bash
   docker compose restart trading_agent
   ```

3. **Verify system health:**
   ```bash
   docker compose logs --tail=50 trading_agent
   ```

### Monitoring

```bash
# Live logs
docker compose logs -f trading_agent

# Check container health
docker ps

# Access monitor dashboard (if enabled)
# Open browser: http://YOUR_EC2_IP:8050

# Check resource usage
docker stats
```

### Emergency Stop

```bash
# Stop trading agent only (keeps OpenAlgo running)
docker compose stop trading_agent

# Stop everything
docker compose down
```

## Production Deployment

### Switch to Live Trading

**ONLY after 1 week of successful paper trading:**

1. Edit `.env`:
   ```bash
   nano baseline_v1_live/.env
   # Change: PAPER_TRADING=false
   ```

2. Restart:
   ```bash
   docker compose restart trading_agent
   ```

3. **Monitor first 30 minutes closely!**

### Security Hardening

1. **Firewall rules:**
   ```bash
   # Allow only SSH and monitoring dashboard
   sudo ufw allow 22/tcp
   sudo ufw allow 8050/tcp
   sudo ufw enable
   ```

2. **Restrict OpenAlgo to localhost:**
   In `docker-compose.yaml`, change:
   ```yaml
   ports:
     - "127.0.0.1:5000:5000"  # Only accessible from localhost
   ```

3. **Regular backups:**
   ```bash
   # Backup state database
   docker cp baseline_v1_live:/app/state/live_state.db ./backups/live_state_$(date +%Y%m%d).db
   ```

## Troubleshooting

### Container won't start

```bash
# Check logs
docker compose logs trading_agent

# Check environment
docker compose exec trading_agent env

# Rebuild from scratch (safe — data is in bind mounts under ./data/)
docker compose down
docker compose build --no-cache
docker compose up -d
```

### OpenAlgo connection issues

```bash
# Verify OpenAlgo is running
docker compose ps openalgo
curl http://localhost:5000/

# Check network
docker compose exec trading_agent ping openalgo
```

### State database locked

```bash
# Stop agent, backup, restart
docker compose stop trading_agent
docker cp baseline_v1_live:/app/state/live_state.db ./backup.db
docker compose start trading_agent
```

## GitHub Workflow

### Push Changes

```bash
# NEVER commit .env files!
git status  # Verify .env not in list

git add .
git commit -m "Update trading logic"
git push origin main
```

### Pull and Deploy on EC2

```bash
# On EC2 instance
cd ~/nifty_options_agent
git pull origin main
docker compose up -d --build
```

## Volume Management

### Data Persistence

State and logs are stored in bind-mounted host directories under `./data/`:
- `./data/trading_state/`: SQLite database (positions, orders, swings)
- `./data/openalgo_db/`: Historify DuckDB + OpenAlgo databases
- `./data/openalgo_angelone_db/`: Angel One OpenAlgo databases
- `./baseline_v1_live/logs/`: Application logs (mounted from host)

### Backup State

```bash
# Manual backup (simple — data is on host filesystem)
cp -r data/ ~/data_backup_$(date +%Y%m%d_%H%M%S)/

# Restore backup
cp -r ~/data_backup_YYYYMMDD_HHMMSS/* data/
```

## Maintenance

### Update Docker Images

```bash
# Pull latest OpenAlgo image
docker compose pull openalgo

# Rebuild trading agent
docker compose build --no-cache trading_agent

# Restart
docker compose up -d
```

### Clean Up

```bash
# Remove stopped containers
docker compose down

# Remove unused images
docker image prune -a

# Remove unused volumes (CAUTION: loses state)
docker volume prune
```

## Performance Optimization

### Resource Limits

Add to `docker-compose.yaml`:
```yaml
services:
  trading_agent:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 1G
```

### Logging Optimization

Already configured in docker-compose:
- Max log size: 50MB
- Max log files: 5 (rotation)

## Support

- Logs: `docker compose logs -f trading_agent`
- State DB: `docker cp baseline_v1_live:/app/state/live_state.db ./local_copy.db`
- Monitor Dashboard: `http://YOUR_EC2_IP:8050`
