"""
Merge Aryan NIFTY Options Data (2024-2025) with Existing Combined Parquet (2022-2023)

Produces a single comprehensive parquet: data/nifty_options_full.parquet
- Existing: Jan 2022 to Nov 2023 (33.4M rows)
- Aryan: Jan 2024 to Dec 2025 (~44K CSV files)
- Gap: Dec 2023 (no data available)

Usage:
    python -m scripts.merge_aryan_data                    # Full run
    python -m scripts.merge_aryan_data --dry-run          # Discovery stats only
    python -m scripts.merge_aryan_data --aryan-only       # Skip existing parquet, Aryan only
"""

import argparse
import gc
import os
import re
import time
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from scripts.clean_and_merge_data import expiry_to_yymmdd, parse_nifty_option_filename

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARYAN_BASE_DIR = Path(r"D:\nifty_options_agent\data\Aryan Nifty 2024-25 data")
EXISTING_PARQUET = Path(r"D:\nifty_options_agent\data\nifty_options_combined.parquet")
DEFAULT_OUTPUT = Path(r"D:\nifty_options_agent\data\nifty_options_full.parquet")

NIFTY_OPTION_RE = re.compile(r"^NIFTY\d{2}")
VARIANT_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")

YEAR_DIRS = ["2024_NiftyOpt", "2025_NiftyOpt"]


# ---------------------------------------------------------------------------
# Step 1: Discover Aryan Option Files
# ---------------------------------------------------------------------------

def discover_aryan_files(base_dir: Path) -> list:
    """
    Walk {Year}_NiftyOpt/{Month}_NOpt/ dirs to find all NIFTY option CSVs.
    Handles both flat and nested month directories via os.walk.
    """
    results = []
    skipped = 0

    for year_dir_name in YEAR_DIRS:
        year_dir = base_dir / year_dir_name
        if not year_dir.exists():
            print(f"  WARNING: {year_dir} does not exist, skipping")
            continue

        year_count = 0
        # Walk all subdirectories (handles flat + nested month dirs)
        for root, _dirs, files in os.walk(year_dir):
            # Skip spot data directories
            if "Nifty Spot Data" in root:
                continue

            for fname in files:
                if not fname.lower().endswith(".csv"):
                    continue

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
                year_count += 1

        print(f"  {year_dir_name}: {year_count:,} NIFTY option files")

    print(f"Total discovered: {len(results):,} files ({skipped} skipped)")
    return results


# ---------------------------------------------------------------------------
# Step 2: Load a Single Aryan CSV
# ---------------------------------------------------------------------------

def load_aryan_csv(filepath: Path, expiry_date, strike: int, option_type: str) -> pd.DataFrame:
    """Load a single Aryan CSV (DD-MM-YYYY date format) and transform to target format."""
    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        return pd.DataFrame()

    if len(df) == 0:
        return pd.DataFrame()

    # Normalize column names (strip angle brackets)
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

    # Combine date + time into Datetime (DD-MM-YYYY format)
    try:
        df["Datetime"] = pd.to_datetime(
            df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip(),
            format="%d-%m-%Y %H:%M:%S",
        )
    except Exception:
        return pd.DataFrame()

    # Add metadata columns
    df["Strike"] = strike
    df["Expiry"] = expiry_to_yymmdd(expiry_date)
    df["OptionType"] = option_type
    expiry_ts = pd.Timestamp(expiry_date)
    df["d_to_expiry"] = (expiry_ts - df["Datetime"].dt.normalize()).dt.days

    # Ensure Open Interest exists (should always be present for option files)
    if "Open Interest" not in df.columns:
        df["Open Interest"] = 0

    target_cols = [
        "Datetime", "Open", "High", "Low", "Close", "Volume", "Open Interest",
        "Strike", "Expiry", "OptionType", "d_to_expiry",
    ]

    for col in target_cols:
        if col not in df.columns:
            return pd.DataFrame()

    return df[target_cols]


# ---------------------------------------------------------------------------
# Step 3: Load All Aryan Option Data
# ---------------------------------------------------------------------------

def load_all_aryan_data(discovered_files: list) -> pd.DataFrame:
    """Load all Aryan CSVs and concatenate."""
    total = len(discovered_files)
    chunks = []
    errors = 0

    print(f"\nLoading {total:,} Aryan CSV files...")
    start_time = time.time()

    for i, (filepath, expiry_date, strike, option_type) in enumerate(discovered_files):
        if (i + 1) % 5000 == 0 or (i + 1) == total:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(f"  Progress: {i + 1:,}/{total:,} ({rate:.0f} files/sec)")

        df = load_aryan_csv(filepath, expiry_date, strike, option_type)
        if len(df) > 0:
            chunks.append(df)
        else:
            errors += 1

    if not chunks:
        print("WARNING: No Aryan data loaded!")
        return pd.DataFrame()

    print(f"Concatenating {len(chunks):,} DataFrames ({errors} errors)...")
    result = pd.concat(chunks, ignore_index=True)

    # Deduplicate
    before = len(result)
    result = result.drop_duplicates(subset=["Datetime", "Strike", "Expiry", "OptionType"])
    dupes = before - len(result)
    if dupes > 0:
        print(f"Removed {dupes:,} duplicate rows")

    print(f"Aryan option data: {len(result):,} rows")
    return result


# ---------------------------------------------------------------------------
# Step 4: Load Aryan Spot Data
# ---------------------------------------------------------------------------

def load_aryan_spot_data(base_dir: Path) -> pd.DataFrame:
    """Load and concatenate all .NSEI.csv spot files from 2024 and 2025."""
    spot_files = []
    for year_dir_name in YEAR_DIRS:
        spot_base = base_dir / year_dir_name / "Nifty Spot Data"
        if not spot_base.exists():
            print(f"  WARNING: {spot_base} does not exist")
            continue
        for root, _dirs, files in os.walk(spot_base):
            for fname in files:
                if fname.endswith(".NSEI.csv"):
                    spot_files.append(Path(root) / fname)

    print(f"Found {len(spot_files)} spot data files")

    chunks = []
    for fpath in sorted(spot_files):
        try:
            df = pd.read_csv(fpath)
        except Exception as e:
            print(f"  WARNING: Failed to read {fpath}: {e}")
            continue

        # Normalize columns
        col_map = {}
        for col in df.columns:
            col_clean = col.strip().lower().replace("<", "").replace(">", "")
            if col_clean == "date":
                col_map[col] = "date"
            elif col_clean == "time":
                col_map[col] = "time"
            elif col_clean == "open":
                col_map[col] = "Spot_Open"
            elif col_clean == "high":
                col_map[col] = "Spot_High"
            elif col_clean == "low":
                col_map[col] = "Spot_Low"
            elif col_clean == "close":
                col_map[col] = "Spot_Close"
        df = df.rename(columns=col_map)

        try:
            df["Datetime"] = pd.to_datetime(
                df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip(),
                format="%d-%m-%Y %H:%M:%S",
            )
        except Exception:
            continue

        df = df[["Datetime", "Spot_Open", "Spot_High", "Spot_Low", "Spot_Close"]]
        chunks.append(df)

    if not chunks:
        print("WARNING: No spot data loaded!")
        return pd.DataFrame()

    spot = pd.concat(chunks, ignore_index=True)
    spot = spot.drop_duplicates(subset=["Datetime"])
    spot = spot.sort_values("Datetime").reset_index(drop=True)
    print(f"Spot data: {len(spot):,} bars ({spot['Datetime'].min()} to {spot['Datetime'].max()})")
    return spot


# ---------------------------------------------------------------------------
# Step 5: Merge and Save
# ---------------------------------------------------------------------------

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
            nan_pct = df[col].isna().mean() * 100
            print(f"  {col} NaN: {nan_pct:.2f}%")

    # Per-year breakdown
    print("\nPer-year breakdown:")
    for year in sorted(df["Datetime"].dt.year.unique()):
        mask = df["Datetime"].dt.year == year
        print(f"  {year}: {mask.sum():,} rows")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Merge Aryan NIFTY Options Data (2024-2025)")
    parser.add_argument("--dry-run", action="store_true", help="Show discovery stats only")
    parser.add_argument("--aryan-only", action="store_true", help="Only process Aryan data (no existing parquet)")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT), help="Output parquet path")
    args = parser.parse_args()

    output_path = Path(args.output)
    total_start = time.time()

    # Step 1: Discover Aryan files
    print("=" * 60)
    print("STEP 1: Discovering Aryan option files")
    print("=" * 60)
    discovered = discover_aryan_files(ARYAN_BASE_DIR)

    if args.dry_run:
        print(f"\n[DRY RUN] Would process {len(discovered):,} Aryan files")
        return

    # Step 2: Load Aryan option data
    print("\n" + "=" * 60)
    print("STEP 2: Loading Aryan CSVs")
    print("=" * 60)
    aryan_df = load_all_aryan_data(discovered)

    if len(aryan_df) == 0:
        print("ERROR: No Aryan data loaded. Aborting.")
        return

    # Step 3: Load and merge spot data
    print("\n" + "=" * 60)
    print("STEP 3: Loading Aryan spot data")
    print("=" * 60)
    spot_df = load_aryan_spot_data(ARYAN_BASE_DIR)

    if len(spot_df) > 0:
        aryan_df = aryan_df.merge(spot_df, on="Datetime", how="left")
        matched = aryan_df["Spot_Open"].notna().sum()
        print(f"Spot matched: {matched:,} / {len(aryan_df):,} ({matched / len(aryan_df) * 100:.1f}%)")
    else:
        for col in ["Spot_Open", "Spot_High", "Spot_Low", "Spot_Close"]:
            aryan_df[col] = pd.NA

    del spot_df

    # Sort Aryan data before saving (sort while only one dataset in memory)
    print("Sorting Aryan data...")
    aryan_df.sort_values(["Datetime", "Strike", "Expiry", "OptionType"], inplace=True)

    # Dedup Aryan data
    before = len(aryan_df)
    aryan_df.drop_duplicates(subset=["Datetime", "Strike", "Expiry", "OptionType"], inplace=True)
    dupes = before - len(aryan_df)
    if dupes > 0:
        print(f"Removed {dupes:,} Aryan duplicate rows")

    # Step 4: Save Aryan as temp parquet, then merge via pyarrow (avoids OOM)
    print("\n" + "=" * 60)
    print("STEP 4: Saving Aryan parquet + merging with existing")
    print("=" * 60)

    aryan_parquet = output_path.parent / "nifty_options_aryan_temp.parquet"

    if not args.aryan_only and EXISTING_PARQUET.exists():
        # Match column order to existing parquet
        existing_meta = pq.read_schema(EXISTING_PARQUET)
        target_cols = [f.name for f in existing_meta]
        aryan_df = aryan_df[target_cols]

    aryan_df.to_parquet(aryan_parquet, index=False)
    aryan_rows = len(aryan_df)
    del aryan_df
    gc.collect()
    print(f"Aryan parquet saved: {aryan_rows:,} rows ({aryan_parquet.stat().st_size / 1e6:.0f} MB)")

    if not args.aryan_only and EXISTING_PARQUET.exists():
        # Use pyarrow to concatenate two parquet files without loading both into pandas
        print("Reading existing parquet via pyarrow...")
        existing_table = pq.read_table(EXISTING_PARQUET)
        existing_rows = existing_table.num_rows
        print(f"Existing: {existing_rows:,} rows")

        print("Reading Aryan parquet via pyarrow...")
        aryan_table = pq.read_table(aryan_parquet)

        print("Concatenating via pyarrow...")
        import pyarrow as pa
        combined_table = pa.concat_tables([existing_table, aryan_table])
        del existing_table, aryan_table
        gc.collect()

        print(f"Combined: {combined_table.num_rows:,} rows")

        # Save combined
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(combined_table, output_path)
        del combined_table
        gc.collect()
    else:
        # Just rename temp to output
        if aryan_parquet != output_path:
            import shutil
            shutil.move(str(aryan_parquet), str(output_path))
        if not args.aryan_only:
            print(f"WARNING: {EXISTING_PARQUET} not found, using Aryan data only")

    # Clean up temp file
    if aryan_parquet.exists() and aryan_parquet != output_path:
        aryan_parquet.unlink()

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"\nSaved to {output_path} ({size_mb:.1f} MB)")

    # Step 5: Verify
    print("\n" + "=" * 60)
    print("STEP 5: Verification")
    print("=" * 60)
    # Read just metadata for verification (not full data)
    pf = pq.ParquetFile(output_path)
    print(f"Total rows: {pf.metadata.num_rows:,}")
    print(f"Columns: {pf.schema_arrow.names}")
    print(f"Row groups: {pf.metadata.num_row_groups}")

    # Quick sample for date range
    sample = pd.read_parquet(output_path, columns=["Datetime"])
    print(f"Date range: {sample['Datetime'].min()} to {sample['Datetime'].max()}")

    # Per-year breakdown
    for year in sorted(sample["Datetime"].dt.year.unique()):
        mask = sample["Datetime"].dt.year == year
        print(f"  {year}: {mask.sum():,} rows")

    elapsed = time.time() - total_start
    print(f"\nTotal time: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
