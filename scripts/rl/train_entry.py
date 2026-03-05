"""
Train Entry Model — QR-DQN on TradingEntryEnv.

Trains the entry agent from scratch (no behavior cloning).
QR-DQN learns distributional Q-values: Q(state, action) as a distribution
over possible returns, not just the mean.

Usage:
    python -m scripts.rl.train_entry                     # Default: 200K steps
    python -m scripts.rl.train_entry --steps 500000      # Longer run
    python -m scripts.rl.train_entry --eval-freq 5000    # More frequent eval
    python -m scripts.rl.train_entry --resume results/rl_models/entry_model_latest.zip

Output:
    results/rl_models/entry_model_latest.zip   — latest checkpoint
    results/rl_models/entry_model_best.zip     — best eval performance
    results/rl_models/training_log.csv         — per-episode metrics
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

# Silence TF/JAX warnings from SB3
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

from sb3_contrib import QRDQN
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback
from stable_baselines3.common.monitor import Monitor

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%H:%M:%S',
)
# Suppress noisy loggers
logging.getLogger('baseline_v1_live.swing_detector').setLevel(logging.ERROR)
logging.getLogger('baseline_v1_live.config').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ForceEntryWrapper — prevents lazy-agent SKIP-everything collapse
# ---------------------------------------------------------------------------

class ForceEntryWrapper(gymnasium.ActionWrapper):
    """Override SKIP (action=0) with a random ENTER action at some rate.

    During early training the agent discovers SKIP gives 0 reward, which
    looks better than noisy ENTER outcomes. This wrapper forces the agent
    to experience ENTER transitions so it can learn which entries are
    actually profitable.

    The rate decays linearly: starts at `initial_rate` and drops to
    `final_rate` over `decay_steps` training steps.
    """

    def __init__(self, env, initial_rate=0.5, final_rate=0.0,
                 decay_steps=100_000):
        super().__init__(env)
        self.initial_rate = initial_rate
        self.final_rate = final_rate
        self.decay_steps = decay_steps
        self._step_count = 0
        self._rng = np.random.default_rng(42)
        self._forced_entries = 0
        self._total_skips = 0

    @property
    def force_rate(self):
        """Current probability of overriding SKIP -> random ENTER."""
        progress = min(1.0, self._step_count / max(self.decay_steps, 1))
        return self.initial_rate + progress * (self.final_rate - self.initial_rate)

    def action(self, action):
        self._step_count += 1
        if action == 0:  # SKIP
            self._total_skips += 1
            if self._rng.random() < self.force_rate:
                # Override to random ENTER (actions 1-6)
                self._forced_entries += 1
                return int(self._rng.integers(1, 7))
        return action


class ForceEntryDecayCallback(BaseCallback):
    """Logs the ForceEntryWrapper stats periodically."""

    def __init__(self, wrapper: ForceEntryWrapper, log_freq=500, verbose=0):
        super().__init__(verbose)
        self.wrapper = wrapper

    def _on_step(self):
        # Sync wrapper step count with training steps
        self.wrapper._step_count = self.num_timesteps
        return True


# ---------------------------------------------------------------------------
# Custom callback — logs per-episode metrics
# ---------------------------------------------------------------------------

class EpisodeLoggerCallback(BaseCallback):
    """Logs episode metrics to CSV and prints progress."""

    def __init__(self, log_path: str, print_freq: int = 10, verbose=0):
        super().__init__(verbose)
        self.log_path = Path(log_path)
        self.print_freq = print_freq
        self._episode_count = 0
        self._episode_rewards = []
        self._episode_lengths = []
        self._csv_file = None
        self._csv_writer = None

    def _on_training_start(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._csv_file = open(self.log_path, 'w', newline='')
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow([
            'episode', 'timestep', 'reward', 'length',
            'cumulative_R', 'trades', 'target_R', 'stop_R',
            'avg_reward_20', 'avg_trades_20',
        ])

    def _on_step(self):
        # Check for episode end via Monitor wrapper info
        infos = self.locals.get('infos', [])
        for info in infos:
            if 'episode' in info:
                ep_reward = info['episode']['r']
                ep_length = info['episode']['l']
                self._episode_count += 1
                self._episode_rewards.append(ep_reward)
                self._episode_lengths.append(ep_length)

                # Read episode summary from info (set before reset)
                cum_R = info.get('final_cumR', 0.0)
                trades = info.get('final_trades', 0)
                target_R = info.get('final_target_R', 5.0)
                stop_R = info.get('final_stop_R', -5.0)

                # Rolling averages
                recent_rewards = self._episode_rewards[-20:]
                recent_trades = self._episode_lengths[-20:]
                avg_r = np.mean(recent_rewards)

                self._csv_writer.writerow([
                    self._episode_count, self.num_timesteps,
                    f'{ep_reward:.3f}', ep_length,
                    f'{cum_R:.3f}', trades,
                    f'{target_R:.0f}', f'{stop_R:.0f}',
                    f'{avg_r:.3f}', f'{np.mean(recent_trades):.1f}',
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


# ---------------------------------------------------------------------------
# Eval callback with episode-level metrics
# ---------------------------------------------------------------------------

class DetailedEvalCallback(EvalCallback):
    """EvalCallback that also prints eval episode details."""

    def _on_step(self):
        result = super()._on_step()
        # Print when eval happens
        if self.n_calls % self.eval_freq == 0 and self.last_mean_reward is not None:
            logger.info(
                f'  [EVAL] step={self.num_timesteps} | '
                f'mean_reward={self.last_mean_reward:+.2f} | '
                f'best={self.best_mean_reward:+.2f}'
            )
        return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

## Time-series split dates (consistent with probability model)
TRAIN_END = '2023-05-22'
TEST_START = '2023-07-22'   # skip val period (May 23 - Jul 21)


def make_env(data_path, eval_mode=False, seed=42, share_data_from=None,
             start_date=None, end_date=None, force_entry_rate=0.0,
             force_entry_decay_steps=100_000):
    """Create a monitored TradingEntryEnv.

    If share_data_from is provided (another TradingEntryEnv), reuse its
    loaded data to avoid loading the 430MB parquet twice.

    Returns (monitored_env, force_wrapper_or_None).
    """
    from scripts.rl.env import TradingEntryEnv

    if share_data_from is not None:
        # Create env sharing data, but with different date range
        env = TradingEntryEnv.__new__(TradingEntryEnv)
        gymnasium.Env.__init__(env)
        env._data = share_data_from._data
        env._day_groups = share_data_from._day_groups
        all_days = sorted(share_data_from._day_groups.keys())
        from datetime import date as date_cls
        if start_date:
            s = date_cls.fromisoformat(start_date)
            all_days = [d for d in all_days if d >= s]
        if end_date:
            e = date_cls.fromisoformat(end_date)
            all_days = [d for d in all_days if d <= e]
        env.trading_days = all_days
        env.eval_mode = eval_mode
        env._day_idx = 0
        env._rng = np.random.default_rng(seed)
        env.outcome_model = None
        n_features = 18
        env.action_space = spaces.Discrete(8)
        env.observation_space = spaces.Box(
            -np.inf, np.inf, shape=(n_features,), dtype=np.float32,
        )
        env.day = None
        env.target_R = 5.0
        env.stop_R = -5.0
        env._fixed_target_R = None
        env._fixed_stop_R = None
        env.positions = []
        env.cumulative_R = 0.0
        env.trades_today = 0
        env.bar_idx = 0
        env._current_break = None
        logger.info(
            f'Shared env: {len(env.trading_days)} days '
            f'({env.trading_days[0]} to {env.trading_days[-1]})'
        )
    else:
        env = TradingEntryEnv(
            data_path=data_path,
            eval_mode=eval_mode,
            seed=seed,
            start_date=start_date,
            end_date=end_date,
        )

    # Wrap with forced entry exploration (training only)
    force_wrapper = None
    if force_entry_rate > 0:
        force_wrapper = ForceEntryWrapper(
            env,
            initial_rate=force_entry_rate,
            final_rate=0.0,
            decay_steps=force_entry_decay_steps,
        )
        env = force_wrapper

    env = Monitor(env)
    return env, force_wrapper


def main():
    parser = argparse.ArgumentParser(description='Train QR-DQN Entry Model')
    parser.add_argument('--steps', type=int, default=200_000,
                        help='Total training timesteps (default: 200K)')
    parser.add_argument('--eval-freq', type=int, default=5000,
                        help='Evaluate every N steps (default: 5000)')
    parser.add_argument('--eval-episodes', type=int, default=20,
                        help='Episodes per eval (default: 20)')
    parser.add_argument('--data', type=str,
                        default='data/nifty_options_combined.parquet',
                        help='Path to parquet data')
    parser.add_argument('--output', type=str, default='results/rl_models',
                        help='Output directory for models')
    parser.add_argument('--resume', type=str, default=None,
                        help='Resume from saved model .zip')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--print-freq', type=int, default=10,
                        help='Print every N episodes')
    parser.add_argument('--force-entry', type=float, default=0.5,
                        help='Initial forced entry rate (0-1). '
                             'Decays to 0 over training. (default: 0.5)')
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create training env (train period only, with forced entry)
    logger.info(f'Creating training env (up to {TRAIN_END})...')
    t0 = time_mod.time()
    train_env, force_wrapper = make_env(
        args.data, eval_mode=False, seed=args.seed,
        end_date=TRAIN_END,
        force_entry_rate=args.force_entry,
        force_entry_decay_steps=int(args.steps * 0.7),  # decay over 70% of training
    )
    logger.info(f'Training env ready in {time_mod.time()-t0:.1f}s')
    if force_wrapper:
        logger.info(
            f'  Force entry: {args.force_entry:.0%} -> 0% '
            f'over {int(args.steps * 0.7):,} steps'
        )

    # Create eval env — test period, no forced entry (pure policy)
    logger.info(f'Creating eval env (from {TEST_START}, shared data)...')
    # Unwrap to get TradingEntryEnv (past Monitor and ForceEntry wrappers)
    train_inner = train_env
    while hasattr(train_inner, 'env'):
        train_inner = train_inner.env
    eval_env, _ = make_env(
        args.data, eval_mode=True, seed=args.seed + 1000,
        share_data_from=train_inner,
        start_date=TEST_START,
    )
    logger.info('Eval env ready')

    # Model
    if args.resume:
        logger.info(f'Resuming from {args.resume}')
        model = QRDQN.load(args.resume, env=train_env)
    else:
        logger.info('Creating QR-DQN model from scratch...')
        model = QRDQN(
            'MlpPolicy',
            train_env,
            policy_kwargs=dict(
                net_arch=[128, 64],
                n_quantiles=50,
            ),
            learning_rate=1e-4,
            buffer_size=100_000,
            learning_starts=500,       # Start learning after 500 steps (~10 episodes)
            batch_size=64,
            gamma=1.0,                 # No discount — episode is one day, reward = R-multiples
            tau=1.0,                   # Hard target update (default for QRDQN)
            train_freq=4,
            gradient_steps=1,
            target_update_interval=1000,
            exploration_fraction=0.3,  # Explore for 30% of training
            exploration_initial_eps=1.0,
            exploration_final_eps=0.05,
            verbose=0,
            seed=args.seed,
            device='auto',
        )

    # Print model info
    total_params = sum(
        p.numel() for p in model.policy.parameters()
    )
    logger.info(f'Model params: {total_params:,} ({total_params * 4 / 1024:.1f} KB)')

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
    if force_wrapper:
        callbacks.append(ForceEntryDecayCallback(force_wrapper))

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
    final_path = str(output_dir / 'entry_model_latest')
    model.save(final_path)
    logger.info(f'Saved final model to {final_path}.zip')

    # Quick summary
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
