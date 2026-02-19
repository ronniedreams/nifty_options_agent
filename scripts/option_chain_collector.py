"""
NIFTY Option Chain Daily Data Collector (Historify)

Downloads 1-min OHLCV data for the full NIFTY option chain after market close.
Stores in Historify DuckDB on EC2 for ML training and backtesting.

How it works:
1. Login to OpenAlgo dashboard (session cookie auth)
2. Discover upcoming NIFTY weekly expiries via Historify FNO API
3. Get all CE + PE strikes for each expiry
4. Bulk-add new symbols to Historify watchlist (idempotent)
5. Create incremental download job for today's 1-min data
6. Poll until job completes, send Telegram summary
7. Clean up expired expiry symbols from watchlist

Usage:
    # Manual run (after market close)
    python -m scripts.option_chain_collector

    # Preview only — no API calls made
    python -m scripts.option_chain_collector --dry-run

    # Force cleanup of expired expiry symbols
    python -m scripts.option_chain_collector --cleanup-only

EC2 Cron (3:35 PM IST = 10:05 UTC):
    35 15 * * 1-5 cd /home/ubuntu/nifty_options_agent && python3 -m scripts.option_chain_collector >> /home/ubuntu/nifty_options_agent/logs/option_chain_collector.log 2>&1

Requirements:
    pip install requests pytz python-dotenv
"""

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta

import pytz
import requests

# ---------------------------------------------------------------------------
# Load .env from nifty_options_agent repo root
# ---------------------------------------------------------------------------
_script_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_script_dir)
_env_path = os.path.join(_repo_root, ".env")

try:
    from dotenv import load_dotenv
    load_dotenv(_env_path)
except ImportError:
    # Fallback: manual .env parsing (no python-dotenv required)
    if os.path.exists(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(
                        _k.strip(), _v.strip().strip('"').strip("'")
                    )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OPENALGO_HOST = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000").rstrip("/")
OPENALGO_API_KEY = os.getenv("OPENALGO_API_KEY", "")

TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "EC2_PROD")

UNDERLYING = "NIFTY"
EXCHANGE = "NFO"
INTERVAL = "1m"
NUM_EXPIRIES = 2          # Current week + next week
JOB_POLL_INTERVAL = 30   # Seconds between job status checks
JOB_TIMEOUT = 3600       # Max seconds to wait for job (1 hour)

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> None:
    """Send Telegram notification (fire-and-forget, never raises)."""
    if not (TELEGRAM_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": f"[{INSTANCE_NAME}] {message}",
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


# ---------------------------------------------------------------------------
# Expiry helpers
# ---------------------------------------------------------------------------

def parse_expiry_date(expiry_str: str) -> date:
    """Parse Historify expiry string 'DD-MMM-YY' (e.g. '20-FEB-26') to date."""
    return datetime.strptime(expiry_str, "%d-%b-%y").date()


def get_upcoming_expiries(all_expiries: list, n: int = 2) -> list:
    """Return next N expiries >= today, sorted ascending."""
    today = date.today()
    upcoming = []
    for expiry in sorted(all_expiries, key=parse_expiry_date):
        if parse_expiry_date(expiry) >= today:
            upcoming.append(expiry)
            if len(upcoming) >= n:
                break
    return upcoming


def get_expired_expiries(all_expiries: list) -> list:
    """Return expiries that have already passed (expiry date < today)."""
    today = date.today()
    return [e for e in all_expiries if parse_expiry_date(e) < today]


# ---------------------------------------------------------------------------
# OpenAlgo Historify HTTP Client
# ---------------------------------------------------------------------------

class HistorifyClient:
    """
    HTTP client for OpenAlgo Historify APIs.

    Authenticates via X-API-Key header (requires OpenAlgo API key).
    No session/CSRF needed — the check_session_validity decorator in OpenAlgo
    accepts a valid API key as an alternative to browser session auth.
    """

    def __init__(self, host: str, api_key: str):
        self.host = host
        self.api_key = api_key
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "X-API-Key": api_key,
        })

    def login(self) -> bool:
        """Validate connectivity — make a lightweight test call to Historify."""
        if not self.api_key:
            logger.error("OPENALGO_API_KEY is not set in .env")
            return False
        try:
            # Use get_expiries as a connectivity check (lightweight, read-only)
            resp = self._session.get(
                f"{self.host}/historify/api/fno/expiries",
                params={"underlying": UNDERLYING, "exchange": EXCHANGE},
                timeout=15,
            )
            if resp.status_code == 200:
                logger.info(f"Connected to OpenAlgo Historify at {self.host}")
                return True
            logger.error(f"Historify connectivity check failed: HTTP {resp.status_code} — {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Historify connectivity check error: {e}")
        return False

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self._session.get(
            f"{self.host}{path}", params=params, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = self._session.post(
            f"{self.host}{path}", json=body, timeout=60
        )
        if not resp.ok:
            logger.error(f"POST {path} returned HTTP {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()
        return resp.json()

    # --- FNO Discovery ---

    def get_expiries(self) -> list:
        """GET /historify/api/fno/expiries — all NIFTY NFO expiries."""
        resp = self._get(
            "/historify/api/fno/expiries",
            params={"underlying": UNDERLYING, "exchange": EXCHANGE},
        )
        return resp.get("data", [])

    def get_chain(self, expiry: str) -> list:
        """GET /historify/api/fno/chain — all CE + PE symbols for expiry."""
        resp = self._get(
            "/historify/api/fno/chain",
            params={
                "underlying": UNDERLYING,
                "exchange": EXCHANGE,
                "expiry": expiry,
                "limit": 2000,
            },
        )
        return resp.get("data", [])

    # --- Watchlist ---

    def get_watchlist(self) -> list:
        """GET /historify/api/watchlist."""
        return self._get("/historify/api/watchlist").get("data", [])

    def bulk_add(self, symbols: list) -> dict:
        """POST /historify/api/watchlist/bulk."""
        return self._post("/historify/api/watchlist/bulk", {"symbols": symbols})

    def bulk_remove(self, symbols: list) -> dict:
        """POST /historify/api/watchlist/bulk/delete."""
        return self._post("/historify/api/watchlist/bulk/delete", {"symbols": symbols})

    # --- Download Jobs ---

    def create_job(self, symbols: list, start_date: str, end_date: str) -> str:
        """POST /historify/api/jobs — returns job_id."""
        resp = self._post(
            "/historify/api/jobs",
            {
                "job_type": "custom",
                "symbols": symbols,
                "interval": INTERVAL,
                "start_date": start_date,
                "end_date": end_date,
                "incremental": True,
                "api_key": self.api_key,  # Bypass session.get("user") for programmatic access
            },
        )
        return resp.get("job_id", "")

    def get_job(self, job_id: str) -> dict:
        """GET /historify/api/jobs/<job_id>."""
        return self._get(f"/historify/api/jobs/{job_id}").get("job", {})

    def wait_for_job(self, job_id: str) -> dict:
        """Poll job status until terminal state or timeout."""
        terminal = {"completed", "completed_with_errors", "failed", "cancelled"}
        deadline = time.time() + JOB_TIMEOUT

        while time.time() < deadline:
            try:
                job = self.get_job(job_id)
                status = job.get("status", "unknown")
                if status in terminal:
                    return job
                done = job.get("completed", 0)
                total = job.get("total_symbols", "?")
                logger.info(f"  Job {job_id}: {status} ({done}/{total} symbols)")
            except Exception as e:
                logger.warning(f"  Error polling job {job_id}: {e}")
            time.sleep(JOB_POLL_INTERVAL)

        logger.error(f"Job {job_id} timed out after {JOB_TIMEOUT}s")
        return {"status": "timeout", "completed": 0, "failed": 0}


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def run_collection(client: HistorifyClient, today_str: str) -> int:
    """
    Run the full collection workflow. Returns exit code (0=success, 1=error).
    """
    # 1. Fetch all NIFTY expiries
    logger.info("Step 1/5: Fetching NIFTY expiries...")
    try:
        all_expiries = client.get_expiries()
    except Exception as e:
        logger.error(f"Failed to fetch expiries: {e}")
        send_telegram(f"❌ <b>Option Chain Collector FAILED</b>\nCould not fetch expiries: {e}")
        return 1

    if not all_expiries:
        logger.error("No expiries returned from Historify FNO API")
        send_telegram("❌ <b>Option Chain Collector FAILED</b>\nNo NIFTY expiries found")
        return 1

    upcoming = get_upcoming_expiries(all_expiries, NUM_EXPIRIES)
    expired = get_expired_expiries(all_expiries)
    logger.info(f"  Upcoming: {upcoming}")
    logger.info(f"  Expired ({len(expired)}): {expired[:5]}{'...' if len(expired) > 5 else ''}")

    if not upcoming:
        logger.error("No upcoming expiries found")
        send_telegram("❌ <b>Option Chain Collector FAILED</b>\nNo upcoming expiries found")
        return 1

    # 2. Get chain symbols for upcoming expiries
    logger.info(f"Step 2/5: Fetching option chain for {len(upcoming)} expir(ies)...")
    all_chain_symbols = []
    expiry_stats = {}

    for expiry in upcoming:
        try:
            symbols = client.get_chain(expiry)
        except Exception as e:
            logger.error(f"  Failed to fetch chain for {expiry}: {e}")
            continue

        ce_count = sum(1 for s in symbols if s.get("instrumenttype") == "CE")
        pe_count = sum(1 for s in symbols if s.get("instrumenttype") == "PE")
        expiry_stats[expiry] = {"total": len(symbols), "ce": ce_count, "pe": pe_count}
        logger.info(f"  {expiry}: {len(symbols)} symbols ({ce_count} CE + {pe_count} PE)")
        all_chain_symbols.extend(symbols)

    if not all_chain_symbols:
        logger.error("No chain symbols found for any upcoming expiry")
        send_telegram("❌ <b>Option Chain Collector FAILED</b>\nNo chain symbols found")
        return 1

    logger.info(f"  Total: {len(all_chain_symbols)} symbols across {len(expiry_stats)} expir(ies)")

    # 3. Update watchlist (bulk add new symbols)
    logger.info("Step 3/5: Updating watchlist...")
    try:
        watchlist = client.get_watchlist()
    except Exception as e:
        logger.error(f"Failed to fetch watchlist: {e}")
        watchlist = []

    watchlist_set = {(w["symbol"], w["exchange"]) for w in watchlist}
    new_symbols = [
        {"symbol": s["symbol"], "exchange": s.get("exchange", EXCHANGE)}
        for s in all_chain_symbols
        if (s["symbol"], s.get("exchange", EXCHANGE)) not in watchlist_set
    ]

    watchlist_added = 0
    if new_symbols:
        logger.info(f"  Adding {len(new_symbols)} new symbols (skipping {len(all_chain_symbols) - len(new_symbols)} existing)...")
        try:
            result = client.bulk_add(new_symbols)
            watchlist_added = result.get("added", 0)
            skipped = result.get("skipped", 0)
            failed = result.get("failed", [])
            logger.info(f"  Watchlist: {watchlist_added} added, {skipped} skipped, {len(failed)} failed")
        except Exception as e:
            logger.error(f"  Bulk add failed: {e}")
    else:
        logger.info("  All symbols already in watchlist, nothing to add")

    # 4. Create download job for today
    logger.info(f"Step 4/5: Creating download job for {today_str}...")
    download_symbols = [
        {"symbol": s["symbol"], "exchange": s.get("exchange", EXCHANGE)}
        for s in all_chain_symbols
    ]

    try:
        job_id = client.create_job(download_symbols, today_str, today_str)
    except Exception as e:
        logger.error(f"Failed to create download job: {e}")
        send_telegram(
            f"❌ <b>Option Chain Collector FAILED</b>\n"
            f"Date: {today_str}\n"
            f"Could not create download job: {e}"
        )
        return 1

    if not job_id:
        logger.error("create_job returned empty job_id")
        send_telegram(
            f"❌ <b>Option Chain Collector FAILED</b>\n"
            f"Date: {today_str}\nEmpty job_id from API"
        )
        return 1

    logger.info(f"  Job created: {job_id} ({len(download_symbols)} symbols, interval: {INTERVAL})")
    logger.info(f"  Polling every {JOB_POLL_INTERVAL}s (timeout: {JOB_TIMEOUT}s)...")

    job_result = client.wait_for_job(job_id)
    final_status = job_result.get("status", "unknown")
    completed_count = job_result.get("completed", 0)
    failed_count = job_result.get("failed", 0)

    logger.info(
        f"  Job {job_id} done: {final_status} "
        f"({completed_count} ok, {failed_count} failed)"
    )

    # 5. Cleanup expired symbols from watchlist
    logger.info("Step 5/5: Cleaning up expired expiry symbols...")
    removed_count = 0
    if expired and watchlist:
        # Get chain symbols for each expired expiry to find what to remove
        expired_symbols_set = set()
        for exp in expired:
            try:
                exp_chain = client.get_chain(exp)
                for s in exp_chain:
                    expired_symbols_set.add((s["symbol"], s.get("exchange", EXCHANGE)))
            except Exception as e:
                logger.warning(f"  Could not fetch chain for expired {exp}: {e}")

        if expired_symbols_set:
            to_remove = [
                {"symbol": w["symbol"], "exchange": w["exchange"]}
                for w in watchlist
                if (w["symbol"], w["exchange"]) in expired_symbols_set
            ]
            if to_remove:
                logger.info(f"  Removing {len(to_remove)} expired symbols from watchlist...")
                try:
                    result = client.bulk_remove(to_remove)
                    removed_count = result.get("removed", 0)
                    logger.info(f"  Removed {removed_count} expired symbols")
                except Exception as e:
                    logger.warning(f"  Bulk remove failed: {e}")
            else:
                logger.info("  No expired symbols found in current watchlist")
        else:
            logger.info("  No expired chain symbols found (master contract may already be updated)")
    else:
        logger.info("  Nothing to clean up")

    # --- Summary ---
    expiry_line = " | ".join(
        f"{e}: {s['total']} ({s['ce']}CE+{s['pe']}PE)"
        for e, s in expiry_stats.items()
    )
    status_emoji = "✅" if final_status == "completed" else "⚠️"
    status_plain = "OK" if final_status == "completed" else "WARN"
    cleanup_line = f"\nCleaned up: {removed_count} expired symbols" if removed_count > 0 else ""

    summary = (
        f"{status_emoji} <b>Option Chain Collected</b>\n\n"
        f"Date: {today_str}\n"
        f"Expiries: {expiry_line}\n"
        f"Downloaded: {completed_count}/{len(download_symbols)}"
        + (f" ✅ | Failed: {failed_count} ❌" if failed_count > 0 else " ✅")
        + f"\nJob: {job_id} ({final_status})"
        + cleanup_line
        + f"\nStorage: DuckDB (Historify)"
    )

    # Plain-text version for logger (no emojis — Rule 16)
    summary_plain = (
        f"[{status_plain}] Option Chain Collected\n"
        f"Date: {today_str}\n"
        f"Expiries: {expiry_line}\n"
        f"Downloaded: {completed_count}/{len(download_symbols)}"
        + (f" | Failed: {failed_count}" if failed_count > 0 else " | OK")
        + f"\nJob: {job_id} ({final_status})"
        + cleanup_line
        + f"\nStorage: DuckDB (Historify)"
    )

    logger.info("=" * 60)
    logger.info(summary_plain)
    logger.info("=" * 60)
    send_telegram(summary)

    # Non-zero exit if job failed/timed out
    if final_status not in ("completed", "completed_with_errors"):
        return 1
    return 0


def run_dry(client: HistorifyClient) -> int:
    """Dry run: fetch and print symbols without making any changes."""
    logger.info("[DRY RUN] Fetching NIFTY expiries (read-only)...")
    # Login still needed to call Historify endpoints
    if not client.login():
        logger.error("Could not login — cannot fetch chain data")
        return 1

    try:
        all_expiries = client.get_expiries()
    except Exception as e:
        logger.error(f"Failed to fetch expiries: {e}")
        return 1

    upcoming = get_upcoming_expiries(all_expiries, NUM_EXPIRIES)
    logger.info(f"Upcoming expiries: {upcoming}")

    total = 0
    for expiry in upcoming:
        try:
            symbols = client.get_chain(expiry)
        except Exception as e:
            logger.error(f"  Failed to fetch chain for {expiry}: {e}")
            continue
        ce = sum(1 for s in symbols if s.get("instrumenttype") == "CE")
        pe = sum(1 for s in symbols if s.get("instrumenttype") == "PE")
        logger.info(f"  {expiry}: {len(symbols)} symbols ({ce} CE + {pe} PE)")
        total += len(symbols)

    logger.info(f"[DRY RUN] Total symbols that would be added/downloaded: {total}")
    logger.info("[DRY RUN] No changes made")
    return 0


def run_cleanup_only(client: HistorifyClient) -> int:
    """Only run the expired expiry cleanup step."""
    logger.info("[CLEANUP] Cleaning up expired expiry symbols from watchlist...")
    if not client.login():
        return 1

    try:
        all_expiries = client.get_expiries()
        expired = get_expired_expiries(all_expiries)
        watchlist = client.get_watchlist()
    except Exception as e:
        logger.error(f"Failed to fetch data: {e}")
        return 1

    logger.info(f"Expired expiries ({len(expired)}): {expired[:10]}")
    logger.info(f"Watchlist size: {len(watchlist)}")

    expired_symbols_set = set()
    for exp in expired:
        try:
            exp_chain = client.get_chain(exp)
            for s in exp_chain:
                expired_symbols_set.add((s["symbol"], s.get("exchange", EXCHANGE)))
        except Exception as e:
            logger.warning(f"  Could not fetch chain for {exp}: {e}")

    to_remove = [
        {"symbol": w["symbol"], "exchange": w["exchange"]}
        for w in watchlist
        if (w["symbol"], w["exchange"]) in expired_symbols_set
    ]

    if not to_remove:
        logger.info("Nothing to remove")
        return 0

    logger.info(f"Removing {len(to_remove)} expired symbols...")
    try:
        result = client.bulk_remove(to_remove)
        logger.info(f"Removed: {result.get('removed', 0)}, Skipped: {result.get('skipped', 0)}")
    except Exception as e:
        logger.error(f"Bulk remove failed: {e}")
        return 1

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="NIFTY Option Chain Daily Data Collector"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print symbols only, no watchlist/job changes",
    )
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Only remove expired expiry symbols from watchlist",
    )
    args = parser.parse_args()

    today_ist = datetime.now(IST)
    today_str = today_ist.strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("NIFTY Option Chain Collector")
    logger.info(f"Date (IST): {today_str} {today_ist.strftime('%H:%M:%S')}")
    logger.info(f"OpenAlgo: {OPENALGO_HOST}")
    logger.info(
        "Mode: "
        + ("DRY RUN" if args.dry_run else "CLEANUP ONLY" if args.cleanup_only else "LIVE")
    )
    logger.info("=" * 60)

    client = HistorifyClient(OPENALGO_HOST, OPENALGO_API_KEY)

    if args.dry_run:
        exit_code = run_dry(client)
    elif args.cleanup_only:
        exit_code = run_cleanup_only(client)
    else:
        if not client.login():
            logger.error("Cannot login to OpenAlgo. Aborting.")
            send_telegram(
                "❌ <b>Option Chain Collector FAILED</b>\n"
                f"Date: {today_str}\nCould not login to OpenAlgo at {OPENALGO_HOST}"
            )
            sys.exit(1)
        exit_code = run_collection(client, today_str)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
