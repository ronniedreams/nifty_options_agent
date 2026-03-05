"""
Evaluate V3 Agent — Run trained model through test set with detailed logging.

Extends evaluate_entry.py with:
- Action distribution by decision type (entry vs review)
- Pyramiding stats (sequences started, max depth)
- EXIT_ALL / STOP_SESSION usage patterns
- Side-by-side comparison with V1 baselines

Usage:
    python -m scripts.rl.evaluate_v3
    python -m scripts.rl.evaluate_v3 --model results/rl_models_v3/best_model.zip
    python -m scripts.rl.evaluate_v3 --target-R 5.0 --stop-R -5.0
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

TEST_START = '2023-07-22'

ACTION_NAMES = ['SKIP/HOLD', 'ENTER', 'EXIT_ALL', 'STOP_SESSION']


def evaluate(model_path, data_path, n_episodes, output_dir, seed=42,
             fixed_target_R=5.0, fixed_stop_R=-5.0):
    from scripts.rl.env_v3 import TradingSessionEnv

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f'Loading model from {model_path}')
    model = QRDQN.load(model_path)
    logger.info(f'Model action space: Discrete({model.action_space.n})')

    logger.info(f'Creating eval env (from {TEST_START})...')
    env = TradingSessionEnv(
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
    logger.info(f'Running {n_episodes} episodes (deterministic)')

    # Daily CSV
    daily_csv_path = output_dir / 'eval_daily_v3.csv'
    daily_csv = open(daily_csv_path, 'w', newline='')
    daily_writer = csv.writer(daily_csv)
    daily_writer.writerow([
        'episode', 'date', 'target_R', 'stop_R',
        'cumulative_R', 'trades', 'total_decisions',
        'entry_decisions', 'review_decisions',
        'entry_skips', 'entry_enters',
        'review_holds', 'review_exit_alls', 'review_stops',
        'sl_exits', 'exit_all_count', 'stop_session_count',
    ])

    # Tracking
    all_daily_R = []
    entry_action_counts = {i: 0 for i in range(4)}
    review_action_counts = {i: 0 for i in range(4)}
    total_trades = 0
    total_sl_exits = 0
    total_exit_all_uses = 0
    total_stop_session_uses = 0
    max_pyramid_depth_seen = 0
    pyramid_sequences_started = 0

    for ep in range(n_episodes):
        obs, info = env.reset()
        day_date = env.day.current_date

        entry_decisions = 0
        review_decisions = 0
        ep_entry_skips = 0
        ep_entry_enters = 0
        ep_review_holds = 0
        ep_review_exits = 0
        ep_review_stops = 0
        ep_sl_exits = 0
        ep_exit_alls = 0
        ep_stops = 0
        prev_pos_count = 0

        done = False
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            action = int(action)

            # Track by decision type
            decision_type = obs[18]
            if decision_type < 0.5:
                entry_decisions += 1
                entry_action_counts[action] += 1
                if action == 0:
                    ep_entry_skips += 1
                elif action == 1:
                    ep_entry_enters += 1
            else:
                review_decisions += 1
                review_action_counts[action] += 1
                if action == 0:
                    ep_review_holds += 1
                elif action == 2:
                    ep_review_exits += 1
                    ep_exit_alls += 1
                elif action == 3:
                    ep_review_stops += 1
                    ep_stops += 1

            # Track pyramid depth
            pos_count = env._position_count()
            if pos_count > max_pyramid_depth_seen:
                max_pyramid_depth_seen = pos_count

            obs, reward, terminated, truncated, step_info = env.step(action)
            done = terminated or truncated

            # Detect SL exits (position count dropped without EXIT_ALL/STOP)
            new_pos_count = env._position_count()
            if new_pos_count < pos_count and action not in [2, 3]:
                ep_sl_exits += (pos_count - new_pos_count)

            # Track new sequences
            if new_pos_count > 0 and prev_pos_count == 0:
                pyramid_sequences_started += 1
            prev_pos_count = new_pos_count

        cum_R = env.cumulative_R
        trades_count = env.trades_today
        all_daily_R.append(cum_R)
        total_trades += trades_count
        total_sl_exits += ep_sl_exits
        total_exit_all_uses += ep_exit_alls
        total_stop_session_uses += ep_stops

        daily_writer.writerow([
            ep + 1, day_date, f'{fixed_target_R:.0f}', f'{fixed_stop_R:.0f}',
            f'{cum_R:.3f}', trades_count,
            entry_decisions + review_decisions,
            entry_decisions, review_decisions,
            ep_entry_skips, ep_entry_enters,
            ep_review_holds, ep_review_exits, ep_review_stops,
            ep_sl_exits, ep_exit_alls, ep_stops,
        ])

        if (ep + 1) % 10 == 0 or ep == 0:
            logger.info(
                f'Day {ep+1:3d}/{n_episodes} | {day_date} | '
                f'cumR={cum_R:+6.2f} | trades={trades_count:3d} | '
                f'entry={entry_decisions}(skip={ep_entry_skips},enter={ep_entry_enters}) | '
                f'review={review_decisions}(hold={ep_review_holds},exit={ep_review_exits},stop={ep_review_stops})'
            )

    daily_csv.close()

    # -------------------------------------------------------------------------
    # Aggregate statistics
    # -------------------------------------------------------------------------
    daily_R = np.array(all_daily_R)
    profitable_days = np.sum(daily_R > 0)
    losing_days = np.sum(daily_R < 0)
    flat_days = np.sum(daily_R == 0)

    logger.info('')
    logger.info('=' * 70)
    logger.info('V3 EVALUATION RESULTS')
    logger.info('=' * 70)
    logger.info(f'Test period: {env.trading_days[0]} to {env.trading_days[min(n_episodes-1, n_days-1)]}')
    logger.info(f'Episodes: {n_episodes}')
    logger.info('')

    # Daily P&L
    logger.info('--- Daily P&L ---')
    logger.info(f'  Mean daily R:     {daily_R.mean():+.3f}')
    logger.info(f'  Median daily R:   {np.median(daily_R):+.3f}')
    logger.info(f'  Std daily R:      {daily_R.std():.3f}')
    sharpe = 0.0
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
    logger.info(f'  Pyramid sequences: {pyramid_sequences_started}')
    logger.info(f'  Max pyramid depth: {max_pyramid_depth_seen}')
    logger.info('')

    # Action distribution by decision type
    total_entry_actions = sum(entry_action_counts.values())
    total_review_actions = sum(review_action_counts.values())

    logger.info('--- Entry Decision Actions ---')
    for a, count in sorted(entry_action_counts.items()):
        pct = 100 * count / max(1, total_entry_actions)
        logger.info(f'  {ACTION_NAMES[a]:15s}: {count:6d} ({pct:5.1f}%)')
    logger.info('')

    logger.info('--- Review Decision Actions ---')
    for a, count in sorted(review_action_counts.items()):
        pct = 100 * count / max(1, total_review_actions)
        logger.info(f'  {ACTION_NAMES[a]:15s}: {count:6d} ({pct:5.1f}%)')
    logger.info('')

    # Exit analysis
    total_exits = total_sl_exits + total_exit_all_uses + total_stop_session_uses
    if total_exits == 0:
        total_exits = 1  # avoid division by zero
    logger.info('--- Exit Analysis ---')
    logger.info(f'  SL exits:         {total_sl_exits} ({100*total_sl_exits/total_exits:.1f}%)')
    logger.info(f'  EXIT_ALL:         {total_exit_all_uses} ({100*total_exit_all_uses/total_exits:.1f}%)')
    logger.info(f'  STOP_SESSION:     {total_stop_session_uses} ({100*total_stop_session_uses/total_exits:.1f}%)')
    logger.info(f'  Force exit (EOD): counted in SL/daily limit')
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

    # V1 baseline comparison
    logger.info('--- V1 Baseline Comparison ---')
    logger.info(f'  V1 (+5R cap):  +0.32 R/day, Sharpe 0.39')
    logger.info(f'  V1 (no cap):   +1.39 R/day, Sharpe 2.42')
    logger.info(f'  V3 Agent:      {daily_R.mean():+.2f} R/day, Sharpe {sharpe:.2f}')
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
        'sharpe': sharpe,
        'max_drawdown': float(max_drawdown),
        'exit_all_pct': 100 * total_exit_all_uses / max(1, total_exits),
        'stop_session_pct': 100 * total_stop_session_uses / max(1, total_exits),
    }


def main():
    parser = argparse.ArgumentParser(description='Evaluate V3 QR-DQN Agent')
    parser.add_argument('--model', type=str,
                        default='results/rl_models_v3/best_model.zip')
    parser.add_argument('--data', type=str,
                        default='data/nifty_options_combined.parquet')
    parser.add_argument('--episodes', type=int, default=86)
    parser.add_argument('--output', type=str, default='results/rl_eval_v3')
    parser.add_argument('--seed', type=int, default=1000)
    parser.add_argument('--target-R', type=float, default=5.0)
    parser.add_argument('--stop-R', type=float, default=-5.0)
    args = parser.parse_args()

    evaluate(args.model, args.data, args.episodes, args.output, args.seed,
             fixed_target_R=args.target_R, fixed_stop_R=args.stop_R)


if __name__ == '__main__':
    main()
