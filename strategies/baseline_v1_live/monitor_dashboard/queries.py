POSITIONS = """
SELECT * FROM positions
WHERE is_closed = 0
ORDER BY entry_time DESC
"""

PENDING_ORDERS = """
SELECT * FROM pending_orders
ORDER BY placed_at DESC
"""

DAILY_STATE = """
SELECT * FROM daily_state
ORDER BY updated_at DESC
LIMIT 1
"""

# Stage-1: Static Filters Candidates (passed price range 100-300 and VWAP ≥4%)
STAGE1_STATIC_CANDIDATES = """
SELECT
    sc.symbol,
    sc.swing_low,
    sc.vwap_at_swing,
    sc.timestamp,
    sc.option_type,
    ROUND(((sc.swing_low - sc.vwap_at_swing) / sc.vwap_at_swing) * 100, 2) as vwap_premium_pct,
    CASE
        WHEN b.highest_high IS NOT NULL THEN
            ROUND((b.highest_high + 1 - sc.swing_low), 2)
        ELSE NULL
    END as sl_points,
    CASE
        WHEN b.highest_high IS NOT NULL THEN
            ROUND(((b.highest_high + 1 - sc.swing_low) / sc.swing_low) * 100, 2)
        ELSE NULL
    END as sl_pct
FROM swing_candidates sc
LEFT JOIN (
    SELECT
        symbol,
        MAX(high) as highest_high
    FROM bars
    GROUP BY symbol
) b ON sc.symbol = b.symbol
WHERE sc.active = 1
ORDER BY sc.option_type, sc.timestamp DESC
"""

# Stage-2: Dynamic Filters Candidates (passed Stage-1 AND currently pass SL% 2-10%)
STAGE2_DYNAMIC_CANDIDATES = """
SELECT
    sc.symbol,
    sc.swing_low,
    sc.vwap_at_swing,
    sc.timestamp,
    sc.option_type,
    ROUND(((sc.swing_low - sc.vwap_at_swing) / sc.vwap_at_swing) * 100, 2) as vwap_premium_pct,
    b.highest_high,
    ROUND((b.highest_high + 1), 2) as sl_price,
    ROUND((b.highest_high + 1 - sc.swing_low), 2) as sl_points,
    ROUND(((b.highest_high + 1 - sc.swing_low) / sc.swing_low) * 100, 2) as sl_pct,
    'Qualified' as status
FROM swing_candidates sc
INNER JOIN (
    SELECT
        symbol,
        MAX(high) as highest_high
    FROM bars
    GROUP BY symbol
) b ON sc.symbol = b.symbol
WHERE sc.active = 1
    AND ((b.highest_high + 1 - sc.swing_low) / sc.swing_low) >= 0.02  -- MIN_SL_PERCENT
    AND ((b.highest_high + 1 - sc.swing_low) / sc.swing_low) <= 0.10  -- MAX_SL_PERCENT
ORDER BY sc.option_type, sc.timestamp DESC
"""

# Filter Summary Metrics
FILTER_SUMMARY_METRICS = """
SELECT
    (SELECT COUNT(*) FROM all_swings_log WHERE swing_type = 'Low' AND DATE(swing_time) = DATE('now', 'localtime')) as total_swings_detected,
    (SELECT COUNT(*) FROM swing_candidates WHERE active = 1) as static_filter_pass,
    (SELECT COUNT(*) FROM (
        SELECT sc.symbol
        FROM swing_candidates sc
        INNER JOIN (SELECT symbol, MAX(high) as highest_high FROM bars GROUP BY symbol) b ON sc.symbol = b.symbol
        WHERE sc.active = 1
            AND ((b.highest_high + 1 - sc.swing_low) / sc.swing_low) >= 0.02
            AND ((b.highest_high + 1 - sc.swing_low) / sc.swing_low) <= 0.10
    )) as sl_filter_pass,
    (SELECT COUNT(*) FROM best_strikes WHERE is_current = 1 AND DATE(updated_at) = DATE('now', 'localtime')) as best_strikes_selected
"""

# Legacy alias for backward compatibility
SWING_CANDIDATES = STAGE1_STATIC_CANDIDATES

# Stage-3: Final Qualifiers (best CE and best PE selected via tie-breaker)
STAGE3_FINAL_QUALIFIERS = """
SELECT
    option_type,
    symbol,
    entry_price,
    sl_price,
    sl_points,
    ROUND(vwap_premium_percent, 2) as vwap_premium_pct,
    ROUND(((sl_price - entry_price) / entry_price) * 100, 2) as sl_pct,
    swing_timestamp,
    updated_at,
    'Final' as status
FROM best_strikes
WHERE is_current = 1
AND DATE(updated_at) = DATE('now', 'localtime')
ORDER BY option_type DESC, updated_at DESC
"""

# Legacy alias for backward compatibility
BEST_STRIKES = STAGE3_FINAL_QUALIFIERS

FILTER_REJECTIONS = """
SELECT
    timestamp,
    symbol,
    option_type,
    ROUND(swing_low, 2) as swing_low,
    ROUND(vwap_at_swing, 2) as vwap_at_swing,
    ROUND(vwap_premium_percent * 100, 2) as vwap_premium_pct,
    ROUND(sl_percent * 100, 2) as sl_pct,
    rejection_reason
FROM filter_rejections
ORDER BY timestamp DESC
LIMIT 50
"""

TRADE_LOG = """
SELECT * FROM trade_log
ORDER BY exit_time DESC
LIMIT 50
"""

# Chart queries
OHLC_DATA = """
SELECT timestamp, open, high, low, close, volume
FROM bars
WHERE symbol = ?
ORDER BY timestamp ASC
"""

SWING_DATA = """
SELECT swing_type, swing_price, swing_time, vwap, bar_index
FROM all_swings_log
WHERE symbol = ?
ORDER BY swing_time ASC
"""

POSITION_FOR_SYMBOL = """
SELECT entry_price, sl_price, entry_time, exit_time, is_closed
FROM positions
WHERE symbol = ?
ORDER BY entry_time DESC
LIMIT 1
"""

AVAILABLE_STRIKES = """
SELECT DISTINCT symbol FROM bars
WHERE symbol LIKE ?
ORDER BY symbol
"""

# Bar Viewer - All bars from 9:15 AM onwards (today's session)
LAST_20_BARS = """
SELECT timestamp, open, high, low, close, volume
FROM bars
WHERE symbol = ?
AND DATE(timestamp) = DATE('now', 'localtime')
ORDER BY timestamp ASC
"""

# Get current expiry from daily_state (set by baseline strategy)
NEAREST_EXPIRY = """
SELECT expiry
FROM daily_state
WHERE trade_date = DATE('now', 'localtime')
LIMIT 1
"""
