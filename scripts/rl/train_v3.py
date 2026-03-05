"""
Train V3 Agent — QR-DQN with Behavior Cloning warmstart.

Seeds the replay buffer with BC transitions before training begins.
No ForceEntryWrapper needed — BC provides a baseline policy.

Usage:
    python -m scripts.rl.train_v3 --steps 500000 --bc-data results/bc_data_v3/bc_transitions.npz
    python -m scripts.rl.train_v3 --steps 500000 --eval-freq 5000
    python -m scripts.rl.train_v3 --resume results/rl_models_v3/v3_model_latest.zip

Output:
    results/rl_models_v3/v3_model_latest.zip  — latest checkpoint
    results/rl_models_v3/best_model.zip       — best eval performance
    results/rl_models_v3/training_log.csv     — per-episode metrics
"""

import argparse
import csv
import logging
import os
import sys
import time as time_mod
from pathlib import Path

import gymnasium
import numpy as np
from gymnasium import spaces

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

from sb3_contrib import QRDQN
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback
from stable_baselines3.common.monitor import Monitor

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

# Time-series split
TRAIN_END = '2024-12-31'
TEST_START = '2025-01-01'

ACTION_NAMES = [
    'HOLD', 'ENTER_TP_0.5R', 'ENTER_TP_1.0R', 'ENTER_TP_2.0R', 'ENTER_TP_3.0R',
    'MKT_EXIT_1', 'MKT_EXIT_2', 'MKT_EXIT_3', 'MKT_EXIT_4', 'MKT_EXIT_5',
    'EXIT_ALL', 'STOP_SESSION',
]


# ---------------------------------------------------------------------------
# EpisodeLoggerCallback
# ---------------------------------------------------------------------------

class EpisodeLoggerCallback(BaseCallback):
    """Logs episode metrics to CSV and prints progress."""

    def __init__(self, log_path: str, print_freq: int = 10, verbose=0):
        super().__init__(verbose)
        self.log_path = Path(log_path)
        self.print_freq = print_freq
        self._episode_count = 0
        self._episode_rewards = []
        self._csv_file = None
        self._csv_writer = None

    def _on_training_start(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._csv_file = open(self.log_path, 'w', newline='')
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow([
            'episode', 'timestep', 'reward', 'length',
            'cumulative_R', 'trades', 'avg_reward_20', 'avg_cumR_20',
        ])

    def _on_step(self):
        infos = self.locals.get('infos', [])
        for info in infos:
            if 'episode' in info:
                ep_reward = info['episode']['r']
                ep_length = info['episode']['l']
                self._episode_count += 1
                self._episode_rewards.append(ep_reward)

                cum_R = info.get('final_cumR', 0.0)
                trades = info.get('final_trades', 0)

                recent = self._episode_rewards[-20:]
                avg_r = np.mean(recent)

                self._csv_writer.writerow([
                    self._episode_count, self.num_timesteps,
                    f'{ep_reward:.3f}', ep_length,
                    f'{cum_R:.3f}', trades,
                    f'{avg_r:.3f}', f'{cum_R:.3f}',
                ])
                self._csv_file.flush()

                if self._episode_count % self.print_freq == 0:
                    logger.info(
                        f'Ep {self._episode_count:5d} | '
                        f'step {self.num_timesteps:7d} | '
                        f'R={ep_reward:+6.2f} | '
                        f'cumR={cum_R:+6.2f} | '
                        f'trades={trades:3d} | '
                        f'avg20={avg_r:+6.2f}'
                    )
        return True

    def _on_training_end(self):
        if self._csv_file:
            self._csv_file.close()


class DetailedEvalCallback(EvalCallback):
    """EvalCallback with logging."""

    def _on_step(self):
        result = super()._on_step()
        if self.n_calls % self.eval_freq == 0 and self.last_mean_reward is not None:
            logger.info(
                f'  [EVAL] step={self.num_timesteps} | '
                f'mean_reward={self.last_mean_reward:+.2f} | '
                f'best={self.best_mean_reward:+.2f}'
            )
        return result


# ---------------------------------------------------------------------------
# BC buffer seeding
# ---------------------------------------------------------------------------

def seed_replay_buffer(model, bc_path: str):
    """Load BC transitions and add them to the model's replay buffer."""
    logger.info(f'Loading BC data from {bc_path}...')
    data = np.load(bc_path)
    obs = data['obs']
    actions = data['actions']
    rewards = data['rewards']
    next_obs = data['next_obs']
    dones = data['dones']

    n = len(obs)
    logger.info(f'BC data: {n} transitions')

    # Log action distribution
    action_dist = {}
    for name_idx, name in enumerate(ACTION_NAMES):
        count = np.sum(actions == name_idx)
        if count > 0:
            action_dist[name] = count
    logger.info(f'  Action distribution: {action_dist}')

    buffer = model.replay_buffer

    for i in range(n):
        buffer.add(
            obs=obs[i:i+1],
            next_obs=next_obs[i:i+1],
            action=np.array([actions[i]]),
            reward=np.array([rewards[i]]),
            done=np.array([dones[i]]),
            infos=[{}],
        )

    logger.info(f'Seeded buffer with {n} BC transitions (buffer pos={buffer.pos})')
    return n


# ---------------------------------------------------------------------------
# Environment creation
# ---------------------------------------------------------------------------

def make_env(data_path, eval_mode=False, seed=42,
             start_date=None, end_date=None):
    """Create a monitored TradingSessionEnv."""
    from scripts.rl.env_v3 import TradingSessionEnv

    env = TradingSessionEnv(
        data_path=data_path,
        eval_mode=eval_mode,
        seed=seed,
        start_date=start_date,
        end_date=end_date,
        fixed_target_R=5.0,
        fixed_stop_R=-5.0,
    )

    env = Monitor(env)
    return env


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Train V3 QR-DQN with BC warmstart')
    parser.add_argument('--steps', type=int, default=500_000,
                        help='Total training timesteps (default: 500K)')
    parser.add_argument('--bc-data', type=str,
                        default='results/bc_data_v3/bc_transitions.npz',
                        help='Path to BC transitions NPZ')
    parser.add_argument('--eval-freq', type=int, default=5000)
    parser.add_argument('--eval-episodes', type=int, default=20)
    parser.add_argument('--data', type=str,
                        default='data/nifty_options_full.parquet')
    parser.add_argument('--output', type=str, default='results/rl_models_v3')
    parser.add_argument('--resume', type=str, default=None,
                        help='Resume from saved model .zip')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--print-freq', type=int, default=10)
    parser.add_argument('--train-end', type=str, default=TRAIN_END,
                        help=f'Training data end date (default: {TRAIN_END})')
    parser.add_argument('--test-start', type=str, default=TEST_START,
                        help=f'Eval data start date (default: {TEST_START})')
    parser.add_argument('--no-bc', action='store_true',
                        help='Skip BC seeding (train from scratch)')
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Training env
    logger.info(f'Creating training env (up to {args.train_end})...')
    t0 = time_mod.time()
    train_env = make_env(
        args.data, eval_mode=False, seed=args.seed,
        end_date=args.train_end,
    )
    logger.info(f'Training env ready in {time_mod.time()-t0:.1f}s')

    # Eval env
    logger.info(f'Creating eval env (from {args.test_start})...')
    t0 = time_mod.time()
    eval_env = make_env(
        args.data, eval_mode=True, seed=args.seed + 1000,
        start_date=args.test_start,
    )
    logger.info(f'Eval env ready in {time_mod.time()-t0:.1f}s')

    # Model
    if args.resume:
        logger.info(f'Resuming from {args.resume}')
        model = QRDQN.load(args.resume, env=train_env)
    else:
        logger.info('Creating QR-DQN model...')
        model = QRDQN(
            'MlpPolicy',
            train_env,
            policy_kwargs=dict(
                net_arch=[256, 128],
                n_quantiles=51,
            ),
            learning_rate=1e-4,
            buffer_size=100_000,
            learning_starts=0,
            batch_size=256,
            gamma=0.99,
            tau=1.0,
            train_freq=4,
            gradient_steps=1,
            target_update_interval=1000,
            exploration_fraction=0.3,
            exploration_initial_eps=0.5,
            exploration_final_eps=0.05,
            verbose=0,
            seed=args.seed,
            device='auto',
        )

    # Model info
    total_params = sum(p.numel() for p in model.policy.parameters())
    logger.info(f'Model params: {total_params:,} ({total_params * 4 / 1024:.1f} KB)')

    # Seed buffer with BC data
    bc_path = Path(args.bc_data)
    if not args.no_bc and bc_path.exists():
        bc_count = seed_replay_buffer(model, str(bc_path))
        logger.info(f'BC warmstart: {bc_count} transitions loaded')
    elif args.no_bc:
        logger.info('BC seeding skipped (--no-bc)')
    else:
        logger.warning(f'BC data not found at {bc_path} — training from scratch')

    # Callbacks
    episode_logger = EpisodeLoggerCallback(
        log_path=str(output_dir / 'training_log.csv'),
        print_freq=args.print_freq,
    )

    eval_callback = DetailedEvalCallback(
        eval_env=eval_env,
        n_eval_episodes=args.eval_episodes,
        eval_freq=args.eval_freq,
        best_model_save_path=str(output_dir),
        log_path=str(output_dir / 'eval_log'),
        deterministic=True,
        verbose=0,
    )

    callbacks = [episode_logger, eval_callback]

    # Train
    logger.info(f'Training for {args.steps:,} steps...')
    logger.info(f'  Eval every {args.eval_freq:,} steps ({args.eval_episodes} episodes)')
    logger.info(f'  Output: {output_dir}')
    t0 = time_mod.time()

    model.learn(
        total_timesteps=args.steps,
        callback=callbacks,
        log_interval=None,
        progress_bar=True,
    )

    elapsed = time_mod.time() - t0
    logger.info(f'Training complete in {elapsed:.0f}s ({elapsed/60:.1f} min)')

    # Save final model
    final_path = str(output_dir / 'v3_model_latest')
    model.save(final_path)
    logger.info(f'Saved final model to {final_path}.zip')

    # Summary
    if episode_logger._episode_rewards:
        rewards = episode_logger._episode_rewards
        logger.info(f'Episodes: {len(rewards)}')
        logger.info(f'  First 20 avg:  {np.mean(rewards[:20]):+.2f}')
        logger.info(f'  Last 20 avg:   {np.mean(rewards[-20:]):+.2f}')
        logger.info(f'  Overall avg:   {np.mean(rewards):+.2f}')
        logger.info(f'  Best episode:  {max(rewards):+.2f}')
        logger.info(f'  Worst episode: {min(rewards):+.2f}')


if __name__ == '__main__':
    main()
