"""
Order Manager for Proactive Limit Orders and SL Orders

Manages the complete order lifecycle:
1. Place limit orders BEFORE swing breaks (proactive)
2. Monitor order fills
3. Place SL orders immediately on fill
4. Update/cancel orders based on strike changes
5. Cancel all orders on ±5R daily exit

Order Types:
- Entry: LIMIT order at (swing_low - 1 tick)
- Exit: SL-L order at SL trigger price, limit 3 Rs above
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
import time
import pytz

from openalgo import api

# Minimal OpenAlgoClient for test patching
class OpenAlgoClient(api):
    pass
from .config import (
    OPENALGO_API_KEY,
    OPENALGO_HOST,
    EXCHANGE,
    PRODUCT_TYPE,
    STRATEGY_NAME,
    SL_TRIGGER_PRICE_OFFSET,
    SL_LIMIT_PRICE_OFFSET,
    ORDER_FILL_CHECK_INTERVAL,
    MAX_ORDER_RETRIES,
    ORDER_RETRY_DELAY,
    MAX_SL_FAILURE_COUNT,
    EMERGENCY_EXIT_RETRY_COUNT,
    EMERGENCY_EXIT_RETRY_DELAY,
    DRY_RUN,
)

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class OrderManager:
    """
    Manages order placement, modification, and cancellation
    
    NEW: Tracks orders by option_type (CE/PE) instead of symbol
    """
    
    def __init__(self, client: api = None):
        self.client = client or api(api_key=OPENALGO_API_KEY, host=OPENALGO_HOST)
        
        # Pending limit orders by option type: {'CE': order_info, 'PE': order_info}
        self.pending_limit_orders = {}
        
        # Active SL orders by symbol: {symbol: order_info}
        # (SL orders are per position, so still tracked by symbol)
        self.active_sl_orders = {}
        
        # Filled orders tracking
        self.filled_orders = []
        
        # Last orderbook check time
        self.last_orderbook_check = None
        
        # Emergency SL failure tracking
        self.sl_placement_failures = 0
        self.consecutive_sl_failures = 0
        self.emergency_exit_triggered = False
        
        logger.info("OrderManager initialized (option-type based tracking)")
    
    def place_limit_order(
        self,
        symbol: str,
        limit_price: float,
        quantity: int,
        candidate_info: Dict
    ) -> Optional[str]:
        """
        DEPRECATED: Use manage_limit_order_for_type() instead

        This method uses symbol-based keying which conflicts with
        the new option-type based tracking system.

        Place limit order for swing break entry

        Args:
            symbol: Option symbol (e.g., NIFTY26DEC2418000CE)
            limit_price: Limit price (swing_low - 1 tick)
            quantity: Number of shares (lots × lot_size)
            candidate_info: Full candidate dict from StrikeFilter

        Returns:
            Order ID if successful, None otherwise
        """
        # HARD BLOCK: Raise error to prevent accidental use
        raise RuntimeError(
            f"DEPRECATED: place_limit_order() called for {symbol}. "
            "This method uses symbol-based keying which corrupts the CE/PE tracking system. "
            "Use manage_limit_order_for_type(option_type, candidate, limit_price) instead."
        )

        # Dead code below - keeping for reference but will never execute
        if DRY_RUN:
            logger.info(
                f"[DRY RUN] Would place LIMIT order: "
                f"{symbol} SELL {quantity} @ {limit_price:.2f}"
            )
            order_id = f"DRY_{symbol}_{int(time.time())}"
            self.pending_limit_orders[symbol] = {
                'order_id': order_id,
                'symbol': symbol,
                'limit_price': limit_price,
                'quantity': quantity,
                'status': 'pending',
                'placed_at': datetime.now(IST),
                'candidate_info': candidate_info,
            }
            return order_id
        
        try:
            response = self.client.placeorder(
                strategy=STRATEGY_NAME,
                symbol=symbol,
                action="SELL",  # Shorting
                exchange=EXCHANGE,
                price_type="LIMIT",
                product=PRODUCT_TYPE,
                quantity=quantity,
                price=limit_price
            )
            
            if response.get('status') == 'success':
                order_id = response.get('orderid')
                
                self.pending_limit_orders[symbol] = {
                    'order_id': order_id,
                    'symbol': symbol,
                    'limit_price': limit_price,
                    'quantity': quantity,
                    'status': 'pending',
                    'placed_at': datetime.now(IST),
                    'candidate_info': candidate_info,
                }
                
                logger.info(
                    f"Placed LIMIT order {order_id}: "
                    f"{symbol} SELL {quantity} @ {limit_price:.2f}"
                )
                
                return order_id
            else:
                logger.error(f"Failed to place limit order: {response}")
                return None
                
        except Exception as e:
            logger.error(f"Exception placing limit order for {symbol}: {e}")
            return None
    
    def modify_limit_order(
        self,
        symbol: str,
        new_limit_price: float
    ) -> bool:
        """
        Modify existing limit order price
        
        Args:
            symbol: Option symbol
            new_limit_price: New limit price
        
        Returns:
            True if successful, False otherwise
        """
        if symbol not in self.pending_limit_orders:
            logger.warning(f"No pending limit order for {symbol} to modify")
            return False
        
        order_info = self.pending_limit_orders[symbol]
        order_id = order_info['order_id']
        
        if DRY_RUN:
            logger.info(
                f"[DRY RUN] Would modify order {order_id}: "
                f"price {order_info['limit_price']:.2f} -> {new_limit_price:.2f}"
            )
            order_info['limit_price'] = new_limit_price
            return True
        
        try:
            response = self.client.modifyorder(
                orderid=order_id,
                symbol=symbol,
                exchange=EXCHANGE,
                action="SELL",
                product=PRODUCT_TYPE,
                price_type="LIMIT",
                quantity=order_info['quantity'],
                price=new_limit_price
            )
            
            if response.get('status') == 'success':
                order_info['limit_price'] = new_limit_price
                
                logger.info(
                    f"Modified order {order_id}: "
                    f"new price {new_limit_price:.2f}"
                )
                
                return True
            else:
                logger.error(f"Failed to modify order {order_id}: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Exception modifying order {order_id}: {e}")
            return False
    
    def cancel_limit_order(self, symbol: str) -> bool:
        """
        Cancel pending limit order
        
        Args:
            symbol: Option symbol
        
        Returns:
            True if successful, False otherwise
        """
        if symbol not in self.pending_limit_orders:
            logger.debug(f"No pending limit order for {symbol} to cancel")
            return True  # Already not exists = success
        
        order_info = self.pending_limit_orders[symbol]
        order_id = order_info['order_id']
        
        if DRY_RUN:
            logger.info(f"[DRY RUN] Would cancel order {order_id}")
            del self.pending_limit_orders[symbol]
            return True
        
        try:
            response = self.client.cancelorder(orderid=order_id)
            
            if response.get('status') == 'success':
                del self.pending_limit_orders[symbol]
                
                logger.info(f"Cancelled order {order_id} for {symbol}")
                
                return True
            else:
                logger.error(f"Failed to cancel order {order_id}: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Exception cancelling order {order_id}: {e}")
            return False
    
    def place_sl_order(
        self,
        symbol: str,
        trigger_price: float,
        quantity: int
    ) -> Optional[str]:
        """
        Place SL (Stop Loss Limit) order
        
        Args:
            symbol: Option symbol
            trigger_price: SL trigger price
            quantity: Number of shares (USE FILLED QUANTITY, not intended)
        
        Returns:
            Order ID if successful, None otherwise
        """
        # Calculate limit price (trigger + 3 Rs offset)
        limit_price = trigger_price + SL_LIMIT_PRICE_OFFSET
        
        # 🚨 CRITICAL: Upstox SL-L BUY requires trigger < limit
        if trigger_price >= limit_price:
            logger.error(
                f"Invalid SL order: trigger {trigger_price:.2f} must be < limit {limit_price:.2f} for BUY"
            )
            raise ValueError(
                f"SL BUY trigger ({trigger_price:.2f}) must be < limit ({limit_price:.2f}) for Upstox"
            )
        
        limit_price = trigger_price + SL_LIMIT_PRICE_OFFSET
        
        if DRY_RUN:
            logger.info(
                f"[DRY RUN] Would place SL-L order: "
                f"{symbol} BUY {quantity} @ trigger {trigger_price:.2f}, "
                f"limit {limit_price:.2f}"
            )
            order_id = f"DRY_SL_{symbol}_{int(time.time())}"
            self.active_sl_orders[symbol] = {
                'order_id': order_id,
                'symbol': symbol,
                'trigger_price': trigger_price,
                'limit_price': limit_price,
                'quantity': quantity,
                'placed_at': datetime.now(IST),
            }
            return order_id
        
        try:
            response = self.client.placeorder(
                strategy=STRATEGY_NAME,
                symbol=symbol,
                action="BUY",  # Close short position
                exchange=EXCHANGE,
                price_type="SL",  # Stop Loss order
                product=PRODUCT_TYPE,
                quantity=quantity,
                price=limit_price,
                trigger_price=trigger_price
            )
            
            if response.get('status') == 'success':
                order_id = response.get('orderid')
                
                self.active_sl_orders[symbol] = {
                    'order_id': order_id,
                    'symbol': symbol,
                    'trigger_price': trigger_price,
                    'limit_price': limit_price,
                    'quantity': quantity,
                    'placed_at': datetime.now(IST),
                }
                
                logger.info(
                    f"Placed SL order {order_id}: "
                    f"{symbol} BUY {quantity} @ trigger {trigger_price:.2f}, "
                    f"limit {limit_price:.2f}"
                )
                
                # Reset failure counter on success
                self.consecutive_sl_failures = 0
                
                return order_id
            else:
                logger.error(f"Failed to place SL order: {response}")
                self.consecutive_sl_failures += 1
                self.sl_placement_failures += 1
                return None
                
        except Exception as e:
            logger.error(f"Exception placing SL order for {symbol}: {e}")
            self.consecutive_sl_failures += 1
            self.sl_placement_failures += 1
            return None
    
    def cancel_sl_order(self, symbol: str) -> bool:
        """Cancel SL order"""
        if symbol not in self.active_sl_orders:
            logger.debug(f"No active SL order for {symbol} to cancel")
            return True
        
        order_info = self.active_sl_orders[symbol]
        order_id = order_info['order_id']
        
        if DRY_RUN:
            logger.info(f"[DRY RUN] Would cancel SL order {order_id}")
            del self.active_sl_orders[symbol]
            return True
        
        try:
            response = self.client.cancelorder(orderid=order_id)
            
            if response.get('status') == 'success':
                del self.active_sl_orders[symbol]
                logger.info(f"Cancelled SL order {order_id} for {symbol}")
                return True
            else:
                logger.error(f"Failed to cancel SL order {order_id}: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Exception cancelling SL order {order_id}: {e}")
            return False
    
    def emergency_market_exit(
        self,
        symbol: str,
        quantity: int,
        reason: str = "SL_PLACEMENT_FAILED"
    ) -> Optional[str]:
        """
        EMERGENCY: Force close position with MARKET order
        
        🚨 CRITICAL: Checks position exists before placing order to prevent reverse position
        
        Used when SL order placement fails - position has unlimited risk.
        Retries multiple times to ensure exit.
        
        Args:
            symbol: Option symbol
            quantity: Quantity to close (should match position size)
            reason: Reason for emergency exit (for logging)
        
        Returns:
            Order ID if successful, None if all retries failed
        """
        logger.critical(
            f"🚨 EMERGENCY MARKET EXIT: {symbol} qty={quantity} reason={reason}"
        )
        
        if DRY_RUN:
            logger.info(f"[DRY RUN] Would emergency exit {symbol} at MARKET")
            return f"DRY_EMERGENCY_{symbol}_{int(time.time())}"
        
        # 🚨 CRITICAL: Verify position exists before placing order
        try:
            positions_response = self.client.openposition()
            if positions_response.get('status') == 'success':
                positions = positions_response.get('data', [])
                position_exists = False
                actual_qty = 0
                
                for pos in positions:
                    if pos.get('symbol') == symbol and pos.get('product') == PRODUCT_TYPE:
                        actual_qty = abs(int(pos.get('quantity', 0)))
                        if actual_qty > 0:
                            position_exists = True
                            break
                
                if not position_exists:
                    logger.warning(
                        f"⚠️ Emergency exit cancelled: No open position for {symbol}. "
                        f"Prevents opening reverse long position."
                    )
                    return None
                
                # Use actual position quantity, not passed quantity
                quantity = actual_qty
                logger.info(f"Emergency exit using actual position qty: {quantity}")
        except Exception as e:
            logger.error(f"Failed to verify position before emergency exit: {e}")
            # Proceed with caution using passed quantity
        
        # Try multiple times to ensure position is closed
        for attempt in range(EMERGENCY_EXIT_RETRY_COUNT):
            try:
                response = self.client.placeorder(
                    strategy="baseline_v1_live_emergency",
                    symbol=symbol,
                    action="BUY",  # Close short position
                    exchange=EXCHANGE,
                    price_type="MARKET",
                    product=PRODUCT_TYPE,
                    quantity=quantity
                )
                
                if response.get('status') == 'success':
                    order_id = response.get('orderid')
                    
                    logger.critical(
                        f"✅ Emergency exit successful: order {order_id} | "
                        f"Attempt {attempt + 1}/{EMERGENCY_EXIT_RETRY_COUNT}"
                    )
                    
                    self.emergency_exit_triggered = True
                    return order_id
                else:
                    logger.error(
                        f"Emergency exit attempt {attempt + 1} failed: {response}"
                    )
                    
            except Exception as e:
                logger.error(
                    f"Emergency exit attempt {attempt + 1} exception: {e}",
                    exc_info=True
                )
            
            if attempt < EMERGENCY_EXIT_RETRY_COUNT - 1:
                time.sleep(EMERGENCY_EXIT_RETRY_DELAY)
        
        # All retries failed - CRITICAL SITUATION
        logger.critical(
            f"[ERROR] EMERGENCY EXIT FAILED FOR {symbol} AFTER "
            f"{EMERGENCY_EXIT_RETRY_COUNT} ATTEMPTS - MANUAL INTERVENTION REQUIRED!"
        )
        
        return None

    def place_market_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        reason: str = "DAILY_TARGET"
    ) -> Optional[str]:
        """
        Place MARKET order to close position at daily target/stop

        Used for:
        - Daily +5R target exit
        - Daily -5R stop loss exit
        - EOD force close at 3:15 PM

        Args:
            symbol: Option symbol to close
            quantity: Total quantity to close
            action: "BUY" (to cover short position)
            reason: Exit reason for logging (DAILY_TARGET, DAILY_STOP, EOD_EXIT)

        Returns:
            Order ID if successful, None if failed
        """
        logger.info(f"[MARKET-EXIT] {symbol} qty={quantity} reason={reason}")

        if DRY_RUN:
            logger.info(f"[DRY RUN] Would place MARKET order for {symbol}")
            return f"DRY_MARKET_{symbol}_{int(time.time())}"

        # 3-retry logic (same as other order methods)
        for attempt in range(1, MAX_ORDER_RETRIES + 1):
            try:
                response = self.client.placeorder(
                    strategy=STRATEGY_NAME,
                    symbol=symbol,
                    action=action,
                    exchange=EXCHANGE,
                    price_type="MARKET",
                    quantity=quantity,
                    product=PRODUCT_TYPE
                )

                if response and response.get('status') == 'success':
                    order_id = response.get('orderid')
                    logger.info(f"[MARKET-EXIT] Order placed: {order_id}")
                    return order_id
                else:
                    logger.warning(
                        f"[MARKET-EXIT] Attempt {attempt}/{MAX_ORDER_RETRIES} failed: {response}"
                    )

            except Exception as e:
                logger.error(
                    f"[MARKET-EXIT] Attempt {attempt}/{MAX_ORDER_RETRIES} error: {e}"
                )

            if attempt < MAX_ORDER_RETRIES:
                time.sleep(ORDER_RETRY_DELAY)  # 2-second delay before retry

        logger.error(f"[MARKET-EXIT] Failed after {MAX_ORDER_RETRIES} retries")
        return None

    def should_halt_trading(self) -> bool:
        """
        Check if trading should be halted due to SL failures
        
        Returns:
            True if consecutive SL failures exceed threshold
        """
        if self.consecutive_sl_failures >= MAX_SL_FAILURE_COUNT:
            logger.critical(
                f"🛑 TRADING HALTED: {self.consecutive_sl_failures} consecutive "
                f"SL placement failures (threshold: {MAX_SL_FAILURE_COUNT})"
            )
            return True
        
        return False
    
    def check_fills(self) -> List[Dict]:
        """
        Check for filled orders by polling orderbook
        
        Returns:
            List of newly filled order dicts
        """
        if DRY_RUN:
            return []  # No real fills in dry run
        
        newly_filled = []
        
        try:
            # Get orderbook
            response = self.client.orderbook()
            
            if response.get('status') != 'success':
                logger.error(f"Failed to fetch orderbook: {response}")
                return []
            
            orders = response.get('data', [])
            
            # Check pending limit orders
            for symbol in list(self.pending_limit_orders.keys()):
                order_info = self.pending_limit_orders[symbol]
                order_id = order_info['order_id']
                
                # Find order in orderbook
                order_details = self._find_order_status(orders, order_id)
                
                if not order_details:
                    continue
                
                # 🚨 CRITICAL: Explicit status validation
                if order_details['status'] == 'rejected':
                    logger.error(
                        f"Order {order_id} REJECTED: {symbol} - {order_details['rejected_reason']}"
                    )
                    del self.pending_limit_orders[symbol]
                    continue
                
                if order_details['status'] == 'complete':
                    # ✅ Use FILLED QUANTITY from broker, not intended quantity
                    filled_qty = order_details['filled_quantity']
                    fill_price = order_details['average_price'] or order_info['limit_price']
                    
                    filled_info = {
                        'symbol': symbol,
                        'order_id': order_id,
                        'fill_price': fill_price,
                        'quantity': filled_qty,  # ✅ Actual filled quantity
                        'filled_at': datetime.now(IST),
                        'candidate_info': order_info['candidate_info'],
                    }
                    
                    newly_filled.append(filled_info)
                    self.filled_orders.append(filled_info)
                    
                    # Remove from pending
                    del self.pending_limit_orders[symbol]
                    
                    logger.info(
                        f"Order {order_id} FILLED: "
                        f"{symbol} {filled_qty} @ {fill_price:.2f} (intended: {order_info['quantity']})"
                    )
            
            self.last_orderbook_check = datetime.now(IST)
            
        except Exception as e:
            logger.error(f"Exception checking fills: {e}")
        
        return newly_filled
    
    def _find_order_status(self, orders: List[Dict], order_id: str) -> Optional[Dict]:
        """Find order details in orderbook response
        
        Returns:
            Order dict with status and filled_quantity, or None
        """
        for order in orders:
            if order.get('orderid') == order_id:
                # CRITICAL FIX: OpenAlgo uses 'order_status' not 'status'
                return {
                    'status': order.get('order_status', '').lower(),
                    'filled_quantity': int(order.get('filled_quantity', 0)),
                    'average_price': float(order.get('average_price', 0)),
                    'rejected_reason': order.get('rejected_reason', ''),
                }
        return None
    
    def cancel_all_orders(self):
        """Cancel ALL pending limit and SL orders (for ±5R exit)"""
        logger.info("Cancelling ALL orders...")
        
        # Cancel limit orders
        for symbol in list(self.pending_limit_orders.keys()):
            self.cancel_limit_order(symbol)
        
        # Cancel SL orders
        for symbol in list(self.active_sl_orders.keys()):
            self.cancel_sl_order(symbol)
        
        logger.info("All orders cancelled")
    
    def update_limit_order_for_candidate(
        self,
        candidate: Dict,
        limit_price: float
    ) -> str:
        """
        DEPRECATED: Use manage_limit_order_for_type() instead

        This method uses symbol-based keying which conflicts with CE/PE tracking.
        """
        raise RuntimeError(
            "DEPRECATED: update_limit_order_for_candidate() must not be used. "
            "Use manage_limit_order_for_type(option_type, candidate, limit_price) instead."
        )
    
    def get_status_summary(self) -> Dict:
        """Get order manager status summary"""
        return {
            'pending_limit_orders': len(self.pending_limit_orders),
            'active_sl_orders': len(self.active_sl_orders),
            'filled_orders_today': len(self.filled_orders),
            'option_types_pending': list(self.pending_limit_orders.keys()),
            'symbols_with_sl': list(self.active_sl_orders.keys()),
        }
    
    def get_pending_orders_by_type(self) -> Dict:
        """
        Get currently pending orders grouped by option type
        
        Returns:
            {
                'CE': {'symbol': 'NIFTY...', 'order_id': '...', 'limit_price': ...},
                'PE': {'symbol': 'NIFTY...', 'order_id': '...', 'limit_price': ...}
            }
        """
        return self.pending_limit_orders.copy()
    
    # ═══════════════════════════════════════════════════════════════
    # NEW: Option-Type Based Order Management
    # ═══════════════════════════════════════════════════════════════
    
    def manage_limit_order_for_type(
        self,
        option_type: str,
        candidate: Optional[Dict],
        limit_price: Optional[float]
    ) -> str:
        """
        Manage limit order for option type (CE or PE)

        Args:
            option_type: 'CE' or 'PE'
            candidate: Candidate dict with symbol, quantity, etc. (None to cancel)
            limit_price: Limit price to use (None to cancel)

        Returns:
            'placed', 'modified', 'cancelled', or 'kept'
        """
        # Validate option_type to prevent symbol-based keying
        assert option_type in ['CE', 'PE'], (
            f"Invalid option_type: {option_type}. Must be 'CE' or 'PE'. "
            f"Do not pass symbol strings to this method."
        )

        existing = self.pending_limit_orders.get(option_type)
        
        # Case 1: Cancel existing order (no new candidate)
        if candidate is None or limit_price is None:
            if existing:
                self._cancel_broker_order(existing['order_id'])
                del self.pending_limit_orders[option_type]
                logger.info(f"[CANCEL-{option_type}] Cancelled limit order for {existing['symbol']}")
                return 'cancelled'
            return 'none'
        
        symbol = candidate['symbol']
        quantity = candidate['quantity']
        swing_low = candidate.get('swing_low')
        tick_size = candidate.get('tick_size', 0.05)
        trigger_price = swing_low - tick_size
        limit_price_entry = trigger_price - 3  # 3 rupee buffer for entry
        
        # Case 2: No existing order - place new
        if not existing:
            order_id = self._place_broker_stop_limit_order(symbol, trigger_price, limit_price_entry, quantity)
            if order_id:
                self.pending_limit_orders[option_type] = {
                    'order_id': order_id,
                    'symbol': symbol,
                    'trigger_price': trigger_price,
                    'limit_price': limit_price_entry,
                    'quantity': quantity,
                    'status': 'pending',
                    'placed_at': datetime.now(IST),
                    'candidate_info': candidate
                }
                logger.info(f"[PLACE-{option_type}] {symbol} SL-L trigger {trigger_price:.2f} limit {limit_price_entry:.2f} QTY {quantity}")
                return 'placed'
            return 'failed'

        # Case 3: Different symbol - cancel old, place new
        if existing['symbol'] != symbol:
            self._cancel_broker_order(existing['order_id'])
            order_id = self._place_broker_stop_limit_order(symbol, trigger_price, limit_price_entry, quantity)
            if order_id:
                self.pending_limit_orders[option_type] = {
                    'order_id': order_id,
                    'symbol': symbol,
                    'trigger_price': trigger_price,
                    'limit_price': limit_price_entry,
                    'quantity': quantity,
                    'status': 'pending',
                    'placed_at': datetime.now(IST),
                    'candidate_info': candidate
                }
                logger.info(f"[MODIFY-{option_type}] {existing['symbol']} -> {symbol} trigger {trigger_price:.2f} limit {limit_price_entry:.2f}")
                return 'modified'
            return 'failed'
        
        # Case 4: Same symbol, check if trigger or limit price changed
        if abs(existing['trigger_price'] - trigger_price) > 0.01 or abs(existing['limit_price'] - limit_price_entry) > 0.01:
            # Price changed - cancel old and place new SL order
            self._cancel_broker_order(existing['order_id'])
            order_id = self._place_broker_stop_limit_order(symbol, trigger_price, limit_price_entry, quantity)
            if order_id:
                existing['order_id'] = order_id
                existing['trigger_price'] = trigger_price
                existing['limit_price'] = limit_price_entry
                existing['placed_at'] = datetime.now(IST)
                logger.info(f"[MODIFY-{option_type}] {symbol} trigger {trigger_price:.2f} limit {limit_price_entry:.2f}")
                return 'modified'
            return 'failed'

        # Case 5: Same symbol, same price - keep existing order
        logger.debug(f"[KEEP-{option_type}] {symbol} unchanged (trigger={trigger_price:.2f}, limit={limit_price_entry:.2f})")
        return 'kept'

    def _place_broker_stop_limit_order(self, symbol: str, trigger_price: float, limit_price: float, quantity: int) -> Optional[str]:
        """
        Place stop-limit (SL) order via broker API with retry logic

        Args:
            symbol: Option symbol (e.g., NIFTY30DEC2526000CE)
            trigger_price: Price at which order becomes active (swing_low - tick_size)
            limit_price: Limit price once triggered (trigger_price - 3 Rs)
            quantity: Total quantity (lots × LOT_SIZE)

        Returns:
            Order ID if successful, None otherwise
        """
        if DRY_RUN:
            order_id = f"DRY_SLL_{symbol}_{int(time.time())}"
            logger.info(f"[DRY-RUN] Would place SL-L {symbol} trigger {trigger_price:.2f} limit {limit_price:.2f} QTY {quantity}")
            return order_id

        for attempt in range(MAX_ORDER_RETRIES):
            try:
                response = self.client.placeorder(
                    strategy=STRATEGY_NAME,
                    symbol=symbol,
                    action='SELL',
                    exchange=EXCHANGE,
                    price_type='SL',
                    trigger_price=trigger_price,
                    price=limit_price,
                    quantity=quantity,
                    product=PRODUCT_TYPE
                )

                if response.get('status') == 'success':
                    order_id = response.get('orderid')
                    logger.info(f"[ORDER-PLACED] {symbol} SL trigger {trigger_price:.2f} limit {limit_price:.2f} QTY {quantity} | ID: {order_id}")
                    return order_id
                else:
                    error_msg = response.get('message', 'Unknown error')
                    logger.error(f"SL order failed (attempt {attempt + 1}/{MAX_ORDER_RETRIES}): {error_msg}")
                    if attempt < MAX_ORDER_RETRIES - 1:
                        time.sleep(ORDER_RETRY_DELAY)

            except Exception as e:
                logger.error(f"Exception placing SL order (attempt {attempt + 1}/{MAX_ORDER_RETRIES}): {e}")
                if attempt < MAX_ORDER_RETRIES - 1:
                    time.sleep(ORDER_RETRY_DELAY)

        logger.error(f"Failed to place SL order after {MAX_ORDER_RETRIES} attempts")
        return None

    def _place_broker_limit_order(self, symbol: str, price: float, quantity: int) -> Optional[str]:
        """Place limit order via broker API with retry logic"""
        if DRY_RUN:
            order_id = f"DRY_LIMIT_{symbol}_{int(time.time())}"
            logger.info(f"[DRY-RUN] Would place LIMIT {symbol} @ {price} QTY {quantity}")
            return order_id
        
        for attempt in range(MAX_ORDER_RETRIES):
            try:
                response = self.client.placeorder(
                    strategy=STRATEGY_NAME,
                    symbol=symbol,
                    action='SELL',
                    exchange=EXCHANGE,
                    price_type='LIMIT',
                    price=price,
                    quantity=quantity,
                    product=PRODUCT_TYPE
                )
                
                if response.get('status') == 'success':
                    order_id = response.get('orderid')
                    logger.info(f"[ORDER-PLACED] {symbol} LIMIT @ {price} QTY {quantity} | ID: {order_id}")
                    return order_id
                else:
                    error_msg = response.get('message', 'Unknown error')
                    logger.error(f"Limit order failed (attempt {attempt + 1}/{MAX_ORDER_RETRIES}): {error_msg}")
                    
                    if attempt < MAX_ORDER_RETRIES - 1:
                        time.sleep(ORDER_RETRY_DELAY)
                    
            except Exception as e:
                logger.error(f"Exception placing limit order (attempt {attempt + 1}/{MAX_ORDER_RETRIES}): {e}")
                if attempt < MAX_ORDER_RETRIES - 1:
                    time.sleep(ORDER_RETRY_DELAY)
        
        logger.error(f"Failed to place limit order after {MAX_ORDER_RETRIES} attempts")
        return None
    
    def _cancel_broker_order(self, order_id: str) -> bool:
        """Cancel order via broker API"""
        if DRY_RUN:
            logger.info(f"[DRY-RUN] Would cancel order {order_id}")
            return True
        
        try:
            response = self.client.cancelorder(orderid=order_id)
            return response.get('status') == 'success'
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False
    
    def _modify_broker_order(self, order_id: str, new_price: float) -> bool:
        """Modify order price via broker API"""
        if DRY_RUN:
            logger.info(f"[DRY-RUN] Would modify order {order_id} to price {new_price}")
            return True
        
        try:
            response = self.client.modifyorder(
                orderid=order_id,
                price=new_price
            )
            return response.get('status') == 'success'
        except Exception as e:
            logger.error(f"Error modifying order: {e}")
            return False
    
    def check_fills_by_type(self) -> Dict:
        """
        Check for filled orders, grouped by option type

        Returns:
            {'CE': fill_info_or_None, 'PE': fill_info_or_None}
        """
        fills = {'CE': None, 'PE': None}

        if not self.pending_limit_orders:
            return fills  # No pending orders to check

        try:
            response = self.client.orderbook()

            # CRITICAL: Validate response is a dict
            if not isinstance(response, dict):
                logger.error(f"[CHECK-FILLS] Orderbook response is not a dict: {type(response)}, value: {response}")
                return fills

            # Check if API call succeeded
            if response.get('status') != 'success':
                logger.warning(f"[CHECK-FILLS] Orderbook API error: {response.get('message')}")
                return fills

            # Get orders data with comprehensive validation
            broker_orders = response.get('data')

            # CRITICAL FIX: Handle None, string, and non-list responses
            if broker_orders is None:
                logger.debug(f"[CHECK-FILLS] No orders data (None)")
                return fills

            if isinstance(broker_orders, str):
                logger.warning(f"[CHECK-FILLS] Orderbook data is string (error message): {broker_orders}")
                return fills

            if not isinstance(broker_orders, list):
                # Log the actual structure to debug
                logger.error(
                    f"[CHECK-FILLS] Orderbook data is not a list: {type(broker_orders)}. "
                    f"Data: {broker_orders}"
                )
            
                # Try to extract list from nested structure (some brokers nest it)
                # Common patterns: {"orders": [...]} or {"data": [...]}
                if isinstance(broker_orders, dict):
                    # Try common nested keys
                    for key in ['orders', 'data', 'orderbook']:
                        if key in broker_orders and isinstance(broker_orders[key], list):
                            logger.info(f"[CHECK-FILLS] Found orders list in nested key '{key}'")
                            broker_orders = broker_orders[key]
                            break
                    else:
                        # No valid list found
                        logger.error(f"[CHECK-FILLS] Could not find orders list in dict keys: {list(broker_orders.keys())}")
                        return fills
                else:
                    # Not a dict either, cannot recover
                    return fills

            logger.debug(f"[CHECK-FILLS] Processing {len(broker_orders)} broker orders")

            # Iterate pending orders
            for option_type, pending in list(self.pending_limit_orders.items()):
                # CRITICAL: Validate pending is a dict (not a string or other type)
                if not isinstance(pending, dict):
                    logger.error(
                        f"[CHECK-FILLS] CORRUPTION: pending_limit_orders['{option_type}'] is {type(pending)}, "
                        f"not dict! Value: {pending}. Removing corrupted entry."
                    )
                    del self.pending_limit_orders[option_type]
                    continue

                order_id = pending.get('order_id')
                if not order_id:
                    logger.error(f"[CHECK-FILLS] No order_id for {option_type}. Pending: {pending}")
                    continue

                logger.debug(f"[CHECK-FILLS] Looking for {option_type} order {order_id}")

                # Find order in broker orderbook
                broker_order = None
                for o in broker_orders:
                    # Skip non-dict entries
                    if not isinstance(o, dict):
                        logger.warning(f"[CHECK-FILLS] Broker order is not dict: {type(o)}")
                        continue

                    if o.get('orderid') == order_id:
                        broker_order = o
                        break

                if not broker_order:
                    logger.debug(f"[CHECK-FILLS] Order {order_id} not found in broker orderbook (still pending)")
                    continue

                # CRITICAL FIX: OpenAlgo uses 'order_status' not 'status'
                status = broker_order.get('order_status', '').lower()
                
                # 🚨 Handle rejected orders
                if status == 'rejected':
                    logger.error(
                        f"[CHECK-FILLS] Order {order_id} REJECTED: {broker_order.get('rejected_reason', 'Unknown')}"
                    )
                    self.pending_limit_orders[option_type] = None
                    continue

                if status == 'complete':
                    # ✅ Use FILLED QUANTITY and average price from broker
                    filled_qty = int(broker_order.get('filled_quantity', pending['quantity']))
                    fill_price = float(broker_order.get('average_price') or broker_order.get('price', pending['limit_price']))

                    fill_info = {
                        'option_type': option_type,
                        'symbol': pending['symbol'],
                        'order_id': order_id,
                        'fill_price': fill_price,
                        'quantity': filled_qty,  # ✅ Use actual filled quantity
                        'candidate_info': pending['candidate_info'],
                        'fill_time': datetime.now(IST)
                    }

                    fills[option_type] = fill_info

                    # Remove from pending
                    del self.pending_limit_orders[option_type]

                    logger.info(f"[FILL-{option_type}] {pending['symbol']} @ {fill_price:.2f} QTY {pending['quantity']}")

        except Exception as e:
            logger.error(f"[CHECK-FILLS] Exception: {e}", exc_info=True)

        return fills

    def debug_pending_orders(self) -> str:
        """
        Returns formatted string of pending orders for debugging/logging

        Returns:
            String like: "CE: NIFTY30DEC2526000CE @ 155.45, PE: None"
        """
        try:
            ce_order = self.pending_limit_orders.get('CE')
            pe_order = self.pending_limit_orders.get('PE')

            # CRITICAL: Validate orders are dicts before accessing fields
            if ce_order and isinstance(ce_order, dict):
                ce_str = f"{ce_order.get('symbol', 'UNKNOWN')} @ {ce_order.get('limit_price', 0):.2f}"
            else:
                ce_str = f"INVALID({type(ce_order).__name__})" if ce_order else "None"

            if pe_order and isinstance(pe_order, dict):
                pe_str = f"{pe_order.get('symbol', 'UNKNOWN')} @ {pe_order.get('limit_price', 0):.2f}"
            else:
                pe_str = f"INVALID({type(pe_order).__name__})" if pe_order else "None"

            return f"CE: {ce_str}, PE: {pe_str}"

        except Exception as e:
            return f"ERROR: {e}"

    def reconcile_orders_with_broker(self, open_positions: Dict) -> Dict:
        """
        🔧 CRITICAL: Reconcile local order state with broker after reconnection

        After WebSocket reconnect, orders may have:
        - Filled while we were disconnected
        - Been rejected by RMS
        - Been cancelled by broker
        - SL orders may exist at broker but not locally

        This method syncs local state with broker reality.

        Args:
            open_positions: Dict of currently open positions {symbol: position_info}
                           Used to verify SL orders still exist

        Returns:
            Dict with reconciliation results:
            {
                'limit_orders_removed': [list of symbols],
                'limit_orders_filled': [list of fills],
                'sl_orders_missing': [list of symbols],
                'sl_orders_removed': [list of symbols]
            }
        """
        logger.info("[RECONCILE] Starting order reconciliation with broker...")

        results = {
            'limit_orders_removed': [],
            'limit_orders_filled': [],
            'sl_orders_missing': [],
            'sl_orders_removed': []
        }

        try:
            # Fetch current orderbook from broker
            response = self.client.orderbook()

            if response.get('status') != 'success':
                logger.error(f"[RECONCILE] Failed to fetch orderbook: {response}")
                return results

            broker_orders = response.get('data', [])

            # Create lookup map: order_id -> order_data
            broker_order_map = {order.get('orderid'): order for order in broker_orders}

            logger.info(f"[RECONCILE] Found {len(broker_orders)} orders at broker")

            # ═══════════════════════════════════════════════════════════
            # 1. Reconcile LIMIT ORDERS
            # ═══════════════════════════════════════════════════════════

            for option_type in list(self.pending_limit_orders.keys()):
                order_info = self.pending_limit_orders[option_type]
                order_id = order_info['order_id']
                symbol = order_info['symbol']

                broker_order = broker_order_map.get(order_id)

                if broker_order is None:
                    # Order not found at broker - likely cancelled or filled
                    logger.warning(
                        f"[RECONCILE] Limit order {order_id} ({symbol}) not found at broker - removing"
                    )
                    del self.pending_limit_orders[option_type]
                    results['limit_orders_removed'].append(symbol)
                    continue

                # Check if filled
                # CRITICAL FIX: OpenAlgo uses 'order_status' not 'status'
                status = broker_order.get('order_status', '').lower()

                if status in ['complete', 'filled']:
                    fill_price = float(broker_order.get('average_price') or broker_order.get('price', 0))
                    fill_qty = int(broker_order.get('quantity', 0))

                    logger.warning(
                        f"[RECONCILE] Limit order {order_id} ({symbol}) was FILLED during disconnect "
                        f"@ {fill_price:.2f} QTY {fill_qty}"
                    )

                    # Create fill info (similar to check_fills_by_type)
                    fill_info = {
                        'symbol': symbol,
                        'fill_price': fill_price,
                        'quantity': fill_qty,
                        'option_type': option_type,
                        'candidate_info': order_info.get('candidate_info', {}),
                        'order_id': order_id,
                        'filled_at': datetime.now(IST)
                    }

                    results['limit_orders_filled'].append(fill_info)

                    # Remove from pending
                    del self.pending_limit_orders[option_type]

                elif status in ['REJECTED', 'CANCELLED']:
                    logger.warning(
                        f"[RECONCILE] Limit order {order_id} ({symbol}) was {status} - removing"
                    )
                    del self.pending_limit_orders[option_type]
                    results['limit_orders_removed'].append(symbol)

            # ═══════════════════════════════════════════════════════════
            # 2. Reconcile SL ORDERS
            # ═══════════════════════════════════════════════════════════

            # First check: Do we have SL orders for all open positions?
            for symbol in open_positions.keys():
                if symbol not in self.active_sl_orders:
                    logger.critical(
                        f"[RECONCILE] [WARNING]️ CRITICAL: Position {symbol} has NO SL ORDER in local state!"
                    )
                    results['sl_orders_missing'].append(symbol)

            # Second check: Verify SL orders still exist at broker
            for symbol in list(self.active_sl_orders.keys()):
                order_info = self.active_sl_orders[symbol]
                order_id = order_info['order_id']

                broker_order = broker_order_map.get(order_id)

                if broker_order is None:
                    logger.critical(
                        f"[RECONCILE] [WARNING]️ CRITICAL: SL order {order_id} ({symbol}) not found at broker!"
                    )

                    # Check if position still exists
                    if symbol in open_positions:
                        logger.critical(
                            f"[RECONCILE] Position {symbol} exists but SL is missing - "
                            f"REQUIRES IMMEDIATE MANUAL INTERVENTION!"
                        )
                    else:
                        # Position closed, SL can be removed
                        logger.info(
                            f"[RECONCILE] SL order {symbol} not at broker, position also closed - removing"
                        )
                        del self.active_sl_orders[symbol]
                        results['sl_orders_removed'].append(symbol)
                    continue

                # Verify SL status
                # CRITICAL FIX: OpenAlgo uses 'order_status' not 'status'
                status = broker_order.get('order_status', '').lower()

                if status in ['complete', 'filled', 'triggered']:
                    logger.info(
                        f"[RECONCILE] SL order {order_id} ({symbol}) was triggered/filled - "
                        f"position should be closed"
                    )
                    del self.active_sl_orders[symbol]
                    results['sl_orders_removed'].append(symbol)

                elif status in ['rejected', 'cancelled']:
                    if symbol in open_positions:
                        logger.critical(
                            f"[RECONCILE] [WARNING]️ CRITICAL: SL order {order_id} ({symbol}) was {status} "
                            f"but position still open - MANUAL INTERVENTION REQUIRED!"
                        )
                        results['sl_orders_missing'].append(symbol)
                    else:
                        logger.info(
                            f"[RECONCILE] SL order {symbol} was {status}, position closed - removing"
                        )
                        del self.active_sl_orders[symbol]
                        results['sl_orders_removed'].append(symbol)

            # ═══════════════════════════════════════════════════════════
            # Summary
            # ═══════════════════════════════════════════════════════════

            logger.info(
                f"[RECONCILE] ✅ Reconciliation complete:\n"
                f"  - Limit orders removed: {len(results['limit_orders_removed'])}\n"
                f"  - Limit orders filled: {len(results['limit_orders_filled'])}\n"
                f"  - SL orders missing: {len(results['sl_orders_missing'])}\n"
                f"  - SL orders removed: {len(results['sl_orders_removed'])}"
            )

            if results['sl_orders_missing']:
                logger.critical(
                    f"[RECONCILE] [WARNING]️ CRITICAL ALERT: {len(results['sl_orders_missing'])} positions "
                    f"without SL protection: {results['sl_orders_missing']}"
                )

        except Exception as e:
            logger.error(f"[RECONCILE] Error during reconciliation: {e}", exc_info=True)

        return results


if __name__ == '__main__':
    # Test order manager
    logging.basicConfig(level=logging.INFO)
    
    # Note: Requires valid API key and running OpenAlgo instance
    manager = OrderManager()
    
    print(f"Status: {manager.get_status_summary()}")
