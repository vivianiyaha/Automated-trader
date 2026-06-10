"""
indicators.py - Technical indicator calculations using pandas / ta library.
All functions accept a pandas DataFrame with OHLCV columns and return augmented DataFrames.
"""

import numpy as np
import pandas as pd
from config import EMA_FAST, EMA_SLOW, RSI_PERIOD, ATR_PERIOD


# ─── MOVING AVERAGES ────────────────────────────────────────────────────────

def add_emas(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA 50 and EMA 200 columns."""
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    return df


# ─── RSI ────────────────────────────────────────────────────────────────────

def add_rsi(df: pd.DataFrame, period: int = RSI_PERIOD) -> pd.DataFrame:
    """Add RSI column."""
    df = df.copy()
    delta = df["close"].diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


# ─── ATR ────────────────────────────────────────────────────────────────────

def add_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.DataFrame:
    """Add Average True Range column."""
    df = df.copy()
    hi_lo = df["high"] - df["low"]
    hi_cp = (df["high"] - df["close"].shift()).abs()
    lo_cp = (df["low"]  - df["close"].shift()).abs()
    tr    = pd.concat([hi_lo, hi_cp, lo_cp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(com=period - 1, adjust=False).mean()
    return df


# ─── SWING LEVELS ───────────────────────────────────────────────────────────

def find_swing_highs(df: pd.DataFrame, left: int = 5, right: int = 5) -> pd.Series:
    """Return boolean Series True at swing high pivots."""
    highs = df["high"]
    pivot = pd.Series(False, index=df.index)
    for i in range(left, len(df) - right):
        window = highs.iloc[i - left: i + right + 1]
        if highs.iloc[i] == window.max():
            pivot.iloc[i] = True
    return pivot


def find_swing_lows(df: pd.DataFrame, left: int = 5, right: int = 5) -> pd.Series:
    """Return boolean Series True at swing low pivots."""
    lows = df["low"]
    pivot = pd.Series(False, index=df.index)
    for i in range(left, len(df) - right):
        window = lows.iloc[i - left: i + right + 1]
        if lows.iloc[i] == window.min():
            pivot.iloc[i] = True
    return pivot


# ─── MARKET STRUCTURE ───────────────────────────────────────────────────────

def detect_market_structure(df: pd.DataFrame):
    """
    Detect HH, HL, LH, LL, BOS, CHOCH.
    Returns a dict with structure classification and latest swing levels.
    """
    sh = find_swing_highs(df)
    sl = find_swing_lows(df)

    swing_high_vals = df["high"][sh].values[-4:]
    swing_low_vals  = df["low"][sl].values[-4:]

    result = {
        "swing_highs": list(swing_high_vals),
        "swing_lows":  list(swing_low_vals),
        "bos":    False,
        "choch":  False,
        "trend":  "ranging",
        "hh": False, "hl": False, "lh": False, "ll": False,
    }

    if len(swing_high_vals) >= 2 and len(swing_low_vals) >= 2:
        hh = swing_high_vals[-1] > swing_high_vals[-2]
        hl = swing_low_vals[-1]  > swing_low_vals[-2]
        lh = swing_high_vals[-1] < swing_high_vals[-2]
        ll = swing_low_vals[-1]  < swing_low_vals[-2]

        result.update({"hh": hh, "hl": hl, "lh": lh, "ll": ll})

        if hh and hl:
            result["trend"] = "bullish"
        elif lh and ll:
            result["trend"] = "bearish"

        # BOS: price breaks last swing high/low
        last_close = df["close"].iloc[-1]
        if len(swing_high_vals) >= 1 and last_close > swing_high_vals[-1]:
            result["bos"] = True
        if len(swing_low_vals) >= 1 and last_close < swing_low_vals[-1]:
            result["bos"] = True

        # CHOCH: bullish → bearish or vice versa flip
        if hh and ll:
            result["choch"] = True
        if lh and hl:
            result["choch"] = True

    return result


# ─── ORDER BLOCKS ───────────────────────────────────────────────────────────

def find_order_blocks(df: pd.DataFrame, lookback: int = 30):
    """
    Simple order block detection:
    - Bullish OB: last bearish candle before strong bullish move
    - Bearish OB: last bullish candle before strong bearish move
    Returns list of dicts with ob details.
    """
    obs = []
    for i in range(2, min(len(df), lookback)):
        idx  = len(df) - i
        candle = df.iloc[idx]
        next_c = df.iloc[idx + 1] if idx + 1 < len(df) else None
        if next_c is None:
            continue
        body_curr = abs(candle["close"] - candle["open"])
        body_next = abs(next_c["close"]  - next_c["open"])
        # Bullish OB
        if (candle["close"] < candle["open"] and
                next_c["close"] > next_c["open"] and
                body_next > 1.5 * body_curr):
            obs.append({
                "type": "bullish",
                "high": candle["high"],
                "low":  candle["low"],
                "idx":  idx
            })
        # Bearish OB
        elif (candle["close"] > candle["open"] and
              next_c["close"] < next_c["open"] and
              body_next > 1.5 * body_curr):
            obs.append({
                "type": "bearish",
                "high": candle["high"],
                "low":  candle["low"],
                "idx":  idx
            })
    return obs[:5]   # Return most recent 5


# ─── FAIR VALUE GAPS ────────────────────────────────────────────────────────

def find_fvg(df: pd.DataFrame, lookback: int = 30):
    """
    Detect Fair Value Gaps (3-candle pattern).
    Bullish FVG: candle[i-1].low > candle[i+1].high
    Bearish FVG: candle[i-1].high < candle[i+1].low
    """
    fvgs = []
    start = max(1, len(df) - lookback)
    for i in range(start, len(df) - 1):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]      # noqa – used for context but gaps are prev / next
        nxt  = df.iloc[i + 1]
        if prev["low"] > nxt["high"]:
            fvgs.append({"type": "bullish", "top": prev["low"], "bottom": nxt["high"], "idx": i})
        elif prev["high"] < nxt["low"]:
            fvgs.append({"type": "bearish", "top": nxt["low"], "bottom": prev["high"], "idx": i})
    return fvgs[-3:]


# ─── LIQUIDITY SWEEP ────────────────────────────────────────────────────────

def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Detect if price recently swept a swing high or low
    (wick beyond level then closed back inside range).
    """
    result = {"sweep": False, "direction": None}
    if len(df) < lookback + 2:
        return result

    recent = df.tail(lookback)
    prior  = df.iloc[-(lookback + 10): -(lookback)]
    if prior.empty:
        return result

    swing_h = prior["high"].max()
    swing_l = prior["low"].min()
    last    = df.iloc[-1]

    # Bullish sweep: wick below swing low, closed above it
    if last["low"] < swing_l and last["close"] > swing_l:
        result.update({"sweep": True, "direction": "bullish"})
    # Bearish sweep: wick above swing high, closed below it
    elif last["high"] > swing_h and last["close"] < swing_h:
        result.update({"sweep": True, "direction": "bearish"})

    return result


# ─── PREMIUM / DISCOUNT ZONES ────────────────────────────────────────────────

def get_premium_discount(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    50% equilibrium of the last major range.
    Premium  = above 50%
    Discount = below 50%
    """
    high = df["high"].tail(lookback).max()
    low  = df["low"].tail(lookback).min()
    eq   = (high + low) / 2
    last = df["close"].iloc[-1]
    zone = "premium" if last > eq else "discount"
    return {"high": high, "low": low, "equilibrium": eq, "zone": zone}


# ─── PRICE ACTION PATTERNS ──────────────────────────────────────────────────

def detect_price_action(df: pd.DataFrame) -> dict:
    """Detect pin bars, engulfing candles, rejection candles."""
    patterns = {
        "pin_bar_bull":  False,
        "pin_bar_bear":  False,
        "engulf_bull":   False,
        "engulf_bear":   False,
        "rejection_bull":False,
        "rejection_bear":False,
    }
    if len(df) < 3:
        return patterns

    c1 = df.iloc[-2]
    c2 = df.iloc[-1]
    body1 = abs(c1["close"] - c1["open"])
    body2 = abs(c2["close"] - c2["open"])
    total2 = c2["high"] - c2["low"] if c2["high"] != c2["low"] else 1e-10

    # Pin bar bullish: long lower wick
    lower_wick2 = min(c2["open"], c2["close"]) - c2["low"]
    upper_wick2 = c2["high"] - max(c2["open"], c2["close"])
    if lower_wick2 > 2 * body2 and lower_wick2 > upper_wick2:
        patterns["pin_bar_bull"] = True
    if upper_wick2 > 2 * body2 and upper_wick2 > lower_wick2:
        patterns["pin_bar_bear"] = True

    # Bullish engulfing
    if (c1["close"] < c1["open"] and c2["close"] > c2["open"] and
            c2["close"] > c1["open"] and c2["open"] < c1["close"]):
        patterns["engulf_bull"] = True

    # Bearish engulfing
    if (c1["close"] > c1["open"] and c2["close"] < c2["open"] and
            c2["close"] < c1["open"] and c2["open"] > c1["close"]):
        patterns["engulf_bear"] = True

    # Rejection candles (small body near extreme)
    if body2 / total2 < 0.3 and lower_wick2 / total2 > 0.5:
        patterns["rejection_bull"] = True
    if body2 / total2 < 0.3 and upper_wick2 / total2 > 0.5:
        patterns["rejection_bear"] = True

    return patterns


# ─── VOLUME ANALYSIS ────────────────────────────────────────────────────────

def volume_spike(df: pd.DataFrame, lookback: int = 20) -> bool:
    """True if latest candle volume is > 1.5x average of last N candles."""
    if "volume" not in df.columns or df["volume"].sum() == 0:
        return False
    avg = df["volume"].tail(lookback + 1).iloc[:-1].mean()
    return df["volume"].iloc[-1] > 1.5 * avg


# ─── SUPPORT / RESISTANCE ───────────────────────────────────────────────────

def get_support_resistance(df: pd.DataFrame, left: int = 5, right: int = 5):
    """Return lists of support and resistance price levels."""
    sh = find_swing_highs(df, left, right)
    sl = find_swing_lows(df, left, right)
    resistance = sorted(df["high"][sh].tail(5).tolist(), reverse=True)
    support    = sorted(df["low"][sl].tail(5).tolist(), reverse=True)
    return support, resistance
    
