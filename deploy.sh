#!/bin/bash
# Deploy script for nifty_options_agent
# Usage: ./deploy.sh

set -e

echo "========================================"
echo "Deploying nifty_options_agent"
echo "========================================"

cd ~/nifty_options_agent

echo "[1/5] Creating persistent data directories..."
mkdir -p data/openalgo_db data/openalgo_logs data/openalgo_angelone_db data/openalgo_angelone_logs data/trading_state

echo "[2/5] Pulling latest code from GitHub..."
git pull origin main

echo "[3/5] Building Docker images..."
docker-compose build

echo "[4/5] Restarting containers..."
docker-compose down
docker-compose up -d

echo "[5/5] Waiting for services to start..."
sleep 15

echo "========================================"
echo "Deployment complete! Container status:"
echo "========================================"
docker-compose ps

echo ""
echo "URLs:"
echo "  OpenAlgo: https://openalgo.ronniedreams.in"
echo "  Monitor:  https://monitor.ronniedreams.in"
