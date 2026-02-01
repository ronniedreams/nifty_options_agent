"""
Continuous Strike Filter Engine

Two-stage filtering approach:
1. Static Filter: Run once when swing forms (price range 100-300, VWAP ≥4%)
   - Price range: MIN_ENTRY_PRICE to MAX_ENTRY_PRICE (100-300 Rs)
   - VWAP premium: Entry price must be ≥4% above VWAP at swing formation (IMMUTABLE)

2. Dynamic Filter: Run every bar for all swing candidates (SL% 2-10%, position sizing)
   - SL%: Recalculated every bar as highest_high updates (MUTABLE)
   - Position sizing: Based on R_VALUE and current SL points

Order trigger logic (decision layer - actual order management in order_manager.py):
- Recommends placing limit orders IMMEDIATELY when strike qualifies (no price proximity check)
- Recommends keeping orders even if price moves away (reduces churn)
- Recommends modifying orders only when different strike qualifies
- Recommends cancelling orders only when candidate becomes disqualified (SL% out of range)
- Tracks best CE and best PE separately

Key improvement (Dec 23, 2025):
Orders are now placed IMMEDIATELY when a strike qualifies through all filters.
No price proximity check is required. Orders are kept once placed and only
cancelled/modified if:
  1. A different strike becomes the new best candidate, OR
  2. The current candidate fails dynamic filters and is disqualified
This reduces API calls, broker RMS flags, and ensures order is ready if price returns.
"""

import logging
import copy
from typing import Dict, List, Optional
from datetime import datetime

from .config import (
    MIN_ENTRY_PRICE, MAX_ENTRY_PRICE,
    MIN_VWAP_PREMIUM, MIN_SL_PERCENT, MAX_SL_PERCENT,
    TARGET_SL_POINTS, R_VALUE, LOT_SIZE, MAX_LOTS_PER_POSITION
)
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class ContinuousFilterEngine:
    """
    Continuous filtering and order management engine
    
    Maintains swing candidates and evaluates them on every new bar,
    managing proactive limit orders based on price proximity
    """
    
    def __init__(self, state_manager=None):
        # Swing candidates that passed static filter (100-300 price range)
        self.swing_candidates = {}  # {symbol: swing_info}

        # Stage-1 filtered swings pool (passed both price range AND VWAP >=4% filters)
        # Structure: {'CE': [swing_info1, swing_info2, ...], 'PE': [...]}
        # Swings stay here until: a) price breaks below swing_low, b) new swing for same symbol passes Stage-1
        self.stage1_swings_by_type = {
            'CE': [],  # List of CE swings that passed Stage-1 (price + VWAP)
            'PE': []   # List of PE swings that passed Stage-1 (price + VWAP)
        }
        
        # Current best qualified strikes (updated every bar)
        self.current_best = {
            'CE': None,  # Best call strike
            'PE': None   # Best put strike
        }
        
        # State manager reference for DB logging
        self._state_manager = state_manager
        
        # Tracking for logging
        self.last_log_time = None
        self.last_rejection_log = None
        
        # Track rejection reasons for visibility
        self.rejection_stats = {
            'vwap_premium_low': 0,
            'sl_percent_low': 0,
            'sl_percent_high': 0,
            'no_data': 0
        }
        
        # Track last evaluation state to detect changes (avoid logging same rejection every tick)
        # {symbol: {'status': 'rejected'/'qualified', 'reason': 'rejection_reason'}}
        self.last_evaluation_state = {}
    
    def reset_daily_data(self):
        """Clear in-memory swing data for new trading day"""
        self.swing_candidates.clear()
        self.stage1_swings_by_type = {'CE': [], 'PE': []}
        self.current_best = {'CE': None, 'PE': None}
        self.last_evaluation_state.clear()
        logger.info("[DAILY-RESET] Cleared in-memory swing data")
    
    def add_swing_candidate(self, symbol: str, swing_info: Dict):
        """
        Add new swing candidate (after static price filter)

        Args:
            symbol: Option symbol
            swing_info: {
                'price': swing_low_price,
                'timestamp': swing_timestamp,
                'vwap': vwap_at_swing,
                'option_type': 'CE' or 'PE',
                'index': bar_index
            }
        """
        swing_price = swing_info['price']
        swing_type = swing_info.get('type', 'Low')  # Get swing type (Low/High)

        # Only process swing LOWs (not swing highs) for short option entries
        if swing_type != 'Low':
            logger.debug(f"[SWING-SKIP] {symbol}: Swing {swing_type} at {swing_price:.2f} - only tracking swing LOWs")
            return

        # Static Filter 1: Price range 100-300
        if not (MIN_ENTRY_PRICE <= swing_price <= MAX_ENTRY_PRICE):
            # New swing FAILED price filter - reject it AND remove old swing (new swing pattern formed)
            if symbol in self.swing_candidates:
                old_swing = self.swing_candidates[symbol]
                logger.debug(
                    f"[PRICE-REJECT] {symbol}: New swing @ Rs.{swing_price:.2f} outside 100-300 range - REJECTED. "
                    f"Removing old swing @ Rs.{old_swing['price']:.2f} (stale)"
                )

                # Remove old swing from candidates
                del self.swing_candidates[symbol]

                # Clear evaluation state
                if symbol in self.last_evaluation_state:
                    del self.last_evaluation_state[symbol]

                # Remove from stage1 pool
                option_type = swing_info.get('option_type')
                self.stage1_swings_by_type[option_type] = [
                    s for s in self.stage1_swings_by_type[option_type]
                    if s.get('symbol') != symbol
                ]
            else:
                logger.debug(
                    f"[PRICE-REJECT] {symbol}: New swing @ Rs.{swing_price:.2f} outside 100-300 range - REJECTED"
                )
            return

        # Static Filter 2: VWAP premium (this is FIXED for the swing's lifetime)
        vwap_at_swing = swing_info.get('vwap', swing_price)
        vwap_premium = (swing_price - vwap_at_swing) / vwap_at_swing if vwap_at_swing > 0 else 0
        option_type = swing_info.get('option_type')

        if vwap_premium < MIN_VWAP_PREMIUM:
            # New swing FAILED VWAP - reject it AND remove old swing (new swing pattern formed)
            if symbol in self.swing_candidates:
                old_swing = self.swing_candidates[symbol]
                logger.warning(
                    f"[VWAP-REJECT] {symbol}: New swing @ Rs.{swing_price:.2f} "
                    f"VWAP premium {vwap_premium:.1%} < {MIN_VWAP_PREMIUM:.1%} "
                    f"(VWAP @ swing: Rs.{vwap_at_swing:.2f}) - REJECTED. "
                    f"Removing old swing @ Rs.{old_swing['price']:.2f} (stale)"
                )

                # Remove old swing from candidates
                del self.swing_candidates[symbol]

                # Clear evaluation state
                if symbol in self.last_evaluation_state:
                    del self.last_evaluation_state[symbol]

                # Remove from stage1 pool
                self.stage1_swings_by_type[option_type] = [
                    s for s in self.stage1_swings_by_type[option_type]
                    if s.get('symbol') != symbol
                ]
            else:
                logger.warning(
                    f"[VWAP-REJECT] {symbol}: New swing @ Rs.{swing_price:.2f} "
                    f"VWAP premium {vwap_premium:.1%} < {MIN_VWAP_PREMIUM:.1%} "
                    f"(VWAP @ swing: Rs.{vwap_at_swing:.2f}) - REJECTED"
                )

            # Store rejection in DB for historical tracking
            if self._state_manager:
                try:
                    self._state_manager.save_filter_rejections([{
                        'symbol': symbol,
                        'option_type': swing_info.get('option_type', 'UNKNOWN'),
                        'swing_low': swing_price,
                        'current_price': swing_price,
                        'vwap_at_swing': vwap_at_swing,
                        'vwap_premium_percent': vwap_premium,
                        'sl_percent': 0,
                        'rejection_reason': f'VWAP premium {vwap_premium:.1%} < {MIN_VWAP_PREMIUM:.1%}'
                    }])
                except Exception as e:
                    logger.debug(f"Failed to save VWAP rejection to DB: {e}")
            return  # New swing rejected, old swing removed

        # New swing PASSED both static filters - replace old swing
        if symbol in self.swing_candidates:
            old_swing = self.swing_candidates[symbol]
            logger.info(
                f"[SWING-REPLACED] {symbol}: Old swing @ Rs.{old_swing['price']:.2f} "
                f"replaced by new swing @ Rs.{swing_price:.2f}"
            )

        # Add new swing to candidates (make a copy to avoid reference issues)
        # CRITICAL: Use deepcopy() to prevent modifications to swing_info from affecting stored value
        self.swing_candidates[symbol] = copy.deepcopy(swing_info)

        # Clear any previous evaluation state for this symbol (new swing detected)
        if symbol in self.last_evaluation_state:
            del self.last_evaluation_state[symbol]

        logger.info(f"[SWING-CANDIDATE] {symbol}: Swing @ Rs.{swing_price:.2f} (VWAP: {vwap_at_swing:.2f})")

        # Add to VWAP-qualified pool (new swing already passed VWAP check above)
        # Remove any existing swing for this symbol from pool (alternation rule)
        self.stage1_swings_by_type[option_type] = [
            s for s in self.stage1_swings_by_type[option_type]
            if s.get('symbol') != symbol
        ]

        # Add this swing to the pool (add symbol to the copy we stored, not the original)
        self.swing_candidates[symbol]['symbol'] = symbol  # Ensure symbol is in swing_info
        self.stage1_swings_by_type[option_type].append(self.swing_candidates[symbol])

        logger.info(
            f"[VWAP-QUALIFIED] {symbol}: Swing @ Rs.{swing_price:.2f} "
            f"VWAP premium {vwap_premium:.1%} >= {MIN_VWAP_PREMIUM:.1%} - Added to qualified pool"
        )
    
    def remove_swing_candidate(self, symbol: str):
        """Remove swing candidate (if swing gets invalidated)"""
        if symbol in self.swing_candidates:
            del self.swing_candidates[symbol]

            # Clear evaluation state for removed candidate
            if symbol in self.last_evaluation_state:
                del self.last_evaluation_state[symbol]

            logger.info(f"[SWING-REMOVED] {symbol}")

    def mark_historical_breaks(self, swing_detector) -> int:
        """
        STARTUP PROTECTION: Mark swings that already broke during historical data processing.

        Called AFTER historical bars are processed, BEFORE live mode starts.
        Swings marked as 'broke_in_history' will NOT trigger order placement.

        Args:
            swing_detector: MultiSwingDetector instance with historical bars

        Returns:
            Number of swings marked as broken
        """
        marked_count = 0

        for option_type in ['CE', 'PE']:
            for swing_info in self.stage1_swings_by_type[option_type]:
                symbol = swing_info.get('symbol')
                if not symbol:
                    continue

                swing_low = swing_info['price']
                swing_index = swing_info.get('index', 0)

                # Get detector for this symbol
                detector = swing_detector.get_detector(symbol)
                if not detector or not detector.bars:
                    continue

                # Check if any bar AFTER the swing had low < swing_low (swing broke)
                broke_in_history = False
                for i in range(swing_index + 1, len(detector.bars)):
                    bar = detector.bars[i]
                    bar_low = bar.get('low', float('inf'))
                    if bar_low < swing_low:
                        broke_in_history = True
                        break

                if broke_in_history:
                    swing_info['broke_in_history'] = True
                    marked_count += 1
                    logger.warning(
                        f"[STARTUP-PROTECTION] {symbol}: Swing @ {swing_low:.2f} "
                        f"already broke in historical data - will NOT place order"
                    )
                else:
                    swing_info['broke_in_history'] = False

        logger.info(f"[STARTUP-PROTECTION] Marked {marked_count} swings as already broken in history")
        return marked_count

    def evaluate_all_candidates(self, latest_bars: Dict, swing_detector) -> Dict:
        """
        Evaluate all swing candidates with latest bar data
        
        Run EVERY bar to update best CE and PE strikes
        
        Args:
            latest_bars: {symbol: BarData} - Latest completed bars
            swing_detector: MultiSwingDetector instance for getting highest_high
        
        Returns:
            {'CE': best_ce_candidate, 'PE': best_pe_candidate}
            Each candidate has enriched fields: sl_price, sl_points, lots, etc.
        """
        qualified = {'CE': [], 'PE': []}

        # Debug: Log Stage-1 pool status
        ce_count = len(self.stage1_swings_by_type['CE'])
        pe_count = len(self.stage1_swings_by_type['PE'])
        if ce_count > 0 or pe_count > 0:
            logger.debug(
                f"[STAGE1-POOL] CE: {ce_count} swings, PE: {pe_count} swings. "
                f"CE symbols: {[s.get('symbol') for s in self.stage1_swings_by_type['CE']]}"
            )

        # Check for broken swings in VWAP-qualified pool and remove them
        for option_type in ['CE', 'PE']:
            broken_swings = []
            for swing_info in self.stage1_swings_by_type[option_type]:
                symbol = swing_info.get('symbol')
                if symbol and symbol in latest_bars:
                    current_bar = latest_bars[symbol]
                    swing_low = swing_info['price']
                    
                    # Check if swing broke (price went BELOW swing_low)
                    if current_bar.low < swing_low:
                        logger.info(
                            f"[SWING-BREAK] {symbol}: Swing @ Rs.{swing_low:.2f} broke "
                            f"(Low: {current_bar.low:.2f}) - removing from VWAP pool"
                        )
                        broken_swings.append(swing_info)
            
            # Remove broken swings
            for broken in broken_swings:
                self.stage1_swings_by_type[option_type].remove(broken)
        
        # Reset rejection stats for this evaluation
        self.rejection_stats = {
            'vwap_premium_low': 0,
            'sl_percent_low': 0,
            'sl_percent_high': 0,
            'no_data': 0
        }
        
        # Evaluate VWAP-qualified swings instead of all swing_candidates
        # These swings already passed VWAP filter, now check SL%
        
        for option_type in ['CE', 'PE']:
            for swing_info in self.stage1_swings_by_type[option_type]:
                symbol = swing_info.get('symbol')
                if not symbol:
                    continue
                # Skip if no bar data available
                if symbol not in latest_bars:
                    self.rejection_stats['no_data'] += 1
                    logger.warning(
                        f"[NO-BAR-DATA] {symbol}: No bar data in latest_bars - cannot evaluate SL%. "
                        f"Swing in Stage-1 pool but missing bar data!"
                    )
                    continue
                
                current_bar = latest_bars[symbol]
                
                # Validate swing info has required fields
                if not all(k in swing_info for k in ['price', 'vwap', 'index']):
                    logger.warning(f"[EVAL] {symbol}: Incomplete swing_info, skipping")
                    continue
                
                swing_low = swing_info['price']
                vwap_at_swing = swing_info['vwap']
                swing_type = swing_info.get('type', 'Low')

                # Only process swing LOWs (not swing highs)
                if swing_type != 'Low':
                    continue

                # Get highest high since swing formed
                detector = swing_detector.detectors.get(symbol)
                if not detector:
                    continue
                
                highest_high = self._get_highest_high_since_swing(
                    detector,
                    swing_info['index']
                )
                
                # Calculate dynamic metrics
                sl_price = highest_high + 1
                sl_points = sl_price - swing_low
                sl_percent = sl_points / swing_low
                vwap_premium = (swing_low - vwap_at_swing) / vwap_at_swing
                
                # ═══ DYNAMIC FILTERS ═══
                # Note: VWAP already passed (these are from stage1_swings_by_type)
                # Only check SL% here
                
                # Filter: SL% between 2-10%
                if sl_percent < MIN_SL_PERCENT:
                    self.rejection_stats['sl_percent_low'] += 1
                    continue
                    
                if sl_percent > MAX_SL_PERCENT:
                    self.rejection_stats['sl_percent_high'] += 1
                    continue
                
                # Calculate position size
                lots_required = R_VALUE / (sl_points * LOT_SIZE)
                lots = min(lots_required, MAX_LOTS_PER_POSITION)
                quantity = int(lots) * LOT_SIZE
                actual_R = sl_points * int(lots) * LOT_SIZE
                
                # Enrich candidate with calculated fields
                enriched = {
                    'symbol': symbol,
                    'option_type': option_type,
                    'swing_low': swing_low,
                    'swing_time': swing_info['timestamp'],
                    'vwap_at_swing': vwap_at_swing,
                    'current_price': current_bar.close,
                    'current_high': current_bar.high,
                    'highest_high_since_swing': highest_high,
                    'sl_price': sl_price,
                    'sl_points': sl_points,
                    'sl_percent': sl_percent,
                    'vwap_premium': vwap_premium,
                    'lots': int(lots),
                    'quantity': quantity,
                    'actual_R': actual_R,
                    'score': abs(sl_points - TARGET_SL_POINTS),
                    'entry_price': swing_low
                }
                
                qualified[option_type].append(enriched)
        
        # Select best strike for each option type
        best_strikes = {}
        
        for option_type in ['CE', 'PE']:
            if qualified[option_type]:
                # Sort by score (SL points closest to 10), then by highest entry price
                best = min(qualified[option_type], key=lambda x: (x['score'], -x['entry_price']))
                best_strikes[option_type] = best
                
                # Log if best strike changed
                if self.current_best[option_type] is None or \
                   self.current_best[option_type]['symbol'] != best['symbol']:
                    logger.info(
                        f"[BEST-{option_type}] {best['symbol']}: "
                        f"Entry=Rs.{best['swing_low']:.2f}, SL=Rs.{best['sl_price']:.2f} "
                        f"({best['sl_percent']:.1%}), VWAP={best['vwap_premium']:.1%}, "
                        f"Lots={best['lots']}, R=Rs.{best['actual_R']:.0f}"
                    )
            else:
                best_strikes[option_type] = None
        
        # Update current best
        self.current_best = best_strikes
        
        # Log rejection summary every 30 seconds (INFO level for visibility)
        import time
        now = time.time()
        if self.last_rejection_log is None or (now - self.last_rejection_log) >= 30:
            total_candidates = len(self.swing_candidates)
            total_qualified = len(qualified['CE']) + len(qualified['PE'])
            
            if total_candidates > 0 and total_qualified == 0:
                # All candidates rejected - show why
                logger.info(
                    f"[FILTER-SUMMARY] {total_candidates} candidates, 0 qualified. "
                    f"Rejections: VWAP<4%={self.rejection_stats['vwap_premium_low']}, "
                    f"SL<2%={self.rejection_stats['sl_percent_low']}, "
                    f"SL>10%={self.rejection_stats['sl_percent_high']}, "
                    f"No data={self.rejection_stats['no_data']}"
                )
            self.last_rejection_log = now
        
        return best_strikes
    
    def _get_highest_high_since_swing(self, detector, swing_index: int) -> float:
        """Get highest high from all bars after swing index"""
        if not detector.bars or swing_index >= len(detector.bars):
            logger.warning(f"No bars available or invalid swing_index {swing_index}")
            return 0.0
        
        bars_after_swing = detector.bars[swing_index + 1:]
        
        if not bars_after_swing:
            # No bars after swing yet, use swing bar's high
            return detector.bars[swing_index].get('high', 0.0)
        
        try:
            return max(bar.get('high', 0.0) for bar in bars_after_swing)
        except (ValueError, TypeError) as e:
            logger.error(f"Error calculating highest high: {e}")
            return detector.bars[swing_index].get('high', 0.0)
    
    def get_order_triggers(self, latest_bars: Dict, current_bars: Dict = None, pending_orders: Dict = None) -> Dict:
        """
        Determine which orders should be placed/modified/cancelled

        Args:
            latest_bars: {symbol: BarData} - Latest COMPLETED bars for metrics
            current_bars: {symbol: BarData} - Current INCOMPLETE bars with real-time prices
            pending_orders: {option_type: order_info} - Currently pending orders from OrderManager

        Returns:
            {
                'CE': {'action': 'place'|'modify'|'cancel'|'wait', 'candidate': {...}},
                'PE': {'action': 'place'|'modify'|'cancel'|'wait', 'candidate': {...}}
            }
        """
        triggers = {}
        pending_orders = pending_orders or {}
        current_bars = current_bars or {}

        for option_type in ['CE', 'PE']:
            candidate = self.current_best[option_type]
            existing_order = pending_orders.get(option_type)

            # No candidate qualified - cancel any existing order
            if candidate is None:
                logger.debug(
                    f"[NO-CANDIDATE-{option_type}] self.current_best[{option_type}] is None - "
                    f"no qualified candidate in memory"
                )
                triggers[option_type] = {'action': 'cancel', 'candidate': None, 'reason': 'no qualified candidate'}
                continue

            # Candidate exists - log details
            logger.debug(
                f"[HAS-CANDIDATE-{option_type}] Symbol={candidate['symbol']}, "
                f"Entry={candidate.get('swing_low', 0):.2f}, "
                f"SL={candidate.get('sl_price', 0):.2f}"
            )

            symbol = candidate['symbol']
            swing_low = candidate['swing_low']

            # [CRITICAL] CRITICAL: Use CURRENT bar for real-time price, not completed bar
            # In live trading, we need to react to ticks as they come in, not wait for bar completion
            price_source = current_bars.get(symbol) or latest_bars.get(symbol)
            is_realtime = symbol in current_bars

            if price_source is None:
                triggers[option_type] = {'action': 'wait', 'candidate': candidate}
                continue

            current_price = price_source.close  # Latest tick price (real-time if current_bar available)
            price_above_swing = current_price - swing_low

            # Log price proximity for debugging
            logger.debug(
                f"[PRICE-CHECK-{option_type}] {symbol}: "
                f"Current={current_price:.2f}, Swing={swing_low:.2f}, "
                f"Above={price_above_swing:.2f} Rs, "
                f"Has_Order={existing_order is not None}"
            )

            # ═══ IMPROVED ORDER PLACEMENT LOGIC ═══
            # Key improvement: Don't cancel orders just because price moves away
            # Only cancel if: (1) different strike is now best, or (2) candidate disqualified
            
            # Check if we already have an order for a different symbol
            symbol_changed = existing_order and existing_order.get('symbol') != symbol
            
            if symbol_changed:
                # Different strike is now the best - cancel old and place new
                triggers[option_type] = {
                    'action': 'place',  # OrderManager will handle cancel + place
                    'candidate': candidate,
                    'limit_price': swing_low - 0.05,
                    'current_price': current_price,
                    'reason': f'better strike qualified: {symbol}'
                }
            
            elif price_above_swing <= 0:
                # Price broke below swing - order should have filled
                price_type = "REALTIME" if is_realtime else "COMPLETED_BAR"
                logger.warning(
                    f"[SWING-BREAK] {symbol}: {price_type} price {current_price:.2f} "
                    f"BROKE swing {swing_low:.2f} - Checking if order filled"
                )
                triggers[option_type] = {
                    'action': 'check_fill',
                    'candidate': candidate,
                    'current_price': current_price,
                    'reason': f'price {current_price:.2f} broke swing {swing_low:.2f}'
                }

            elif existing_order and existing_order.get('symbol') == symbol:
                # Order already placed for this symbol - keep it
                # This prevents unnecessary order churn
                triggers[option_type] = {
                    'action': 'wait',  # Keep existing order
                    'candidate': candidate,
                    'reason': f'keeping existing order despite price {current_price:.2f} away from swing'
                }
            
            else:
                # STARTUP PROTECTION: Skip swings that already broke in historical data
                if candidate.get('broke_in_history', False):
                    logger.info(
                        f"[STARTUP-SKIP] {symbol}: Swing @ {swing_low:.2f} already broke in history - skipping order"
                    )
                    triggers[option_type] = {
                        'action': 'wait',
                        'candidate': candidate,
                        'reason': f'swing already broke in historical data before startup'
                    }
                    continue

                # No existing order - place order immediately (qualified candidate)
                # Per ORDER_EXECUTION_THEORY.md: Place order as soon as strike qualifies
                price_type = "REALTIME" if is_realtime else "COMPLETED_BAR"
                logger.info(
                    f"[ORDER-TRIGGER] {symbol}: Strike qualified for Stage-3. "
                    f"{price_type} price {current_price:.2f}, swing {swing_low:.2f} - Placing order"
                )
                triggers[option_type] = {
                    'action': 'place',
                    'candidate': candidate,
                    'limit_price': swing_low - 0.05,
                    'current_price': current_price,
                    'reason': f'strike qualified, no existing order'
                }
        
        return triggers
    
    def _log_decision_point_analysis(self, option_type: str, selected_candidate: Dict, latest_bars: Dict):
        """
        Log comprehensive analysis at decision point (when price within ₹1 of swing)
        
        Logs:
        - Selected strike (with metrics)
        - All rejected strikes (with rejection reasons)
        - All qualified but not selected strikes (with comparison to selected)
        """
        selected_symbol = selected_candidate['symbol']
        selected_sl_points = selected_candidate['sl_points']
        
        logger.info(
            f"\n{'='*80}\n"
            f"[DECISION-POINT] {option_type} Strike Selection Analysis\n"
            f"{'='*80}"
        )
        
        # Log selected strike
        logger.info(
            f"✅ SELECTED: {selected_symbol}\n"
            f"   Entry Price:    Rs.{selected_candidate['swing_low']:.2f}\n"
            f"   Current Price:  Rs.{selected_candidate['current_price']:.2f}\n"
            f"   SL Price:       Rs.{selected_candidate['sl_price']:.2f}\n"
            f"   SL Points:      {selected_sl_points:.2f} (target: {TARGET_SL_POINTS})\n"
            f"   SL %:           {selected_candidate['sl_percent']:.1%}\n"
            f"   VWAP Premium:   {selected_candidate['vwap_premium']:.1%}\n"
            f"   Lots:           {selected_candidate['lots']} ({selected_candidate['quantity']} qty)\n"
            f"   Actual R:       Rs.{selected_candidate['actual_R']:.0f}"
        )
        
        # Analyze all other swing candidates of same option type
        qualified_not_selected = []
        rejected_candidates = []
        
        for symbol, swing_info in self.swing_candidates.items():
            if swing_info.get('option_type') != option_type:
                continue
            
            if symbol == selected_symbol:
                continue  # Skip the selected one
            
            if symbol not in latest_bars:
                continue
            
            # Calculate metrics for this candidate
            swing_low = swing_info['price']
            vwap_at_swing = swing_info['vwap']
            vwap_premium = (swing_low - vwap_at_swing) / vwap_at_swing if vwap_at_swing > 0 else 0
            
            # Check VWAP filter
            rejection_reasons = []
            
            if vwap_premium < MIN_VWAP_PREMIUM:
                rejection_reasons.append(f"VWAP premium {vwap_premium:.1%} < {MIN_VWAP_PREMIUM:.1%}")
            
            # Note: We would need swing_detector reference to calculate SL% for comparison
            # For simplicity, just check VWAP here - SL% rejection will be logged elsewhere
            
            if rejection_reasons:
                rejected_candidates.append({
                    'symbol': symbol,
                    'swing_low': swing_low,
                    'vwap_premium': vwap_premium,
                    'reasons': rejection_reasons
                })
            else:
                # Likely qualified but not selected (would need full metrics to confirm)
                qualified_not_selected.append({
                    'symbol': symbol,
                    'swing_low': swing_low,
                    'vwap_premium': vwap_premium
                })
        
        # Log rejected candidates
        if rejected_candidates:
            logger.info(f"\n❌ REJECTED CANDIDATES ({len(rejected_candidates)}):")
            for rej in rejected_candidates:
                reasons_str = ", ".join(rej['reasons'])
                logger.info(
                    f"   {rej['symbol']}: Entry Rs.{rej['swing_low']:.2f} - {reasons_str}"
                )
        
        # Log qualified but not selected
        if qualified_not_selected:
            logger.info(f"\n[WARNING]️  QUALIFIED BUT NOT SELECTED ({len(qualified_not_selected)}):")
            for qual in qualified_not_selected:
                logger.info(
                    f"   {qual['symbol']}: Entry Rs.{qual['swing_low']:.2f}, "
                    f"VWAP Premium {qual['vwap_premium']:.1%} - "
                    f"Not selected (selected strike has SL points closer to target 10)"
                )
        
        logger.info(f"{'='*80}\n")
    
    def get_summary(self) -> Dict:
        """Get summary of current state"""
        return {
            'total_candidates': len(self.swing_candidates),
            'ce_candidates': len([c for c in self.swing_candidates.values() if c['option_type'] == 'CE']),
            'pe_candidates': len([c for c in self.swing_candidates.values() if c['option_type'] == 'PE']),
            'best_ce': self.current_best['CE']['symbol'] if self.current_best['CE'] else None,
            'best_pe': self.current_best['PE']['symbol'] if self.current_best['PE'] else None
        }


if __name__ == '__main__':
    """Test continuous filter logic"""
    
    engine = ContinuousFilterEngine()
    
    # Test adding swing candidates
    engine.add_swing_candidate('NIFTY23DEC2526200CE', {
        'price': 120.50,
        'timestamp': datetime.now(),
        'vwap': 125.0,
        'option_type': 'CE',
        'index': 10
    })
    
    engine.add_swing_candidate('NIFTY23DEC2526200PE', {
        'price': 85.30,
        'timestamp': datetime.now(),
        'vwap': 82.0,
        'option_type': 'PE',
        'index': 15
    })
    
    print(f"Summary: {engine.get_summary()}")
