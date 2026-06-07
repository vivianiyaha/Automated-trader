"""
strategy.py — AI Market Analysis & Signal Generation Engine.
Implements the weighted scoring system combining:
  Market Structure (25%), SMC (25%), RSI (15%), EMA (15%), Liquidity (10%), Price Action (10%).
"""

import numpy as np
import pandas as pd
from datetime import datetime

import indicators as ind
from config import (
    WEIGHTS, MIN_CONFIDENCE, EMA_FAST, EMA_SLOW,
    RSI_OVERBOUGHT, RSI_OVERSOLD, TP_RR_RATIOS
)
from utils import round_price
import logger


def analyse_market(symbol: str, timeframe: str,
                   df_exec: pd.DataFrame,
                   df_h1: pd.DataFrame   = None,
                   df_h4: pd.DataFrame   = None) -> dict:
    """
    Full market analysis pipeline for one symbol on the execution timeframe.

    Returns a signal dict with all fields needed for display and trade execution.
    """
    if df_exec.empty or len(df_exec) < 50:
        return _no_trade(symbol, timeframe, "Insufficient data")

    # ── Enrich execution frame ────────────────────────────
    df = ind.enrich(df_exec.copy())
    if df.empty:
        return _no_trade(symbol, timeframe, "Indicator calculation failed")

    close   = float(df["close"].iloc[-1])
    atr     = float(df["atr"].iloc[-1]) if "atr" in df.columns else close * 0.001
    rsi_val = float(df["rsi"].iloc[-1]) if "rsi" in df.columns else 50.0
    ema50   = float(df[f"ema_{EMA_FAST}"].iloc[-1])
    ema200  = float(df[f"ema_{EMA_SLOW}"].iloc[-1])

    # ── Sub-score calculations ────────────────────────────

    # 1. Market Structure
    struct     = ind.detect_market_structure(df)
    ms_score, ms_dir = _score_market_structure(struct)

    # 2. SMC Confirmation
    ob         = ind.detect_order_blocks(df)
    fvgs       = ind.detect_fvg(df)
    prem_disc  = ind.detect_premium_discount(df)
    smc_score, smc_dir = _score_smc(ob, fvgs, prem_disc, close, ms_dir)

    # 3. RSI Confirmation
    rsi_score, rsi_dir = _score_rsi(rsi_val)

    # 4. EMA Trend
    ema_score, ema_dir = _score_ema(close, ema50, ema200)

    # 5. Liquidity Sweep
    liq        = ind.detect_liquidity_sweep(df)
    liq_score, liq_dir = _score_liquidity(liq)

    # 6. Price Action
    pin        = ind.detect_pin_bar(df)
    eng        = ind.detect_engulfing(df)
    pa_score, pa_dir = _score_price_action(pin, eng)

    # ── Directional consensus ─────────────────────────────
    scores = {
        "market_structure": (ms_score,  ms_dir),
        "smc_confirmation": (smc_score, smc_dir),
        "rsi_confirmation": (rsi_score, rsi_dir),
        "ema_trend":        (ema_score, ema_dir),
        "liquidity_sweep":  (liq_score, liq_dir),
        "price_action":     (pa_score,  pa_dir),
    }

    direction, confidence = _compute_confidence(scores)

    # ── Higher timeframe filter ───────────────────────────
    htf_trend = _higher_tf_trend(df_h1, df_h4)
    htf_warn  = ""
    if htf_trend and htf_trend != "RANGING" and direction != "NO TRADE":
        if htf_trend == "BULLISH" and direction == "SELL":
            htf_warn = "⚠️ HTF trend is BULLISH — SELL signal is counter-trend"
            confidence *= 0.80   # penalty
        elif htf_trend == "BEARISH" and direction == "BUY":
            htf_warn = "⚠️ HTF trend is BEARISH — BUY signal is counter-trend"
            confidence *= 0.80

    confidence = round(confidence, 1)

    if confidence < MIN_CONFIDENCE or direction == "NO TRADE":
        return _no_trade(symbol, timeframe,
                         f"Confidence {confidence:.1f}% < {MIN_CONFIDENCE}% threshold")

    # ── Levels ────────────────────────────────────────────
    sr      = ind.get_support_resistance(df)
    entry   = close
    sl, tp1, tp2, tp3 = _calculate_levels(entry, atr, direction, symbol)
    rr      = round(abs(tp1 - entry) / abs(sl - entry), 2) if sl != entry else 0

    reason  = _build_reason(struct, ob, fvgs, prem_disc, liq, pin, eng,
                             rsi_val, ema50, ema200, htf_trend, htf_warn)

    sig = {
        "timestamp":      datetime.utcnow().isoformat(),
        "symbol":         symbol,
        "timeframe":      timeframe,
        "signal":         direction,
        "entry":          round_price(symbol, entry),
        "stop_loss":      round_price(symbol, sl),
        "tp1":            round_price(symbol, tp1),
        "tp2":            round_price(symbol, tp2),
        "tp3":            round_price(symbol, tp3),
        "confidence":     confidence,
        "trend":          struct["trend"],
        "htf_trend":      htf_trend or "UNKNOWN",
        "htf_warning":    htf_warn,
        "support":        sr["support"],
        "resistance":     sr["resistance"],
        "rr_ratio":       rr,
        "trade_reason":   reason,
        "risk_warning":   _risk_warning(confidence, struct, htf_warn),
        "scores":         {k: v[0] for k, v in scores.items()},
        "atr":            round(atr, 6),
        "rsi":            round(rsi_val, 1),
    }

    logger.signal(f"[{symbol}] {direction} @ {entry:.5f} — Confidence {confidence}%")
    return sig


# ── Scoring Helpers ────────────────────────────────────────

def _score_market_structure(struct: dict) -> tuple[float, str]:
    trend = struct.get("trend", "RANGING")
    bos   = struct.get("bos",   False)
    choch = struct.get("choch", False)

    if trend == "BULLISH" and bos and not choch:
        return 1.0, "BUY"
    if trend == "BEARISH" and bos and not choch:
        return 1.0, "SELL"
    if trend == "BULLISH" and not bos:
        return 0.6, "BUY"
    if trend == "BEARISH" and not bos:
        return 0.6, "SELL"
    if choch:
        return 0.3, "NO TRADE"
    return 0.2, "NO TRADE"


def _score_smc(ob: dict, fvgs: list, pd_info: dict, price: float,
               ms_dir: str) -> tuple[float, str]:
    score = 0.0
    direction = ms_dir

    zone = pd_info.get("zone", "EQUILIBRIUM")

    if ms_dir == "BUY":
        # Prefer discount zone for buys
        if zone == "DISCOUNT":
            score += 0.4
        # Bullish OB nearby
        bob = ob.get("bullish_ob")
        if bob and bob["low"] <= price <= bob["high"]:
            score += 0.4
        # Bullish FVG unfilled
        if any(f["type"] == "bullish" for f in fvgs):
            score += 0.2

    elif ms_dir == "SELL":
        if zone == "PREMIUM":
            score += 0.4
        beo = ob.get("bearish_ob")
        if beo and beo["low"] <= price <= beo["high"]:
            score += 0.4
        if any(f["type"] == "bearish" for f in fvgs):
            score += 0.2

    return min(score, 1.0), direction


def _score_rsi(rsi: float) -> tuple[float, str]:
    if rsi < RSI_OVERSOLD:
        return 1.0, "BUY"
    if rsi > RSI_OVERBOUGHT:
        return 1.0, "SELL"
    if rsi < 40:
        return 0.6, "BUY"
    if rsi > 60:
        return 0.6, "SELL"
    return 0.2, "NO TRADE"


def _score_ema(price: float, ema50: float, ema200: float) -> tuple[float, str]:
    if price > ema200 and ema50 > ema200:
        return 1.0, "BUY"    # strong bullish
    if price < ema200 and ema50 < ema200:
        return 1.0, "SELL"   # strong bearish
    if price > ema50 > ema200:
        return 0.7, "BUY"
    if price < ema50 < ema200:
        return 0.7, "SELL"
    if price > ema200:
        return 0.4, "BUY"
    if price < ema200:
        return 0.4, "SELL"
    return 0.2, "NO TRADE"


def _score_liquidity(liq: dict) -> tuple[float, str]:
    buy  = liq.get("buy_side_swept",  False)
    sell = liq.get("sell_side_swept", False)
    if buy:
        return 1.0, "BUY"    # swept lows → expect reversal up
    if sell:
        return 1.0, "SELL"   # swept highs → expect reversal down
    return 0.0, "NO TRADE"


def _score_price_action(pin: str | None, eng: str | None) -> tuple[float, str]:
    if pin == "BULLISH" or eng == "BULLISH":
        return 1.0, "BUY"
    if pin == "BEARISH" or eng == "BEARISH":
        return 1.0, "SELL"
    return 0.0, "NO TRADE"


def _compute_confidence(scores: dict) -> tuple[str, float]:
    """
    Weighted scoring → direction + confidence %.
    Tallies weighted buy / sell scores separately.
    """
    buy_score  = 0.0
    sell_score = 0.0

    for key, (raw_score, direction) in scores.items():
        weight = WEIGHTS.get(key, 0.0)
        if direction == "BUY":
            buy_score  += raw_score * weight
        elif direction == "SELL":
            sell_score += raw_score * weight

    if buy_score > sell_score:
        direction   = "BUY"
        confidence  = buy_score * 100
    elif sell_score > buy_score:
        direction  = "SELL"
        confidence = sell_score * 100
    else:
        direction  = "NO TRADE"
        confidence = 0.0

    return direction, confidence


def _calculate_levels(entry: float, atr: float, direction: str,
                      symbol: str) -> tuple[float, float, float, float]:
    """Derive SL and TP levels from ATR multiples."""
    sl_mult = 1.5
    if direction == "BUY":
        sl  = entry - atr * sl_mult
        tp1 = entry + atr * TP_RR_RATIOS[0]
        tp2 = entry + atr * TP_RR_RATIOS[1]
        tp3 = entry + atr * TP_RR_RATIOS[2]
    else:
        sl  = entry + atr * sl_mult
        tp1 = entry - atr * TP_RR_RATIOS[0]
        tp2 = entry - atr * TP_RR_RATIOS[1]
        tp3 = entry - atr * TP_RR_RATIOS[2]
    return sl, tp1, tp2, tp3


def _higher_tf_trend(df_h1: pd.DataFrame, df_h4: pd.DataFrame) -> str | None:
    """Determine dominant trend from H4 > H1."""
    for df in [df_h4, df_h1]:
        if df is not None and not df.empty and len(df) >= 50:
            enriched = ind.enrich(df.copy())
            if not enriched.empty:
                struct = ind.detect_market_structure(enriched)
                return struct.get("trend", "RANGING")
    return None


def _build_reason(struct, ob, fvgs, pd_info, liq, pin, eng,
                  rsi, ema50, ema200, htf_trend, htf_warn) -> str:
    parts = []
    parts.append(f"Market Structure: {struct['trend']} "
                 f"| BOS: {'✓' if struct['bos'] else '✗'} "
                 f"| CHOCH: {'✓' if struct['choch'] else '✗'}")

    if ob.get("bullish_ob"):
        parts.append(f"Bullish Order Block detected near {ob['bullish_ob']['mid']:.5f}")
    if ob.get("bearish_ob"):
        parts.append(f"Bearish Order Block detected near {ob['bearish_ob']['mid']:.5f}")
    if fvgs:
        parts.append(f"{len(fvgs)} Fair Value Gap(s) present")

    parts.append(f"Price Zone: {pd_info.get('zone','?')} "
                 f"(ratio {pd_info.get('ratio',0):.2f})")

    if liq.get("buy_side_swept"):
        parts.append("Buy-side liquidity sweep detected → bullish reversal likely")
    if liq.get("sell_side_swept"):
        parts.append("Sell-side liquidity sweep detected → bearish reversal likely")

    if pin:
        parts.append(f"Pin Bar pattern: {pin}")
    if eng:
        parts.append(f"Engulfing candle: {eng}")

    parts.append(f"RSI: {rsi:.1f} | EMA50: {ema50:.5f} | EMA200: {ema200:.5f}")

    if htf_trend:
        parts.append(f"Higher Timeframe Trend: {htf_trend}")
    if htf_warn:
        parts.append(htf_warn)

    return " | ".join(parts)


def _risk_warning(conf: float, struct: dict, htf_warn: str) -> str:
    warnings = ["Trading involves significant risk of loss."]
    if conf < 80:
        warnings.append("Confidence is moderate — trade with reduced size.")
    if struct.get("choch"):
        warnings.append("CHOCH detected — trend may be reversing.")
    if htf_warn:
        warnings.append(htf_warn)
    warnings.append("Always use a stop loss. Never risk more than you can afford to lose.")
    return " ".join(warnings)


def _no_trade(symbol: str, timeframe: str, reason: str) -> dict:
    """Return a standardised NO TRADE signal."""
    return {
        "timestamp":    datetime.utcnow().isoformat(),
        "symbol":       symbol,
        "timeframe":    timeframe,
        "signal":       "NO TRADE",
        "confidence":   0.0,
        "trade_reason": reason,
        "trend":        "UNKNOWN",
        "htf_trend":    "UNKNOWN",
        "htf_warning":  "",
        "support":      [],
        "resistance":   [],
        "entry":        None,
        "stop_loss":    None,
        "tp1":          None,
        "tp2":          None,
        "tp3":          None,
        "rr_ratio":     0,
        "risk_warning": "No trade recommended at this time.",
        "scores":       {},
        "atr":          0,
        "rsi":          50,
    }
