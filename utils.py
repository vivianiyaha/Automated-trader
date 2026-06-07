"""
utils.py — Shared utility helpers.
"""

from datetime import datetime
import pandas as pd
import numpy as np
from config import PAIR_DISPLAY


def display_name(symbol: str) -> str:
    """Convert internal symbol key to human-readable pair name."""
    return PAIR_DISPLAY.get(symbol, symbol)


def now_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def pct_change(old: float, new: float) -> float:
    """Safe percentage change calculation."""
    if old == 0:
        return 0.0
    return ((new - old) / abs(old)) * 100


def pip_value(symbol: str, price: float) -> float:
    """Approximate pip/tick value for the symbol."""
    if symbol.startswith("cry"):
        return 0.01          # crypto: 1 cent per unit
    if "JPY" in symbol:
        return 0.01 / price  # JPY pairs: 0.01 pip
    return 0.0001 / price    # most forex: 0.0001 pip


def round_price(symbol: str, price: float) -> float:
    """Round price to appropriate decimal places."""
    if symbol.startswith("cry"):
        return round(price, 4)
    if "JPY" in symbol:
        return round(price, 3)
    return round(price, 5)


def calc_position_size(balance: float, risk_pct: float, sl_distance: float,
                       pip_val: float) -> float:
    """
    Standard position-sizing formula.
    position_size = (balance * risk_pct/100) / (sl_distance / pip_val)
    Returns lot size, floored to 2 decimal places.
    """
    if sl_distance <= 0 or pip_val <= 0:
        return 0.01
    risk_amount = balance * (risk_pct / 100)
    sl_in_pips  = sl_distance / pip_val
    lot_size    = risk_amount / (sl_in_pips * 10)   # 1 lot ≈ $10/pip approx
    return max(0.01, round(lot_size, 2))


def candles_to_df(candles: list) -> pd.DataFrame:
    """Convert Deriv API candle list to a tidy OHLCV DataFrame."""
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    df.columns = [c.lower() for c in df.columns]
    rename = {"epoch": "time", "open_time": "time"}
    df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "time" in df.columns:
        df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.sort_values("datetime", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def colour_pnl(val: float) -> str:
    """Streamlit markdown colour for P&L values."""
    if val > 0:
        return f"🟢 +{val:.2f}"
    elif val < 0:
        return f"🔴 {val:.2f}"
    return f"⚪ {val:.2f}"
