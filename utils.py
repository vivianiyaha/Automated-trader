"""
utils.py - Shared utility functions.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from config import COLOR_GREEN, COLOR_RED, COLOR_BLACK, COLOR_WHITE, COLOR_MUTED, COLOR_CARD


def format_currency(value: float, decimals: int = 2) -> str:
    """Format a float as a currency string with sign."""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.{decimals}f}"


def color_pnl(value: float) -> str:
    """Return green or red hex based on value sign."""
    return COLOR_GREEN if value >= 0 else COLOR_RED


def build_candle_chart(df: pd.DataFrame, symbol: str,
                        signals: dict = None) -> go.Figure:
    """
    Build a Plotly candlestick + EMA + RSI chart.

    Args:
        df:      OHLCV DataFrame with ema_fast, ema_slow, rsi columns
        symbol:  Display name
        signals: Optional dict with entry, sl, tp1 keys

    Returns:
        Plotly Figure
    """
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.04,
    )

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=df["time"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        increasing_line_color=COLOR_GREEN,
        decreasing_line_color=COLOR_RED,
        name="Price"
    ), row=1, col=1)

    # EMAs
    if "ema_fast" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["ema_fast"],
            line=dict(color="#FFD700", width=1),
            name="EMA 50"
        ), row=1, col=1)

    if "ema_slow" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["ema_slow"],
            line=dict(color="#00BFFF", width=1.5, dash="dash"),
            name="EMA 200"
        ), row=1, col=1)

    # Signal levels
    if signals:
        for key, val, colour, dash in [
            ("entry", signals.get("entry"), "#FFFFFF", "dot"),
            ("sl",    signals.get("sl"),    COLOR_RED,  "dash"),
            ("tp1",   signals.get("tp1"),   COLOR_GREEN,"dashdot"),
        ]:
            if val:
                fig.add_hline(y=val, line_color=colour, line_dash=dash,
                               line_width=1.2, row=1, col=1)

    # RSI
    if "rsi" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["rsi"],
            line=dict(color="#BB86FC", width=1.5),
            name="RSI"
        ), row=2, col=1)
        fig.add_hline(y=70, line_color=COLOR_RED,   line_dash="dot",
                       line_width=1, row=2, col=1)
        fig.add_hline(y=30, line_color=COLOR_GREEN,  line_dash="dot",
                       line_width=1, row=2, col=1)
        fig.add_hline(y=50, line_color=COLOR_MUTED, line_dash="dot",
                       line_width=1, row=2, col=1)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=COLOR_CARD,
        plot_bgcolor=COLOR_BLACK,
        font=dict(color=COLOR_WHITE, family="Inter, sans-serif", size=11),
        title=dict(text=symbol, font=dict(size=14, color=COLOR_WHITE)),
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h",
                    yanchor="bottom", y=1.02),
        xaxis_rangeslider_visible=False,
        height=480,
    )
    fig.update_yaxes(gridcolor="#1E1E1E", zerolinecolor="#333")
    fig.update_xaxes(gridcolor="#1E1E1E")
    return fig


def signal_badge_html(signal: str, confidence: float) -> str:
    """Return an HTML badge string for a signal."""
    if signal == "BUY":
        bg, fg = "#00FF88", "#000"
    elif signal == "SELL":
        bg, fg = "#FF3A3A", "#FFF"
    else:
        bg, fg = "#333", "#AAA"
    return (f'<span style="background:{bg};color:{fg};padding:2px 10px;'
            f'border-radius:4px;font-weight:700;font-size:13px;">'
            f'{signal}</span> '
            f'<span style="color:#AAA;font-size:12px;">{confidence:.1f}%</span>')


def confidence_color(confidence: float) -> str:
    """Return hex color for a confidence percentage."""
    if confidence >= 85:
        return COLOR_GREEN
    elif confidence >= 75:
        return "#FFA500"
    else:
        return COLOR_RED
            
