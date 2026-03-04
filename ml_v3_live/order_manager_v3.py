"""
Order Manager for V3 Live Trading

V3 uses MARKET orders (not proactive limit orders like baseline):
- Entry: Market order AFTER RL model decides ENTER
- Exit SL: SL-L order placed after market entry fill
- Pyramid SL shifting: Cancel old SL → place new SL for total quantity

OpenAlgo API patterns follow baseline order_manager.py (retry, cancel).
"""

import logging
import time
from typing import Dict, Optional

import pytz
from openalgo import api

from .config import (
    UPSTOX_OPENALGO_HOST,
    UPSTOX_OPENALGO_API_KEY,
    EXCHANGE,
    PRODUCT_TYPE,
    V3_STRATEGY_NAME,
    SL_TRIGGER_PRICE_OFFSET,
    SL_LIMIT_PRICE_OFFSET,
    MAX_ORDER_RETRIES,
    ORDER_RETRY_DELAY,
    DRY_RUN,
)

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')


class OpenAlgoClient(api):
    """Minimal wrapper for test patching."""
    pass


class OrderManagerV3:
    """
    Manages V3 order lifecycle via Upstox OpenAlgo API.

    Entry: Market orders (placed after RL model decides ENTER)
    Exit SL: SL-L orders (trigger = shared_sl_trigger, limit = trigger + 3)
    """

    def __init__(self, host: str = None, api_key: str = None):
        self.host = host or UPSTOX_OPENALGO_HOST
        self.api_key = api_key or UPSTOX_OPENALGO_API_KEY
        self.client = None
        self._init_client()

    def _init_client(self):
        if not self.api_key:
            logger.warning("[V3-ORDER] No Upstox API key configured")
            return
        try:
            self.client = OpenAlgoClient(
                api_key=self.api_key,
                host=self.host,
            )
            logger.info(f"[V3-ORDER] OpenAlgo client initialized: {self.host}")
        except Exception as e:
            logger.error(f"[V3-ORDER] Failed to init OpenAlgo client: {e}")
            self.client = None

    def _retry_call(self, func, *args, **kwargs):
        """Call broker API with retry logic."""
        for attempt in range(1, MAX_ORDER_RETRIES + 1):
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                logger.warning(
                    f"[V3-ORDER] Attempt {attempt}/{MAX_ORDER_RETRIES} failed: {e}"
                )
                if attempt < MAX_ORDER_RETRIES:
                    time.sleep(ORDER_RETRY_DELAY)
        logger.error(f"[V3-ORDER] All {MAX_ORDER_RETRIES} attempts failed")
        return None

    def place_market_entry(self, symbol: str, quantity: int) -> Optional[str]:
        """Place a market SELL order for entry (option shorting).

        Args:
            symbol: Option symbol (e.g., NIFTY06MAR2625000CE)
            quantity: Order quantity (lots * LOT_SIZE)

        Returns:
            Order ID string, or None on failure
        """
        if DRY_RUN:
            logger.info(f"[V3-ORDER] DRY_RUN: SELL MARKET {symbol} qty={quantity}")
            return "DRY_RUN_ENTRY"

        if not self.client:
            logger.error("[V3-ORDER] No client — cannot place entry order")
            return None

        logger.info(f"[V3-ORDER] Placing SELL MARKET {symbol} qty={quantity}")

        response = self._retry_call(
            self.client.placeorder,
            strategy=V3_STRATEGY_NAME,
            symbol=symbol,
            action="SELL",
            exchange=EXCHANGE,
            price_type="MARKET",
            quantity=str(quantity),
            product=PRODUCT_TYPE,
        )

        order_id = self._extract_order_id(response)
        if order_id:
            logger.info(f"[V3-ORDER] Entry order placed: {order_id}")
        else:
            logger.error(f"[V3-ORDER] Entry order failed: {response}")
        return order_id

    def place_sl_order(self, symbol: str, quantity: int,
                       sl_trigger: float) -> Optional[str]:
        """Place a SL-L BUY order for exit protection.

        Args:
            symbol: Option symbol
            quantity: Total quantity for the pyramid sequence
            sl_trigger: Stop-loss trigger price (highest_high + 1)

        Returns:
            Order ID string, or None on failure
        """
        limit_price = round(sl_trigger + SL_LIMIT_PRICE_OFFSET, 2)

        if DRY_RUN:
            logger.info(
                f"[V3-ORDER] DRY_RUN: BUY SL {symbol} qty={quantity} "
                f"trigger={sl_trigger:.2f} limit={limit_price:.2f}"
            )
            return "DRY_RUN_SL"

        if not self.client:
            logger.error("[V3-ORDER] No client — cannot place SL order")
            return None

        logger.info(
            f"[V3-ORDER] Placing BUY SL {symbol} qty={quantity} "
            f"trigger={sl_trigger:.2f} limit={limit_price:.2f}"
        )

        response = self._retry_call(
            self.client.placeorder,
            strategy=V3_STRATEGY_NAME,
            symbol=symbol,
            action="BUY",
            exchange=EXCHANGE,
            price_type="SL",
            trigger_price=str(sl_trigger),
            price=str(limit_price),
            quantity=str(quantity),
            product=PRODUCT_TYPE,
        )

        order_id = self._extract_order_id(response)
        if order_id:
            logger.info(f"[V3-ORDER] SL order placed: {order_id}")
        else:
            logger.error(f"[V3-ORDER] SL order failed: {response}")
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID.

        Returns True on success, False on failure.
        """
        if DRY_RUN:
            logger.info(f"[V3-ORDER] DRY_RUN: Cancel order {order_id}")
            return True

        if not self.client:
            logger.error("[V3-ORDER] No client — cannot cancel order")
            return False

        logger.info(f"[V3-ORDER] Cancelling order {order_id}")

        response = self._retry_call(
            self.client.cancelorder,
            order_id=order_id,  # keyword is order_id (underscore)
            strategy=V3_STRATEGY_NAME,
        )

        if response and self._is_success(response):
            logger.info(f"[V3-ORDER] Cancel confirmed: {order_id}")
            return True
        else:
            logger.error(f"[V3-ORDER] Cancel failed: {order_id} -> {response}")
            return False

    def place_market_exit(self, symbol: str, quantity: int) -> Optional[str]:
        """Place a market BUY order for exit (close short position).

        Used for: EXIT_ALL, STOP_SESSION, daily limits, force close.
        """
        if DRY_RUN:
            logger.info(f"[V3-ORDER] DRY_RUN: BUY MARKET {symbol} qty={quantity}")
            return "DRY_RUN_EXIT"

        if not self.client:
            logger.error("[V3-ORDER] No client — cannot place exit order")
            return None

        logger.info(f"[V3-ORDER] Placing BUY MARKET {symbol} qty={quantity}")

        response = self._retry_call(
            self.client.placeorder,
            strategy=V3_STRATEGY_NAME,
            symbol=symbol,
            action="BUY",
            exchange=EXCHANGE,
            price_type="MARKET",
            quantity=str(quantity),
            product=PRODUCT_TYPE,
        )

        order_id = self._extract_order_id(response)
        if order_id:
            logger.info(f"[V3-ORDER] Exit order placed: {order_id}")
        else:
            logger.error(f"[V3-ORDER] Exit order failed: {response}")
        return order_id

    def get_orderbook(self) -> list:
        """Fetch orderbook for V3 strategy."""
        if not self.client:
            return []

        try:
            response = self.client.orderbook(strategy=V3_STRATEGY_NAME)
            if isinstance(response, list):
                return response
            if isinstance(response, dict) and 'data' in response:
                data = response['data']
                return data if isinstance(data, list) else []
            return []
        except Exception as e:
            logger.error(f"[V3-ORDER] Orderbook fetch failed: {e}")
            return []

    def get_positionbook(self) -> list:
        """Fetch position book for V3 strategy."""
        if not self.client:
            return []

        try:
            response = self.client.positionbook(strategy=V3_STRATEGY_NAME)
            if isinstance(response, list):
                return response
            if isinstance(response, dict) and 'data' in response:
                data = response['data']
                return data if isinstance(data, list) else []
            return []
        except Exception as e:
            logger.error(f"[V3-ORDER] Positionbook fetch failed: {e}")
            return []

    def shift_sl_order(self, old_sl_order_id: Optional[str],
                       symbol: str, new_quantity: int,
                       new_sl_trigger: float) -> Optional[str]:
        """Shift SL order for pyramid: cancel old SL → place new SL.

        When adding to a pyramid, the shared SL needs to cover the
        total quantity at the new (tighter) trigger level.

        Returns new SL order ID, or None on failure.
        """
        # Cancel existing SL
        if old_sl_order_id:
            cancelled = self.cancel_order(old_sl_order_id)
            if not cancelled:
                logger.warning(
                    f"[V3-ORDER] Could not cancel old SL {old_sl_order_id}, "
                    f"placing new SL anyway"
                )

        # Place new SL for total quantity
        new_sl_id = self.place_sl_order(symbol, new_quantity, new_sl_trigger)
        return new_sl_id

    def _extract_order_id(self, response) -> Optional[str]:
        """Extract order ID from OpenAlgo response."""
        if response is None:
            return None
        if isinstance(response, dict):
            # Try common response formats
            for key in ['orderid', 'order_id', 'data']:
                val = response.get(key)
                if val and isinstance(val, str):
                    return val
                if isinstance(val, dict):
                    oid = val.get('orderid') or val.get('order_id')
                    if oid:
                        return str(oid)
        if isinstance(response, str):
            return response
        return None

    def _is_success(self, response) -> bool:
        """Check if a response indicates success."""
        if response is None:
            return False
        if isinstance(response, dict):
            status = response.get('status', '').lower()
            return status in ('success', 'ok', 'true')
        return True
