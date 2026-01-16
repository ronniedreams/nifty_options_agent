"""
Strike Filter Engine for Entry Selection

Applies baseline_v1 entry filters to swing low breaks and selects
the best strike based on tie-breaker rules.

Entry Filters:
1. Entry price: 100-300 INR
2. VWAP premium: >4% (price > VWAP × 1.04 at swing low)
3. SL%: 2-10% (calculated from highest high since swing)

Tie-Breaker (Priority Order):
1. SL points closest to 10
2. Highest entry price

Position Sizing:
- Calculate lot size based on R_VALUE = ₹6,500
- Cap at MAX_LOTS_PER_POSITION = 10
"""

import logging
from typing import List, Dict, Optional

from .config import (
    MIN_ENTRY_PRICE,
    MAX_ENTRY_PRICE,
    MIN_VWAP_PREMIUM,
    MIN_SL_PERCENT,
    MAX_SL_PERCENT,
    TARGET_SL_POINTS,
    R_VALUE,
    LOT_SIZE,
    MAX_LOTS_PER_POSITION,
)

logger = logging.getLogger(__name__)


class StrikeFilter:
    """
    Filter and select best strike from swing low breaks
    """
    
    def __init__(self):
        logger.info("StrikeFilter initialized")
    
    def apply_filters(self, break_candidates: List[Dict]) -> Optional[Dict]:
        """
        Apply entry filters and select best strike
        
        Args:
            break_candidates: List of break_info dicts from SwingDetector
                Each dict contains:
                - symbol, strike, option_type
                - entry_price, break_time, swing_low_time
                - vwap_at_swing_low, highest_high_since_swing
        
        Returns:
            Best candidate dict with added fields:
            - sl_price, sl_percent, sl_points
            - vwap_premium, lots, quantity, actual_R
            Or None if no candidates pass filters
        """
        if not break_candidates:
            return None
        
        logger.info(f"[FILTER] Filtering {len(break_candidates)} swing break candidates...")
        
        # Apply filters and enrich candidates
        qualified = []
        rejected = []
        
        for candidate in break_candidates:
            enriched = self._apply_entry_filters(candidate)
            if enriched:
                qualified.append(enriched)
            else:
                rejected.append(candidate)
        
        # Log summary
        logger.info(f"[FILTER] Qualified: {len(qualified)}, Rejected: {len(rejected)}")
        
        if not qualified:
            logger.info("No candidates passed all filters")
            return None
        
        # Select best using tie-breaker rules
        best = self._select_best_strike(qualified)
        
        logger.info(
            f"Selected {best['symbol']}: "
            f"Entry={best['entry_price']:.2f}, "
            f"SL={best['sl_price']:.2f} ({best['sl_points']:.1f} pts), "
            f"Lots={best['lots']}, "
            f"R={best['actual_R']:.0f}"
        )
        
        return best
    
    def _apply_entry_filters(self, candidate: Dict) -> Optional[Dict]:
        """
        Apply entry filters to single candidate
        
        Returns enriched candidate dict or None if filtered out
        """
        entry_price = candidate['entry_price']
        vwap_at_swing = candidate['vwap_at_swing_low']
        highest_high = candidate['highest_high_since_swing']
        
        # Filter 1: Entry price 100-300
        if not (MIN_ENTRY_PRICE <= entry_price <= MAX_ENTRY_PRICE):
            logger.info(
                f"[X] {candidate['symbol']}: Price filter failed "
                f"(Rs.{entry_price:.2f} not in Rs.100-300)"
            )
            return None
        
        # Filter 2: VWAP premium > 4%
        vwap_premium = (entry_price - vwap_at_swing) / vwap_at_swing
        if vwap_premium < MIN_VWAP_PREMIUM:
            logger.info(
                f"[X] {candidate['symbol']}: VWAP premium failed "
                f"({vwap_premium:.1%} < 4%)"
            )
            return None
        
        # Calculate SL price (from highest high + 1)
        sl_price = highest_high + 1
        sl_percent = (sl_price - entry_price) / entry_price
        sl_points = sl_price - entry_price
        
        # Filter 3: SL% 2-10%
        if not (MIN_SL_PERCENT <= sl_percent <= MAX_SL_PERCENT):
            logger.info(
                f"[X] {candidate['symbol']}: SL% filter failed "
                f"({sl_percent:.1%} not in 2-10%)"
            )
            return None
        
        # Calculate position size
        lots, quantity, actual_R = self._calculate_position_size(
            entry_price, sl_price
        )
        
        # Enrich candidate
        candidate['sl_price'] = sl_price
        candidate['sl_percent'] = sl_percent
        candidate['sl_points'] = sl_points
        candidate['vwap_premium'] = vwap_premium
        candidate['lots'] = lots
        candidate['quantity'] = quantity
        candidate['actual_R'] = actual_R
        
        # Log qualified candidate
        logger.info(
            f"[OK] {candidate['symbol']}: QUALIFIED | "
            f"Entry=Rs.{entry_price:.2f}, VWAP+{vwap_premium:.1%}, "
            f"SL=Rs.{sl_price:.2f} ({sl_percent:.1%}), "
            f"Lots={lots}"
        )
        
        return candidate
    
    def _calculate_position_size(self, entry_price: float, sl_price: float) -> tuple:
        """
        Calculate position size to risk R_VALUE
        
        Args:
            entry_price: Entry price
            sl_price: Stop loss price
        
        Returns:
            (lots, quantity, actual_R)
        """
        risk_per_unit = sl_price - entry_price
        
        if risk_per_unit <= 0:
            logger.warning(f"Invalid SL: entry={entry_price}, sl={sl_price}")
            return 1, LOT_SIZE, risk_per_unit * LOT_SIZE
        
        # Calculate required quantity for R_VALUE risk
        required_qty = R_VALUE / risk_per_unit
        
        # Convert to lots
        required_lots = required_qty / LOT_SIZE
        
        # Round to integer lots (minimum 1, maximum MAX_LOTS_PER_POSITION)
        final_lots = max(1, min(int(required_lots), MAX_LOTS_PER_POSITION))
        
        final_qty = final_lots * LOT_SIZE
        actual_R = risk_per_unit * final_qty
        
        logger.debug(
            f"Position sizing: Entry={entry_price:.2f}, SL={sl_price:.2f}, "
            f"Risk/unit={risk_per_unit:.2f}, "
            f"Required lots={required_lots:.2f}, "
            f"Final lots={final_lots}, "
            f"Actual R={actual_R:.0f}"
        )
        
        return final_lots, final_qty, actual_R
    
    def _select_best_strike(self, candidates: List[Dict]) -> Dict:
        """
        Select best strike using tie-breaker rules
        
        Tie-Breaker Priority:
        1. SL points closest to TARGET_SL_POINTS (10)
        2. Highest entry price
        
        Args:
            candidates: List of qualified candidates
        
        Returns:
            Best candidate dict
        """
        # Sort by:
        # 1. Distance from target SL (ascending - closer is better)
        # 2. Entry price (descending - higher is better)
        candidates_sorted = sorted(
            candidates,
            key=lambda x: (
                abs(x['sl_points'] - TARGET_SL_POINTS),  # Closest to 10
                -x['entry_price']                         # Highest price
            )
        )
        
        best = candidates_sorted[0]
        
        logger.debug(
            f"Tie-breaker: Selected {best['symbol']} from {len(candidates)} candidates "
            f"(SL distance: {abs(best['sl_points'] - TARGET_SL_POINTS):.2f}, "
            f"Entry: {best['entry_price']:.2f})"
        )
        
        return best
    
    def validate_entry(self, candidate: Dict, current_price: float) -> bool:
        """
        Validate entry is still valid at current price
        
        Args:
            candidate: Selected strike candidate
            current_price: Current market price
        
        Returns:
            True if entry still valid, False otherwise
        """
        # Check if price hasn't moved too far from entry price
        entry_price = candidate['entry_price']
        price_deviation = abs(current_price - entry_price) / entry_price
        
        # Allow 2% deviation
        if price_deviation > 0.02:
            logger.warning(
                f"{candidate['symbol']}: Price moved too far "
                f"(Entry: {entry_price:.2f}, Current: {current_price:.2f})"
            )
            return False
        
        # Check if still within price range
        if not (MIN_ENTRY_PRICE <= current_price <= MAX_ENTRY_PRICE):
            logger.warning(
                f"{candidate['symbol']}: Current price {current_price:.2f} "
                f"outside 100-300 range"
            )
            return False
        
        return True


if __name__ == '__main__':
    # Test strike filter
    logging.basicConfig(level=logging.DEBUG)
    
    filter_engine = StrikeFilter()
    
    # Mock break candidates
    test_candidates = [
        {
            'symbol': 'NIFTY26DEC2418000CE',
            'strike': 18000,
            'option_type': 'CE',
            'entry_price': 250,
            'break_time': None,
            'swing_low_time': None,
            'vwap_at_swing_low': 240,  # 4.17% premium
            'highest_high_since_swing': 274,  # SL at 275 = 10% SL, 25 points
        },
        {
            'symbol': 'NIFTY26DEC2418050CE',
            'strike': 18050,
            'option_type': 'CE',
            'entry_price': 200,
            'break_time': None,
            'swing_low_time': None,
            'vwap_at_swing_low': 190,  # 5.26% premium
            'highest_high_since_swing': 209,  # SL at 210 = 5% SL, 10 points (PERFECT!)
        },
        {
            'symbol': 'NIFTY26DEC2418100CE',
            'strike': 18100,
            'option_type': 'CE',
            'entry_price': 150,
            'break_time': None,
            'swing_low_time': None,
            'vwap_at_swing_low': 140,  # 7.14% premium
            'highest_high_since_swing': 154,  # SL at 155 = 3.33% SL, 5 points
        },
    ]
    
    best = filter_engine.apply_filters(test_candidates)
    
    if best:
        print(f"\nBest Strike: {best['symbol']}")
        print(f"Entry: {best['entry_price']:.2f}")
        print(f"SL: {best['sl_price']:.2f} ({best['sl_points']:.1f} points, {best['sl_percent']:.2%})")
        print(f"VWAP Premium: {best['vwap_premium']:.2%}")
        print(f"Position: {best['lots']} lots ({best['quantity']} qty)")
        print(f"Risk (R): Rs.{best['actual_R']:.0f}")
    else:
        print("No qualified strikes")
