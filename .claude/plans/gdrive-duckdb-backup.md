# Plan: Weekly DuckDB Backup to Google Drive

## Context
The Historify DuckDB database on EC2 (`~/nifty_options_agent/data/openalgo_db/historify.duckdb`) grows ~110 MB/day (782 MB for 7 days). It stores 1-minute option chain candle data that cannot be re-downloaded. Backups protect against data loss from EC2 issues, accidental deletion, or disk failure.

## Strategy
- **Mon-Thu**: Upload as `historify_latest.duckdb.gz` (overwrite same file — always 1 recent copy)
- **Friday**: Upload as `historify_2026-02-28.duckdb.gz` (dated weekly snapshot — kept permanently)
- **Storage**: Google Drive (2 TB available), destination folder: `nifty-backups/`
- **Format**: DuckDB + gzip (~200 MB per copy). Can convert to Parquet later if needed.

## Current State
- **EC2**: No `rclone` installed, no Google Drive configured
- **Crontab**: 2 existing jobs (container monitor every 2min, option chain collector at 3:35 PM Mon-Fri)
- **DB location**: `~/nifty_options_agent/data/openalgo_db/historify.duckdb` (782 MB as of 2026-02-25)

## Implementation Plan

### Step 1: Install rclone on EC2
```bash
sudo apt-get install -y rclone
```

### Step 2: Configure rclone with Google Drive (one-time)
```bash
rclone config
# Follow prompts: name=gdrive, type=drive, OAuth token
```
Note: OAuth requires a browser. Use `rclone authorize` on laptop, then paste token on EC2.

### Step 3: Create backup script on EC2
**File**: `~/nifty_options_agent/scripts/backup_duckdb.sh`

```bash
#!/bin/bash
# Weekly DuckDB backup to Google Drive
# Mon-Thu: overwrite historify_latest.duckdb.gz
# Friday: create dated weekly snapshot historify_YYYY-MM-DD.duckdb.gz

set -e

DB_PATH="$HOME/nifty_options_agent/data/openalgo_db/historify.duckdb"
GDRIVE_DIR="nifty-backups"
DAY_OF_WEEK=$(date +%u)  # 1=Mon, 5=Fri

# Compress
TEMP_FILE="/tmp/historify_backup.duckdb.gz"
gzip -c "$DB_PATH" > "$TEMP_FILE"
SIZE=$(du -h "$TEMP_FILE" | cut -f1)

# Upload
if [ "$DAY_OF_WEEK" -eq 5 ]; then
    # Friday: dated weekly snapshot
    DATE=$(date +%Y-%m-%d)
    DEST_NAME="historify_${DATE}.duckdb.gz"
    rclone copyto "$TEMP_FILE" "gdrive:${GDRIVE_DIR}/${DEST_NAME}"
    echo "[$(date)] Weekly backup uploaded: ${DEST_NAME} (${SIZE})"
else
    # Mon-Thu: overwrite latest
    DEST_NAME="historify_latest.duckdb.gz"
    rclone copyto "$TEMP_FILE" "gdrive:${GDRIVE_DIR}/${DEST_NAME}"
    echo "[$(date)] Daily backup uploaded: ${DEST_NAME} (${SIZE})"
fi

# Clean up
rm -f "$TEMP_FILE"
```

### Step 4: Add cron job
Run at **4:00 PM IST Mon-Fri** (after option chain collector at 3:35 PM, before EC2 shutdown at 4:30 PM):
```
0 16 * * 1-5 bash ~/nifty_options_agent/scripts/backup_duckdb.sh >> ~/nifty_options_agent/logs/duckdb_backup.log 2>&1
```

## Storage Estimate
- `historify_latest.duckdb.gz`: ~200 MB (constant, overwritten daily)
- Weekly snapshots: ~200 MB × 52 weeks/year = ~10 GB/year
- Google Drive: 2 TB available — decades of headroom

## Manual Steps Required (one-time setup)
1. Install rclone on EC2
2. Authorize rclone with Google Drive (OAuth flow — needs browser on laptop)
3. Test: `rclone lsd gdrive:` to verify connection

## Automated Steps (via SSH)
1. Create backup script
2. Add cron job
3. Test backup end-to-end

## Verification
1. Run backup script manually, confirm file appears in Google Drive
2. Download backup, decompress, verify DuckDB opens: `duckdb historify.duckdb "SELECT COUNT(*) FROM market_data"`
3. Confirm cron runs next trading day at 4:00 PM

## Future: Parquet Conversion (optional, on laptop)
```sql
-- Open the backup DuckDB and export to Parquet
COPY market_data TO 'market_data.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);
```
