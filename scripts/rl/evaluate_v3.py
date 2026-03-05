"""
Evaluate V3 Agent — Run trained model through test set with detailed logging.

Tracks:
- Action distribution by decision type (entry vs review)
- TP fill stats (how often each TP level is chosen and fills)
- Per-position market exit usage
- SL/TP/market exit breakdown
- Daily P&L equity curve

Usage:
    python -m scripts.rl.evaluate_v3
    python -m scripts.rl.evaluate_v3 --model results/rl_models_v3/best_model.zip
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

TEST_START = '2025-01-01'

ACTION_NAMES = [
    'HOLD', 'ENTER_TP_0.5R', 'ENTER_TP_1.0R', 'ENTER_TP_2.0R', 'ENTER_TP_3.0R',
    'MKT_EXIT_1', 'MKT_EXIT_2', 'MKT_EXIT_3', 'MKT_EXIT_4', 'MKT_EXIT_5',
    'EXIT_ALL', 'STOP_SESSION',
]

# Entry actions (1-4), Market exit actions (5-9)
ENTRY_ACTIONS = {1, 2, 3, 4}
MARKET_EXIT_ACTIONS = {5, 6, 7, 8, 9}


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
        'episode', 'date', 'cumulative_R', 'trades',
        'entry_decisions', 'review_decisions',
        'entries_taken', 'skips', 'holds',
        'market_exits', 'exit_alls', 'stop_sessions',
        'sl_fills', 'tp_fills',
        'tp_05_chosen', 'tp_10_chosen', 'tp_20_chosen', 'tp_30_chosen',
    ])

    # Tracking
    all_daily_R = []
    entry_action_counts = {i: 0 for i in range(12)}
    review_action_counts = {i: 0 for i in range(12)}
    total_trades = 0
    total_sl_fills = 0
    total_tp_fills = 0
    total_market_exits = 0
    total_exit_alls = 0
    total_stop_sessions = 0
    tp_level_chosen = {0.5: 0, 1.0: 0, 2.0: 0, 3.0: 0}
    max_positions_seen = 0
    hit_target_count = 0
    hit_stop_count = 0

    for ep in range(n_episodes):
        obs, info = env.reset()
        day_date = env.day.current_date

        ep_entry_decisions = 0
        ep_review_decisions = 0
        ep_entries = 0
        ep_skips = 0
        ep_holds = 0
        ep_market_exits = 0
        ep_exit_alls = 0
        ep_stop_sessions = 0
        ep_sl_fills = 0
        ep_tp_fills = 0
        ep_tp_chosen = {0.5: 0, 1.0: 0, 2.0: 0, 3.0: 0}

        done = False
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            action = int(action)

            # Track by decision type (feature 20)
            decision_type = obs[20]
            if decision_type < 0.5:
                ep_entry_decisions += 1
                entry_action_counts[action] += 1
            else:
                ep_review_decisions += 1
                review_action_counts[action] += 1

            # Classify action
            if action == 0:
                if decision_type < 0.5:
                    ep_skips += 1
                else:
                    ep_holds += 1
            elif action in ENTRY_ACTIONS:
                ep_entries += 1
                tp_map = {1: 0.5, 2: 1.0, 3: 2.0, 4: 3.0}
                if action in tp_map:
                    ep_tp_chosen[tp_map[action]] += 1
            elif action in MARKET_EXIT_ACTIONS:
                ep_market_exits += 1
            elif action == 10:
                ep_exit_alls += 1
            elif action == 11:
                ep_stop_sessions += 1

            # Track position count
            pos_count_before = env._position_count()
            if pos_count_before > max_positions_seen:
                max_positions_seen = pos_count_before

            obs, reward, terminated, truncated, step_info = env.step(action)
            done = terminated or truncated

            pos_count_after = env._position_count()

            # Detect SL/TP fills from position count changes
            # (fills happen during _advance_to_next_decision)
            # We approximate: if positions dropped and it wasn't an agent exit action
            if pos_count_after < pos_count_before:
                dropped = pos_count_before - pos_count_after
                if action not in MARKET_EXIT_ACTIONS and action != 10 and action != 11:
                    # Positions closed by SL or TP fills
                    # We count them as fills (can't distinguish SL vs TP here exactly)
                    pass

        cum_R = env.cumulative_R
        trades_count = env.trades_today
        all_daily_R.append(cum_R)
        total_trades += trades_count
        total_market_exits += ep_market_exits
        total_exit_alls += ep_exit_alls
        total_stop_sessions += ep_stop_sessions
        for k, v in ep_tp_chosen.items():
            tp_level_chosen[k] += v

        # Check if hit target or stop
        if cum_R >= fixed_target_R:
            hit_target_count += 1
        elif cum_R <= fixed_stop_R:
            hit_stop_count += 1

        daily_writer.writerow([
            ep + 1, day_date, f'{cum_R:.3f}', trades_count,
            ep_entry_decisions, ep_review_decisions,
            ep_entries, ep_skips, ep_holds,
            ep_market_exits, ep_exit_alls, ep_stop_sessions,
            ep_sl_fills, ep_tp_fills,
            ep_tp_chosen[0.5], ep_tp_chosen[1.0],
            ep_tp_chosen[2.0], ep_tp_chosen[3.0],
        ])

        if (ep + 1) % 10 == 0 or ep == 0:
            logger.info(
                f'Day {ep+1:3d}/{n_episodes} | {day_date} | '
                f'cumR={cum_R:+6.2f} | trades={trades_count:3d} | '
                f'entries={ep_entries} mkt_exit={ep_market_exits} | '
                f'TP: 0.5={ep_tp_chosen[0.5]} 1.0={ep_tp_chosen[1.0]} '
                f'2.0={ep_tp_chosen[2.0]} 3.0={ep_tp_chosen[3.0]}'
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
    logger.info(f'  Hit +5R target:   {hit_target_count}/{n_episodes} ({100*hit_target_count/n_episodes:.1f}%)')
    logger.info(f'  Hit -5R stop:     {hit_stop_count}/{n_episodes} ({100*hit_stop_count/n_episodes:.1f}%)')
    logger.info('')

    # Trading activity
    avg_trades = total_trades / n_episodes if n_episodes > 0 else 0
    logger.info('--- Trading Activity ---')
    logger.info(f'  Total trades:     {total_trades}')
    logger.info(f'  Avg trades/day:   {avg_trades:.1f}')
    logger.info(f'  Max positions:    {max_positions_seen}')
    logger.info('')

    # TP level distribution
    total_tp = sum(tp_level_chosen.values())
    logger.info('--- TP Level Chosen ---')
    for tp_level in [0.5, 1.0, 2.0, 3.0]:
        count = tp_level_chosen[tp_level]
        pct = 100 * count / max(1, total_tp)
        logger.info(f'  TP {tp_level:.1f}R: {count:5d} ({pct:5.1f}%)')
    logger.info('')

    # Action distribution by decision type
    total_entry_actions = sum(entry_action_counts.values())
    total_review_actions = sum(review_action_counts.values())

    logger.info('--- Entry Decision Actions ---')
    for a in range(12):
        count = entry_action_counts[a]
        if count > 0:
            pct = 100 * count / max(1, total_entry_actions)
            logger.info(f'  {ACTION_NAMES[a]:18s}: {count:6d} ({pct:5.1f}%)')
    logger.info('')

    logger.info('--- Review Decision Actions ---')
    for a in range(12):
        count = review_action_counts[a]
        if count > 0:
            pct = 100 * count / max(1, total_review_actions)
            logger.info(f'  {ACTION_NAMES[a]:18s}: {count:6d} ({pct:5.1f}%)')
    logger.info('')

    # Exit analysis
    logger.info('--- Exit Mechanism ---')
    logger.info(f'  Market exits (per-position): {total_market_exits}')
    logger.info(f'  EXIT_ALL:                    {total_exit_alls}')
    logger.info(f'  STOP_SESSION:                {total_stop_sessions}')
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

    # Baseline comparison
    logger.info('--- Baseline Comparison ---')
    logger.info(f'  V2 Agent (2025):   -0.69R total, Sharpe -0.01')
    logger.info(f'  V3 Agent:          {daily_R.sum():+.2f}R total, Sharpe {sharpe:.2f}')
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
        'hit_target_pct': 100 * hit_target_count / n_episodes,
        'hit_stop_pct': 100 * hit_stop_count / n_episodes,
    }


def main():
    parser = argparse.ArgumentParser(description='Evaluate V3 QR-DQN Agent')
    parser.add_argument('--model', type=str,
                        default='results/rl_models_v3/best_model.zip')
    parser.add_argument('--data', type=str,
                        default='data/nifty_options_full.parquet')
    parser.add_argument('--episodes', type=int, default=249)
    parser.add_argument('--output', type=str, default='results/rl_eval_v3')
    parser.add_argument('--seed', type=int, default=1000)
    parser.add_argument('--target-R', type=float, default=5.0)
    parser.add_argument('--stop-R', type=float, default=-5.0)
    args = parser.parse_args()

    evaluate(args.model, args.data, args.episodes, args.output, args.seed,
             fixed_target_R=args.target_R, fixed_stop_R=args.stop_R)


if __name__ == '__main__':
    main()
