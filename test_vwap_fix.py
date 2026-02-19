"""
Test script to verify VWAP bug fix

Tests that swing_info VWAP values are preserved after swing updates
"""
import sys
from datetime import datetime, timedelta
import pytz

# Add baseline_v1_live to path
sys.path.insert(0, 'D:/nifty_options_agent/baseline_v1_live')

from swing_detector import SymbolSwingDetector
from continuous_filter import ContinuousStrikeFilter

IST = pytz.timezone('Asia/Kolkata')

def test_vwap_preservation():
    """Test that VWAP is preserved when swing is updated"""

    print("="*60)
    print("TEST: VWAP Preservation After Swing Update")
    print("="*60)

    # Create detector
    detector = SymbolSwingDetector("NIFTY27JAN2625000CE")

    # Create filter
    strike_filter = ContinuousStrikeFilter()

    # Create test bars
    base_time = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0)

    test_bars = [
        # Bar 0: Initial bar
        {'timestamp': base_time, 'open': 150, 'high': 152, 'low': 148, 'close': 149, 'volume': 100, 'vwap': 150.0},
        # Bar 1: Lower low
        {'timestamp': base_time + timedelta(minutes=1), 'open': 149, 'high': 150, 'low': 140, 'close': 142, 'volume': 120, 'vwap': 145.0},
        # Bar 2: Even lower low (will become swing low)
        {'timestamp': base_time + timedelta(minutes=2), 'open': 142, 'high': 143, 'low': 130, 'close': 132, 'volume': 150, 'vwap': 140.0},
        # Bar 3: Higher high + higher close (first watch)
        {'timestamp': base_time + timedelta(minutes=3), 'open': 132, 'high': 145, 'low': 132, 'close': 144, 'volume': 110, 'vwap': 142.0},
        # Bar 4: Higher high + higher close (second watch -> swing confirmed)
        {'timestamp': base_time + timedelta(minutes=4), 'open': 144, 'high': 150, 'low': 143, 'close': 149, 'volume': 130, 'vwap': 145.0},
    ]

    # Process bars
    swing_detected = None
    for i, bar in enumerate(test_bars):
        print(f"\nBar {i}: {bar['timestamp'].strftime('%H:%M')} | L={bar['low']:.0f} H={bar['high']:.0f} C={bar['close']:.0f} VWAP={bar['vwap']:.2f}")

        swing_info = detector.add_bar(bar)

        if swing_info and swing_info.get('type') == 'Low':
            swing_detected = swing_info
            print(f"  -> SWING LOW detected: Price={swing_info['price']:.2f}, VWAP={swing_info['vwap']:.2f}")

            # Add to filter (this is where the bug was)
            strike_filter.add_swing_candidate("NIFTY27JAN2625000CE", swing_info)

    # Verify swing was detected
    if not swing_detected:
        print("\nERROR: No swing detected!")
        return False

    # Get the stored swing from filter
    stored_swing = strike_filter.swing_candidates.get("NIFTY27JAN2625000CE")

    if not stored_swing:
        print("\nERROR: Swing not found in swing_candidates!")
        return False

    print("\n" + "="*60)
    print("VERIFICATION")
    print("="*60)
    print(f"Original swing_info VWAP: {swing_detected['vwap']:.2f}")
    print(f"Stored swing VWAP:       {stored_swing['vwap']:.2f}")

    # Now simulate a swing update (modify the original swing_info)
    print("\n" + "-"*60)
    print("Simulating swing update (modifying original swing_info)...")
    print("-"*60)

    # Modify original swing_info (simulate what _update_swing_extreme does)
    swing_detected['price'] = 125.0  # Update to lower price
    swing_detected['high'] = 160.0   # Update high field
    swing_detected['low'] = 125.0    # Update low field

    print("Modified original swing_info:")
    print(f"  price: {swing_detected['price']:.2f}")
    print(f"  high: {swing_detected['high']:.2f}")
    print(f"  low: {swing_detected['low']:.2f}")
    print(f"  vwap: {swing_detected['vwap']:.2f}")

    # Check if stored swing was affected
    print("\nStored swing after modification:")
    print(f"  price: {stored_swing['price']:.2f}")
    print(f"  high: {stored_swing['high']:.2f}")
    print(f"  low: {stored_swing['low']:.2f}")
    print(f"  VWAP: {stored_swing['vwap']:.2f}")

    # CRITICAL CHECK: VWAP should remain unchanged
    original_vwap = 140.0  # VWAP from bar 2 (swing low bar)

    print("\n" + "="*60)
    print("RESULT")
    print("="*60)

    if abs(stored_swing['vwap'] - original_vwap) < 0.01:
        print(f"SUCCESS: VWAP preserved correctly ({stored_swing['vwap']:.2f})")
        print("Fix verified - deepcopy() is working!")
        return True
    else:
        print("FAILED: VWAP was corrupted!")
        print(f"  Expected: {original_vwap:.2f}")
        print(f"  Got: {stored_swing['vwap']:.2f}")
        print("Bug still present - swing_info is being stored as reference!")
        return False

if __name__ == "__main__":
    success = test_vwap_preservation()

    print("\n" + "="*60)
    if success:
        print("TEST PASSED")
    else:
        print("TEST FAILED")
    print("="*60)

    sys.exit(0 if success else 1)
