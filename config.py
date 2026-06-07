"""
config.py — Central configuration for Deriv AI Auto Trader
All constants, defaults, and market definitions live here.
"""

# ── Deriv API ──────────────────────────────────────────────
DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"

# ── Supported Markets ──────────────────────────────────────
FOREX_PAIRS = [
    "frxNZDUSD", "frxAUDCHF", "frxAUDUSD", "frxAUDNZD", "frxAUDCAD",
    "frxNZDCHF", "frxNZDCAD", "frxNZDJPY", "frxCADCHF",
]

CRYPTO_PAIRS = [
    "cryLTCUSD", "cryXRPUSD", "cryBCHUSD",
]

ALL_PAIRS = FOREX_PAIRS + CRYPTO_PAIRS

PAIR_DISPLAY = {
    "frxNZDUSD": "NZD/USD",  "frxAUDCHF": "AUD/CHF",
    "frxAUDUSD": "AUD/USD",  "frxAUDNZD": "AUD/NZD",
    "frxAUDCAD": "AUD/CAD",  "frxNZDCHF": "NZD/CHF",
    "frxNZDCAD": "NZD/CAD",  "frxNZDJPY": "NZD/JPY",
    "frxCADCHF": "CAD/CHF",  "cryLTCUSD": "LTC/USD",
    "cryXRPUSD": "XRP/USD",  "cryBCHUSD": "BCH/USD",
}

# ── Timeframes ─────────────────────────────────────────────
TIMEFRAMES = {
    "M1":  60,   "M5":  300,  "M15": 900,
    "M30": 1800, "H1":  3600, "H4":  14400,
}

HIGHER_TF  = ["H4", "H1"]
EXEC_TF    = ["M15", "M5"]

# ── Strategy / Indicator Defaults ──────────────────────────
EMA_FAST        = 50
EMA_SLOW        = 200
RSI_PERIOD      = 14
RSI_OVERBOUGHT  = 70
RSI_OVERSOLD    = 30
ATR_PERIOD      = 14

# ── AI Scoring Weights (must sum to 1.0) ──────────────────
WEIGHTS = {
    "market_structure":  0.25,
    "smc_confirmation":  0.25,
    "rsi_confirmation":  0.15,
    "ema_trend":         0.15,
    "liquidity_sweep":   0.10,
    "price_action":      0.10,
}

MIN_CONFIDENCE = 75   # % threshold to open a trade

# ── Risk Defaults ──────────────────────────────────────────
DEFAULT_RISK_PCT      = 1.0    # % of balance per trade
DEFAULT_LOT_SIZE      = 0.01
DEFAULT_DAILY_LOSS    = 5.0    # % of balance
DEFAULT_DAILY_PROFIT  = 10.0   # % of balance
DEFAULT_MAX_TRADES    = 5

TP_RR_RATIOS = [1.5, 2.5, 4.0]   # TP1, TP2, TP3 R:R multiples

# ── Candle / Data Settings ────────────────────────────────
CANDLE_COUNT  = 500   # history candles to fetch
SCAN_INTERVAL = 60    # seconds between market scans

# ── Database ──────────────────────────────────────────────
DB_PATH = "data/trader.db"

# ── Logging ───────────────────────────────────────────────
LOG_DIR      = "logs"
LOG_FILE     = "logs/trader.log"
CSV_LOG_FILE = "logs/trade_log.csv"
