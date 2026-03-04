"""
Position Tracker for V3 Live Trading

R-multiple accounting with pyramid awareness.
Tracks cumulative R, per-position unrealized R, and daily P&L.

Key differences from baseline:
- Pyramid-aware: tracks sequences, not individual positions
- Transaction costs included in R calculation (matches env_v3)
- Simpler exit: market orders (no proactive limit)
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pytz

from .config import (
    R_VALUE,
    BROKERAGE_PER_TRADE,
    STT_RATE,
    EXCHANGE_TXN_RATE,
    GST_RATE,
    DAILY_TARGET_R,
    DAILY_STOP_R,
)
from .pyramid_manager import PyramidManager, PyramidPosition, PyramidSequence

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class PositionTrackerV3:
    """
    Track R-multiples and daily P&L for V3 pyramid positions.
    """

    def __init__(self):
        self.cumulative_R = 0.0
        self.trades_today = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.max_drawdown_R = 0.0
        self.peak_R = 0.0
        self._trade_log: List[dict] = []

    def reset_daily(self):
        self.cumulative_R = 0.0
        self.trades_today = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.max_drawdown_R = 0.0
        self.peak_R = 0.0
        self._trade_log.clear()

    def transaction_cost_R(self, entry_price: float, exit_price: float,
                           quantity: int) -> float:
        """Calculate transaction cost in R units. Matches env_v3 exactly."""
        buy_turnover = entry_price * quantity
        sell_turnover = exit_price * quantity
        total_turnover = buy_turnover + sell_turnover
        brokerage = BROKERAGE_PER_TRADE
        stt = STT_RATE * sell_turnover
        exchange_txn = EXCHANGE_TXN_RATE * total_turnover
        gst = GST_RATE * (brokerage + exchange_txn)
        total_cost = brokerage + stt + exchange_txn + gst
        return total_cost / R_VALUE

    def record_sl_exit(self, sequence: PyramidSequence,
                       exit_price: float, exit_reason: str = 'SL_HIT') -> List[dict]:
        """Record exit of all positions in a pyramid sequence via SL hit.

        Returns list of trade records for logging.
        """
        trade_records = []
        for i, pos in enumerate(sequence.positions):
            if pos.actual_R_value > 0:
                # Short position: profit = entry - exit
                realized_R = (
                    (pos.entry_price - exit_price) * pos.quantity
                    / pos.actual_R_value
                )
            else:
                realized_R = 0.0

            cost_R = self.transaction_cost_R(pos.entry_price, exit_price, pos.quantity)
            realized_R -= cost_R

            pnl = (pos.entry_price - exit_price) * pos.quantity
            pnl -= cost_R * R_VALUE

            self.cumulative_R += realized_R
            self.trades_today += 1
            if realized_R > 0:
                self.winning_trades += 1
            else:
                self.losing_trades += 1

            self._update_drawdown()

            record = {
                'symbol': pos.symbol,
                'option_type': pos.option_type,
                'action': 'EXIT',
                'entry_price': pos.entry_price,
                'exit_price': exit_price,
                'quantity': pos.quantity,
                'lots': pos.lots,
                'sl_points': pos.sl_points_at_entry,
                'realized_R': realized_R,
                'pnl': pnl,
                'entry_time': pos.entry_time.isoformat() if isinstance(pos.entry_time, datetime) else str(pos.entry_time),
                'exit_time': datetime.now(IST).isoformat(),
                'exit_reason': exit_reason,
                'sequence_position': i,
                'cumulative_R': self.cumulative_R,
            }
            trade_records.append(record)
            self._trade_log.append(record)

            logger.info(
                f"[RL-V1-EXIT] {pos.symbol} {exit_reason}: "
                f"Entry={pos.entry_price:.2f} Exit={exit_price:.2f} "
                f"R={realized_R:+.2f} PnL={pnl:+.0f} "
                f"CumR={self.cumulative_R:+.2f}"
            )

        return trade_records

    def record_market_exit(self, sequence: PyramidSequence,
                           latest_bars: Dict[str, dict],
                           exit_reason: str = 'EXIT_ALL') -> List[dict]:
        """Record exit of all positions at current market price.

        Used for EXIT_ALL, STOP_SESSION, daily limits, force close.
        """
        trade_records = []
        for i, pos in enumerate(sequence.positions):
            bar = latest_bars.get(pos.symbol)
            if bar is not None:
                exit_price = bar['close']
            else:
                exit_price = pos.entry_price
                logger.warning(f"[RL-V1-EXIT] No bar for {pos.symbol}, using entry price")

            if pos.actual_R_value > 0:
                realized_R = (
                    (pos.entry_price - exit_price) * pos.quantity
                    / pos.actual_R_value
                )
            else:
                realized_R = 0.0

            cost_R = self.transaction_cost_R(pos.entry_price, exit_price, pos.quantity)
            realized_R -= cost_R

            pnl = (pos.entry_price - exit_price) * pos.quantity
            pnl -= cost_R * R_VALUE

            self.cumulative_R += realized_R
            self.trades_today += 1
            if realized_R > 0:
                self.winning_trades += 1
            else:
                self.losing_trades += 1

            self._update_drawdown()

            record = {
                'symbol': pos.symbol,
                'option_type': pos.option_type,
                'action': 'EXIT',
                'entry_price': pos.entry_price,
                'exit_price': exit_price,
                'quantity': pos.quantity,
                'lots': pos.lots,
                'sl_points': pos.sl_points_at_entry,
                'realized_R': realized_R,
                'pnl': pnl,
                'entry_time': pos.entry_time.isoformat() if isinstance(pos.entry_time, datetime) else str(pos.entry_time),
                'exit_time': datetime.now(IST).isoformat(),
                'exit_reason': exit_reason,
                'sequence_position': i,
                'cumulative_R': self.cumulative_R,
            }
            trade_records.append(record)
            self._trade_log.append(record)

            logger.info(
                f"[RL-V1-EXIT] {pos.symbol} {exit_reason}: "
                f"Entry={pos.entry_price:.2f} Exit={exit_price:.2f} "
                f"R={realized_R:+.2f} PnL={pnl:+.0f} "
                f"CumR={self.cumulative_R:+.2f}"
            )

        return trade_records

    def total_unrealized_R(self, pyramid_mgr: PyramidManager,
                           latest_bars: Dict[str, dict]) -> float:
        """Calculate total unrealized R across all positions."""
        total = 0.0
        for pos in pyramid_mgr.all_positions():
            bar = latest_bars.get(pos.symbol)
            if bar is None or pos.actual_R_value <= 0:
                continue
            unrealized = (
                (pos.entry_price - bar['close']) * pos.quantity
                / pos.actual_R_value
            )
            total += unrealized
        return total

    def total_R(self, pyramid_mgr: PyramidManager,
                latest_bars: Dict[str, dict]) -> float:
        """Total R = realized + unrealized."""
        return self.cumulative_R + self.total_unrealized_R(pyramid_mgr, latest_bars)

    def check_daily_limits(self, pyramid_mgr: PyramidManager,
                           latest_bars: Dict[str, dict]) -> Optional[str]:
        """Check if daily target or stop has been hit.

        Returns:
            'TARGET' if +5R hit, 'STOP' if -5R hit, None otherwise
        """
        total = self.total_R(pyramid_mgr, latest_bars)
        if total >= DAILY_TARGET_R:
            return 'TARGET'
        if total <= DAILY_STOP_R:
            return 'STOP'
        return None

    def get_daily_summary(self) -> dict:
        return {
            'total_trades': self.trades_today,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'cumulative_R': self.cumulative_R,
            'max_drawdown_R': self.max_drawdown_R,
            'pnl': self.cumulative_R * R_VALUE,
        }

    def _update_drawdown(self):
        if self.cumulative_R > self.peak_R:
            self.peak_R = self.cumulative_R
        drawdown = self.peak_R - self.cumulative_R
        if drawdown > self.max_drawdown_R:
            self.max_drawdown_R = drawdown
