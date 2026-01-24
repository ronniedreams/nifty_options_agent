import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import plotly.express as px

from db import read_df
from ui_components import kpi, df_table, candlestick_chart, build_symbol
import queries as q
from config import STRATEGY_NAME, FAST_REFRESH


IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# Streamlit Page Config
# ─────────────────────────────────────────────
st.set_page_config(
    page_title=f"{STRATEGY_NAME} Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Auto refresh

# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────
now = datetime.now(IST).strftime("%H:%M:%S IST")

st.markdown(
    f"""
    # 🧠 {STRATEGY_NAME} – Live Dashboard  
    **Time:** {now}
    ---
    """
)

# ─────────────────────────────────────────────
# KPI ROW
# ─────────────────────────────────────────────
daily = read_df(q.DAILY_STATE)

cumulative_r = daily["cumulative_R"].iloc[0] if not daily.empty else 0
total_pnl = daily["total_pnl"].iloc[0] if not daily.empty else 0
positions = read_df(q.POSITIONS)

ce_count = len(positions[positions.option_type == "CE"])
pe_count = len(positions[positions.option_type == "PE"])

col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    kpi("Cumulative R", f"{cumulative_r:.2f}", "#2ecc71" if cumulative_r >= 0 else "#e74c3c")
with col2:
    kpi("Total P&L ₹", f"{total_pnl:,.0f}")
with col3:
    kpi("Open Positions", len(positions))
with col4:
    kpi("CE / PE", f"{ce_count} / {pe_count}")
with col5:
    kpi("Status", "RUNNING", "#3498db")

st.markdown("---")

# ─────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────
tabs = st.tabs([
    "📌 Live Positions",
    "📦 Orders",
    "🔬 Filter Pipeline (3 Stages)",
    "🚨 Events",
    "📊 Performance",
    "📈 Chart",
    "🔍 Bar Viewer",
    "🛑 Controls"
])

# ─────────────────────────────────────────────
# TAB 1: LIVE POSITIONS
# ─────────────────────────────────────────────
with tabs[0]:
    st.subheader("Open Positions")
    df_table(positions)

# ─────────────────────────────────────────────
# TAB 2: ORDERS
# ─────────────────────────────────────────────
with tabs[1]:
    st.subheader("Pending Orders")
    orders = read_df(q.PENDING_ORDERS)
    df_table(orders)

# ─────────────────────────────────────────────
# TAB 3: SWINGS & FILTERS (Three-Stage Filter Pipeline)
# ─────────────────────────────────────────────
with tabs[2]:
    st.markdown("""
    ### Strike Filtration Pipeline

    **Theory:** Two-stage filtering + tie-breaker selection
    - **Stage-1 (Static):** Price range 100-300 Rs, VWAP ≥4% (run once at swing formation, IMMUTABLE)
    - **Stage-2 (Dynamic):** SL% 2-10% (recalculated every bar as highest_high updates, MUTABLE)
    - **Stage-3 (Tie-Breaker):** Best CE & PE (SL points closest to 10, then highest entry price)

    **Note:** SL Price = Highest High + 1 Rs (buffer for slippage)
    """)

    st.markdown("---")

    # Filter Summary Metrics
    st.subheader("📊 Filter Effectiveness Summary")
    summary = read_df(q.FILTER_SUMMARY_METRICS)

    if not summary.empty:
        total = summary['total_swings_detected'].iloc[0]
        static = summary['static_filter_pass'].iloc[0]
        sl_pass = summary['sl_filter_pass'].iloc[0]
        best = summary['best_strikes_selected'].iloc[0]

        # Calculate percentages
        static_pct = (static / total * 100) if total > 0 else 0
        sl_pct = (sl_pass / static * 100) if static > 0 else 0

        # Display metrics in columns
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Total Swings Detected", total)
        with col2:
            st.metric("Static Filter Pass", f"{static} ({static_pct:.0f}%)")
        with col3:
            st.metric("SL% Filter Pass", f"{sl_pass} ({sl_pct:.0f}%)")
        with col4:
            st.metric("Best Strikes Selected", f"{best} (max 2)")

        # Show funnel visualization
        if total > 0:
            st.caption(f"**Funnel:** {total} swings → {static} static pass ({static_pct:.0f}%) → {sl_pass} SL% pass ({sl_pct:.0f}%) → {best} final")
    else:
        st.info("No filter metrics available yet")

    st.markdown("---")

    # Stage-1: Static Filters Candidates
    st.subheader("📋 Stage-1: Static Filters Candidates")
    st.caption("Swings that passed price range (100-300 Rs) and VWAP premium (≥4%) at swing formation")
    stage1 = read_df(q.STAGE1_STATIC_CANDIDATES)
    if not stage1.empty:
        # Show count by option type
        ce_count = len(stage1[stage1['option_type'] == 'CE'])
        pe_count = len(stage1[stage1['option_type'] == 'PE'])
        st.info(f"**{len(stage1)} Total Candidates** | CE: {ce_count} | PE: {pe_count}")
    df_table(stage1, height=250)

    st.markdown("---")

    # Stage-2: Dynamic Filters Candidates
    st.subheader("⚡ Stage-2: Dynamic Filters Candidates")
    st.caption("Stage-1 candidates that CURRENTLY pass SL% filter (2-10%). SL% recalculated every bar.")
    stage2 = read_df(q.STAGE2_DYNAMIC_CANDIDATES)
    if not stage2.empty:
        # Show count by option type
        ce_count = len(stage2[stage2['option_type'] == 'CE'])
        pe_count = len(stage2[stage2['option_type'] == 'PE'])
        st.success(f"**{len(stage2)} Qualified Candidates** | CE: {ce_count} | PE: {pe_count}")
    else:
        st.warning("⚠️ No candidates currently pass dynamic SL% filter (2-10%)")
    df_table(stage2, height=250)

    st.markdown("---")

    # Stage-3: Final Qualifiers
    st.subheader("🎯 Stage-3: Final Qualifiers (Best Strikes)")
    st.caption("Best CE and best PE selected from Stage-2 using tie-breaker (SL points closest to 10, then highest entry price)")
    stage3 = read_df(q.STAGE3_FINAL_QUALIFIERS)
    if not stage3.empty:
        # Highlight the final strikes
        st.success(f"**{len(stage3)} Final Strikes Ready** (max 2: 1 CE + 1 PE)")
    else:
        st.warning("⚠️ No final qualifiers - waiting for candidates to pass all filters")
    df_table(stage3, height=150)

    st.markdown("---")

    # Recent Rejections
    st.subheader("❌ Recent Rejections")
    st.caption("Swings rejected by static or dynamic filters (last 50)")
    rej = read_df(q.FILTER_REJECTIONS)
    df_table(rej, height=300)

# ─────────────────────────────────────────────
# TAB 4: EVENTS
# ─────────────────────────────────────────────
with tabs[3]:
    st.subheader("Recent Trade Events")
    trades = read_df(q.TRADE_LOG)
    df_table(trades)

# ─────────────────────────────────────────────
# TAB 5: PERFORMANCE
# ─────────────────────────────────────────────
with tabs[4]:
    if not trades.empty:
        st.subheader("R Distribution")
        fig = px.histogram(trades, x="realized_R", nbins=20)
        st.plotly_chart(fig, use_container_width=True)

# ─────────────────────────────────────────────
# TAB 6: CHART
# ─────────────────────────────────────────────
with tabs[5]:
    st.subheader("Option Price Chart")

    # Get current expiry from daily_state (set by baseline strategy)
    expiry_result = read_df(q.NEAREST_EXPIRY)
    if not expiry_result.empty and expiry_result['expiry'].iloc[0]:
        default_expiry = expiry_result['expiry'].iloc[0]
    else:
        # Fallback: Extract from available symbols if daily_state doesn't have it yet
        fallback_query = """
        SELECT DISTINCT substr(symbol, 6, 7) as expiry
        FROM bars
        WHERE symbol LIKE 'NIFTY%'
        ORDER BY expiry DESC
        LIMIT 1
        """
        fallback_result = read_df(fallback_query)
        default_expiry = fallback_result['expiry'].iloc[0] if not fallback_result.empty else "30JAN26"

    # Create input controls
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])

    with col1:
        expiry = st.text_input("Expiry (DDMMMYY)", value=default_expiry)

    with col2:
        strike = st.number_input("Strike", min_value=10000, max_value=30000, value=26000, step=50)

    with col3:
        option_type = st.radio("Option Type", ["CE", "PE"], horizontal=True)

    with col4:
        st.write("")  # Spacer
        show_chart = st.button("📊 Show Chart", use_container_width=True)

    # Display chart when button is clicked
    if show_chart or st.session_state.get('chart_loaded', False):
        st.session_state['chart_loaded'] = True

        symbol = build_symbol(expiry, strike, option_type)

        # Fetch OHLC data
        ohlc_df = read_df(q.OHLC_DATA, params=(symbol,))

        # Filter to most recent date only (avoid multi-day VWAP calculation issues)
        most_recent_date = None
        if not ohlc_df.empty:
            ohlc_df['timestamp'] = pd.to_datetime(ohlc_df['timestamp'])
            ohlc_df['date'] = ohlc_df['timestamp'].dt.date
            most_recent_date = ohlc_df['date'].max()
            ohlc_df = ohlc_df[ohlc_df['date'] == most_recent_date].copy()
            ohlc_df = ohlc_df.drop(columns=['date'])

        # Show info with data coverage warning
        coverage_pct = (len(ohlc_df) / 375) * 100  # 375 = full trading session bars
        if coverage_pct < 50:
            st.warning(f"⚠️ **Partial Data**: {symbol} | Date: {most_recent_date} | {len(ohlc_df)} bars ({coverage_pct:.1f}% coverage)")
        else:
            st.info(f"📊 **{symbol}** | Trading Date: **{most_recent_date}** | Bars: **{len(ohlc_df)}** ({coverage_pct:.1f}% coverage)")

        # Fetch swing data
        swings_df = read_df(q.SWING_DATA, params=(symbol,))

        # Filter swings to same date
        if not swings_df.empty and not ohlc_df.empty:
            swings_df['swing_time'] = pd.to_datetime(swings_df['swing_time'])
            swings_df['date'] = swings_df['swing_time'].dt.date
            swings_df = swings_df[swings_df['date'] == most_recent_date].copy()
            swings_df = swings_df.drop(columns=['date'])

        # Fetch position data (if any)
        position_df = read_df(q.POSITION_FOR_SYMBOL, params=(symbol,))

        # Calculate VWAP for metrics display
        current_vwap = None
        if not ohlc_df.empty:
            ohlc_df['typical_price'] = (ohlc_df['high'] + ohlc_df['low'] + ohlc_df['close']) / 3
            ohlc_df['tp_volume'] = ohlc_df['typical_price'] * ohlc_df['volume']
            ohlc_df['cumulative_tp_volume'] = ohlc_df['tp_volume'].cumsum()
            ohlc_df['cumulative_volume'] = ohlc_df['volume'].cumsum()
            ohlc_df['vwap'] = ohlc_df['cumulative_tp_volume'] / ohlc_df['cumulative_volume']
            current_vwap = ohlc_df['vwap'].iloc[-1]

        # Display chart info
        col_info1, col_info2, col_info3, col_info4 = st.columns(4)
        with col_info1:
            st.metric("Bars Available", len(ohlc_df))
        with col_info2:
            st.metric("Swing Points", len(swings_df))
        with col_info3:
            if current_vwap is not None:
                st.metric("Current VWAP", f"₹{current_vwap:.2f}")
            else:
                st.metric("Current VWAP", "N/A")
        with col_info4:
            st.metric("Has Position", "Yes" if not position_df.empty else "No")

        # Debug info (can be removed later)
        with st.expander("🔍 Debug Info"):
            st.write(f"OHLC shape: {ohlc_df.shape}")
            st.write(f"Timestamp dtype: {ohlc_df['timestamp'].dtype}")
            st.write(f"First timestamp: {ohlc_df['timestamp'].iloc[0]}")
            st.write(f"Last timestamp: {ohlc_df['timestamp'].iloc[-1]}")
            st.write("Sample data:")
            st.dataframe(ohlc_df[['timestamp', 'open', 'high', 'low', 'close']].head())

        # Display chart
        candlestick_chart(ohlc_df, swings_df, position_df, symbol)

        # Display additional info
        if not position_df.empty and not position_df.iloc[0]['is_closed']:
            pos = position_df.iloc[0]
            st.success(f"**Active Position** | Entry: ₹{pos['entry_price']:.2f} | SL: ₹{pos['sl_price']:.2f}")

# ─────────────────────────────────────────────
# TAB 7: BAR VIEWER (Full Session from 9:15 AM)
# ─────────────────────────────────────────────
with tabs[6]:
    st.subheader("🔍 Bar Viewer - Full Session (9:15 AM onwards) with Swing Labels")

    # Get current expiry from daily_state (set by baseline strategy)
    expiry_result = read_df(q.NEAREST_EXPIRY)
    if not expiry_result.empty and expiry_result['expiry'].iloc[0]:
        default_expiry = expiry_result['expiry'].iloc[0]
    else:
        # Fallback: Extract from available symbols if daily_state doesn't have it yet
        fallback_query = """
        SELECT DISTINCT substr(symbol, 6, 7) as expiry
        FROM bars
        WHERE symbol LIKE 'NIFTY%'
        ORDER BY expiry DESC
        LIMIT 1
        """
        fallback_result = read_df(fallback_query)
        default_expiry = fallback_result['expiry'].iloc[0] if not fallback_result.empty else "30JAN26"

    # Create input controls
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])

    with col1:
        expiry_viewer = st.text_input("Expiry (DDMMMYY)", value=default_expiry, key="bar_viewer_expiry")

    with col2:
        strike_viewer = st.number_input("Strike", min_value=10000, max_value=30000, value=26000, step=50, key="bar_viewer_strike")

    with col3:
        option_type_viewer = st.radio("Option Type", ["CE", "PE"], horizontal=True, key="bar_viewer_option_type")

    with col4:
        st.write("")  # Spacer
        show_bars = st.button("📊 Show Bars", use_container_width=True)

    # Display bars when button is clicked
    if show_bars or st.session_state.get('bar_viewer_loaded', False):
        st.session_state['bar_viewer_loaded'] = True

        symbol = build_symbol(expiry_viewer, strike_viewer, option_type_viewer)

        # Fetch all bars from 9:15 AM onwards (today's session)
        bars_df = read_df(q.LAST_20_BARS, params=(symbol,))

        if bars_df.empty:
            st.warning(f"⚠️ No bar data found for {symbol}")
        else:
            # Bars are already in chronological order (oldest to newest)
            bars_df = bars_df.reset_index(drop=True)

            # Convert timestamp
            bars_df['timestamp'] = pd.to_datetime(bars_df['timestamp'])

            # Fetch swing data for the same time range
            if len(bars_df) > 0:
                # Convert to ISO format with timezone for SQLite match
                time_start_str = bars_df['timestamp'].min().isoformat()
                time_end_str = bars_df['timestamp'].max().isoformat()

                # Get swings in this time range
                swings_query = """
                SELECT swing_type, swing_price, swing_time, vwap, bar_index
                FROM all_swings_log
                WHERE symbol = ?
                AND swing_time BETWEEN ? AND ?
                ORDER BY swing_time ASC
                """
                swings_df = read_df(swings_query, params=(symbol, time_start_str, time_end_str))
            else:
                swings_df = pd.DataFrame()

            # Display info
            col_info1, col_info2, col_info3 = st.columns(3)
            with col_info1:
                st.metric("Bars Displayed", len(bars_df))
            with col_info2:
                st.metric("Swing Points", len(swings_df))
            with col_info3:
                time_range = f"{bars_df['timestamp'].iloc[0].strftime('%H:%M')} - {bars_df['timestamp'].iloc[-1].strftime('%H:%M')}"
                st.metric("Time Range", time_range)

            # Merge swing labels into bar data
            display_df = bars_df.copy()
            display_df['swing_label'] = ''

            if not swings_df.empty:
                swings_df['swing_time'] = pd.to_datetime(swings_df['swing_time'])

                # Convert both to string format for comparison (handles timezone mismatches)
                display_df['ts_str'] = display_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
                swings_df['ts_str'] = swings_df['swing_time'].dt.strftime('%Y-%m-%d %H:%M:%S')

                for _, swing in swings_df.iterrows():
                    # Find matching bar by timestamp string
                    mask = display_df['ts_str'] == swing['ts_str']
                    if mask.any():
                        swing_type = swing['swing_type']
                        swing_price = swing['swing_price']
                        display_df.loc[mask, 'swing_label'] = f"{swing_type} @ {swing_price:.2f}"

                # Drop temp column
                display_df = display_df.drop(columns=['ts_str'])

            # Display bar data table with swing labels
            st.subheader("📋 Bar Data with Swing Labels")
            table_display = display_df.copy()
            table_display['timestamp'] = table_display['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
            # Reorder columns to show swing_label after timestamp
            cols = ['timestamp', 'swing_label', 'open', 'high', 'low', 'close', 'volume']
            st.dataframe(table_display[cols], use_container_width=True, height=600)

            # Display swing summary if any
            if not swings_df.empty:
                st.subheader("🔄 Swing Summary")
                swing_summary = swings_df[['swing_time', 'swing_type', 'swing_price', 'vwap']].copy()
                swing_summary['swing_time'] = swing_summary['swing_time'].dt.strftime('%Y-%m-%d %H:%M:%S')
                st.dataframe(swing_summary, use_container_width=True)

# ─────────────────────────────────────────────
# TAB 8: CONTROLS (SAFE)
# ─────────────────────────────────────────────
with tabs[7]:
    st.warning("Controls are SAFE MODE (no orders)")

    if st.button("⏸ Pause New Entries"):
        st.info("Pause flag written (implement DB flag read in strategy)")

    if st.button("🛑 Kill Switch"):
        st.error("Kill switch triggered (implement DB flag read in strategy)")

# ─────────────────────────────────────────────
# AUTO REFRESH
# ─────────────────────────────────────────────
st.caption(f"Auto-refresh every {FAST_REFRESH}s")
import time
time.sleep(FAST_REFRESH)
st.rerun()

