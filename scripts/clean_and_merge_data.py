"""
NIFTY Options Data Cleaner & Merger

Combines two data sources into a single Parquet file for ML training:
1. Dinesh CSVs (Jan 2022 - Mar 2023): ~29K per-symbol CSV files
2. rl_dataset_v2_with_spot (Feb 2023 - Nov 2023): single 649MB CSV

For the overlap period (Feb 27 - Mar 2023): prefers rl_dataset_v2_with_spot.

Usage:
    python -m scripts.clean_and_merge_data                    # Full run
    python -m scripts.clean_and_merge_data --dry-run          # Stats only
    python -m scripts.clean_and_merge_data --dinesh-only      # Dinesh data only
    python -m scripts.clean_and_merge_data --output data/custom.parquet
"""

import argparse
import calendar
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DINESH_BASE_DIR = Path(r"D:\marketcalls\options_agent\nifty_data_by_dinesh")
RL_DATASET_PATH = Path(r"D:\nifty_options_agent\data\rl_dataset_v2_with_spot.csv")
SPOT_DATA_PATH = DINESH_BASE_DIR / "Nifty lndex" / "NIFTY 50_minute.csv"
DEFAULT_OUTPUT = Path(r"D:\nifty_options_agent\data\nifty_options_combined.parquet")

CUTOFF_DATE = "2023-02-27"  # rl_dataset_v2 starts here; Dinesh data kept before this

MONTH_CODES = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Regex to identify NIFTY option files (not index files like NIFTY100, NIFTY500)
NIFTY_OPTION_RE = re.compile(r"^NIFTY\d{2}")

# Regex to strip (2), (3) etc. suffix from filenames
VARIANT_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")


# ---------------------------------------------------------------------------
# Step 1: Filename Parser
# ---------------------------------------------------------------------------

def last_thursday(year: int, month: int) -> date:
    """Find the last Thursday of a given month/year."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 3:  # 3 = Thursday
        d = d.replace(day=d.day - 1)
    return d


def parse_nifty_option_filename(filename: str):
    """
    Parse a NIFTY option filename to extract (expiry_date, strike, option_type).

    Examples:
        NIFTY2210615100CE.csv      -> (date(2022,1,6), 15100, 'CE')
        NIFTY22FEB17500PE.csv      -> (date(2022,2,24), 17500, 'PE')  (last Thursday)
        NIFTY2210617500CE (3).csv  -> (date(2022,1,6), 17500, 'CE')
        NIFTY22100614400PE.csv     -> (date(2022,10,6), 14400, 'PE')

    Returns (expiry_date, strike, option_type) or None if unparseable.
    """
    # Strip extension and variant suffix
    name = filename
    if name.lower().endswith(".csv"):
        name = name[:-4]
    name = VARIANT_SUFFIX_RE.sub("", name).strip()

    if not name.startswith("NIFTY"):
        return None

    body = name[5:]  # strip "NIFTY"

    # Extract 2-digit year
    if len(body) < 2 or not body[:2].isdigit():
        return None
    year = 2000 + int(body[:2])
    body = body[2:]

    # Extract CE/PE from end
    if body.endswith("CE"):
        option_type = "CE"
        body = body[:-2]
    elif body.endswith("PE"):
        option_type = "PE"
        body = body[:-2]
    else:
        return None

    # Remaining body = expiry_encoding + strike_digits

    # Try 1: Monthly expiry (3-letter month code)
    if len(body) >= 3 and body[:3].upper() in MONTH_CODES:
        month = MONTH_CODES[body[:3].upper()]
        strike_str = body[3:]
        if not strike_str.isdigit() or len(strike_str) == 0:
            return None
        strike = int(strike_str)
        expiry_date = last_thursday(year, month)
        return (expiry_date, strike, option_type)

    # Remaining body must be all digits
    if not body.isdigit():
        return None

    # Try 2: Double-digit month (01-12) + 2-digit day + strike
    # Format: MMDD + strike (at least 4 chars for MMDD)
    # Handles: months 10-12 always, and zero-padded months 01-09 (seen in 2023 data)
    if len(body) >= 5:  # need at least MMDD + 1 strike digit
        mm = int(body[:2])
        dd = int(body[2:4])
        strike_str = body[4:]
        if 1 <= mm <= 12 and 1 <= dd <= 31 and len(strike_str) > 0:
            strike = int(strike_str)
            if strike >= 5000:  # valid NIFTY strike
                try:
                    expiry_date = date(year, mm, dd)
                    return (expiry_date, strike, option_type)
                except ValueError:
                    pass  # invalid date, try next pattern

    # Try 3: Single-digit month (1-9) + 2-digit day + strike
    # Format: MDD + strike (at least 3 chars for MDD)
    if len(body) >= 4:  # need at least MDD + 1 strike digit
        m = int(body[0])
        dd = int(body[1:3])
        strike_str = body[3:]
        if 1 <= m <= 9 and 1 <= dd <= 31 and len(strike_str) > 0:
            strike = int(strike_str)
            if strike >= 5000:  # valid NIFTY strike
                try:
                    expiry_date = date(year, m, dd)
                    return (expiry_date, strike, option_type)
                except ValueError:
                    pass

    return None


def expiry_to_yymmdd(expiry_date: date) -> str:
    """Convert a date to YYMMDD string format matching rl_dataset_v2."""
    return expiry_date.strftime("%y%m%d")


# ---------------------------------------------------------------------------
# Step 2: File Discovery
# ---------------------------------------------------------------------------

def discover_dinesh_files(base_dir: Path) -> list:
    """
    Recursively find all NIFTY option CSVs across all 'Options *' directories.

    Returns list of (filepath, expiry_date, strike, option_type).
    Filters out non-NIFTY files (stock options, index files).
    """
    results = []
    skipped = 0

    options_dirs = sorted(base_dir.glob("Options */"))
    print(f"Found {len(options_dirs)} 'Options *' directories")

    for options_dir in options_dirs:
        dir_count = 0
        # Walk recursively to handle nested dirs (Jul/Aug 2022)
        for root, _dirs, files in os.walk(options_dir):
            for fname in files:
                if not fname.lower().endswith(".csv"):
                    continue

                # Filter: must match NIFTY + 2 digits (not NIFTY100, NIFTY500 etc.)
                base_name = VARIANT_SUFFIX_RE.sub("", fname.replace(".csv", "").replace(".CSV", "")).strip()
                if not NIFTY_OPTION_RE.match(base_name):
                    skipped += 1
                    continue

                parsed = parse_nifty_option_filename(fname)
                if parsed is None:
                    skipped += 1
                    continue

                expiry_date, strike, option_type = parsed
                filepath = Path(root) / fname
                results.append((filepath, expiry_date, strike, option_type))
                dir_count += 1

        print(f"  {options_dir.name}: {dir_count} NIFTY option files")

    print(f"Total discovered: {len(results)} files ({skipped} skipped)")
    return results


# ---------------------------------------------------------------------------
# Step 3: Load & Transform Dinesh Data
# ---------------------------------------------------------------------------

def load_dinesh_csv(filepath: Path, expiry_date: date, strike: int, option_type: str) -> pd.DataFrame:
    """Load a single Dinesh CSV and transform to target format."""
    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        print(f"  WARNING: Failed to read {filepath}: {e}")
        return pd.DataFrame()

    if len(df) == 0:
        return pd.DataFrame()

    # Normalize column names (handle angle brackets and variations)
    col_map = {}
    for col in df.columns:
        col_clean = col.strip().lower().replace("<", "").replace(">", "")
        if col_clean == "ticker":
            col_map[col] = "ticker"
        elif col_clean == "date":
            col_map[col] = "date"
        elif col_clean == "time":
            col_map[col] = "time"
        elif col_clean == "open":
            col_map[col] = "Open"
        elif col_clean == "high":
            col_map[col] = "High"
        elif col_clean == "low":
            col_map[col] = "Low"
        elif col_clean == "close":
            col_map[col] = "Close"
        elif col_clean == "volume":
            col_map[col] = "Volume"
        elif col_clean in ("o/i", "oi"):
            col_map[col] = "Open Interest"

    df = df.rename(columns=col_map)

    # Combine date + time into Datetime
    try:
        df["Datetime"] = pd.to_datetime(
            df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip(),
            format="%m/%d/%Y %H:%M:%S",
        )
    except Exception as e:
        print(f"  WARNING: Date parse failed for {filepath}: {e}")
        return pd.DataFrame()

    # Add metadata columns
    df["Strike"] = strike
    df["Expiry"] = expiry_to_yymmdd(expiry_date)
    df["OptionType"] = option_type
    expiry_ts = pd.Timestamp(expiry_date)
    df["d_to_expiry"] = (expiry_ts - df["Datetime"].dt.normalize()).dt.days

    # Select and order target columns
    target_cols = [
        "Datetime", "Open", "High", "Low", "Close", "Volume", "Open Interest",
        "Strike", "Expiry", "OptionType", "d_to_expiry",
    ]

    for col in target_cols:
        if col not in df.columns:
            print(f"  WARNING: Missing column '{col}' in {filepath}")
            return pd.DataFrame()

    return df[target_cols]


def load_all_dinesh_data(discovered_files: list) -> pd.DataFrame:
    """Load all Dinesh CSVs and concatenate into a single DataFrame."""
    total = len(discovered_files)
    chunks = []
    errors = 0

    print(f"\nLoading {total} Dinesh CSV files...")
    start_time = time.time()

    for i, (filepath, expiry_date, strike, option_type) in enumerate(discovered_files):
        if (i + 1) % 2000 == 0 or (i + 1) == total:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(f"  Progress: {i + 1}/{total} ({rate:.0f} files/sec)")

        df = load_dinesh_csv(filepath, expiry_date, strike, option_type)
        if len(df) > 0:
            chunks.append(df)
        else:
            errors += 1

    if not chunks:
        print("WARNING: No Dinesh data loaded!")
        return pd.DataFrame()

    print(f"Concatenating {len(chunks)} DataFrames ({errors} errors)...")
    result = pd.concat(chunks, ignore_index=True)

    # Deduplicate by (Datetime, Strike, Expiry, OptionType)
    before = len(result)
    result = result.drop_duplicates(subset=["Datetime", "Strike", "Expiry", "OptionType"])
    dupes = before - len(result)
    if dupes > 0:
        print(f"Removed {dupes:,} duplicate rows")

    print(f"Dinesh data: {len(result):,} rows loaded")
    return result


# ---------------------------------------------------------------------------
# Step 4: Load Spot Data
# ---------------------------------------------------------------------------

def load_spot_data() -> pd.DataFrame:
    """Load NIFTY 50 minute spot data and filter to Dinesh period."""
    print(f"\nLoading spot data from {SPOT_DATA_PATH}...")
    spot = pd.read_csv(SPOT_DATA_PATH)
    spot["date"] = pd.to_datetime(spot["date"])

    # Filter to relevant range (Jan 2022 - Mar 2023)
    mask = (spot["date"] >= "2022-01-01") & (spot["date"] < "2023-03-31")
    spot = spot[mask].copy()

    spot = spot.rename(columns={
        "date": "Datetime",
        "open": "Spot_Open",
        "high": "Spot_High",
        "low": "Spot_Low",
        "close": "Spot_Close",
    })

    # Drop volume column from spot (not needed)
    spot = spot[["Datetime", "Spot_Open", "Spot_High", "Spot_Low", "Spot_Close"]]

    print(f"Spot data: {len(spot):,} bars ({spot['Datetime'].min()} to {spot['Datetime'].max()})")
    return spot


# ---------------------------------------------------------------------------
# Step 5: Process rl_dataset_v2_with_spot
# ---------------------------------------------------------------------------

def load_rl_dataset() -> pd.DataFrame:
    """Load rl_dataset_v2_with_spot and drop SwingType column."""
    print(f"\nLoading rl_dataset_v2 from {RL_DATASET_PATH}...")
    df = pd.read_csv(RL_DATASET_PATH)
    df["Datetime"] = pd.to_datetime(df["Datetime"])

    # Drop SwingType
    if "SwingType" in df.columns:
        df = df.drop(columns=["SwingType"])

    # Ensure consistent types (pandas may read YYMMDD as int)
    df["Expiry"] = df["Expiry"].astype(str)
    df["Strike"] = df["Strike"].astype(int)

    print(f"rl_dataset_v2: {len(df):,} rows ({df['Datetime'].min()} to {df['Datetime'].max()})")
    return df


# ---------------------------------------------------------------------------
# Step 6: Merge & Deduplicate
# ---------------------------------------------------------------------------

def merge_datasets(dinesh_df: pd.DataFrame, rl_df: pd.DataFrame) -> pd.DataFrame:
    """Merge Dinesh and rl_dataset_v2, preferring rl_dataset for overlap period."""
    # Dinesh: keep only rows before cutoff date
    if len(dinesh_df) > 0:
        dinesh_before = len(dinesh_df)
        dinesh_df = dinesh_df[dinesh_df["Datetime"] < CUTOFF_DATE].copy()
        trimmed = dinesh_before - len(dinesh_df)
        if trimmed > 0:
            print(f"Trimmed {trimmed:,} Dinesh rows at/after cutoff ({CUTOFF_DATE})")
        print(f"Dinesh after trim: {len(dinesh_df):,} rows")

    # rl_dataset: keep all rows
    print(f"rl_dataset: {len(rl_df):,} rows")

    # Concatenate
    if len(dinesh_df) > 0 and len(rl_df) > 0:
        combined = pd.concat([dinesh_df, rl_df], ignore_index=True)
    elif len(dinesh_df) > 0:
        combined = dinesh_df
    else:
        combined = rl_df

    # Sort
    combined = combined.sort_values(["Datetime", "Strike", "Expiry", "OptionType"]).reset_index(drop=True)

    # Final dedup
    before = len(combined)
    combined = combined.drop_duplicates(subset=["Datetime", "Strike", "Expiry", "OptionType"])
    dupes = before - len(combined)
    if dupes > 0:
        print(f"Removed {dupes:,} duplicate rows in final merge")

    return combined


# ---------------------------------------------------------------------------
# Step 7: Save Output
# ---------------------------------------------------------------------------

def save_output(df: pd.DataFrame, output_path: Path):
    """Save combined DataFrame to Parquet."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"\nSaved to {output_path} ({size_mb:.1f} MB)")


def print_summary(df: pd.DataFrame):
    """Print summary statistics."""
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total rows:      {len(df):,}")
    print(f"Date range:      {df['Datetime'].min()} to {df['Datetime'].max()}")
    print(f"Unique strikes:  {df['Strike'].nunique()}")
    print(f"Unique expiries: {df['Expiry'].nunique()}")
    print(f"Option types:    {sorted(df['OptionType'].unique())}")
    print(f"Columns:         {list(df.columns)}")

    # Spot coverage
    for col in ["Spot_Open", "Spot_High", "Spot_Low", "Spot_Close"]:
        if col in df.columns:
            nan_count = df[col].isna().sum()
            pct = nan_count / len(df) * 100
            print(f"  {col} NaN: {nan_count:,} ({pct:.2f}%)")

    # Date distribution
    df_dates = df["Datetime"].dt.date
    print(f"\nTrading days:    {df_dates.nunique()}")
    print(f"  Dinesh period (< {CUTOFF_DATE}): {(df_dates < date(2023, 2, 27)).sum():,} rows")
    print(f"  rl_dataset period (>= {CUTOFF_DATE}): {(df_dates >= date(2023, 2, 27)).sum():,} rows")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="NIFTY Options Data Cleaner & Merger")
    parser.add_argument("--dry-run", action="store_true", help="Show stats only, no output file")
    parser.add_argument("--dinesh-only", action="store_true", help="Process only Dinesh data")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT), help="Output file path")
    args = parser.parse_args()

    output_path = Path(args.output)
    total_start = time.time()

    # Step 2: Discover Dinesh files
    print("=" * 60)
    print("STEP 1: Discovering Dinesh files")
    print("=" * 60)
    discovered = discover_dinesh_files(DINESH_BASE_DIR)

    if args.dry_run:
        # Just show discovery stats
        print(f"\n[DRY RUN] Would process {len(discovered)} Dinesh files")
        if not args.dinesh_only:
            print(f"[DRY RUN] Would also load rl_dataset_v2 from {RL_DATASET_PATH}")
        return

    # Step 3: Load Dinesh data
    print("\n" + "=" * 60)
    print("STEP 2: Loading Dinesh CSVs")
    print("=" * 60)
    dinesh_df = load_all_dinesh_data(discovered)

    # Step 4: Merge spot data onto Dinesh
    if len(dinesh_df) > 0:
        print("\n" + "=" * 60)
        print("STEP 3: Merging spot data")
        print("=" * 60)
        spot_df = load_spot_data()
        before_spot = len(dinesh_df)
        dinesh_df = dinesh_df.merge(spot_df, on="Datetime", how="left")
        print(f"Spot merge: {before_spot:,} rows -> {len(dinesh_df):,} rows")
        matched = dinesh_df["Spot_Open"].notna().sum()
        print(f"Spot matched: {matched:,} / {len(dinesh_df):,} ({matched / len(dinesh_df) * 100:.1f}%)")

    if args.dinesh_only:
        # Skip rl_dataset
        print_summary(dinesh_df)
        save_output(dinesh_df, output_path)
        elapsed = time.time() - total_start
        print(f"\nTotal time: {elapsed:.1f}s")
        return

    # Step 5: Load rl_dataset_v2
    print("\n" + "=" * 60)
    print("STEP 4: Loading rl_dataset_v2")
    print("=" * 60)
    rl_df = load_rl_dataset()

    # Step 6: Merge
    print("\n" + "=" * 60)
    print("STEP 5: Merging datasets")
    print("=" * 60)
    combined = merge_datasets(dinesh_df, rl_df)

    # Step 7: Save
    print_summary(combined)
    save_output(combined, output_path)

    elapsed = time.time() - total_start
    print(f"\nTotal time: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
