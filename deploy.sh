#!/bin/bash
# Deploy script for nifty_options_agent
# Usage: ./deploy.sh

set -e

echo "========================================"
echo "Deploying nifty_options_agent"
echo "========================================"

cd ~/nifty_options_agent

echo "[1/4] Pulling latest code from GitHub..."
git pull origin main

echo "[2/4] Building Docker images..."
docker-compose build

echo "[3/4] Restarting containers..."
docker-compose down
docker-compose up -d

echo "[4/4] Waiting for services to start..."
sleep 15

echo "========================================"
echo "Deployment complete! Container status:"
echo "========================================"
docker-compose ps

echo ""
echo "URLs:"
echo "  OpenAlgo: https://openalgo.ronniedreams.in"
echo "  Monitor:  https://monitor.ronniedreams.in"
