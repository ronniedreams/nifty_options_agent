#!/bin/bash
# =============================================================================
# One-time migration: Named Docker volumes → Host bind mounts
# =============================================================================
# Run this ONCE on EC2 before switching to the new docker-compose.yaml.
# After migration, data lives in ./data/ and survives any Docker cleanup.
#
# Usage: cd ~/nifty_options_agent && bash migrate_volumes.sh
# =============================================================================

set -e

echo "========================================"
echo "Migrating Docker named volumes to bind mounts"
echo "========================================"

cd ~/nifty_options_agent

# Create target directories
echo "[1/6] Creating bind mount directories..."
mkdir -p data/openalgo_db data/openalgo_logs data/openalgo_angelone_db data/openalgo_angelone_logs data/trading_state

# Check if containers are running (we need them running to docker cp)
if ! docker ps --format '{{.Names}}' | grep -q '^openalgo$'; then
    echo "[WARN] openalgo container not running. Starting temporarily..."
    docker-compose up -d openalgo
    sleep 10
fi

# Copy data from named volumes via running containers
echo "[2/6] Copying Zerodha OpenAlgo DB (includes historify.duckdb)..."
docker cp openalgo:/app/db/. data/openalgo_db/
echo "  Files copied:"
ls -lh data/openalgo_db/

echo "[3/6] Copying Zerodha OpenAlgo logs..."
docker cp openalgo:/app/log/. data/openalgo_logs/ 2>/dev/null || echo "  (no logs to copy)"

echo "[4/6] Copying Angel One OpenAlgo DB..."
if docker ps --format '{{.Names}}' | grep -q '^openalgo_angelone$'; then
    docker cp openalgo_angelone:/app/db/. data/openalgo_angelone_db/
    echo "  Files copied:"
    ls -lh data/openalgo_angelone_db/
else
    echo "  (angelone container not running, skipping)"
fi

echo "[5/6] Copying Angel One OpenAlgo logs..."
if docker ps --format '{{.Names}}' | grep -q '^openalgo_angelone$'; then
    docker cp openalgo_angelone:/app/log/. data/openalgo_angelone_logs/ 2>/dev/null || echo "  (no logs to copy)"
else
    echo "  (angelone container not running, skipping)"
fi

echo "[6/6] Copying trading state DB..."
if docker ps --format '{{.Names}}' | grep -q '^baseline_v1_live$'; then
    docker cp baseline_v1_live:/app/state/. data/trading_state/
    echo "  Files copied:"
    ls -lh data/trading_state/
else
    echo "  (trading agent not running, skipping)"
fi

echo ""
echo "========================================"
echo "Migration complete!"
echo "========================================"
echo ""
echo "Verify historify.duckdb exists:"
ls -lh data/openalgo_db/historify.duckdb 2>/dev/null || echo "  WARNING: historify.duckdb not found!"
echo ""
echo "Next steps:"
echo "  1. docker-compose down"
echo "  2. git pull origin <branch>   (to get the new docker-compose.yaml)"
echo "  3. docker-compose up -d"
echo ""
echo "Old named volumes are still intact. To reclaim space after verifying:"
echo "  docker volume rm nifty_options_agent_openalgo_data nifty_options_agent_openalgo_logs"
echo "  docker volume rm nifty_options_agent_openalgo_angelone_data nifty_options_agent_openalgo_angelone_logs"
echo "  docker volume rm nifty_options_agent_trading_state"
