import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import re

def kpi(label, value, color="#2c3e50"):
    st.markdown(
        f"""
        <div style="
            background-color:{color};
            padding:12px;
            border-radius:10px;
            text-align:center;
            color:white;
        ">
            <h4 style="margin-bottom:4px;">{label}</h4>
            <h2 style="margin:0;">{value}</h2>
        </div>
        """,
        unsafe_allow_html=True
    )

def df_table(df: pd.DataFrame, height=400):
    if df is None or df.empty:
        st.info("No data available")
    else:
        st.dataframe(df, height=height, use_container_width=True)


def build_symbol(expiry: str, strike: int, option_type: str) -> str:
    """Build option symbol from components"""
    return f"NIFTY{expiry}{strike}{option_type}"


def parse_symbol(symbol: str) -> dict:
    """Parse symbol into components: NIFTY30DEC2526000CE -> expiry, strike, type"""
    pattern = r"NIFTY(\d{2}[A-Z]{3}\d{2})(\d+)(CE|PE)"
    match = re.match(pattern, symbol)
    if match:
        return {
            "expiry": match.group(1),
            "strike": int(match.group(2)),
            "option_type": match.group(3)
        }
    return None


def candlestick_chart(ohlc_df: pd.DataFrame, swings_df: pd.DataFrame,
                     position_df: pd.DataFrame, symbol: str):
    """
    Create candlestick chart matching offline_data_viewer.py style

    Args:
        ohlc_df: DataFrame with columns [timestamp, open, high, low, close, volume]
        swings_df: DataFrame with columns [swing_type, swing_price, swing_time, vwap, bar_index]
        position_df: DataFrame with columns [entry_price, sl_price, entry_time, exit_time, is_closed]
        symbol: Symbol name for title
    """

    if ohlc_df.empty:
        st.warning(f"No OHLC data available for {symbol}")
        return

    # Ensure we have a copy to avoid SettingWithCopyWarning
    ohlc_df = ohlc_df.copy()

    # Convert timestamp to datetime if needed
    if ohlc_df['timestamp'].dtype == 'object':
        ohlc_df['timestamp'] = pd.to_datetime(ohlc_df['timestamp'])

    # Sort by timestamp to ensure proper ordering
    ohlc_df = ohlc_df.sort_values('timestamp').reset_index(drop=True)

    # Calculate VWAP if not already calculated
    if 'vwap' not in ohlc_df.columns:
        ohlc_df['typical_price'] = (ohlc_df['high'] + ohlc_df['low'] + ohlc_df['close']) / 3
        ohlc_df['tp_volume'] = ohlc_df['typical_price'] * ohlc_df['volume']
        ohlc_df['cumulative_tp_volume'] = ohlc_df['tp_volume'].cumsum()
        ohlc_df['cumulative_volume'] = ohlc_df['volume'].cumsum()
        ohlc_df['vwap'] = ohlc_df['cumulative_tp_volume'] / ohlc_df['cumulative_volume']

    # Remove duplicate swings
    if not swings_df.empty:
        if swings_df['swing_time'].dtype == 'object':
            swings_df['swing_time'] = pd.to_datetime(swings_df['swing_time'])
        swings_df = swings_df.drop_duplicates(subset=['swing_time', 'swing_type'], keep='first')

    # Calculate offset for swing markers (based on average range)
    avg_range = (ohlc_df['high'] - ohlc_df['low']).mean()
    SWING_OFFSET = avg_range * 0.15

    # Create figure
    fig = go.Figure()

    # 1. OHLC bars (better for sparse data than candlesticks)
    fig.add_trace(go.Ohlc(
        x=ohlc_df['timestamp'],
        open=ohlc_df['open'],
        high=ohlc_df['high'],
        low=ohlc_df['low'],
        close=ohlc_df['close'],
        name='Price',
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350',
        line=dict(width=2)
    ))

    # 2. VWAP line (cyan, like offline viewer)
    fig.add_trace(go.Scatter(
        x=ohlc_df['timestamp'],
        y=ohlc_df['vwap'],
        mode='lines',
        name='VWAP',
        line=dict(color='cyan', width=2),
        opacity=0.7
    ))

    # 3. Swing markers
    if not swings_df.empty:
        swing_lows = swings_df[swings_df['swing_type'] == 'Low']
        swing_highs = swings_df[swings_df['swing_type'] == 'High']

        # Swing Low = red triangle-up BELOW the low
        if not swing_lows.empty:
            fig.add_trace(go.Scatter(
                x=swing_lows['swing_time'],
                y=swing_lows['swing_price'] - SWING_OFFSET,
                mode='markers',
                marker=dict(
                    symbol='triangle-up',
                    size=12,
                    color='red'
                ),
                name='Swing Low',
                hovertext=[f"Swing Low: ₹{p:.2f}<br>VWAP: ₹{v:.2f}"
                      for p, v in zip(swing_lows['swing_price'], swing_lows['vwap'])],
                hoverinfo='text'
            ))

        # Swing High = green triangle-down ABOVE the high
        if not swing_highs.empty:
            fig.add_trace(go.Scatter(
                x=swing_highs['swing_time'],
                y=swing_highs['swing_price'] + SWING_OFFSET,
                mode='markers',
                marker=dict(
                    symbol='triangle-down',
                    size=12,
                    color='green'
                ),
                name='Swing High',
                hovertext=[f"Swing High: ₹{p:.2f}<br>VWAP: ₹{v:.2f}"
                      for p, v in zip(swing_highs['swing_price'], swing_highs['vwap'])],
                hoverinfo='text'
            ))

    # Add position entry and SL levels
    if not position_df.empty:
        pos = position_df.iloc[0]

        # Entry line
        fig.add_hline(
            y=pos['entry_price'],
            line_dash="dash",
            line_color="#2196f3",
            annotation_text=f"Entry: {pos['entry_price']:.2f}",
            annotation_position="right"
        )

        # SL line
        fig.add_hline(
            y=pos['sl_price'],
            line_dash="dot",
            line_color="#ff5722",
            annotation_text=f"SL: {pos['sl_price']:.2f}",
            annotation_position="right"
        )

        # Add vertical line for entry time
        if pd.notna(pos['entry_time']):
            fig.add_vline(
                x=pos['entry_time'],
                line_dash="dash",
                line_color="#2196f3",
                opacity=0.3
            )

        # Add vertical line for exit time if closed
        if pos['is_closed'] and pd.notna(pos['exit_time']):
            fig.add_vline(
                x=pos['exit_time'],
                line_dash="dash",
                line_color="#ff5722",
                opacity=0.3
            )

    # Stats for annotation
    num_swing_highs = len(swings_df[swings_df['swing_type'] == 'High']) if not swings_df.empty else 0
    num_swing_lows = len(swings_df[swings_df['swing_type'] == 'Low']) if not swings_df.empty else 0
    num_bars = len(ohlc_df)

    # Calculate time range
    time_start = ohlc_df['timestamp'].iloc[0].strftime('%H:%M')
    time_end = ohlc_df['timestamp'].iloc[-1].strftime('%H:%M')
    time_range = f"{time_start} - {time_end}"

    # Update layout (matching offline_data_viewer.py with explicit dark colors)
    fig.update_layout(
        title=f"{symbol} - 1 Minute OHLC Chart ({time_range} IST)",
        template="plotly_dark",
        xaxis_title="Time",
        yaxis_title="Price",
        height=750,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="black",
            font_size=12,
            font_family="monospace"
        ),
        margin=dict(r=200),
        # Explicit dark theme colors
        plot_bgcolor='#111111',
        paper_bgcolor='#1e1e1e',
        font=dict(color='white'),
        xaxis=dict(
            gridcolor='#2a2a2a',
            showgrid=True,
            zeroline=False
        ),
        yaxis=dict(
            gridcolor='#2a2a2a',
            showgrid=True,
            zeroline=False
        )
    )

    # Add stats annotation (like offline viewer)
    stats_text = (
        f"<b>Chart Stats</b><br><br>"
        f"🟢 Swing Highs: <b>{num_swing_highs}</b><br>"
        f"🔴 Swing Lows: <b>{num_swing_lows}</b><br>"
        f"📊 Total Bars: <b>{num_bars}</b><br>"
        f"⏱️ Time: <b>{time_range}</b>"
    )

    fig.add_annotation(
        text=stats_text,
        xref="paper",
        yref="paper",
        x=1.02,
        y=0.95,
        showarrow=False,
        align="left",
        bordercolor="gray",
        borderwidth=1,
        bgcolor="black",
        font=dict(size=12, color="white")
    )

    st.plotly_chart(fig, use_container_width=True)
