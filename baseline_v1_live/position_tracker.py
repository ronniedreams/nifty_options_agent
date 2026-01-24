"""
Position Tracker with R-Multiple Accounting

Tracks open positions, calculates cumulative R, and enforces position limits.

Position Limits:
- Max 5 total positions
- Max 3 CE positions
- Max 3 PE positions

Daily Exits:
- +5R cumulative: Exit all positions
- -5R cumulative: Exit all positions
- 3:15 PM: Force exit all positions

R Calculation:
- actual_R per position = (entry_price - sl_price) × quantity
- profit/loss in R = pnl_rupees / actual_R
- cumulative_R = Σ(closed R) + Σ(unrealized R)
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
import pytz

from openalgo import api
from .config import (
    OPENALGO_API_KEY,
    OPENALGO_HOST,
    MAX_POSITIONS,
    MAX_CE_POSITIONS,
    MAX_PE_POSITIONS,
    DAILY_TARGET_R,
    DAILY_STOP_R,
    TOTAL_CAPITAL,
    DRY_RUN,
)

try:
    from .telegram_notifier import get_notifier
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class Position:
    """Single position with R-multiple accounting"""
    
    def __init__(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        quantity: int,
        actual_R: float,
        entry_time: datetime,
        candidate_info: Dict
    ):
        self.symbol = symbol
        self.entry_price = entry_price
        self.sl_price = sl_price
        self.quantity = quantity
        self.actual_R = actual_R
        self.entry_time = entry_time
        self.candidate_info = candidate_info
        
        # Current state
        self.current_price = entry_price
        self.unrealized_pnl = 0.0
        self.unrealized_R = 0.0
        
        # Exit state (when closed)
        self.exit_price = None
        self.exit_time = None
        self.exit_reason = None
        self.realized_pnl = 0.0
        self.realized_R = 0.0
        self.is_closed = False
        
        # Derived fields
        self.strike = candidate_info.get('strike')
        self.option_type = candidate_info.get('option_type')  # CE or PE
        self.lots = candidate_info.get('lots')
    
    def update_price(self, current_price: float):
        """Update current price and recalculate unrealized P&L"""
        self.current_price = current_price
        
        # Shorting: profit when price falls
        self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
        self.unrealized_R = self.unrealized_pnl / self.actual_R if self.actual_R > 0 else 0
    
    def close(self, exit_price: float, exit_reason: str):
        """Close position and calculate realized P&L"""
        self.exit_price = exit_price
        self.exit_time = datetime.now(IST)
        self.exit_reason = exit_reason
        
        # Shorting: profit when price falls
        self.realized_pnl = (self.entry_price - exit_price) * self.quantity
        self.realized_R = self.realized_pnl / self.actual_R if self.actual_R > 0 else 0
        
        self.is_closed = True
        
        logger.info(
            f"Position closed: {self.symbol} "
            f"Entry={self.entry_price:.2f}, Exit={exit_price:.2f}, "
            f"P&L=Rs.{self.realized_pnl:.0f} ({self.realized_R:+.2f}R), "
            f"Reason={exit_reason}"
        )
    
    def to_dict(self) -> Dict:
        """Convert to dict for logging/persistence"""
        return {
            'symbol': self.symbol,
            'strike': self.strike,
            'option_type': self.option_type,
            'entry_price': self.entry_price,
            'sl_price': self.sl_price,
            'quantity': self.quantity,
            'lots': self.lots,
            'actual_R': self.actual_R,
            'entry_time': self.entry_time.isoformat() if self.entry_time else None,
            'current_price': self.current_price,
            'unrealized_pnl': self.unrealized_pnl,
            'unrealized_R': self.unrealized_R,
            'exit_price': self.exit_price,
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
            'exit_reason': self.exit_reason,
            'realized_pnl': self.realized_pnl,
            'realized_R': self.realized_R,
            'is_closed': self.is_closed,
        }


class PositionTracker:
    """
    Track all positions with R-multiple accounting
    """
    
    def __init__(self, client: api = None, order_manager = None):
        self.client = client or api(api_key=OPENALGO_API_KEY, host=OPENALGO_HOST)
        self.order_manager = order_manager  # NEW: Store reference for market orders

        # Open positions: {symbol: Position}
        self.open_positions = {}

        # Closed positions (today only)
        self.closed_positions = []

        # Daily exit state
        self.daily_exit_triggered = False
        self.daily_exit_reason = None

        # Current date for intraday reset
        self.current_date = None

        # Telegram notifier
        self.telegram = get_notifier() if TELEGRAM_AVAILABLE else None

        logger.info("PositionTracker initialized")
    
    def reset_for_new_day(self):
        """Reset for new trading day"""
        self.open_positions = {}
        self.closed_positions = []
        self.daily_exit_triggered = False
        self.daily_exit_reason = None
        self.current_date = datetime.now(IST).date()
        logger.info(f"PositionTracker reset for new day: {self.current_date}")
    
    def can_open_position(self, symbol: str, option_type: str) -> tuple:
        """
        Check if new position allowed

        Args:
            symbol: Option symbol
            option_type: 'CE' or 'PE'

        Returns:
            (can_open: bool, reason: str)
        """
        # Check if daily exit already triggered
        if self.daily_exit_triggered:
            return False, f"Daily exit triggered: {self.daily_exit_reason}"

        # CRITICAL FIX: Check if position already exists for this symbol
        # This prevents the multiple orders bug where the same symbol gets
        # re-ordered after a fill is detected
        if symbol in self.open_positions:
            return False, f"Position already exists for {symbol}"

        # Count current positions
        total_positions = len(self.open_positions)
        ce_positions = sum(1 for pos in self.open_positions.values() if pos.option_type == 'CE')
        pe_positions = sum(1 for pos in self.open_positions.values() if pos.option_type == 'PE')
        
        # Check total limit
        if total_positions >= MAX_POSITIONS:
            return False, f"Max {MAX_POSITIONS} positions already open"
        
        # Check CE limit
        if option_type == 'CE' and ce_positions >= MAX_CE_POSITIONS:
            return False, f"Max {MAX_CE_POSITIONS} CE positions already open"
        
        # Check PE limit
        if option_type == 'PE' and pe_positions >= MAX_PE_POSITIONS:
            return False, f"Max {MAX_PE_POSITIONS} PE positions already open"
        
        return True, "OK"
    
    def add_position(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        quantity: int,
        actual_R: float,
        candidate_info: Dict
    ) -> Position:
        """
        Add new position
        
        Returns:
            Position object
        """
        position = Position(
            symbol=symbol,
            entry_price=entry_price,
            sl_price=sl_price,
            quantity=quantity,
            actual_R=actual_R,
            entry_time=datetime.now(IST),
            candidate_info=candidate_info
        )
        
        self.open_positions[symbol] = position
        
        logger.info(
            f"Position opened: {symbol} "
            f"Entry={entry_price:.2f}, SL={sl_price:.2f}, "
            f"Qty={quantity}, R=Rs.{actual_R:.0f}"
        )
        
        return position
    
    def update_prices(self, prices: Dict[str, float]):
        """
        Update current prices for all positions
        
        Args:
            prices: {symbol: current_price}
        """
        for symbol, price in prices.items():
            if symbol in self.open_positions:
                self.open_positions[symbol].update_price(price)
    
    def close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str
    ) -> Optional[Position]:
        """
        Close position
        
        Args:
            symbol: Position symbol
            exit_price: Exit price
            exit_reason: Reason for exit (SL_HIT, DAILY_TARGET, EOD, etc.)
        
        Returns:
            Closed Position object or None if not found
        """
        if symbol not in self.open_positions:
            logger.warning(f"Cannot close position: {symbol} not found")
            return None
        
        position = self.open_positions[symbol]
        position.close(exit_price, exit_reason)
        
        # Move to closed
        # Send Telegram notification
        if self.telegram:
            self.telegram.notify_trade_exit(position.to_dict(), exit_reason)
        
        self.closed_positions.append(position)
        del self.open_positions[symbol]
        
        return position
    
    def close_all_positions(self, exit_reason: str, current_prices: Dict[str, float]):
        """
        Close ALL open positions (for ±5R or EOD exit)

        NEW: Places MARKET orders at broker to actually close positions

        Args:
            exit_reason: Reason for exit (+5R_TARGET, -5R_STOP, EOD_EXIT)
            current_prices: {symbol: current_price} for P&L calculation
        """
        logger.info(f"Closing ALL positions: {exit_reason}")

        for symbol in list(self.open_positions.keys()):
            position = self.open_positions[symbol]
            exit_price = current_prices.get(symbol)

            if exit_price is None:
                logger.warning(f"No price for {symbol}, using last known price")
                exit_price = position.current_price

            # NEW: Place broker orders if order_manager provided
            if self.order_manager:
                # 1. Cancel existing exit SL order
                logger.info(f"[EXIT] Cancelling SL for {symbol}")
                self.order_manager.cancel_sl_order(symbol)

                # 2. Place MARKET order to close position
                logger.info(f"[EXIT] Placing MARKET order for {symbol}")
                order_id = self.order_manager.place_market_order(
                    symbol=symbol,
                    quantity=position.quantity,
                    action="BUY",  # Cover the short
                    reason=exit_reason
                )

                if not order_id:
                    logger.error(
                        f"[EXIT] Failed to place market order for {symbol}. "
                        f"Position will auto-square at 3:15 PM (MIS product)."
                    )
                    # Continue with other positions

            # Update internal state (existing logic)
            self.close_position(symbol, exit_price, exit_reason)
    
    def get_cumulative_R(self) -> float:
        """
        Calculate cumulative R (closed + unrealized)
        
        Returns:
            Total R for the day
        """
        # Closed positions R
        closed_R = sum(pos.realized_R for pos in self.closed_positions)
        
        # Unrealized positions R
        unrealized_R = sum(pos.unrealized_R for pos in self.open_positions.values())
        
        return closed_R + unrealized_R
    
    def check_daily_exit(self) -> Optional[str]:
        """
        Check if daily ±5R exit triggered
        
        Returns:
            Exit reason if triggered, None otherwise
        """
        if self.daily_exit_triggered:
            return self.daily_exit_reason
        
        cumulative_R = self.get_cumulative_R()
        
        if cumulative_R >= DAILY_TARGET_R:
            self.daily_exit_triggered = True
            self.daily_exit_reason = f'+{DAILY_TARGET_R}R_TARGET'
            logger.warning(
                f"Daily +{DAILY_TARGET_R}R target hit! Cumulative R: {cumulative_R:+.2f}"
            )
            return self.daily_exit_reason
        
        if cumulative_R <= DAILY_STOP_R:
            self.daily_exit_triggered = True
            self.daily_exit_reason = f'{DAILY_STOP_R}R_STOP'
            logger.warning(
                f"Daily {DAILY_STOP_R}R stop hit! Cumulative R: {cumulative_R:+.2f}"
            )
            return self.daily_exit_reason
        
        return None
    
    def get_position_summary(self) -> Dict:
        """Get position summary stats"""
        cumulative_R = self.get_cumulative_R()
        
        closed_pnl = sum(pos.realized_pnl for pos in self.closed_positions)
        unrealized_pnl = sum(pos.unrealized_pnl for pos in self.open_positions.values())
        
        ce_count = sum(1 for pos in self.open_positions.values() if pos.option_type == 'CE')
        pe_count = sum(1 for pos in self.open_positions.values() if pos.option_type == 'PE')
        
        return {
            'total_positions': len(self.open_positions),
            'ce_positions': ce_count,
            'pe_positions': pe_count,
            'closed_positions_today': len(self.closed_positions),
            'cumulative_R': cumulative_R,
            'closed_pnl': closed_pnl,
            'unrealized_pnl': unrealized_pnl,
            'total_pnl': closed_pnl + unrealized_pnl,
            'daily_exit_triggered': self.daily_exit_triggered,
            'daily_exit_reason': self.daily_exit_reason,
        }
    
    def get_all_positions(self) -> List[Dict]:
        """Get all positions as dicts"""
        return [pos.to_dict() for pos in list(self.open_positions.values()) + self.closed_positions]
    
    def reconcile_with_broker(self):
        """
        [CRITICAL] ENHANCED: Bi-directional position reconciliation with broker
        
        Detects TWO types of discrepancies:
        1. Phantom positions: We track, broker doesn't have (SL likely hit)
        2. Orphaned positions: Broker has, we don't track (missed fill notification)
        
        Called every 60 seconds from main loop
        """
        if DRY_RUN:
            return
        
        try:
            response = self.client.positionbook()
            
            if response.get('status') != 'success':
                logger.error(f"Failed to fetch positions: {response}")
                return
            
            broker_positions = response.get('data', [])
            
            # Create mapping: {symbol: broker_position_dict}
            broker_positions_map = {}
            for pos in broker_positions:
                # Filter for strategy-relevant positions only
                # (avoid flagging other manual trades)
                symbol = pos.get('symbol', '')

                # Skip if not a NIFTY option
                if 'NIFTY' not in symbol or not (symbol.endswith('CE') or symbol.endswith('PE')):
                    continue

                # Extract quantity and avg_price with error handling
                quantity_raw = pos.get('quantity', pos.get('qty', 0))
                avg_price_raw = pos.get('averageprice', pos.get('average_price', pos.get('avgprice', 0)))

                try:
                    quantity = abs(int(quantity_raw)) if quantity_raw is not None else 0
                except (ValueError, TypeError):
                    logger.debug(f"[RECONCILE] Invalid quantity for {symbol}: {quantity_raw}")
                    continue

                try:
                    avg_price = float(avg_price_raw) if avg_price_raw is not None else 0.0
                except (ValueError, TypeError):
                    logger.debug(f"[RECONCILE] Invalid avg_price for {symbol}: {avg_price_raw}")
                    continue

                # CRITICAL FILTER: Skip closed/stale positions
                # Filter out positions with qty=0 or avg_price near zero
                # Brokers often return stale closed positions with these values
                if quantity == 0:
                    logger.debug(f"[RECONCILE] Skipping closed position (qty=0): {symbol}")
                    continue

                if avg_price <= 0.01:  # Catch 0, 0.00, and near-zero values
                    logger.debug(f"[RECONCILE] Skipping stale position (avg_price={avg_price:.4f}): {symbol} (qty={quantity})")
                    continue

                # Valid position - add to map
                broker_positions_map[symbol] = pos
            
            broker_symbols = set(broker_positions_map.keys())
            tracked_symbols = set(self.open_positions.keys())
            
            # === CHECK 1: Phantom Positions (we track, broker doesn't have) ===
            phantom_symbols = tracked_symbols - broker_symbols
            
            for symbol in phantom_symbols:
                position = self.open_positions[symbol]
                
                logger.critical(
                    f"[WARNING] PHANTOM POSITION DETECTED: {symbol} | "
                    f"We track, broker doesn't have (likely SL hit)"
                )
                
                # Close locally with last known price
                self.close_position(symbol, position.current_price, 'SL_HIT_RECONCILED')
                
                # Send Telegram alert
                if self.telegram:
                    self.telegram.send_message(
                        f"[WARNING]️ Phantom position closed: {symbol}\n"
                        f"Likely SL hit, broker confirmed exit."
                    )
            
            # === CHECK 2: Orphaned Positions (broker has, we don't track) ===
            orphaned_symbols = broker_symbols - tracked_symbols
            
            for symbol in orphaned_symbols:
                broker_pos = broker_positions_map[symbol]
                quantity = abs(int(broker_pos.get('quantity', 0)))
                avg_price = float(broker_pos.get('averageprice', 0))
                
                logger.critical(
                    f"[WARNING] ORPHANED POSITION DETECTED: {symbol} | "
                    f"Broker has qty={quantity} @ {avg_price:.2f}, we don't track | "
                    f"Possible missed fill notification"
                )
                
                # Send critical Telegram alert
                if self.telegram:
                    self.telegram.send_message(
                        f"🚨 ORPHANED POSITION ALERT\n\n"
                        f"Symbol: {symbol}\n"
                        f"Qty: {quantity}\n"
                        f"Avg Price: {avg_price:.2f}\n\n"
                        f"[WARNING]️ Position exists in broker but not in tracker!\n"
                        f"Possible causes:\n"
                        f"- Missed fill notification\n"
                        f"- System crash after fill\n"
                        f"- Manual broker trade\n\n"
                        f"[WARNING]️ MANUAL INTERVENTION REQUIRED"
                    )
                
                # Don't auto-add orphaned positions - too risky
                # (Could be manual trade, unknown SL, etc.)
                # Just alert and require manual verification
            
            # === CHECK 3: Quantity Mismatch (both have, but different qty) ===
            common_symbols = tracked_symbols & broker_symbols
            
            for symbol in common_symbols:
                tracked_qty = self.open_positions[symbol].quantity
                broker_qty = abs(int(broker_positions_map[symbol].get('quantity', 0)))
                
                if tracked_qty != broker_qty:
                    logger.critical(
                        f"[WARNING] QUANTITY MISMATCH: {symbol} | "
                        f"Tracked: {tracked_qty}, Broker: {broker_qty}"
                    )
                    
                    if self.telegram:
                        self.telegram.send_message(
                            f"[WARNING]️ Quantity mismatch: {symbol}\n"
                            f"Tracked: {tracked_qty}\n"
                            f"Broker: {broker_qty}\n\n"
                            f"Possible partial fill or manual modification"
                        )
            
            # Log successful reconciliation
            if not phantom_symbols and not orphaned_symbols:
                logger.debug(
                    f"[OK] Position reconciliation OK: "
                    f"{len(tracked_symbols)} positions match broker"
                )
            
        except Exception as e:
            logger.error(f"Exception during position reconciliation: {e}", exc_info=True)


if __name__ == '__main__':
    # Test position tracker
    logging.basicConfig(level=logging.INFO)
    
    tracker = PositionTracker()
    
    # Mock candidate info
    candidate = {
        'symbol': 'NIFTY26DEC2418000CE',
        'strike': 18000,
        'option_type': 'CE',
        'lots': 10,
        'quantity': 650,
        'actual_R': 6500,
    }
    
    # Test can_open_position
    can_open, reason = tracker.can_open_position('NIFTY26DEC2418000CE', 'CE')
    print(f"Can open: {can_open}, Reason: {reason}")
    
    # Add position
    tracker.add_position(
        symbol='NIFTY26DEC2418000CE',
        entry_price=250,
        sl_price=260,
        quantity=650,
        actual_R=6500,
        candidate_info=candidate
    )
    
    # Update price
    tracker.update_prices({'NIFTY26DEC2418000CE': 245})
    
    # Get summary
    summary = tracker.get_position_summary()
    print(f"\nSummary: {summary}")
    
    # Check daily exit
    exit_reason = tracker.check_daily_exit()
    print(f"Daily exit: {exit_reason}")
