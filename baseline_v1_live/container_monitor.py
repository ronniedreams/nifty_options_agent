"""
Container Health Monitor

Monitors Docker container status and sends Telegram alerts if containers crash.
Runs as a background process on EC2 to detect container failures.

Usage:
    python -m baseline_v1_live.container_monitor

Or run with cron every 2 minutes:
    */2 * * * * cd ~/nifty_options_agent && python -m baseline_v1_live.container_monitor >> /var/log/container_monitor.log 2>&1
"""

import logging
import subprocess
import time
import os
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')

# Setup logging
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'container_monitor.log')
handler = logging.FileHandler(log_file)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# State file to prevent alert spam (only alert once per crash)
STATE_FILE = os.path.join(log_dir, 'container_health_state.txt')


def get_container_status():
    """
    Get Docker Compose container status

    Returns: dict with container names as keys and status as values
    """
    try:
        # Run docker-compose ps to get container status
        result = subprocess.run(
            ['docker-compose', 'ps', '--format', 'json'],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            logger.error(f"docker-compose ps failed: {result.stderr}")
            return None

        import json
        # docker-compose v2 returns NDJSON (one JSON object per line)
        # docker-compose v1 returns a JSON array — handle both
        raw = result.stdout.strip()
        try:
            parsed = json.loads(raw)
            # v1: parsed is a list
            containers = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            # v2: NDJSON — parse line by line
            containers = [json.loads(line) for line in raw.splitlines() if line.strip()]

        status = {}
        for container in containers:
            name = container.get('Name', 'unknown')
            state = container.get('State', 'unknown')
            status[name] = state

        return status

    except subprocess.TimeoutExpired:
        logger.error("docker-compose ps timed out")
        return None
    except Exception as e:
        logger.error(f"Failed to get container status: {e}")
        return None


def send_telegram_alert(message: str):
    """Send Telegram alert"""
    try:
        from .config import TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

        if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("Telegram not configured, skipping alert")
            return False

        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
        }

        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("Telegram alert sent successfully")
            return True
        else:
            logger.error(f"Telegram API error: {response.status_code}")
            return False

    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
        return False


def load_previous_state():
    """Load previous container states from file"""
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        state = {}
        with open(STATE_FILE, 'r') as f:
            for line in f:
                parts = line.strip().split('=')
                if len(parts) == 2:
                    state[parts[0]] = parts[1]
        return state
    except Exception as e:
        logger.error(f"Failed to load state file: {e}")
        return {}


def save_state(status: dict):
    """Save current container states to file"""
    try:
        with open(STATE_FILE, 'w') as f:
            for name, state in status.items():
                f.write(f"{name}={state}\n")
    except Exception as e:
        logger.error(f"Failed to save state file: {e}")


def check_container_health():
    """
    Main health check function

    Compares current container status with previous state and alerts on changes
    """
    logger.info("=== Container Health Check ===")

    # Get current status
    current_status = get_container_status()
    if current_status is None:
        logger.error("Failed to get container status")
        return

    logger.info(f"Current status: {current_status}")

    # Load previous state
    previous_state = load_previous_state()
    logger.info(f"Previous state: {previous_state}")

    # Check for changes
    alerts = []

    # Check for crashed containers (state changed from "Up" to something else)
    for container_name, current_state in current_status.items():
        previous_state_val = previous_state.get(container_name, 'unknown')

        # Container crashed (was running, now not)
        if previous_state_val == 'Up' and current_state != 'Up':
            ec2_host = os.environ.get('EC2_HOST', '<EC2_HOST>')
            alert = (
                f"[CONTAINER_CRASH] {container_name} crashed!\n"
                f"Previous: {previous_state_val}\n"
                f"Current: {current_state}\n"
                f"Time: {datetime.now(IST).strftime('%H:%M:%S %Z')}\n\n"
                f"Debug instructions:\n"
                f"1. SSH into EC2: {ec2_host}\n"
                f"2. Check logs: docker-compose logs -f {container_name}\n"
                f"3. Restart: docker-compose restart {container_name}\n"
                f"4. Or rebuild: docker-compose down && docker-compose up -d"
            )
            alerts.append(alert)
            logger.warning(f"Container crash detected: {container_name}")

        # Container recovered (was down, now up)
        elif previous_state_val != 'Up' and current_state == 'Up':
            alert = f"[CONTAINER_RECOVERED] {container_name} is now running!"
            alerts.append(alert)
            logger.info(f"Container recovered: {container_name}")

    # Send alerts via Telegram
    for alert in alerts:
        logger.info(f"Sending alert: {alert}")
        send_telegram_alert(alert)

    # Save current state for next check
    save_state(current_status)

    if not alerts:
        logger.info("All containers healthy")


def main():
    """Main entry point"""
    try:
        logger.info(f"Container Monitor started at {datetime.now(IST).strftime('%H:%M:%S %Z')}")
        check_container_health()
        logger.info("Container Monitor check completed")
    except Exception as e:
        logger.error(f"Container Monitor failed: {e}", exc_info=True)
        # Try to send alert about the monitor itself failing
        try:
            send_telegram_alert(f"[CRITICAL] Container Monitor failed: {str(e)[:100]}")
        except:
            pass


if __name__ == '__main__':
    main()
