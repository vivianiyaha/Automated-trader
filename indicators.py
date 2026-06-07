"""
indicators.py — Technical indicator calculations using the `ta` library + custom SMC logic.
All functions accept a pandas DataFrame with OHLCV columns and return enriched DataFrames
or scalar values.
"""

import numpy as np
import pandas as pd

try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False

from config import EMA_FAST, EMA_SLOW, RSI_PERIOD, ATR_PERIOD


# ── Core Indicators ────────────────────────────────────────

def add_ema(df: pd.DataFrame, period: int, col: str = "close") -> pd.DataFrame:
    """Add EMA column to DataFrame."""
    df[f"ema_{period}"] = df[col].ewm(span=period, adjust=False).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = RSI_PERIOD) -> pd.DataFrame:
    """Add RSI column."""
    delta  = df["close"].diff()
    gain   = delta.clip(lower=0).rolling(period).mean()
    loss   = (-delta.clip(upper=0)).rolling(period).mean()
    rs     = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def add_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.DataFrame:
    """Add Average True Range column."""
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr         = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"]  = tr.rolling(period).mean()
    return df


def add_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Add volume moving average (if volume column exists)."""
    if "volume" in df.columns:
        df["volume_ma"] = df["volume"].rolling(period).mean()
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all standard indicators to a raw OHLCV DataFrame."""
    if df.empty or len(df) < EMA_SLOW + 5:
        return df
    df = add_ema(df, EMA_FAST)
    df = add_ema(df, EMA_SLOW)
    df = add_rsi(df)
    df = add_atr(df)
    df = add_volume_ma(df)
    df.dropna(subset=[f"ema_{EMA_FAST}", f"ema_{EMA_SLOW}"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ── Market Structure ───────────────────────────────────────

def find_swing_highs(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    """Return boolean Series marking swing-high candles."""
    highs = df["high"]
    result = pd.Series(False, index=df.index)
    for i in range(lookback, len(df) - lookback):
        window = highs.iloc[i - lookback: i + lookback + 1]
        if highs.iloc[i] == window.max():
            result.iloc[i] = True
    return result


def find_swing_lows(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    """Return boolean Series marking swing-low candles."""
    lows   = df["low"]
    result = pd.Series(False, index=df.index)
    for i in range(lookback, len(df) - lookback):
        window = lows.iloc[i - lookback: i + lookback + 1]
        if lows.iloc[i] == window.min():
            result.iloc[i] = True
    return result


def detect_market_structure(df: pd.DataFrame) -> dict:
    """
    Identify HH, HL, LH, LL, BOS, and CHOCH from recent swing points.
    Returns a dict with keys: trend, bos, choch, last_hh, last_ll, last_hl, last_lh.
    """
    if len(df) < 30:
        return {"trend": "RANGING", "bos": False, "choch": False,
                "last_hh": None, "last_ll": None, "last_hl": None, "last_lh": None}

    swing_highs = df[find_swing_highs(df)]["high"].tolist()
    swing_lows  = df[find_swing_lows(df)]["low"].tolist()

    trend  = "RANGING"
    bos    = False
    choch  = False
    last_hh = swing_highs[-1] if swing_highs else None
    last_ll = swing_lows[-1]  if swing_lows  else None

    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1]  > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1]  < swing_lows[-2]

        if hh and hl:
            trend = "BULLISH"
            bos   = hh
        elif lh and ll:
            trend = "BEARISH"
            bos   = ll
        else:
            trend = "RANGING"

        # CHOCH: previous bullish → now lower high
        if hh and not hl:
            choch = True
        elif ll and not lh:
            choch = True

    return {
        "trend":   trend,
        "bos":     bos,
        "choch":   choch,
        "last_hh": last_hh,
        "last_ll": last_ll,
        "last_hl": swing_lows[-1]  if len(swing_lows)  >= 2 else None,
        "last_lh": swing_highs[-1] if len(swing_highs) >= 2 else None,
    }


# ── ICT / SMC Concepts ────────────────────────────────────

def detect_order_blocks(df: pd.DataFrame, n: int = 3) -> dict:
    """
    Identify the most recent bullish and bearish order blocks.
    Bullish OB: last bearish candle before a strong bullish move.
    Bearish OB: last bullish candle before a strong bearish move.
    """
    result = {"bullish_ob": None, "bearish_ob": None}
    if len(df) < 10:
        return result

    closes = df["close"].values
    opens  = df["open"].values
    highs  = df["high"].values
    lows   = df["low"].values

    for i in range(len(df) - n - 1, max(0, len(df) - 60), -1):
        # Bullish OB: bearish candle followed by n bullish candles
        if closes[i] < opens[i]:   # bearish
            if all(closes[i + j] > opens[i + j] for j in range(1, n + 1)):
                result["bullish_ob"] = {
                    "high": highs[i], "low": lows[i],
                    "mid":  (highs[i] + lows[i]) / 2,
                }
                break

    for i in range(len(df) - n - 1, max(0, len(df) - 60), -1):
        # Bearish OB: bullish candle followed by n bearish candles
        if closes[i] > opens[i]:   # bullish
            if all(closes[i + j] < opens[i + j] for j in range(1, n + 1)):
                result["bearish_ob"] = {
                    "high": highs[i], "low": lows[i],
                    "mid":  (highs[i] + lows[i]) / 2,
                }
                break

    return result


def detect_fvg(df: pd.DataFrame) -> list:
    """
    Detect Fair Value Gaps (FVG) in the last 50 candles.
    Returns a list of {'type': 'bullish'/'bearish', 'top': x, 'bottom': x}.
    """
    fvgs = []
    subset = df.tail(50)
    highs  = subset["high"].values
    lows   = subset["low"].values

    for i in range(1, len(subset) - 1):
        # Bullish FVG: candle[i-1].high < candle[i+1].low
        if highs[i - 1] < lows[i + 1]:
            fvgs.append({"type": "bullish", "top": lows[i + 1], "bottom": highs[i - 1]})
        # Bearish FVG: candle[i-1].low > candle[i+1].high
        if lows[i - 1] > highs[i + 1]:
            fvgs.append({"type": "bearish", "top": lows[i - 1], "bottom": highs[i + 1]})

    return fvgs[-5:]   # return most recent 5


def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Detect whether price recently swept above a prior swing high (sell-side liquidity)
    or below a prior swing low (buy-side liquidity).
    """
    result = {"buy_side_swept": False, "sell_side_swept": False}
    if len(df) < lookback + 5:
        return result

    recent  = df.tail(5)
    history = df.iloc[-(lookback + 5): -5]

    if history.empty:
        return result

    prior_high = history["high"].max()
    prior_low  = history["low"].min()

    # Sell-side sweep: price wicks above prior high then closes below
    if (recent["high"] > prior_high).any() and (recent["close"] < prior_high).any():
        result["sell_side_swept"] = True

    # Buy-side sweep: price wicks below prior low then closes above
    if (recent["low"] < prior_low).any() and (recent["close"] > prior_low).any():
        result["buy_side_swept"] = True

    return result


def detect_premium_discount(df: pd.DataFrame) -> dict:
    """
    Determine if price is in premium (above equilibrium) or discount (below).
    Uses the last significant swing range.
    """
    if len(df) < 20:
        return {"zone": "EQUILIBRIUM", "ratio": 0.5}

    recent  = df.tail(100)
    high    = recent["high"].max()
    low     = recent["low"].min()
    mid     = (high + low) / 2
    current = df["close"].iloc[-1]

    ratio = (current - low) / (high - low) if (high - low) > 0 else 0.5

    if ratio > 0.618:
        zone = "PREMIUM"
    elif ratio < 0.382:
        zone = "DISCOUNT"
    else:
        zone = "EQUILIBRIUM"

    return {"zone": zone, "ratio": ratio, "high": high, "low": low, "mid": mid}


# ── Price Action Patterns ─────────────────────────────────

def detect_pin_bar(df: pd.DataFrame, min_wick_ratio: float = 2.0) -> str | None:
    """
    Detect pin bar on the last candle.
    Returns 'BULLISH', 'BEARISH', or None.
    """
    if len(df) < 2:
        return None
    c    = df.iloc[-1]
    body = abs(c["close"] - c["open"])
    full = c["high"] - c["low"]
    if body == 0 or full == 0:
        return None

    upper_wick = c["high"] - max(c["close"], c["open"])
    lower_wick = min(c["close"], c["open"]) - c["low"]

    if lower_wick >= body * min_wick_ratio and upper_wick < body:
        return "BULLISH"
    if upper_wick >= body * min_wick_ratio and lower_wick < body:
        return "BEARISH"
    return None


def detect_engulfing(df: pd.DataFrame) -> str | None:
    """
    Detect bullish or bearish engulfing pattern on last two candles.
    """
    if len(df) < 2:
        return None
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    prev_bull = prev["close"] > prev["open"]
    curr_bull = curr["close"] > curr["open"]

    if not prev_bull and curr_bull:
        if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
            return "BULLISH"
    if prev_bull and not curr_bull:
        if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
            return "BEARISH"
    return None


def get_support_resistance(df: pd.DataFrame, n: int = 3) -> dict:
    """
    Return the top-n swing highs (resistance) and swing lows (support).
    """
    sh_mask  = find_swing_highs(df)
    sl_mask  = find_swing_lows(df)
    res_lvls = sorted(df[sh_mask]["high"].unique(), reverse=True)[:n]
    sup_lvls = sorted(df[sl_mask]["low"].unique())[:n]
    return {"resistance": list(res_lvls), "support": list(sup_lvls)}
