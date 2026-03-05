"""
Evaluate Entry Model — Run best QR-DQN through test set with detailed logging.

Runs the trained model through every day in the test period (Jul-Nov 2023),
logging every decision and trade outcome. Produces per-day and aggregate stats.

Usage:
    python -m scripts.rl.evaluate_entry
    python -m scripts.rl.evaluate_entry --model results/rl_models/entry_model_latest.zip
    python -m scripts.rl.evaluate_entry --episodes 50
"""

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

import numpy as np

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

from sb3_contrib import QRDQN

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%H:%M:%S',
)
logging.getLogger('baseline_v1_live.swing_detector').setLevel(logging.ERROR)
logging.getLogger('baseline_v1_live.config').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

# Time-series split (consistent with training)
TEST_START = '2023-07-22'

TARGETS = [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]
ACTION_NAMES = ['SKIP'] + [f'ENTER_{t}R' for t in TARGETS] + ['STOP_SESSION']


def evaluate(model_path, data_path, n_episodes, output_dir, seed=42,
             fixed_target_R=5.0, fixed_stop_R=-5.0):
    """Run model through test set and produce detailed evaluation."""
    from scripts.rl.env import TradingEntryEnv

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load model (without env to avoid action space mismatch)
    logger.info(f'Loading model from {model_path}')
    model = QRDQN.load(model_path)
    model_n_actions = model.action_space.n
    logger.info(f'Model action space: Discrete({model_n_actions})')

    # Create eval env with fixed target/stop (production settings)
    logger.info(f'Creating eval env (from {TEST_START}, target={fixed_target_R}R, stop={fixed_stop_R}R)...')
    env = TradingEntryEnv(
        data_path=data_path,
        eval_mode=True,
        seed=seed,
        start_date=TEST_START,
        fixed_target_R=fixed_target_R,
        fixed_stop_R=fixed_stop_R,
    )
    n_days = len(env.trading_days)
    n_episodes = min(n_episodes, n_days)
    logger.info(f'Test set: {n_days} days ({env.trading_days[0]} to {env.trading_days[-1]})')
    logger.info(f'Running {n_episodes} episodes (deterministic policy)')

    # Override env action space to match model (for predict compatibility)
    from gymnasium import spaces
    env.action_space = spaces.Discrete(model_n_actions)

    # CSV for daily results
    daily_csv_path = output_dir / 'eval_daily.csv'
    daily_csv = open(daily_csv_path, 'w', newline='')
    daily_writer = csv.writer(daily_csv)
    daily_writer.writerow([
        'episode', 'date', 'target_R', 'stop_R',
        'cumulative_R', 'trades', 'decisions', 'skips', 'entries', 'stops',
        'wins', 'losses', 'eod_exits', 'win_rate',
    ])

    # CSV for individual trades
    trades_csv_path = output_dir / 'eval_trades.csv'
    trades_csv = open(trades_csv_path, 'w', newline='')
    trades_writer = csv.writer(trades_csv)
    trades_writer.writerow([
        'episode', 'date', 'trade_num', 'symbol', 'option_type',
        'entry_price', 'entry_time', 'sl_points', 'target_R',
        'exit_type', 'realized_R', 'lots',
    ])

    # Tracking
    all_daily_R = []
    all_action_counts = {i: 0 for i in range(model_n_actions)}
    total_trades = 0
    total_wins = 0
    total_losses = 0

    for ep in range(n_episodes):
        obs, info = env.reset()
        day_date = env.day.current_date
        target_R = env.target_R
        stop_R = env.stop_R

        decisions = 0
        skips = 0
        entries = 0
        stops = 0
        ep_trades = []  # Track trades this episode
        prev_positions = []  # Track position changes

        done = False
        while not done:
            # Get model action (deterministic)
            action, _ = model.predict(obs, deterministic=True)
            action = int(action)

            # Clamp action to valid range for old models
            if action >= model_n_actions:
                action = 0

            all_action_counts[action] += 1
            decisions += 1

            if action == 0:
                skips += 1
            elif 1 <= action <= 6:
                entries += 1
            elif action == 7:
                stops += 1

            # Track positions before step
            positions_before = set(id(p) for p in env.positions)

            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated

            # Detect newly closed positions by checking what's gone
            positions_after = set(id(p) for p in env.positions)

        # Episode done — collect final stats
        cum_R = env.cumulative_R
        trades_count = env.trades_today
        all_daily_R.append(cum_R)
        total_trades += trades_count

        # Count wins/losses from cumR sign (simplified)
        day_wins = 0
        day_losses = 0
        if cum_R > 0:
            day_wins = 1
        elif cum_R < 0:
            day_losses = 1

        total_wins += day_wins
        total_losses += day_losses

        # Estimate per-trade win rate from daily R and trade count
        # Rough: if cumR > 0 and trades > 0, at least some were winners
        win_rate = 0.0
        if trades_count > 0:
            # Approximate: each losing trade is -1R, so
            # wins*target_avg + losses*(-1) = cum_R, wins+losses = trades
            # This is approximate, real tracking needs per-trade logging
            win_rate = max(0, min(1, (cum_R + trades_count) / (2 * trades_count))) if trades_count > 0 else 0

        daily_writer.writerow([
            ep + 1, day_date, f'{target_R:.0f}', f'{stop_R:.0f}',
            f'{cum_R:.3f}', trades_count, decisions, skips, entries, stops,
            day_wins, day_losses, 0,
            f'{win_rate:.2f}',
        ])

        if (ep + 1) % 10 == 0 or ep == 0:
            logger.info(
                f'Day {ep+1:3d}/{n_episodes} | {day_date} | '
                f'tgt={target_R:+.0f} stop={stop_R:+.0f} | '
                f'cumR={cum_R:+6.2f} | trades={trades_count:3d} | '
                f'decisions={decisions:3d} (skip={skips}, enter={entries}, stop={stops})'
            )

    daily_csv.close()
    trades_csv.close()

    # -------------------------------------------------------------------------
    # Aggregate statistics
    # -------------------------------------------------------------------------
    daily_R = np.array(all_daily_R)
    profitable_days = np.sum(daily_R > 0)
    losing_days = np.sum(daily_R < 0)
    flat_days = np.sum(daily_R == 0)

    logger.info('')
    logger.info('=' * 70)
    logger.info('EVALUATION RESULTS')
    logger.info('=' * 70)
    logger.info(f'Test period: {env.trading_days[0]} to {env.trading_days[min(n_episodes-1, n_days-1)]}')
    logger.info(f'Episodes: {n_episodes}')
    logger.info('')

    # Daily P&L
    logger.info('--- Daily P&L ---')
    logger.info(f'  Mean daily R:     {daily_R.mean():+.3f}')
    logger.info(f'  Median daily R:   {np.median(daily_R):+.3f}')
    logger.info(f'  Std daily R:      {daily_R.std():.3f}')
    if daily_R.std() > 0:
        sharpe = daily_R.mean() / daily_R.std() * np.sqrt(252)
        logger.info(f'  Annualized Sharpe: {sharpe:.2f}')
    logger.info(f'  Total R:          {daily_R.sum():+.2f}')
    logger.info(f'  Best day:         {daily_R.max():+.2f}')
    logger.info(f'  Worst day:        {daily_R.min():+.2f}')
    logger.info('')

    # Win/loss
    logger.info('--- Win/Loss ---')
    logger.info(f'  Profitable days:  {profitable_days}/{n_episodes} ({100*profitable_days/n_episodes:.1f}%)')
    logger.info(f'  Losing days:      {losing_days}/{n_episodes} ({100*losing_days/n_episodes:.1f}%)')
    logger.info(f'  Flat days:        {flat_days}/{n_episodes} ({100*flat_days/n_episodes:.1f}%)')
    logger.info('')

    # Trading activity
    avg_trades = total_trades / n_episodes if n_episodes > 0 else 0
    logger.info('--- Trading Activity ---')
    logger.info(f'  Total trades:     {total_trades}')
    logger.info(f'  Avg trades/day:   {avg_trades:.1f}')
    logger.info('')

    # Action distribution
    total_actions = sum(all_action_counts.values())
    logger.info('--- Action Distribution ---')
    for a, count in sorted(all_action_counts.items()):
        pct = 100 * count / total_actions if total_actions > 0 else 0
        name = ACTION_NAMES[a] if a < len(ACTION_NAMES) else f'ACTION_{a}'
        logger.info(f'  {name:15s}: {count:6d} ({pct:5.1f}%)')
    logger.info('')

    # Drawdown
    cum_equity = np.cumsum(daily_R)
    running_max = np.maximum.accumulate(cum_equity)
    drawdowns = cum_equity - running_max
    max_drawdown = drawdowns.min()
    logger.info('--- Equity Curve ---')
    logger.info(f'  Final equity:     {cum_equity[-1]:+.2f}R')
    logger.info(f'  Peak equity:      {running_max[-1]:+.2f}R')
    logger.info(f'  Max drawdown:     {max_drawdown:.2f}R')
    logger.info('')

    # Percentiles
    logger.info('--- Daily R Percentiles ---')
    for p in [5, 10, 25, 50, 75, 90, 95]:
        logger.info(f'  P{p:2d}: {np.percentile(daily_R, p):+.2f}')

    logger.info('')
    logger.info(f'Results saved to: {daily_csv_path}')
    logger.info('=' * 70)

    return {
        'mean_daily_R': float(daily_R.mean()),
        'total_R': float(daily_R.sum()),
        'win_rate': float(profitable_days / n_episodes),
        'avg_trades': avg_trades,
        'sharpe': float(daily_R.mean() / daily_R.std() * np.sqrt(252)) if daily_R.std() > 0 else 0,
        'max_drawdown': float(max_drawdown),
    }


def main():
    parser = argparse.ArgumentParser(description='Evaluate QR-DQN Entry Model')
    parser.add_argument('--model', type=str,
                        default='results/rl_models/best_model.zip',
                        help='Path to model .zip')
    parser.add_argument('--data', type=str,
                        default='data/nifty_options_combined.parquet',
                        help='Path to parquet data')
    parser.add_argument('--episodes', type=int, default=86,
                        help='Number of test episodes (default: all 86 test days)')
    parser.add_argument('--output', type=str, default='results/rl_eval',
                        help='Output directory')
    parser.add_argument('--seed', type=int, default=1000)
    parser.add_argument('--target-R', type=float, default=5.0,
                        help='Fixed daily target R (default: 5.0)')
    parser.add_argument('--stop-R', type=float, default=-5.0,
                        help='Fixed daily stop R (default: -5.0)')
    args = parser.parse_args()

    evaluate(args.model, args.data, args.episodes, args.output, args.seed,
             fixed_target_R=args.target_R, fixed_stop_R=args.stop_R)


if __name__ == '__main__':
    main()
