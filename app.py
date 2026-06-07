"""
app.py — Deriv AI Auto Trader — Main Streamlit Dashboard
A production-ready multi-pair AI trading bot with ICT/SMC strategy.
"""

import os
import sys
import time
import threading
from datetime import datetime

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# ── Bootstrap paths ───────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

import database as db
from config import (ALL_PAIRS, FOREX_PAIRS, CRYPTO_PAIRS, PAIR_DISPLAY,
                    TIMEFRAMES, DEFAULT_RISK_PCT, DEFAULT_DAILY_LOSS,
                    DEFAULT_MAX_TRADES, MIN_CONFIDENCE)
from risk_manager import RiskManager
from trade_executor import TradeExecutor
from utils import display_name, colour_pnl
import logger

# ── Streamlit page config ─────────────────────────────────
st.set_page_config(
    page_title  = "Deriv AI Auto Trader",
    page_icon   = "⚡",
    layout      = "wide",
    initial_sidebar_state = "expanded",
)

# ── Custom CSS ────────────────────────────────────────────
st.markdown("""
<style>
/* ─── Google Fonts ─────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Syne', sans-serif;
    background-color: #080c14;
    color: #e2e8f0;
}

/* ─── Sidebar ──────────────────────────── */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0d1117 0%, #0a0f1a 100%);
    border-right: 1px solid #1e2d40;
}
section[data-testid="stSidebar"] * { font-family: 'Space Mono', monospace !important; }

/* ─── Metric cards ─────────────────────── */
[data-testid="stMetric"] {
    background: linear-gradient(135deg, #0d1829 0%, #0a1220 100%);
    border: 1px solid #1a2d42;
    border-radius: 12px;
    padding: 1rem 1.2rem;
    box-shadow: 0 4px 24px rgba(0,200,255,0.04);
    transition: border-color 0.3s;
}
[data-testid="stMetric"]:hover { border-color: #00c8ff44; }
[data-testid="stMetricLabel"]  { color: #64a0bf !important; font-size: 0.72rem !important; text-transform: uppercase; letter-spacing: 0.1em; }
[data-testid="stMetricValue"]  { color: #e2f4ff !important; font-family: 'Space Mono', monospace !important; }
[data-testid="stMetricDelta"]  { font-family: 'Space Mono', monospace !important; }

/* ─── Buttons ──────────────────────────── */
.stButton > button {
    font-family: 'Space Mono', monospace;
    font-weight: 700;
    letter-spacing: 0.06em;
    border-radius: 8px;
    transition: all 0.2s;
}
.stButton > button:hover { transform: translateY(-1px); }

/* ─── Headings ─────────────────────────── */
h1, h2, h3 { font-family: 'Syne', sans-serif; font-weight: 800; }

/* ─── Signal cards ─────────────────────── */
.signal-buy  { background:#0a1f12; border:1px solid #00c87744; border-radius:10px; padding:1rem; }
.signal-sell { background:#1f0a0a; border:1px solid #ff444444; border-radius:10px; padding:1rem; }
.signal-none { background:#0d1117; border:1px solid #2d3748;   border-radius:10px; padding:1rem; }

/* ─── Log entry ────────────────────────── */
.log-entry { font-family:'Space Mono',monospace; font-size:0.78rem; padding:0.25rem 0; border-bottom:1px solid #1a2332; }
.log-TRADE  { color:#00e5a0; }
.log-SIGNAL { color:#00bfff; }
.log-WARN   { color:#ffbd2e; }
.log-ERROR  { color:#ff5555; }
.log-INFO   { color:#a0b8cc; }

/* ─── Table tweaks ─────────────────────── */
[data-testid="stDataFrame"] { border: 1px solid #1e2d40 !important; border-radius: 10px; }

/* ─── Ticker header ────────────────────── */
.ticker-header {
    background: linear-gradient(90deg, #020b18 0%, #041224 50%, #020b18 100%);
    border: 1px solid #0a2040;
    border-radius: 14px;
    padding: 1.2rem 1.8rem;
    margin-bottom: 1.2rem;
    display: flex;
    align-items: center;
    gap: 1rem;
}

/* ─── Tab bar ──────────────────────────── */
button[data-baseweb="tab"] {
    font-family: 'Space Mono', monospace !important;
    font-size: 0.8rem !important;
    letter-spacing: 0.05em;
}

/* ─── Score bar ────────────────────────── */
.score-bar-wrap { margin: 0.15rem 0; }
.score-bar-bg   { background:#0d1829; border-radius:4px; height:8px; }
.score-bar-fill-buy  { background: linear-gradient(90deg,#00c877,#00bfff); height:8px; border-radius:4px; }
.score-bar-fill-sell { background: linear-gradient(90deg,#ff5555,#ff9944); height:8px; border-radius:4px; }
</style>
""", unsafe_allow_html=True)

# ── State init ────────────────────────────────────────────
def _init_state():
    defaults = {
        "executor":     None,
        "bot_running":  False,
        "demo_mode":    True,
        "api_token":    "",
        "last_refresh": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()
db.init_db()

# ── Sidebar ────────────────────────────────────────────────

with st.sidebar:
    st.markdown("""
    <div style='text-align:center;padding:1rem 0 0.5rem'>
      <span style='font-size:2.2rem'>⚡</span><br>
      <span style='font-family:Syne,sans-serif;font-size:1.3rem;font-weight:800;
                   background:linear-gradient(90deg,#00c8ff,#00ff99);
                   -webkit-background-clip:text;-webkit-text-fill-color:transparent'>
        Deriv AI Trader
      </span><br>
      <span style='font-size:0.65rem;color:#4a7090;letter-spacing:0.15em'>
        ICT · SMC · PRICE ACTION
      </span>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    # ── Connection
    st.markdown("**🔑 API CONNECTION**")
    demo_mode = st.toggle("Demo Mode (no real funds)", value=True)
    st.session_state["demo_mode"] = demo_mode

    api_token = ""
    if not demo_mode:
        api_token = st.text_input("Deriv API Token", type="password",
                                  placeholder="Enter your token...")
    st.session_state["api_token"] = api_token

    account_type = st.selectbox("Account Type", ["Real", "Demo"])
    st.divider()

    # ── Risk
    st.markdown("**⚖️ RISK MANAGEMENT**")
    risk_pct     = st.slider("Risk Per Trade (%)", 0.1, 5.0, DEFAULT_RISK_PCT, 0.1)
    daily_loss   = st.slider("Daily Loss Limit (%)", 1.0, 20.0, DEFAULT_DAILY_LOSS, 0.5)
    daily_profit = st.slider("Daily Profit Target (%)", 1.0, 30.0, 10.0, 0.5)
    max_trades   = st.number_input("Max Open Trades", 1, 20, DEFAULT_MAX_TRADES)
    lot_size     = st.number_input("Default Lot Size", 0.01, 10.0, 0.01, 0.01)
    st.divider()

    # ── Pairs
    st.markdown("**📊 MARKETS**")
    forex_sel  = st.multiselect("Forex Pairs",
        options=FOREX_PAIRS,
        default=["frxAUDUSD", "frxNZDUSD"],
        format_func=display_name)
    crypto_sel = st.multiselect("Crypto Pairs",
        options=CRYPTO_PAIRS,
        default=[],
        format_func=display_name)
    selected_pairs = forex_sel + crypto_sel

    exec_tf = st.selectbox("Execution Timeframe",
                            ["M5", "M15", "M30", "H1"], index=1)
    st.divider()

    # ── Bot Controls
    st.markdown("**🤖 BOT CONTROLS**")
    col_a, col_b = st.columns(2)

    with col_a:
        start_clicked = st.button("▶ START", use_container_width=True,
                                  type="primary",
                                  disabled=st.session_state["bot_running"])
    with col_b:
        stop_clicked  = st.button("⏹ STOP",  use_container_width=True,
                                  disabled=not st.session_state["bot_running"])

    close_all_btn = st.button("🚨 CLOSE ALL TRADES", use_container_width=True)
    reset_btn     = st.button("🔄 RESET SESSION",    use_container_width=True)

    # Bot status indicator
    status_colour = "#00e5a0" if st.session_state["bot_running"] else "#ff5555"
    status_label  = "RUNNING" if st.session_state["bot_running"] else "STOPPED"
    st.markdown(f"""
    <div style='text-align:center;margin-top:0.8rem;padding:0.5rem;
                border:1px solid {status_colour}44;border-radius:8px;
                background:{status_colour}11'>
      <span style='color:{status_colour};font-family:Space Mono,monospace;
                   font-size:0.75rem;font-weight:700;letter-spacing:0.15em'>
        ● {status_label}
      </span>
    </div>
    """, unsafe_allow_html=True)

    st.divider()
    st.markdown("<span style='font-size:0.65rem;color:#2a3d52'>"
                "v1.0 | For educational purposes only</span>",
                unsafe_allow_html=True)


# ── Button handlers ────────────────────────────────────────

def _build_executor() -> TradeExecutor:
    rm = RiskManager(
        balance         = 10_000.0,
        risk_pct        = risk_pct,
        daily_loss_pct  = daily_loss,
        daily_profit_pct= daily_profit,
        max_trades      = int(max_trades),
        lot_size        = lot_size,
    )
    return TradeExecutor(
        api_token       = st.session_state["api_token"],
        demo_mode       = st.session_state["demo_mode"],
        risk_manager    = rm,
        selected_pairs  = selected_pairs if selected_pairs else ["frxAUDUSD"],
        exec_timeframe  = exec_tf,
    )


if start_clicked:
    if not selected_pairs:
        st.sidebar.error("Select at least one pair")
    else:
        exc = _build_executor()
        ok  = exc.start()
        if ok:
            st.session_state["executor"]    = exc
            st.session_state["bot_running"] = True
            logger.info("Bot started from dashboard")
        else:
            st.sidebar.error("Connection failed — check token or enable Demo mode")

if stop_clicked:
    exc = st.session_state.get("executor")
    if exc:
        exc.stop()
    st.session_state["bot_running"] = False

if close_all_btn:
    exc = st.session_state.get("executor")
    if exc:
        n = exc.close_all()
        st.sidebar.success(f"Closed {n} trade(s)")
    else:
        st.sidebar.warning("Bot not running")

if reset_btn:
    exc = st.session_state.get("executor")
    if exc and exc.is_running:
        exc.rm.reset_session()
    logger.clear_logs()
    st.sidebar.success("Session reset")


# ── Helper: fetch live data ────────────────────────────────
def _get_executor() -> TradeExecutor | None:
    return st.session_state.get("executor")


def _account_balance() -> float:
    exc = _get_executor()
    if exc and exc.api:
        return exc.rm.balance
    return 0.0


def _daily_pnl() -> float:
    exc = _get_executor()
    if exc:
        return exc.rm.daily_pnl
    return 0.0


# ══════════════════════════════════════════════════════════
# MAIN DASHBOARD
# ══════════════════════════════════════════════════════════

st.markdown("""
<div class='ticker-header'>
  <span style='font-size:1.8rem'>⚡</span>
  <div>
    <div style='font-family:Syne,sans-serif;font-size:1.5rem;font-weight:800;
                background:linear-gradient(90deg,#00c8ff,#00ff99);
                -webkit-background-clip:text;-webkit-text-fill-color:transparent'>
      Deriv AI Auto Trader
    </div>
    <div style='font-size:0.7rem;color:#4a7090;letter-spacing:0.12em;font-family:Space Mono'>
      ICT · SMART MONEY CONCEPTS · MULTI-TIMEFRAME ANALYSIS
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Account KPIs ──────────────────────────────────────────
today   = db.get_today_summary()
balance = _account_balance()
dpnl    = _daily_pnl()
open_df = db.get_open_trades()
closed_df = db.get_closed_trades(limit=200)
win_rate  = today.get("win_rate", 0.0)

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("💰 Balance",      f"${balance:,.2f}",
          delta=f"{dpnl:+.2f}" if balance else None)
k2.metric("📈 Daily P&L",    f"${dpnl:+.2f}",
          delta_color="normal" if dpnl >= 0 else "inverse")
k3.metric("🏆 Win Rate",     f"{win_rate:.1f}%")
k4.metric("📂 Open Trades",  len(open_df))
k5.metric("✅ Closed Today", today.get("trades_total", 0))
k6.metric("🎯 Confidence Min", f"{MIN_CONFIDENCE}%")

st.divider()

# ── Tabs ──────────────────────────────────────────────────
tab_chart, tab_signals, tab_open, tab_closed, tab_logs, tab_db = st.tabs([
    "📊 Chart", "🔔 Signals", "📂 Open Trades",
    "📋 Closed Trades", "📝 Live Log", "🗄️ Database"
])


# ══════ TAB 1 — CHART ════════════════════════════════════
with tab_chart:
    exc = _get_executor()

    c1, c2 = st.columns([3, 1])
    with c1:
        chart_symbol = st.selectbox(
            "Symbol", options=selected_pairs if selected_pairs else ["frxAUDUSD"],
            format_func=display_name, key="chart_sym")
    with c2:
        chart_tf = st.selectbox("Timeframe",
                                ["M5","M15","M30","H1","H4"], index=1, key="chart_tf")

    # Fetch candles
    candles_df = pd.DataFrame()
    if exc and exc.api:
        with st.spinner("Loading chart data…"):
            candles_df = exc.api.get_candles(chart_symbol, chart_tf, count=150)

    if candles_df.empty:
        # Generate demo data for display
        from deriv_api import MockDerivAPI
        mock = MockDerivAPI()
        candles_df = mock.get_candles(chart_symbol, chart_tf, count=150)

    if not candles_df.empty:
        # Add indicators for chart
        from indicators import add_ema, add_rsi, add_atr
        candles_df = add_ema(add_ema(candles_df.copy(), 50), 200)
        candles_df = add_rsi(candles_df)

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.75, 0.25],
            vertical_spacing=0.02,
        )

        # Candlestick
        # Candlestick Chart
    fig.add_trace(
      go.Candlestick(
        x=candles_df["datetime"] if "datetime" in candles_df.columns else candles_df.index,
        open=candles_df["open"],
        high=candles_df["high"],
        low=candles_df["low"],
        close=candles_df["close"],
        name="Price",
        increasing=dict(
          line=dict(color="#00e5a0")
        ),
        decreasing=dict(
            line=dict(color="#ff5555")
        )
      ),
      row=1,
      col=1
    )
        # EMAs
    if "ema_50" in candles_df.columns:
      fig.add_trace(go.Scatter(
                x=candles_df.get("datetime", candles_df.index),
                y=candles_df["ema_50"], name="EMA 50",
                line=dict(color="#00bfff", width=1.5, dash="dot"),
            ), row=1, col=1)
        if "ema_200" in candles_df.columns:
            fig.add_trace(go.Scatter(
                x=candles_df.get("datetime", candles_df.index),
                y=candles_df["ema_200"], name="EMA 200",
                line=dict(color="#ff9944", width=1.5),
            ), row=1, col=1)

        # Overlay signal markers
        exc2 = _get_executor()
        if exc2:
            for sig in exc2.get_last_signals():
                if sig.get("symbol") == chart_symbol and sig.get("entry"):
                    col = "#00e5a0" if sig["signal"] == "BUY" else "#ff5555"
                    fig.add_hline(y=sig["entry"],    line_color=col,    line_dash="dash", row=1, col=1)
                    if sig.get("stop_loss"):
                        fig.add_hline(y=sig["stop_loss"], line_color="#ffbd2e", line_dash="dot",  row=1, col=1)
                    if sig.get("tp1"):
                        fig.add_hline(y=sig["tp1"],       line_color="#00e5a0", line_dash="longdash", row=1, col=1)

        # RSI
        if "rsi" in candles_df.columns:
            fig.add_trace(go.Scatter(
                x=candles_df.get("datetime", candles_df.index),
                y=candles_df["rsi"], name="RSI",
                line=dict(color="#a78bfa", width=1.5),
            ), row=2, col=1)
            fig.add_hline(y=70, line_color="#ff5555", line_dash="dot", row=2, col=1)
            fig.add_hline(y=30, line_color="#00e5a0", line_dash="dot", row=2, col=1)

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#080c14",
            plot_bgcolor="#080c14",
            xaxis_rangeslider_visible=False,
            height=520,
            margin=dict(l=0, r=0, t=30, b=0),
            legend=dict(font=dict(family="Space Mono", size=10)),
            font=dict(family="Syne"),
        )
        fig.update_xaxes(gridcolor="#0d1829", showgrid=True)
        fig.update_yaxes(gridcolor="#0d1829", showgrid=True)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Start the bot or select a pair to display chart data.")


# ══════ TAB 2 — SIGNALS ══════════════════════════════════
with tab_signals:
    exc = _get_executor()
    signals = exc.get_last_signals() if exc else []
    sig_db  = db.get_signals(limit=50)

    if signals:
        st.markdown(f"#### Live Signals — {datetime.utcnow().strftime('%H:%M:%S')} UTC")
        for sig in signals:
            direction = sig.get("signal", "NO TRADE")
            conf      = sig.get("confidence", 0)
            sym       = display_name(sig.get("symbol", ""))

            css_class = ("signal-buy"  if direction == "BUY" else
                         "signal-sell" if direction == "SELL" else "signal-none")

            dir_emoji = "🟢 BUY" if direction == "BUY" else ("🔴 SELL" if direction == "SELL" else "⚪ NO TRADE")

            with st.expander(f"{sym}  |  {dir_emoji}  |  Confidence: {conf:.0f}%", expanded=(direction != "NO TRADE")):
                st.markdown(f"<div class='{css_class}'>", unsafe_allow_html=True)

                if direction != "NO TRADE":
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Entry",       f"{sig.get('entry',0):.5f}")
                    col2.metric("Stop Loss",   f"{sig.get('stop_loss',0):.5f}")
                    col3.metric("R:R",         f"1:{sig.get('rr_ratio',0):.1f}")

                    c1, c2, c3 = st.columns(3)
                    c1.metric("TP1", f"{sig.get('tp1',0):.5f}")
                    c2.metric("TP2", f"{sig.get('tp2',0):.5f}")
                    c3.metric("TP3", f"{sig.get('tp3',0):.5f}")

                    # Score breakdown
                    scores = sig.get("scores", {})
                    if scores:
                        st.markdown("**AI Score Breakdown**")
                        labels = {
                            "market_structure": "Market Structure",
                            "smc_confirmation": "SMC Confirmation",
                            "rsi_confirmation": "RSI",
                            "ema_trend":        "EMA Trend",
                            "liquidity_sweep":  "Liquidity Sweep",
                            "price_action":     "Price Action",
                        }
                        from config import WEIGHTS
                        for key, label in labels.items():
                            raw   = scores.get(key, 0)
                            wt    = WEIGHTS.get(key, 0)
                            pct   = int(raw * 100)
                            bar_w = int(raw * 200)
                            fill_cls = "score-bar-fill-buy" if direction == "BUY" else "score-bar-fill-sell"
                            st.markdown(
                                f"<div class='score-bar-wrap'>"
                                f"<span style='font-size:0.72rem;color:#64a0bf;font-family:Space Mono'>"
                                f"{label} ({int(wt*100)}%)</span> — {pct}%<br>"
                                f"<div class='score-bar-bg'>"
                                f"<div class='{fill_cls}' style='width:{bar_w}px'></div>"
                                f"</div></div>",
                                unsafe_allow_html=True
                            )

                    st.markdown(f"**HTF Trend:** {sig.get('htf_trend','?')}")
                    if sig.get("htf_warning"):
                        st.warning(sig["htf_warning"])

                    # Support / Resistance
                    sup = sig.get("support", [])
                    res = sig.get("resistance", [])
                    if sup or res:
                        sc, rc = st.columns(2)
                        sc.markdown("**Support**\n" + "\n".join(f"• {v:.5f}" for v in sup))
                        rc.markdown("**Resistance**\n" + "\n".join(f"• {v:.5f}" for v in res))

                st.markdown(f"**Trend:** {sig.get('trend','?')}")
                st.markdown(f"**Reason:** {sig.get('trade_reason','')}")
                st.markdown(f"**Risk Warning:** _{sig.get('risk_warning','')}_")
                st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("No live signals yet — start the bot to begin scanning.")

    if not sig_db.empty:
        st.markdown("---")
        st.markdown("#### Signal History (database)")
        display_cols = ["timestamp","symbol","timeframe","signal","confidence",
                        "entry","stop_loss","tp1","rr_ratio","trend"]
        display_cols = [c for c in display_cols if c in sig_db.columns]
        st.dataframe(sig_db[display_cols].head(30), use_container_width=True)


# ══════ TAB 3 — OPEN TRADES ══════════════════════════════
with tab_open:
    open_db = db.get_open_trades()
    if open_db.empty:
        st.info("No open trades.")
    else:
        st.markdown(f"**{len(open_db)} open position(s)**")
        show_cols = ["id","timestamp","symbol","direction","entry_price",
                     "stop_loss","tp1","lot_size","confidence"]
        show_cols = [c for c in show_cols if c in open_db.columns]
        st.dataframe(open_db[show_cols], use_container_width=True, height=300)

        # Expose individual close buttons
        exc = _get_executor()
        if exc:
            st.markdown("**Manual Close**")
            for _, row in open_db.iterrows():
                cid = row.get("contract_id","")
                sym = display_name(row.get("symbol",""))
                if st.button(f"Close {sym} #{row['id']}", key=f"close_{row['id']}"):
                    result = exc.api.sell_contract(cid)
                    if result:
                        st.success(f"Closed {sym}")
                    else:
                        st.error("Close failed")


# ══════ TAB 4 — CLOSED TRADES ════════════════════════════
with tab_closed:
    closed_db = db.get_closed_trades(200)
    if closed_db.empty:
        st.info("No closed trades yet.")
    else:
        # Stats bar
        total_trades = len(closed_db)
        wins  = (closed_db["pnl"] > 0).sum() if "pnl" in closed_db.columns else 0
        gross = closed_db["pnl"].sum()        if "pnl" in closed_db.columns else 0

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Total Trades", total_trades)
        mc2.metric("Wins",         wins)
        mc3.metric("Losses",       total_trades - wins)
        mc4.metric("Gross P&L",    f"${gross:+.2f}")

        # P&L equity curve
        if "pnl" in closed_db.columns and not closed_db["pnl"].isna().all():
            pnl_series = closed_db["pnl"].iloc[::-1].cumsum().reset_index(drop=True)
            fig2 = go.Figure(go.Scatter(
                y=pnl_series,
                mode="lines+markers",
                line=dict(color="#00c8ff", width=2),
                fill="tozeroy",
                fillcolor="#00c8ff11",
                name="Cumulative P&L",
            ))
            fig2.update_layout(
                template="plotly_dark", paper_bgcolor="#080c14",
                plot_bgcolor="#080c14", height=200,
                margin=dict(l=0,r=0,t=20,b=0),
                yaxis_title="P&L ($)",
            )
            st.plotly_chart(fig2, use_container_width=True)

        show = ["id","timestamp","symbol","direction","entry_price","exit_price",
                "pnl","exit_reason","confidence"]
        show = [c for c in show if c in closed_db.columns]
        st.dataframe(closed_db[show], use_container_width=True, height=350)


# ══════ TAB 5 — LIVE LOG ═════════════════════════════════
with tab_logs:
    logs = logger.get_logs()
    if not logs:
        st.info("No log entries yet.")
    else:
        log_html = ""
        for entry in logs[:200]:
            lvl = entry.get("level","INFO")
            log_html += (f"<div class='log-entry log-{lvl}'>"
                         f"<span style='color:#2a6080'>[{entry['time']}]</span> "
                         f"<strong>{lvl}</strong> — {entry['message']}</div>")

        st.markdown(
            f"<div style='height:480px;overflow-y:auto;background:#040810;"
            f"border:1px solid #0d1829;border-radius:10px;padding:0.8rem'>"
            f"{log_html}</div>",
            unsafe_allow_html=True
        )

    if st.button("🗑 Clear Log"):
        logger.clear_logs()
        st.rerun()


# ══════ TAB 6 — DATABASE ═════════════════════════════════
with tab_db:
    st.markdown("#### Raw Database View")

    db_table = st.selectbox("Table", ["signals","trades","account_history","daily_summary"])
    import sqlite3
    from config import DB_PATH

    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        try:
            raw = pd.read_sql_query(f"SELECT * FROM {db_table} ORDER BY id DESC LIMIT 100", conn)
            st.dataframe(raw, use_container_width=True)
        except Exception as e:
            st.error(f"Query error: {e}")
        finally:
            conn.close()
    else:
        st.info("Database not yet created — start the bot first.")

    # Download buttons
    if not closed_df.empty:
        csv = closed_df.to_csv(index=False)
        st.download_button("⬇ Download Trades CSV", csv,
                           "trades.csv", "text/csv")


# ── Auto-refresh ──────────────────────────────────────────
if st.session_state["bot_running"]:
    time.sleep(0.5)
    st.rerun()
