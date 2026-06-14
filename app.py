"""
app.py - Deriv AI Automated Trader — Main Streamlit Dashboard
Run: streamlit run app.py
"""

import asyncio
import threading
import time
import os
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime

# ── Project imports ────────────────────────────────────────────────────────
from config import (
    ALL_PAIRS, PAIR_DISPLAY, TIMEFRAMES, EXECUTION_TIMEFRAMES,
    COLOR_GREEN, COLOR_RED, COLOR_BLACK, COLOR_CARD, COLOR_WHITE,
    COLOR_MUTED, COLOR_BORDER, DERIV_API_TOKEN,
    DEFAULT_RISK_PCT, DEFAULT_LOT_SIZE, DEFAULT_DAILY_LOSS, DEFAULT_MAX_TRADES
)
from database import (
    init_db, get_signals, get_open_trades, get_closed_trades,
    get_today_summary, get_account_history
)
from logger import get_logs, clear_logs
from risk_manager import RiskManager, RiskSettings
from deriv_api import DerivAPI
from trade_executor import TradeExecutor
from utils import build_candle_chart, format_currency, color_pnl, signal_badge_html

# ── Page Config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AUTOMATED BOT TRADER",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS ─────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&family=JetBrains+Mono&display=swap');

  html, body, [class*="css"] {{
    font-family: 'Inter', sans-serif;
    background-color: {COLOR_BLACK};
    color: {COLOR_WHITE};
  }}

  /* Remove default streamlit padding */
  .block-container {{ padding: 1rem 1.5rem 2rem 1.5rem !important; }}
  header {{ background: transparent !important; }}
  .stDeployButton {{ display: none; }}

  /* ── Metric Cards ───────────────────────────── */
  .metric-card {{
    background: {COLOR_CARD};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 14px 16px;
    text-align: center;
    min-height: 80px;
  }}
  .metric-label {{
    font-size: 11px;
    color: {COLOR_MUTED};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 4px;
  }}
  .metric-value {{
    font-size: 22px;
    font-weight: 700;
    line-height: 1.2;
  }}
  .metric-sub {{
    font-size: 11px;
    color: {COLOR_MUTED};
    margin-top: 2px;
  }}

  /* ── Signal Cards ───────────────────────────── */
  .signal-card {{
    background: {COLOR_CARD};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 8px;
  }}
  .signal-buy  {{ border-left: 4px solid {COLOR_GREEN}; }}
  .signal-sell {{ border-left: 4px solid {COLOR_RED}; }}
  .signal-none {{ border-left: 4px solid #555; }}

  /* ── Log feed ───────────────────────────────── */
  .log-container {{
    background: #080808;
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 10px;
    max-height: 300px;
    overflow-y: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
  }}
  .log-info    {{ color: {COLOR_WHITE}; }}
  .log-warning {{ color: #FFA500; }}
  .log-error   {{ color: {COLOR_RED}; }}

  /* ── Tables ─────────────────────────────────── */
  .stDataFrame {{ background: {COLOR_CARD}; border-radius: 8px; }}
  thead tr th  {{ background: #1A1A1A !important; color: {COLOR_MUTED} !important;
                  font-size: 11px !important; text-transform: uppercase; }}

  /* ── Buttons ────────────────────────────────── */
  .stButton > button {{
    border-radius: 6px !important;
    font-weight: 700 !important;
    letter-spacing: 0.04em !important;
    transition: all 0.2s;
  }}
  .stButton > button:hover {{ opacity: 0.85; }}

  /* ── Sidebar ────────────────────────────────── */
  section[data-testid="stSidebar"] {{
    background: #0D0D0D;
    border-right: 1px solid {COLOR_BORDER};
  }}
  .sidebar-title {{
    font-size: 11px; color: {COLOR_MUTED}; text-transform: uppercase;
    letter-spacing: 0.1em; margin: 12px 0 6px 0;
    border-bottom: 1px solid {COLOR_BORDER}; padding-bottom: 4px;
  }}

  /* ── Status badge ───────────────────────────── */
  .status-running {{ color: {COLOR_GREEN}; font-weight: 700; }}
  .status-stopped {{ color: {COLOR_RED};   font-weight: 700; }}
</style>
""", unsafe_allow_html=True)


# ── Session State Init ─────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "bot_running":   False,
        "executor":      None,
        "api":           None,
        "risk_mgr":      None,
        "loop_thread":   None,
        "event_loop":    None,
        "api_token":     DERIV_API_TOKEN,
        "account_type":  "Demo",
        "selected_pairs": list(PAIR_DISPLAY.keys())[:3],
        "exec_tf":       "M15",
        "lot_size":      DEFAULT_LOT_SIZE,
        "risk_pct":      DEFAULT_RISK_PCT,
        "daily_loss":    DEFAULT_DAILY_LOSS,
        "max_trades":    DEFAULT_MAX_TRADES,
        "stake_amount":  10.0,
        "chart_symbol":  list(PAIR_DISPLAY.keys())[0],
        "balance":       0.0,
        "equity":        0.0,
        "session_start": datetime.utcnow().strftime("%H:%M UTC"),
        "demo_mode":     True,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()
init_db()


# ── Async helpers ──────────────────────────────────────────────────────────

def _run_async(coro):
    """Run an async coroutine from a sync context."""
    loop = st.session_state.get("event_loop")
    if loop and loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=30)
    return asyncio.run(coro)


def _start_background_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


# ── Bot Controls ───────────────────────────────────────────────────────────

def start_bot():
    """Connect to Deriv API and launch the trading executor."""
    token = st.session_state.api_token
    if not token:
        st.error("⚠️ Enter your Deriv API token in the sidebar before starting.")
        return

    # Create dedicated event loop in background thread
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=_start_background_loop,
                               args=(loop,), daemon=True)
    thread.start()
    st.session_state.event_loop  = loop
    st.session_state.loop_thread = thread

    api = DerivAPI(token=token)
    connected = asyncio.run_coroutine_threadsafe(api.connect(), loop).result(timeout=20)

    if not connected:
        st.error("❌ Could not connect to Deriv. Check your token.")
        loop.call_soon_threadsafe(loop.stop)
        return

    # Fetch initial balance
    bal_info = asyncio.run_coroutine_threadsafe(api.get_balance(), loop).result(timeout=10)
    balance  = float(bal_info.get("balance", 0))
    st.session_state.balance = balance
    st.session_state.equity  = balance

    # Risk manager
    settings = RiskSettings(
        risk_pct=st.session_state.risk_pct,
        daily_loss_pct=st.session_state.daily_loss,
        max_open=st.session_state.max_trades,
        lot_size=st.session_state.lot_size,
    )
    risk_mgr = RiskManager(settings)
    risk_mgr.init_session(balance)

    # Executor
    executor = TradeExecutor(
        api=api,
        risk_mgr=risk_mgr,
        selected_pairs=st.session_state.selected_pairs,
        exec_timeframe=st.session_state.exec_tf,
        stake_amount=st.session_state.stake_amount,
    )
    asyncio.run_coroutine_threadsafe(
        _executor_task(executor), loop
    )

    st.session_state.api       = api
    st.session_state.risk_mgr  = risk_mgr
    st.session_state.executor  = executor
    st.session_state.bot_running = True


async def _executor_task(executor: TradeExecutor):
    """Async wrapper so the executor loop runs inside the background event loop."""
    await executor.run()


def stop_bot():
    executor: TradeExecutor = st.session_state.executor
    if executor:
        executor.stop()
    loop = st.session_state.event_loop
    if loop:
        asyncio.run_coroutine_threadsafe(
            st.session_state.api.disconnect(), loop
        )
    st.session_state.bot_running = False


def close_all_trades():
    executor: TradeExecutor = st.session_state.executor
    loop = st.session_state.event_loop
    if executor and loop:
        asyncio.run_coroutine_threadsafe(executor.close_all_trades(), loop)


def reset_session():
    stop_bot()
    clear_logs()
    st.session_state.balance    = 0.0
    st.session_state.equity     = 0.0
    st.session_state.bot_running = False
    st.session_state.executor   = None
    st.session_state.api        = None
    st.session_state.risk_mgr   = None


# ── Helper renders ─────────────────────────────────────────────────────────

def metric_card(label: str, value: str, sub: str = "", color: str = COLOR_WHITE) -> str:
    return f"""
    <div class="metric-card">
      <div class="metric-label">{label}</div>
      <div class="metric-value" style="color:{color};">{value}</div>
      {f'<div class="metric-sub">{sub}</div>' if sub else ''}
    </div>"""


def render_signal_card(sig) -> str:
    cls = {"BUY": "signal-buy", "SELL": "signal-sell"}.get(sig.signal, "signal-none")
    color = COLOR_GREEN if sig.signal == "BUY" else (COLOR_RED if sig.signal == "SELL" else COLOR_MUTED)
    conf_color = COLOR_GREEN if sig.confidence >= 85 else ("#FFA500" if sig.confidence >= 75 else COLOR_MUTED)
    return f"""
    <div class="signal-card {cls}">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <span style="font-weight:700;font-size:14px;">{PAIR_DISPLAY.get(sig.symbol, sig.symbol)}</span>
        <span style="background:{color};color:#000;padding:2px 10px;border-radius:4px;
                     font-weight:700;font-size:12px;">{sig.signal}</span>
      </div>
      <div style="display:flex;gap:16px;font-size:12px;color:{COLOR_MUTED};">
        <span>TF: <b style="color:{COLOR_WHITE};">{sig.timeframe}</b></span>
        <span>Conf: <b style="color:{conf_color};">{sig.confidence:.1f}%</b></span>
        <span>RR: <b style="color:{COLOR_WHITE};">1:{sig.rr}</b></span>
        <span>Trend: <b style="color:{COLOR_WHITE};">{sig.trend.title()}</b></span>
      </div>
      {f'<div style="font-size:11px;color:{COLOR_MUTED};margin-top:6px;">Entry: {sig.entry:.5f} | SL: {sig.sl:.5f} | TP1: {sig.tp1:.5f}</div>' if sig.entry else ''}
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style='text-align:center;padding:12px 0 4px 0;'>
      <div style='font-size:22px;font-weight:800;letter-spacing:0.02em;'>
        <span style='color:#00FF88;'>DERIV</span>
        <span style='color:#fff;'> AI</span>
      </div>
      <div style='font-size:11px;color:#888;letter-spacing:0.15em;margin-top:2px;'>
        AUTOMATED TRADER
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # ── API Config ─────────────────────────────────────────────────────────
    st.markdown('<div class="sidebar-title">🔑 API Configuration</div>', unsafe_allow_html=True)
    st.session_state.api_token = st.text_input(
        "Deriv API Token", value=st.session_state.api_token,
        type="password", help="Get from app.deriv.com → API Token"
    )
    st.session_state.account_type = st.selectbox(
        "Account Type", ["Demo", "Real"],
        index=0 if st.session_state.account_type == "Demo" else 1
    )

    # ── Trade Settings ─────────────────────────────────────────────────────
    st.markdown('<div class="sidebar-title">⚙️ Trade Settings</div>', unsafe_allow_html=True)

    st.session_state.stake_amount = st.number_input(
        "Stake Per Trade ($)", min_value=1.0, max_value=10000.0,
        value=st.session_state.stake_amount, step=1.0
    )
    st.session_state.lot_size = st.number_input(
        "Lot Size (0 = auto)", min_value=0.0, max_value=100.0,
        value=st.session_state.lot_size, step=0.01
    )
    st.session_state.risk_pct = st.slider(
        "Risk Per Trade (%)", min_value=0.1, max_value=10.0,
        value=st.session_state.risk_pct, step=0.1
    )
    st.session_state.daily_loss = st.slider(
        "Daily Loss Limit (%)", min_value=1.0, max_value=20.0,
        value=st.session_state.daily_loss, step=0.5
    )
    st.session_state.max_trades = st.number_input(
        "Max Open Trades", min_value=1, max_value=20,
        value=st.session_state.max_trades, step=1
    )

    # ── Market Selection ───────────────────────────────────────────────────
    st.markdown('<div class="sidebar-title">📊 Market Selection</div>', unsafe_allow_html=True)
    pair_options = list(PAIR_DISPLAY.keys())
    pair_labels  = [PAIR_DISPLAY[p] for p in pair_options]
    selected_labels = st.multiselect(
        "Select Pairs",
        options=pair_labels,
        default=[PAIR_DISPLAY[p] for p in st.session_state.selected_pairs]
    )
    st.session_state.selected_pairs = [
        k for k, v in PAIR_DISPLAY.items() if v in selected_labels
    ]

    # ── Timeframe ──────────────────────────────────────────────────────────
    st.session_state.exec_tf = st.selectbox(
        "Execution Timeframe", EXECUTION_TIMEFRAMES,
        index=EXECUTION_TIMEFRAMES.index(st.session_state.exec_tf)
    )

    st.divider()

    # ── Control Buttons ────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        if not st.session_state.bot_running:
            if st.button("▶ START", use_container_width=True, type="primary"):
                start_bot()
                st.rerun()
        else:
            if st.button("⏹ STOP", use_container_width=True):
                stop_bot()
                st.rerun()
    with col2:
        if st.button("✖ CLOSE ALL", use_container_width=True):
            close_all_trades()

    if st.button("↺ RESET SESSION", use_container_width=True):
        reset_session()
        st.rerun()

    # ── Status ─────────────────────────────────────────────────────────────
    st.divider()
    status_cls = "status-running" if st.session_state.bot_running else "status-stopped"
    status_txt = "● RUNNING" if st.session_state.bot_running else "● STOPPED"
    st.markdown(f'<div style="text-align:center;" class="{status_cls}">{status_txt}</div>',
                unsafe_allow_html=True)
    st.caption(f"Session started: {st.session_state.session_start}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

# ── Header ─────────────────────────────────────────────────────────────────
st.markdown("""
<div style='display:flex;align-items:center;gap:12px;margin-bottom:4px;'>
  <div style='font-size:26px;font-weight:800;'>
    <span style='color:#00FF88;'>DERIV</span>
    <span style='color:#FFF;'> AI AUTOMATED TRADER</span>
  </div>
</div>
""", unsafe_allow_html=True)

# Fetch live data for display
today   = get_today_summary()
risk_mgr: RiskManager = st.session_state.risk_mgr
balance  = risk_mgr.session.current_balance if risk_mgr else st.session_state.balance
equity   = balance
daily_pnl = risk_mgr.session.daily_pnl if risk_mgr else 0.0
win_rate  = risk_mgr.win_rate if risk_mgr else 0.0

open_df   = get_open_trades()
closed_df = get_closed_trades(50)

# ── KPI Row ────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5, k6, k7 = st.columns(7)

metrics = [
    (k1, "Balance",      f"${balance:,.2f}", "",                      COLOR_WHITE),
    (k2, "Equity",       f"${equity:,.2f}",  "",                      COLOR_WHITE),
    (k3, "Daily P&L",    format_currency(daily_pnl),
                          f"{risk_mgr.daily_pnl_pct if risk_mgr else 0:+.2f}%",
                          color_pnl(daily_pnl)),
    (k4, "Win Rate",     f"{win_rate:.1f}%", f"{today.get('wins',0)}W / {today.get('losses',0)}L",
                          COLOR_GREEN if win_rate >= 60 else COLOR_RED),
    (k5, "Open Trades",  str(len(open_df)), f"Max {st.session_state.max_trades}", COLOR_WHITE),
    (k6, "Closed Today", str(today.get('trades', 0)), "",              COLOR_WHITE),
    (k7, "Status",
          "RUNNING" if st.session_state.bot_running else "STOPPED",
          "",
          COLOR_GREEN if st.session_state.bot_running else COLOR_RED),
]

for col, label, value, sub, color in metrics:
    col.markdown(metric_card(label, value, sub, color), unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Main Layout: Chart (left) + Signals (right) ────────────────────────────
chart_col, sig_col = st.columns([2, 1])

with chart_col:
    st.markdown(f'<div style="font-size:13px;font-weight:600;color:{COLOR_MUTED};'
                f'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;">'
                f'📈 Live Chart</div>', unsafe_allow_html=True)

    # Chart symbol selector
    chart_pairs   = [PAIR_DISPLAY[p] for p in st.session_state.selected_pairs] or list(PAIR_DISPLAY.values())[:1]
    chart_display = st.selectbox("Symbol", chart_pairs, label_visibility="collapsed")
    chart_symbol  = next((k for k, v in PAIR_DISPLAY.items() if v == chart_display),
                          st.session_state.selected_pairs[0])

    # Try to render a live chart; fallback to placeholder
    chart_placeholder = st.empty()

    executor: TradeExecutor = st.session_state.executor
    sig_for_chart = None

    if executor:
        sig_for_chart = executor.last_signals.get(chart_symbol)
        loop = st.session_state.event_loop
        if loop:
            try:
                gran = DerivAPI.granularity(st.session_state.exec_tf)
                df_chart = asyncio.run_coroutine_threadsafe(
                    st.session_state.api.get_candles(chart_symbol, gran, 100), loop
                ).result(timeout=15)
                if df_chart is not None:
                    from indicators import add_emas, add_rsi
                    df_chart = add_emas(add_rsi(df_chart))
                    signals_dict = None
                    if sig_for_chart and sig_for_chart.signal != "NO TRADE":
                        signals_dict = {
                            "entry": sig_for_chart.entry,
                            "sl":    sig_for_chart.sl,
                            "tp1":   sig_for_chart.tp1,
                        }
                    fig = build_candle_chart(df_chart, chart_display, signals_dict)
                    chart_placeholder.plotly_chart(fig, use_container_width=True,
                                                    config={"displayModeBar": False})
                else:
                    chart_placeholder.info("Waiting for candle data…")
            except Exception as e:
                chart_placeholder.warning(f"Chart unavailable: {e}")
        else:
            chart_placeholder.info("Start the bot to load live charts.")
    else:
        # Static placeholder chart
        fig_empty = go.Figure()
        fig_empty.update_layout(
            template="plotly_dark", paper_bgcolor=COLOR_CARD, plot_bgcolor=COLOR_BLACK,
            height=480, annotations=[dict(text="Start the bot to load charts",
                                          xref="paper", yref="paper", x=0.5, y=0.5,
                                          showarrow=False, font=dict(color=COLOR_MUTED, size=16))]
        )
        chart_placeholder.plotly_chart(fig_empty, use_container_width=True,
                                         config={"displayModeBar": False})

with sig_col:
    st.markdown(f'<div style="font-size:13px;font-weight:600;color:{COLOR_MUTED};'
                f'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;">'
                f'🎯 Current Signals</div>', unsafe_allow_html=True)

    sig_area = st.container()
    with sig_area:
        if executor:
            sigs = executor.last_signals
            if sigs:
                for sym, sig in sigs.items():
                    if sig.signal != "NO TRADE":
                        st.markdown(render_signal_card(sig), unsafe_allow_html=True)
                if not any(s.signal != "NO TRADE" for s in sigs.values()):
                    st.markdown(f'<div style="color:{COLOR_MUTED};padding:20px;text-align:center;">'
                                f'No active signals.<br>Bot is scanning markets…</div>',
                                unsafe_allow_html=True)
            else:
                st.markdown(f'<div style="color:{COLOR_MUTED};padding:20px;text-align:center;">'
                            f'Awaiting first scan…</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div style="color:{COLOR_MUTED};padding:20px;text-align:center;">'
                        f'Start the bot to see signals.</div>', unsafe_allow_html=True)

st.divider()

# ── Tables: Open & Closed Positions ────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(
    ["📂 Open Positions", "✅ Closed Trades", "📊 Signal History", "📜 Trade Log"]
)

with tab1:
    if open_df.empty:
        st.markdown(f'<div style="color:{COLOR_MUTED};padding:16px;text-align:center;">'
                    f'No open positions.</div>', unsafe_allow_html=True)
    else:
        display_cols = ["id","symbol","direction","entry","sl","tp1","tp2","lot_size",
                        "confidence","ts_open"]
        show = open_df[[c for c in display_cols if c in open_df.columns]].copy()
        show["symbol"] = show["symbol"].map(lambda s: PAIR_DISPLAY.get(s, s))
        st.dataframe(show, use_container_width=True, hide_index=True)

with tab2:
    if closed_df.empty:
        st.markdown(f'<div style="color:{COLOR_MUTED};padding:16px;text-align:center;">'
                    f'No closed trades yet.</div>', unsafe_allow_html=True)
    else:
        show_c = closed_df.copy()
        show_c["symbol"] = show_c["symbol"].map(lambda s: PAIR_DISPLAY.get(s, s))
        show_c = show_c[["id","symbol","direction","entry","exit_price","profit",
                           "confidence","ts_open","ts_close"]]
        # Colour profit column
        def style_profit(val):
            try:
                return f"color: {COLOR_GREEN}" if float(val) >= 0 else f"color: {COLOR_RED}"
            except Exception:
                return ""
        styled = show_c.style.applymap(style_profit, subset=["profit"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

with tab3:
    sig_df = get_signals(100)
    if sig_df.empty:
        st.markdown(f'<div style="color:{COLOR_MUTED};padding:16px;text-align:center;">'
                    f'No signals generated yet.</div>', unsafe_allow_html=True)
    else:
        sig_df["symbol"] = sig_df["symbol"].map(lambda s: PAIR_DISPLAY.get(s, s))
        cols = ["ts","symbol","timeframe","signal","entry","sl","tp1","confidence","trend"]
        st.dataframe(sig_df[[c for c in cols if c in sig_df.columns]],
                     use_container_width=True, hide_index=True)

with tab4:
    logs = get_logs(150)
    if not logs:
        st.markdown(f'<div style="color:{COLOR_MUTED};padding:16px;text-align:center;">'
                    f'No log entries yet.</div>', unsafe_allow_html=True)
    else:
        log_html = '<div class="log-container">'
        for entry in logs:
            lvl = entry.get("level", "INFO").lower()
            ts  = entry.get("ts", "")
            msg = entry.get("message", "")
            log_html += f'<div class="log-{lvl}"><span style="color:#555;">[{ts}]</span> {msg}</div>'
        log_html += "</div>"
        st.markdown(log_html, unsafe_allow_html=True)

        if st.button("Clear Logs"):
            clear_logs()
            st.rerun()

# ── Auto-refresh ───────────────────────────────────────────────────────────
if st.session_state.bot_running:
    st.markdown(
        f'<div style="text-align:right;font-size:11px;color:{COLOR_MUTED};'
        f'margin-top:8px;">Auto-refreshing every 30s | '
        f'{datetime.utcnow().strftime("%H:%M:%S UTC")}</div>',
        unsafe_allow_html=True
    )
    time.sleep(30)
    st.rerun()
