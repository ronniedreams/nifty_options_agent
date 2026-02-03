"""
Test WebSocket-Based NIFTY Spot Price Detection

This script validates the WebSocket integration for auto-detection:
1. Connects to WebSocket
2. Subscribes to NIFTY spot
3. Tests spot price retrieval (current LTP)
4. Tests bar retrieval (specific timestamp)
5. Tests auto-detection flow

Usage:
    python -m baseline_v1_live.test_websocket_spot
"""

import logging
import sys
import time
from datetime import datetime, timedelta
import pytz

# Add parent directory to path
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from baseline_v1_live.data_pipeline import DataPipeline
from baseline_v1_live.auto_detector import AutoDetector
from baseline_v1_live.config import OPENALGO_API_KEY, OPENALGO_HOST

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')


def print_section(title):
    """Print a formatted section header"""
    print("\n" + "="*80)
    print(f"  {title}")
    print("="*80 + "\n")


def test_websocket_connection():
    """Test 1: WebSocket Connection"""
    print_section("TEST 1: WebSocket Connection")

    try:
        pipeline = DataPipeline()
        logger.info("Creating DataPipeline instance...")

        pipeline.connect()
        logger.info("WebSocket connection established")

        if pipeline.is_connected:
            logger.info("SUCCESS: WebSocket is connected")
            return pipeline
        else:
            logger.error("FAILED: WebSocket connection failed")
            return None

    except Exception as e:
        logger.error(f"FAILED: Exception during connection: {e}")
        return None


def test_spot_subscription(pipeline):
    """Test 2: NIFTY Spot Subscription"""
    print_section("TEST 2: NIFTY Spot Subscription")

    spot_symbol = "Nifty 50"
    logger.info(f"Subscribing to NIFTY spot: {spot_symbol}")

    try:
        # Subscribe to spot only (no options)
        pipeline.subscribe_options([], spot_symbol=spot_symbol)

        # Wait for data to flow
        logger.info("Waiting 5 seconds for WebSocket data...")
        time.sleep(5)

        # Check if symbol is in subscribed list
        if spot_symbol in pipeline.subscribed_symbols:
            logger.info(f"SUCCESS: {spot_symbol} is in subscribed symbols")
            return True
        else:
            logger.error(f"FAILED: {spot_symbol} not found in subscribed symbols")
            logger.info(f"Subscribed symbols: {pipeline.subscribed_symbols}")
            return False

    except Exception as e:
        logger.error(f"FAILED: Exception during subscription: {e}")
        return False


def test_spot_price_retrieval(pipeline):
    """Test 3: Spot Price Retrieval (Current LTP)"""
    print_section("TEST 3: Spot Price Retrieval (Current LTP)")

    spot_symbol = "Nifty 50"

    try:
        # Get current spot price
        spot_price = pipeline.get_spot_price(spot_symbol)

        if spot_price is not None:
            logger.info(f"SUCCESS: NIFTY Spot LTP: {spot_price}")
            logger.info(f"  Type: {type(spot_price)}")
            logger.info(f"  Value: {spot_price:.2f}")

            # Sanity check (NIFTY usually between 15000-30000)
            if 15000 <= spot_price <= 30000:
                logger.info("  Spot price is within expected range (15000-30000)")
                return spot_price
            else:
                logger.warning(f"  WARNING: Spot price {spot_price} is outside expected range!")
                return spot_price
        else:
            logger.error("FAILED: Spot price is None (no data received)")
            logger.info("Possible reasons:")
            logger.info("  1. WebSocket not receiving ticks yet (wait longer)")
            logger.info("  2. Symbol name mismatch (try 'NIFTY' or 'NSE:NIFTY')")
            logger.info("  3. Market closed (check time)")
            return None

    except Exception as e:
        logger.error(f"FAILED: Exception during spot price retrieval: {e}")
        return None


def test_bar_retrieval(pipeline):
    """Test 4: Bar Retrieval (Historical/Current)"""
    print_section("TEST 4: Bar Retrieval (Latest Completed Bar)")

    spot_symbol = "Nifty 50"

    try:
        # Get latest completed bar
        bar = pipeline.get_latest_bar(spot_symbol)

        if bar is not None:
            logger.info(f"SUCCESS: Retrieved latest bar")
            logger.info(f"  Timestamp: {bar.timestamp.strftime('%Y-%m-%d %H:%M')}")
            logger.info(f"  Open:  {bar.open:.2f}")
            logger.info(f"  High:  {bar.high:.2f}")
            logger.info(f"  Low:   {bar.low:.2f}")
            logger.info(f"  Close: {bar.close:.2f}")
            logger.info(f"  Volume: {bar.volume}")
            logger.info(f"  VWAP: {bar.vwap:.2f if bar.vwap else 'N/A'}")
            logger.info(f"  Tick Count: {bar.tick_count}")
            return True
        else:
            logger.warning("No completed bar available yet (may need to wait for minute boundary)")

            # Check current bar
            current_bar = pipeline.get_current_bar(spot_symbol)
            if current_bar:
                logger.info("Current (incomplete) bar exists:")
                logger.info(f"  Timestamp: {current_bar.timestamp.strftime('%Y-%m-%d %H:%M')}")
                logger.info(f"  Close (LTP): {current_bar.close:.2f if current_bar.close else 'N/A'}")
                logger.info(f"  Tick Count: {current_bar.tick_count}")
                return True
            else:
                logger.error("FAILED: No bars available (completed or current)")
                return False

    except Exception as e:
        logger.error(f"FAILED: Exception during bar retrieval: {e}")
        return False


def test_auto_detection(pipeline):
    """Test 5: Auto-Detection with WebSocket"""
    print_section("TEST 5: Auto-Detection with WebSocket")

    spot_symbol = "Nifty 50"

    try:
        # Create AutoDetector with WebSocket support
        detector = AutoDetector(
            api_key=OPENALGO_API_KEY,
            host=OPENALGO_HOST,
            data_pipeline=pipeline,
            spot_symbol=spot_symbol
        )

        logger.info("Testing WebSocket-based spot price fetch...")

        # Test fetch_spot_price_from_websocket (won't wait for 9:16 if after that time)
        spot_price = detector.fetch_spot_price_from_websocket()

        if spot_price is not None:
            logger.info(f"SUCCESS: WebSocket spot price: {spot_price}")

            # Calculate ATM
            atm_strike = detector.calculate_atm_strike(spot_price)
            logger.info(f"  Calculated ATM: {spot_price:.2f} -> {atm_strike}")

            return True
        else:
            logger.warning("WebSocket spot price unavailable, would fallback to API")
            logger.info("Testing API fallback...")

            try:
                spot_price = detector.fetch_spot_price()
                logger.info(f"SUCCESS: API fallback spot price: {spot_price}")
                atm_strike = detector.calculate_atm_strike(spot_price)
                logger.info(f"  Calculated ATM: {spot_price:.2f} -> {atm_strike}")
                return True
            except Exception as api_error:
                logger.error(f"API fallback also failed: {api_error}")
                return False

    except Exception as e:
        logger.error(f"FAILED: Exception during auto-detection: {e}")
        return False


def test_full_auto_detect(pipeline):
    """Test 6: Full Auto-Detection Flow"""
    print_section("TEST 6: Full Auto-Detection Flow (ATM + Expiry)")

    spot_symbol = "Nifty 50"

    try:
        detector = AutoDetector(
            api_key=OPENALGO_API_KEY,
            host=OPENALGO_HOST,
            data_pipeline=pipeline,
            spot_symbol=spot_symbol
        )

        logger.info("Running full auto-detection (may take a few seconds)...")

        # This will try WebSocket first, then API fallback
        atm_strike, expiry_date = detector.auto_detect()

        logger.info(f"SUCCESS: Auto-detection complete")
        logger.info(f"  ATM Strike: {atm_strike}")
        logger.info(f"  Expiry Date: {expiry_date}")

        # Validate results
        if 15000 <= atm_strike <= 30000 and atm_strike % 100 == 0:
            logger.info("  ATM strike is valid (multiple of 100, reasonable range)")
        else:
            logger.warning(f"  WARNING: ATM strike {atm_strike} looks suspicious")

        if len(expiry_date) in [7, 8]:  # DDMMMYY or DDDMMMYY format
            logger.info(f"  Expiry format is valid ({len(expiry_date)} chars)")
        else:
            logger.warning(f"  WARNING: Expiry format unusual ({len(expiry_date)} chars)")

        return True

    except Exception as e:
        logger.error(f"FAILED: Exception during full auto-detection: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_symbol_variants(pipeline):
    """Test 7: Try Different Symbol Variants"""
    print_section("TEST 7: Symbol Variant Detection")

    variants = ["Nifty 50", "NIFTY", "NSE:NIFTY", "NIFTY 50"]

    logger.info("Testing different symbol formats to find the correct one...")

    for variant in variants:
        logger.info(f"\nTrying symbol: '{variant}'")

        try:
            # Get spot price
            spot_price = pipeline.get_spot_price(variant)

            if spot_price is not None:
                logger.info(f"  SUCCESS: {variant} returned spot price: {spot_price}")
            else:
                logger.info(f"  FAILED: {variant} returned None")

        except Exception as e:
            logger.info(f"  ERROR: {variant} caused exception: {e}")

    logger.info("\nRecommendation: Use the symbol that returned a valid spot price")


def run_all_tests():
    """Run all tests in sequence"""
    print("\n" + "="*80)
    print("  NIFTY SPOT WEBSOCKET DETECTION TEST SUITE")
    print("="*80)

    now = datetime.now(IST)
    logger.info(f"Test started at: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Check market hours
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if market_open <= now <= market_close:
        logger.info("Market is OPEN - Real-time data should be available")
    else:
        logger.warning("Market is CLOSED - Data may be stale or unavailable")
        logger.info("Some tests may fail due to no live data")

    # Test sequence
    results = {}

    # Test 1: Connection
    pipeline = test_websocket_connection()
    results['connection'] = pipeline is not None

    if not pipeline:
        logger.error("Cannot proceed without WebSocket connection")
        return

    # Test 2: Subscription
    results['subscription'] = test_spot_subscription(pipeline)

    # Test 3: Spot Price
    spot_price = test_spot_price_retrieval(pipeline)
    results['spot_price'] = spot_price is not None

    # Test 4: Bar Retrieval
    results['bar_retrieval'] = test_bar_retrieval(pipeline)

    # Test 5: Auto-Detection
    results['auto_detection'] = test_auto_detection(pipeline)

    # Test 6: Full Flow
    results['full_flow'] = test_full_auto_detect(pipeline)

    # Test 7: Symbol Variants (diagnostic)
    test_symbol_variants(pipeline)

    # Clean up
    print_section("Cleanup")
    logger.info("Disconnecting WebSocket...")
    pipeline.disconnect()
    logger.info("WebSocket disconnected")

    # Summary
    print_section("TEST SUMMARY")

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, passed_status in results.items():
        status = "PASS" if passed_status else "FAIL"
        symbol = "✓" if passed_status else "✗"
        logger.info(f"  {symbol} {test_name.replace('_', ' ').title()}: {status}")

    print("\n" + "-"*80)
    logger.info(f"TOTAL: {passed}/{total} tests passed ({passed/total*100:.0f}%)")
    print("-"*80 + "\n")

    if passed == total:
        logger.info("SUCCESS: All tests passed! WebSocket spot detection is working.")
        return 0
    else:
        logger.warning(f"PARTIAL: {total - passed} test(s) failed. Check logs above.")
        return 1


if __name__ == '__main__':
    try:
        exit_code = run_all_tests()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
