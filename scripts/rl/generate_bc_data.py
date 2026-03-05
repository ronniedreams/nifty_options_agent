"""
Generate Behavior Cloning data for V3 env.

Replays V1 logic through TradingSessionEnv to produce labeled transitions:
- ENTRY decisions: V1 filters (VWAP >= 4%, price 100-300, SL% 2-10%) -> ENTER_TP_1.0R or SKIP
- REVIEW decisions: V1 always holds -> HOLD (action=0)
- STOP_SESSION: never used by V1

Output: NPZ file with obs, action, reward, next_obs, done arrays.

Usage:
    python -m scripts.rl.generate_bc_data
    python -m scripts.rl.generate_bc_data --output results/bc_data_v3/bc_transitions.npz
"""

import argparse
import logging
import sys
import time as time_mod
from pathlib import Path

import numpy as np

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

# V1 filter thresholds
V1_MIN_PRICE = 100
V1_MAX_PRICE = 300
V1_MIN_VWAP_PREMIUM = 0.04
V1_MIN_SL_PERCENT = 0.02
V1_MAX_SL_PERCENT = 0.10


def v1_entry_with_price_check(obs: np.ndarray, break_info: dict) -> int:
    """Full V1 filter including price range check on break_info.

    V3 action space:
        0 = HOLD/SKIP
        2 = ENTER_TP_1.0R (V1 always uses 1R target)

    obs[0]  = vwap_premium_pct (break context, 0 during review)
    obs[1]  = sl_pct (break context, 0 during review)
    obs[20] = decision_type (0=entry, 1=review)
    """
    decision_type = obs[20]

    if decision_type >= 0.5:
        return 0  # Review -> HOLD

    if break_info is None:
        return 0

    entry_price = break_info['entry_price']
    vwap_prem = obs[0]
    sl_pct = obs[1]

    # V1 price filter (tighter than env's 50-500)
    if not (V1_MIN_PRICE <= entry_price <= V1_MAX_PRICE):
        return 0
    if vwap_prem < V1_MIN_VWAP_PREMIUM:
        return 0
    if sl_pct < V1_MIN_SL_PERCENT or sl_pct > V1_MAX_SL_PERCENT:
        return 0

    return 2  # ENTER_TP_1.0R


def generate_bc_data(data_path: str, output_path: str,
                     start_date: str = None, end_date: str = None,
                     seed: int = 42):
    """Run all training days through env, labeling each decision with V1 logic."""
    from scripts.rl.env_v3 import TradingSessionEnv

    logger.info('Creating environment...')
    t0 = time_mod.time()
    env = TradingSessionEnv(
        data_path=data_path,
        eval_mode=True,
        seed=seed,
        start_date=start_date,
        end_date=end_date,
        fixed_target_R=5.0,
        fixed_stop_R=-5.0,
    )
    logger.info(f'Env ready in {time_mod.time()-t0:.1f}s')

    n_days = len(env.trading_days)
    logger.info(f'Processing {n_days} days ({env.trading_days[0]} to {env.trading_days[-1]})')

    # Collect transitions
    all_obs = []
    all_actions = []
    all_rewards = []
    all_next_obs = []
    all_dones = []

    entry_count = 0
    skip_count = 0
    hold_count = 0
    total_decisions = 0

    for day_i in range(n_days):
        obs, info = env.reset()
        if info.get('no_decisions'):
            continue

        done = False
        while not done:
            # Get V1 action
            decision = env._current_decision
            if decision is not None:
                break_info = decision.get('break_info')
                action = v1_entry_with_price_check(obs, break_info)
            else:
                action = 0

            # Store transition
            obs_copy = obs.copy()

            # Step env
            next_obs, reward, terminated, truncated, step_info = env.step(action)
            done = terminated or truncated

            all_obs.append(obs_copy)
            all_actions.append(action)
            all_rewards.append(reward)
            all_next_obs.append(next_obs.copy())
            all_dones.append(done)

            total_decisions += 1
            decision_type = obs_copy[20]  # feature 20 = decision_type
            if decision_type < 0.5:
                # Entry decision
                if action == 2:  # ENTER_TP_1.0R
                    entry_count += 1
                else:
                    skip_count += 1
            else:
                hold_count += 1

            obs = next_obs

        if (day_i + 1) % 50 == 0 or day_i == 0:
            logger.info(
                f'Day {day_i+1:3d}/{n_days} | '
                f'transitions={total_decisions} | '
                f'enter={entry_count} skip={skip_count} hold={hold_count}'
            )

    # Convert to arrays
    obs_arr = np.array(all_obs, dtype=np.float32)
    act_arr = np.array(all_actions, dtype=np.int64)
    rew_arr = np.array(all_rewards, dtype=np.float32)
    next_obs_arr = np.array(all_next_obs, dtype=np.float32)
    done_arr = np.array(all_dones, dtype=np.bool_)

    # Save
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        output_path,
        obs=obs_arr,
        actions=act_arr,
        rewards=rew_arr,
        next_obs=next_obs_arr,
        dones=done_arr,
    )

    logger.info('')
    logger.info('=' * 60)
    logger.info('BC DATA GENERATION COMPLETE')
    logger.info('=' * 60)
    logger.info(f'Total transitions: {total_decisions}')
    logger.info(f'  ENTER_TP_1R (action=2): {entry_count} ({100*entry_count/max(1,total_decisions):.1f}%)')
    logger.info(f'  SKIP  (action=0): {skip_count} ({100*skip_count/max(1,total_decisions):.1f}%)')
    logger.info(f'  HOLD  (action=0): {hold_count} ({100*hold_count/max(1,total_decisions):.1f}%)')
    logger.info(f'Saved to: {output_path}')
    logger.info(f'File size: {output_path.stat().st_size / 1024:.1f} KB')
    logger.info('=' * 60)


def main():
    parser = argparse.ArgumentParser(description='Generate BC data for V3 env')
    parser.add_argument('--data', type=str,
                        default='data/nifty_options_full.parquet',
                        help='Path to parquet data')
    parser.add_argument('--output', type=str,
                        default='results/bc_data_v3/bc_transitions.npz',
                        help='Output NPZ path')
    parser.add_argument('--start-date', type=str, default=None,
                        help='Start date (YYYY-MM-DD), default: all')
    parser.add_argument('--end-date', type=str, default='2024-12-31',
                        help='End date (train period)')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()

    generate_bc_data(
        data_path=args.data,
        output_path=args.output,
        start_date=args.start_date,
        end_date=args.end_date,
        seed=args.seed,
    )


if __name__ == '__main__':
    main()
