"""Deep analytics for V3 evaluation results."""
import sys
import pandas as pd
import numpy as np

def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "results/rl_eval_v3/eval_daily_v3.csv"
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M")
    df["weekday"] = df["date"].dt.day_name()
    df["equity"] = df["cumulative_R"].cumsum()

    R = df["cumulative_R"]

    print("=" * 70)
    print("1. DISTRIBUTION ANALYSIS")
    print("=" * 70)
    print(f"  N days:          {len(R)}")
    print(f"  Mean:            {R.mean():+.3f}R")
    print(f"  Median:          {R.median():+.3f}R")
    print(f"  Std:             {R.std():.3f}R")
    print(f"  Skewness:        {R.skew():+.3f}")
    print(f"  Kurtosis:        {R.kurtosis():+.3f}")
    print(f"  Min:             {R.min():+.3f}R")
    print(f"  Max:             {R.max():+.3f}R")
    print(f"  IQR:             {R.quantile(0.75) - R.quantile(0.25):.3f}R")

    # Bucket distribution
    bins = [-10, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 10]
    labels = ["<-5", "-5to-4", "-4to-3", "-3to-2", "-2to-1", "-1to0",
              "0to1", "1to2", "2to3", "3to4", "4to5", "5to6", ">6"]
    df["bucket"] = pd.cut(R, bins=bins, labels=labels)
    print("\n  Daily R distribution:")
    for b in labels:
        count = (df["bucket"] == b).sum()
        pct = count / len(df) * 100
        bar = "#" * int(pct)
        print(f"    {b:>8s}: {count:3d} ({pct:5.1f}%) {bar}")

    print()
    print("=" * 70)
    print("2. MONTHLY BREAKDOWN")
    print("=" * 70)
    monthly = df.groupby("month").agg(
        days=("cumulative_R", "count"),
        total_R=("cumulative_R", "sum"),
        mean_R=("cumulative_R", "mean"),
        win_rate=("cumulative_R", lambda x: (x > 0).mean() * 100),
        trades=("trades", "sum"),
        avg_trades=("trades", "mean"),
    ).reset_index()
    monthly["sharpe"] = df.groupby("month")["cumulative_R"].apply(
        lambda x: x.mean() / x.std() * np.sqrt(252 / 12) if x.std() > 0 else 0
    ).values

    print(f"  {'Month':<10} {'Days':>4} {'TotalR':>8} {'MeanR':>7} {'Win%':>6} {'Sharpe':>7} {'Trades':>6} {'Avg/d':>5}")
    print(f"  {'-' * 10} {'-' * 4} {'-' * 8} {'-' * 7} {'-' * 6} {'-' * 7} {'-' * 6} {'-' * 5}")
    for _, row in monthly.iterrows():
        print(f"  {str(row['month']):<10} {int(row['days']):>4} {row['total_R']:>+8.1f} {row['mean_R']:>+7.2f} {row['win_rate']:>5.0f}% {row['sharpe']:>+7.2f} {int(row['trades']):>6} {row['avg_trades']:>5.1f}")

    profitable_months = (monthly["total_R"] > 0).sum()
    print(f"\n  Profitable months: {profitable_months}/{len(monthly)} ({profitable_months / len(monthly) * 100:.0f}%)")

    print()
    print("=" * 70)
    print("3. DAY-OF-WEEK ANALYSIS")
    print("=" * 70)
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    dow = df.groupby("weekday").agg(
        days=("cumulative_R", "count"),
        mean_R=("cumulative_R", "mean"),
        median_R=("cumulative_R", "median"),
        win_rate=("cumulative_R", lambda x: (x > 0).mean() * 100),
    ).reindex(dow_order)
    print(f"  {'Day':<12} {'Days':>4} {'MeanR':>7} {'MedianR':>8} {'Win%':>6}")
    for day, row in dow.iterrows():
        print(f"  {day:<12} {int(row['days']):>4} {row['mean_R']:>+7.2f} {row['median_R']:>+8.2f} {row['win_rate']:>5.0f}%")

    print()
    print("=" * 70)
    print("4. STREAKS & CONSISTENCY")
    print("=" * 70)

    def max_streak(series):
        max_s = current = 0
        for v in series:
            if v:
                current += 1
                max_s = max(max_s, current)
            else:
                current = 0
        return max_s

    wins = (R > 0).astype(int)
    losses = (R < 0).astype(int)
    print(f"  Longest winning streak:  {max_streak(wins)} days")
    print(f"  Longest losing streak:   {max_streak(losses)} days")

    rolling_20 = R.rolling(20).mean()
    print(f"  Best 20-day avg:         {rolling_20.max():+.2f}R/day")
    print(f"  Worst 20-day avg:        {rolling_20.min():+.2f}R/day")
    r20_valid = rolling_20.dropna()
    print(f"  20-day avg > 0:          {(r20_valid > 0).sum()}/{len(r20_valid)} ({(r20_valid > 0).mean() * 100:.0f}%)")

    rolling_20_sum = R.rolling(20).sum()
    print(f"  Worst 20-day total:      {rolling_20_sum.min():+.1f}R")
    print(f"  Best 20-day total:       {rolling_20_sum.max():+.1f}R")

    print()
    print("=" * 70)
    print("5. DRAWDOWN ANALYSIS")
    print("=" * 70)
    equity = df["equity"]
    peak = equity.cummax()
    drawdown = equity - peak

    print(f"  Max drawdown:            {drawdown.min():+.1f}R")

    dd_start = None
    dd_periods = []
    for i in range(len(drawdown)):
        if drawdown.iloc[i] < 0 and dd_start is None:
            dd_start = i
        elif drawdown.iloc[i] >= 0 and dd_start is not None:
            dd_periods.append((dd_start, i, i - dd_start))
            dd_start = None
    if dd_start is not None:
        dd_periods.append((dd_start, len(drawdown) - 1, len(drawdown) - 1 - dd_start))

    if dd_periods:
        longest_dd = max(dd_periods, key=lambda x: x[2])
        print(f"  Longest drawdown:        {longest_dd[2]} days ({df['date'].iloc[longest_dd[0]].strftime('%Y-%m-%d')} to {df['date'].iloc[longest_dd[1]].strftime('%Y-%m-%d')})")

        deepest_dd_idx = drawdown.idxmin()
        dd_start_for_deepest = 0
        for i in range(deepest_dd_idx, -1, -1):
            if drawdown.iloc[i] >= 0:
                dd_start_for_deepest = i
                break
        print(f"  Deepest DD period:       {df['date'].iloc[dd_start_for_deepest].strftime('%Y-%m-%d')} to {df['date'].iloc[deepest_dd_idx].strftime('%Y-%m-%d')}")

    dd_vals = drawdown[drawdown < 0]
    if len(dd_vals) > 0:
        print(f"  Days in drawdown:        {len(dd_vals)}/{len(drawdown)} ({len(dd_vals) / len(drawdown) * 100:.0f}%)")
        print(f"  Avg drawdown depth:      {dd_vals.mean():+.1f}R")

    print()
    print("=" * 70)
    print("6. RISK-ADJUSTED METRICS")
    print("=" * 70)
    sharpe = R.mean() / R.std() * np.sqrt(249)
    sortino_denom = R[R < 0].std()
    sortino = R.mean() / sortino_denom * np.sqrt(249) if sortino_denom > 0 else float("inf")
    calmar = R.sum() / abs(drawdown.min()) if drawdown.min() != 0 else float("inf")
    profit_factor = R[R > 0].sum() / abs(R[R < 0].sum()) if R[R < 0].sum() != 0 else float("inf")

    avg_win = R[R > 0].mean()
    avg_loss = R[R < 0].mean()
    win_rate_pct = (R > 0).mean()

    print(f"  Sharpe ratio:            {sharpe:.2f}")
    print(f"  Sortino ratio:           {sortino:.2f}")
    print(f"  Calmar ratio:            {calmar:.2f}")
    print(f"  Profit factor:           {profit_factor:.2f}")
    print(f"  Avg win:                 {avg_win:+.2f}R")
    print(f"  Avg loss:                {avg_loss:+.2f}R")
    print(f"  Win/loss ratio:          {abs(avg_win / avg_loss):.2f}")
    print(f"  Expected value:          {win_rate_pct * avg_win + (1 - win_rate_pct) * avg_loss:+.3f}R/day")
    kelly = win_rate_pct - (1 - win_rate_pct) / abs(avg_win / avg_loss)
    print(f"  Kelly criterion:         {kelly:.1%}")

    print()
    print("=" * 70)
    print("7. TRADE COUNT vs PERFORMANCE")
    print("=" * 70)
    trade_bins = [0, 10, 15, 20, 25, 30, 100]
    trade_labels = ["1-10", "11-15", "16-20", "21-25", "26-30", "31+"]
    df["trade_bucket"] = pd.cut(df["trades"], bins=trade_bins, labels=trade_labels)
    tb = df.groupby("trade_bucket", observed=True).agg(
        days=("cumulative_R", "count"),
        mean_R=("cumulative_R", "mean"),
        win_rate=("cumulative_R", lambda x: (x > 0).mean() * 100),
    )
    print(f"  {'Trades':>8} {'Days':>5} {'MeanR':>7} {'Win%':>6}")
    for bucket, row in tb.iterrows():
        print(f"  {bucket:>8} {int(row['days']):>5} {row['mean_R']:>+7.2f} {row['win_rate']:>5.0f}%")

    corr = df["trades"].corr(df["cumulative_R"])
    print(f"\n  Correlation (trades vs R): {corr:+.3f}")

    print()
    print("=" * 70)
    print("8. TARGET/STOP ANALYSIS")
    print("=" * 70)
    hit_target = (R >= 5.0).sum()
    hit_stop = (R <= -5.0).sum()
    neither = len(R) - hit_target - hit_stop
    print(f"  Hit +5R target:          {hit_target} days ({hit_target / len(R) * 100:.1f}%)")
    print(f"  Hit -5R stop:            {hit_stop} days ({hit_stop / len(R) * 100:.1f}%)")
    print(f"  Neither (time exit):     {neither} days ({neither / len(R) * 100:.1f}%)")
    print(f"  Target/Stop ratio:       {hit_target / max(hit_stop, 1):.1f}x")
    print(f"  R from target days:      {R[R >= 5.0].sum():+.1f}R")
    print(f"  R from stop days:        {R[R <= -5.0].sum():+.1f}R")
    r_normal = R[(R > -5.0) & (R < 5.0)]
    print(f"  R from normal days:      {r_normal.sum():+.1f}R")
    print(f"  Avg R (normal days):     {r_normal.mean():+.3f}R")

    print()
    print("=" * 70)
    print("9. QUARTERLY PERFORMANCE")
    print("=" * 70)
    df["quarter"] = df["date"].dt.to_period("Q")
    quarterly = df.groupby("quarter").agg(
        days=("cumulative_R", "count"),
        total_R=("cumulative_R", "sum"),
        mean_R=("cumulative_R", "mean"),
        win_rate=("cumulative_R", lambda x: (x > 0).mean() * 100),
        max_dd=("equity", lambda x: (x - x.cummax()).min()),
    ).reset_index()
    print(f"  {'Quarter':<10} {'Days':>4} {'TotalR':>8} {'MeanR':>7} {'Win%':>6} {'MaxDD':>7}")
    for _, row in quarterly.iterrows():
        print(f"  {str(row['quarter']):<10} {int(row['days']):>4} {row['total_R']:>+8.1f} {row['mean_R']:>+7.2f} {row['win_rate']:>5.0f}% {row['max_dd']:>+7.1f}")

    print()
    print("=" * 70)
    print("10. ENTRIES vs SL/TP FILLS")
    print("=" * 70)
    total_entries = df["entries_taken"].sum()
    total_sl = df["sl_fills"].sum()
    total_tp = df["tp_fills"].sum()
    total_mkt = df["market_exits"].sum()
    total_exit_all = df["exit_alls"].sum()
    print(f"  Total entries:           {total_entries}")
    print(f"  SL fills:                {total_sl} ({total_sl / max(total_entries, 1) * 100:.1f}% of entries)")
    print(f"  TP fills:                {total_tp} ({total_tp / max(total_entries, 1) * 100:.1f}% of entries)")
    print(f"  Market exits:            {total_mkt}")
    print(f"  EXIT_ALL actions:        {total_exit_all}")
    print(f"  SL:TP ratio:             {total_sl}:{total_tp}")
    if total_sl + total_tp > 0:
        print(f"  TP fill rate:            {total_tp / (total_sl + total_tp) * 100:.1f}%")

    # Where does the P&L come from?
    print()
    print("  P&L Attribution (estimated):")
    # Days with high entries but negative R = SL-heavy
    high_entry_loss = df[(df["entries_taken"] > 50) & (df["cumulative_R"] < 0)]
    high_entry_win = df[(df["entries_taken"] > 50) & (df["cumulative_R"] > 0)]
    low_entry = df[df["entries_taken"] <= 50]
    print(f"  High-activity days (>50 entries): {len(high_entry_loss) + len(high_entry_win)} days, avg R={df[df['entries_taken'] > 50]['cumulative_R'].mean():+.2f}")
    print(f"  Low-activity days (<=50 entries): {len(low_entry)} days, avg R={low_entry['cumulative_R'].mean():+.2f}")

    print()
    print("=" * 70)
    print("SUMMARY VERDICT")
    print("=" * 70)
    flags = []
    greens = []
    if sharpe > 5:
        flags.append(f"Sharpe {sharpe:.1f} is unusually high (>5)")
    if profitable_months == len(monthly):
        flags.append("ALL months profitable (unusual)")
    if R.skew() < -0.5:
        flags.append(f"Negative skew ({R.skew():+.2f}) = fat left tail risk")
    if hit_target / max(hit_stop, 1) > 5:
        greens.append(f"Target/Stop ratio {hit_target / max(hit_stop, 1):.0f}x = strong asymmetry")
    if profitable_months >= 10:
        greens.append(f"{profitable_months}/12 months profitable = consistent")
    if max_streak(losses) <= 5:
        greens.append(f"Max losing streak only {max_streak(losses)} days")
    if calmar > 10:
        flags.append(f"Calmar {calmar:.0f} is very high (possible overfit)")
    if total_tp == 0:
        flags.append("ZERO TP fills! Agent never lets TP orders fill")
    if profit_factor > 1.5:
        greens.append(f"Profit factor {profit_factor:.2f}")
    if sortino > 10:
        greens.append(f"Sortino {sortino:.1f} = good downside control")

    print("  GREEN FLAGS:")
    for g in greens:
        print(f"    + {g}")
    print("  YELLOW/RED FLAGS:")
    for f in flags:
        print(f"    ! {f}")


if __name__ == "__main__":
    main()
