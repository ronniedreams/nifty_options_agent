"""Trace all trades on a specific day with full detail."""
import sys
import logging
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
from sb3_contrib import QRDQN

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.rl.env_v3 import (
    TradingSessionEnv, ACTION_HOLD, ACTION_ENTER_TP_05, ACTION_ENTER_TP_10,
    ACTION_ENTER_TP_20, ACTION_ENTER_TP_30, ACTION_MARKET_EXIT_1,
    ACTION_EXIT_ALL, ACTION_STOP_SESSION, TP_R_LEVELS, DECISION_ENTRY,
    PyramidPosition,
)

ACTION_NAMES = {
    0: "HOLD/SKIP", 1: "ENTER_TP_0.5R", 2: "ENTER_TP_1.0R",
    3: "ENTER_TP_2.0R", 4: "ENTER_TP_3.0R",
    5: "MKT_EXIT_1", 6: "MKT_EXIT_2", 7: "MKT_EXIT_3",
    8: "MKT_EXIT_4", 9: "MKT_EXIT_5",
    10: "EXIT_ALL", 11: "STOP_SESSION",
}


def main():
    target_episode = int(sys.argv[1]) if len(sys.argv) > 1 else 26
    model_path = sys.argv[2] if len(sys.argv) > 2 else "results/rl_models_v3/best_model.zip"
    data_path = sys.argv[3] if len(sys.argv) > 3 else "data/nifty_options_full.parquet"

    model = QRDQN.load(model_path)

    env = TradingSessionEnv(
        data_path=data_path,
        start_date="2025-01-01",
        eval_mode=True,
        seed=42,
    )

    # Fast-forward to target episode
    for ep in range(target_episode):
        obs, _ = env.reset()
        done = False
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(int(action))
            done = terminated or truncated

    # Now run the target episode with full tracing
    obs, _ = env.reset()
    day_date = env.day.current_date
    print("=" * 90)
    print(f"TRADE LOG: {day_date} (episode {target_episode + 1})")
    print("=" * 90)
    print()

    step_num = 0
    trade_num = 0
    events = []
    done = False

    while not done:
        step_num += 1
        decision_type = obs[20]
        dec_str = "ENTRY" if decision_type < 0.5 else "REVIEW"

        # Snapshot before
        n_pos_before = env._position_count()
        pos_before = [(p.symbol, p.entry_price, p.sl_trigger, p.tp_trigger, p.tp_R_level)
                      for p in env.positions]
        cumR_before = env.cumulative_R
        sl_before = env.sl_fills_today
        tp_before = env.tp_fills_today

        action, _ = model.predict(obs, deterministic=True)
        action = int(action)
        action_name = ACTION_NAMES.get(action, f"ACTION_{action}")

        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated

        n_pos_after = env._position_count()
        cumR_after = env.cumulative_R
        delta_R = cumR_after - cumR_before
        sl_after = env.sl_fills_today
        tp_after = env.tp_fills_today
        sl_new = sl_after - sl_before
        tp_new = tp_after - tp_before

        # Determine what happened
        # 1. Agent action effects
        if action in (ACTION_ENTER_TP_05, ACTION_ENTER_TP_10, ACTION_ENTER_TP_20, ACTION_ENTER_TP_30):
            if n_pos_after > n_pos_before or info.get("entered"):
                new_pos = env.positions[-1] if env.positions else None
                if new_pos:
                    trade_num += 1
                    events.append({
                        "trade": trade_num,
                        "type": "ENTRY",
                        "action": action_name,
                        "symbol": new_pos.symbol,
                        "entry_price": new_pos.entry_price,
                        "sl_trigger": new_pos.sl_trigger,
                        "tp_trigger": new_pos.tp_trigger,
                        "tp_R": new_pos.tp_R_level,
                        "lots": new_pos.lots,
                        "quantity": new_pos.quantity,
                        "delta_R": 0.0,
                        "cumR": cumR_after,
                        "positions": n_pos_after,
                        "decision": dec_str,
                    })

        elif action == ACTION_EXIT_ALL and n_pos_before > 0:
            events.append({
                "trade": f"EXIT_ALL({n_pos_before})",
                "type": "EXIT_ALL",
                "action": action_name,
                "symbol": ", ".join(p[0] for p in pos_before),
                "delta_R": delta_R,
                "cumR": cumR_after,
                "positions": n_pos_after,
                "decision": dec_str,
                "detail": f"Closed {n_pos_before} positions",
            })

        elif 5 <= action <= 9 and delta_R != 0:
            slot = action - 5
            sym = pos_before[slot][0] if slot < len(pos_before) else "?"
            events.append({
                "trade": f"MKT_EXIT",
                "type": "MKT_EXIT",
                "action": action_name,
                "symbol": sym,
                "delta_R": delta_R,
                "cumR": cumR_after,
                "positions": n_pos_after,
                "decision": dec_str,
            })

        # 2. SL/TP fills (happen during _advance_to_next_decision, after agent action)
        if sl_new > 0:
            events.append({
                "trade": f"SL_FILL(x{sl_new})",
                "type": "SL_FILL",
                "action": "(automatic)",
                "detail": f"{sl_new} position(s) hit SL",
                "delta_R": delta_R if action == ACTION_HOLD else 0.0,
                "cumR": cumR_after,
                "positions": n_pos_after,
                "decision": "auto",
            })

        if tp_new > 0:
            events.append({
                "trade": f"TP_FILL(x{tp_new})",
                "type": "TP_FILL",
                "action": "(automatic)",
                "detail": f"{tp_new} position(s) hit TP",
                "delta_R": delta_R if action == ACTION_HOLD else 0.0,
                "cumR": cumR_after,
                "positions": n_pos_after,
                "decision": "auto",
            })

        # Daily limit
        if info.get("daily_limit"):
            events.append({
                "trade": "DAILY_LIMIT",
                "type": "LIMIT",
                "action": "force_exit",
                "detail": f"Daily limit hit, all positions closed",
                "cumR": cumR_after,
                "positions": 0,
                "decision": "auto",
            })

    # Print trade log
    print(f"{'#':<14} {'Decision':<8} {'Action':<16} {'Type':<10} {'Symbol':<25} {'Entry':>7} {'SL':>7} {'TP':>7} {'Lots':>4} {'dR':>7} {'cumR':>7} {'Pos':>3}")
    print("-" * 140)

    for e in events:
        trade_id = str(e.get("trade", ""))
        dec = e.get("decision", "")
        action = e.get("action", "")
        etype = e.get("type", "")
        symbol = e.get("symbol", "")
        entry = f"{e['entry_price']:.1f}" if "entry_price" in e else ""
        sl = f"{e['sl_trigger']:.1f}" if "sl_trigger" in e else ""
        tp = f"{e['tp_trigger']:.1f}" if "tp_trigger" in e else ""
        lots = str(e.get("lots", ""))
        delta = f"{e.get('delta_R', 0):+.3f}" if "delta_R" in e else ""
        cumr = f"{e['cumR']:+.3f}" if "cumR" in e else ""
        pos = str(e.get("positions", ""))
        detail = e.get("detail", "")

        line = f"{trade_id:<14} {dec:<8} {action:<16} {etype:<10} {symbol:<25} {entry:>7} {sl:>7} {tp:>7} {lots:>4} {delta:>7} {cumr:>7} {pos:>3}"
        if detail and etype not in ("ENTRY",):
            line += f"  | {detail}"
        print(line)

    print("-" * 140)
    print(f"Final cumR: {env.cumulative_R:+.3f}R | Trades: {env.trades_today} | SL fills: {env.sl_fills_today} | TP fills: {env.tp_fills_today}")


if __name__ == "__main__":
    main()
