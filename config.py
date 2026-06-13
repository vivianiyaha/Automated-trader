"""
config.py - Central configuration for Deriv AI Automated Trader
All API keys, constants, and default settings live here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# DERIV API CREDENTIALS
# ─────────────────────────────────────────────
DERIV_APP_ID = os.getenv("DERIV_APP_ID", "1089")          # Default demo app id
DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN", "")         # Set your token here or in .env
DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3"

# ─────────────────────────────────────────────
# SUPPORTED MARKETS
# ─────────────────────────────────────────────
FOREX_PAIRS = [
    "frxNZDUSD", "frxAUDCHF", "frxAUDUSD", "frxAUDNZD",
    "frxAUDCAD", "frxNZDCHF", "frxNZDCAD", "frxNZDJPY", "frxCADCHF"
]

CRYPTO_PAIRS = [
    "cryLTCUSD", "cryXRPUSD", "cryBCHUSD", "cryETHUSD"
]

ALL_PAIRS = FOREX_PAIRS + CRYPTO_PAIRS

PAIR_DISPLAY = {
    "frxNZDUSD": "NZD/USD", "frxAUDCHF": "AUD/CHF", "frxAUDUSD": "AUD/USD",
    "frxAUDNZD": "AUD/NZD", "frxAUDCAD": "AUD/CAD", "frxNZDCHF": "NZD/CHF",
    "frxNZDCAD": "NZD/CAD", "frxNZDJPY": "NZD/JPY", "frxCADCHF": "CAD/CHF",
    "cryLTCUSD": "LTC/USD", "cryXRPUSD": "XRP/USD", "cryBCHUSD": "BCH/USD", "cryETHUSD": "ETHUSD",
}

# ─────────────────────────────────────────────
# TIMEFRAMES
# ─────────────────────────────────────────────
TIMEFRAMES = {
    "M1":  60,
    "M5":  300,
    "M15": 900,
    "M30": 1800,
    "H1":  3600,
    "H4":  14400,
    "D1":  86400,
}

HIGHER_TIMEFRAMES = ["H4", "H1"]
EXECUTION_TIMEFRAMES = ["M15", "M5"]

# ─────────────────────────────────────────────
# STRATEGY SCORING WEIGHTS
# ─────────────────────────────────────────────
SCORING_WEIGHTS = {
    "market_structure": 0.25,
    "smc_confirmation":  0.25,
    "rsi_confirmation":  0.15,
    "ema_trend":         0.15,
    "liquidity_sweep":   0.10,
    "price_action":      0.10,
}

CONFIDENCE_THRESHOLD = 75.0   # Minimum % to place a trade

# ─────────────────────────────────────────────
# RISK DEFAULTS
# ─────────────────────────────────────────────
DEFAULT_RISK_PCT     = 1.0    # % of balance per trade
DEFAULT_LOT_SIZE     = 0.01
DEFAULT_DAILY_LOSS   = 5.0    # % of balance
DEFAULT_MAX_TRADES   = 5
DEFAULT_TP_MULTIPLIERS = [1.5, 2.5, 3.5]   # RR for TP1, TP2, TP3

# ─────────────────────────────────────────────
# INDICATOR SETTINGS
# ─────────────────────────────────────────────
EMA_FAST  = 50
EMA_SLOW  = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
LOOKBACK_CANDLES = 200   # candles to fetch per analysis

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
DB_PATH = "data/trader.db"

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_DIR      = "logs"
LOG_CSV_PATH = "logs/trade_log.csv"

# ─────────────────────────────────────────────
# UI COLOURS
# ─────────────────────────────────────────────
COLOR_GREEN  = "#00FF88"
COLOR_RED    = "#FF3A3A"
COLOR_BLACK  = "#0A0A0A"
COLOR_CARD   = "#111111"
COLOR_BORDER = "#1E1E1E"
COLOR_WHITE  = "#F0F0F0"
COLOR_MUTED  = "#888888"
