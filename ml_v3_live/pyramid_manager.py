"""
Pyramid Manager for V3 Live Trading

Manages PyramidPosition and PyramidSequence dataclasses for tracking
grouped positions with shared stop-loss. Direct port from env_v3.py.

Key concepts:
- PyramidPosition: One open position within a pyramid sequence
- PyramidSequence: Group of same-side (CE/PE) positions sharing a single SL
- When adding to a pyramid, the shared SL shifts to the new (tighter) level
- If SL is hit, ALL positions in the sequence exit together
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .config import (
    MAX_POSITIONS,
    MAX_CE_POSITIONS,
    MAX_PE_POSITIONS,
    MAX_LOTS,
    R_VALUE,
    LOT_SIZE,
)

logger = logging.getLogger(__name__)


@dataclass
class PyramidPosition:
    """One open position within a pyramid sequence."""
    symbol: str
    option_type: str          # 'CE' or 'PE'
    entry_price: float
    entry_time: datetime
    entry_bar_idx: int
    lots: int
    quantity: int
    actual_R_value: float     # sl_points * quantity at entry (risk in Rs)
    sl_points_at_entry: float
    order_id: Optional[str] = None  # Broker order ID for tracking


@dataclass
class PyramidSequence:
    """A group of same-symbol positions sharing a single SL.

    When a new position is added, the shared SL shifts to the new
    (tighter) level. If SL is hit, ALL positions in the sequence exit.
    """
    symbol: str
    option_type: str
    positions: List[PyramidPosition] = field(default_factory=list)
    shared_sl_trigger: float = 0.0   # highest_high + 1
    highest_high: float = 0.0
    sl_order_id: Optional[str] = None  # Broker SL order ID

    def add_position(self, pos: PyramidPosition, sl_trigger: float,
                     highest_high: float):
        self.positions.append(pos)
        self.shared_sl_trigger = sl_trigger
        self.highest_high = highest_high

    @property
    def position_count(self) -> int:
        return len(self.positions)

    def total_quantity(self) -> int:
        return sum(p.quantity for p in self.positions)


class PyramidManager:
    """
    Manages CE and PE pyramid sequences.

    Ported from env_v3.py with additions for live order tracking.
    """

    def __init__(self):
        self.ce_sequence: Optional[PyramidSequence] = None
        self.pe_sequence: Optional[PyramidSequence] = None

    def get_sequence(self, opt_type: str) -> Optional[PyramidSequence]:
        return self.ce_sequence if opt_type == 'CE' else self.pe_sequence

    def set_sequence(self, opt_type: str, seq: Optional[PyramidSequence]):
        if opt_type == 'CE':
            self.ce_sequence = seq
        else:
            self.pe_sequence = seq

    def all_positions(self) -> List[PyramidPosition]:
        positions = []
        if self.ce_sequence:
            positions.extend(self.ce_sequence.positions)
        if self.pe_sequence:
            positions.extend(self.pe_sequence.positions)
        return positions

    def position_count(self) -> int:
        return len(self.all_positions())

    def can_add_position(self, opt_type: str, symbol: str) -> bool:
        """Check if a new position can be added."""
        if self.position_count() >= MAX_POSITIONS:
            return False

        seq = self.get_sequence(opt_type)

        # Type-specific limits
        if opt_type == 'CE':
            count = self.ce_sequence.position_count if self.ce_sequence else 0
            if count >= MAX_CE_POSITIONS:
                return False
        else:
            count = self.pe_sequence.position_count if self.pe_sequence else 0
            if count >= MAX_PE_POSITIONS:
                return False

        # Same-symbol constraint
        if seq is not None and seq.position_count > 0:
            if seq.symbol != symbol:
                return False

        return True

    def add_to_pyramid(self, break_info: dict, bar_idx: int,
                       order_id: Optional[str] = None) -> Optional[PyramidPosition]:
        """Add a position to the appropriate pyramid sequence.

        Args:
            break_info: Dict with symbol, option_type, entry_price,
                        sl_trigger, sl_points, highest_high, break_time
            bar_idx: Current bar index
            order_id: Optional broker order ID

        Returns:
            PyramidPosition if added, None if rejected
        """
        opt_type = break_info['option_type']
        symbol = break_info['symbol']
        entry_price = break_info['entry_price']
        sl_trigger = break_info['sl_trigger']
        sl_points = break_info['sl_points']
        highest_high = break_info['highest_high']

        if not self.can_add_position(opt_type, symbol):
            logger.info(f"[PYRAMID] Rejected: position limits for {opt_type}")
            return None

        # Size position: R_VALUE / (sl_points * LOT_SIZE), capped at MAX_LOTS
        lots = min(int(R_VALUE / (sl_points * LOT_SIZE)), MAX_LOTS)
        if lots <= 0:
            logger.info(f"[PYRAMID] Rejected: lots=0 for sl_points={sl_points:.1f}")
            return None
        quantity = lots * LOT_SIZE

        pos = PyramidPosition(
            symbol=symbol,
            option_type=opt_type,
            entry_price=entry_price,
            entry_time=break_info['break_time'],
            entry_bar_idx=bar_idx,
            lots=lots,
            quantity=quantity,
            actual_R_value=sl_points * quantity,
            sl_points_at_entry=sl_points,
            order_id=order_id,
        )

        seq = self.get_sequence(opt_type)
        if seq is None or seq.position_count == 0:
            seq = PyramidSequence(symbol=symbol, option_type=opt_type)
            self.set_sequence(opt_type, seq)

        seq.add_position(pos, sl_trigger, highest_high)

        logger.info(
            f"[PYRAMID] Added {symbol} {opt_type}: {lots} lots @ {entry_price:.2f}, "
            f"SL trigger={sl_trigger:.2f}, seq_count={seq.position_count}"
        )
        return pos

    def clear_sequence(self, opt_type: str):
        """Clear a pyramid sequence (after SL hit or exit)."""
        self.set_sequence(opt_type, None)

    def clear_all(self):
        """Clear all pyramid sequences."""
        self.ce_sequence = None
        self.pe_sequence = None

    def to_dict(self) -> dict:
        """Serialize state for persistence."""
        result = {'ce_sequence': None, 'pe_sequence': None}
        for key, seq in [('ce_sequence', self.ce_sequence),
                         ('pe_sequence', self.pe_sequence)]:
            if seq is None:
                continue
            result[key] = {
                'symbol': seq.symbol,
                'option_type': seq.option_type,
                'shared_sl_trigger': seq.shared_sl_trigger,
                'highest_high': seq.highest_high,
                'sl_order_id': seq.sl_order_id,
                'positions': [
                    {
                        'symbol': p.symbol,
                        'option_type': p.option_type,
                        'entry_price': p.entry_price,
                        'entry_time': p.entry_time.isoformat(),
                        'entry_bar_idx': p.entry_bar_idx,
                        'lots': p.lots,
                        'quantity': p.quantity,
                        'actual_R_value': p.actual_R_value,
                        'sl_points_at_entry': p.sl_points_at_entry,
                        'order_id': p.order_id,
                    }
                    for p in seq.positions
                ],
            }
        return result

    @classmethod
    def from_dict(cls, data: dict) -> 'PyramidManager':
        """Deserialize state from persistence."""
        mgr = cls()
        for key in ['ce_sequence', 'pe_sequence']:
            seq_data = data.get(key)
            if seq_data is None:
                continue
            seq = PyramidSequence(
                symbol=seq_data['symbol'],
                option_type=seq_data['option_type'],
                shared_sl_trigger=seq_data['shared_sl_trigger'],
                highest_high=seq_data['highest_high'],
                sl_order_id=seq_data.get('sl_order_id'),
            )
            for p_data in seq_data.get('positions', []):
                pos = PyramidPosition(
                    symbol=p_data['symbol'],
                    option_type=p_data['option_type'],
                    entry_price=p_data['entry_price'],
                    entry_time=datetime.fromisoformat(p_data['entry_time']),
                    entry_bar_idx=p_data['entry_bar_idx'],
                    lots=p_data['lots'],
                    quantity=p_data['quantity'],
                    actual_R_value=p_data['actual_R_value'],
                    sl_points_at_entry=p_data['sl_points_at_entry'],
                    order_id=p_data.get('order_id'),
                )
                seq.positions.append(pos)
            if key == 'ce_sequence':
                mgr.ce_sequence = seq
            else:
                mgr.pe_sequence = seq
        return mgr
