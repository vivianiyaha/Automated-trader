"""
strategy.py - AI Decision Engine combining SMC, ICT, and Price Action signals.
Produces a fully scored TradeSignal for each symbol / timeframe combination.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

from config import SCORING_WEIGHTS, CONFIDENCE_THRESHOLD, DEFAULT_TP_MULTIPLIERS
from indicators import (
    add_emas, add_rsi, add_atr,
    detect_market_structure, find_order_blocks, find_fvg,
    detect_liquidity_sweep, get_premium_discount,
    detect_price_action, volume_spike, get_support_resistance
)


@dataclass
class TradeSignal:
    """Container for a fully analysed trade signal."""
    symbol:      str
    timeframe:   str
    signal:      str          # BUY | SELL | NO TRADE
    entry:       Optional[float] = None
    sl:          Optional[float] = None
    tp1:         Optional[float] = None
    tp2:         Optional[float] = None
    tp3:         Optional[float] = None
    rr:          float = 0.0
    confidence:  float = 0.0
    trend:       str   = "ranging"
    support:     List[float] = field(default_factory=list)
    resistance:  List[float] = field(default_factory=list)
    reason:      str   = ""
    warning:     str   = ""
    ts:          str   = field(default_factory=lambda: datetime.utcnow().isoformat())
    scores:      dict  = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts, "symbol": self.symbol, "timeframe": self.timeframe,
            "signal": self.signal, "entry": self.entry, "sl": self.sl,
            "tp1": self.tp1, "tp2": self.tp2, "tp3": self.tp3,
            "rr": self.rr, "confidence": self.confidence, "trend": self.trend,
            "reason": self.reason
        }


class StrategyEngine:
    """
    Multi-timeframe AI Strategy Engine.
    analyse() is the main entry point – call it with an OHLCV DataFrame.
    """

    def __init__(self):
        self.tp_multipliers = DEFAULT_TP_MULTIPLIERS

    # ─── PUBLIC API ─────────────────────────────────────────────────────────

    def analyse(self, df: pd.DataFrame, symbol: str, timeframe: str,
                htf_trend: str = "ranging") -> TradeSignal:
        """
        Full analysis pipeline.

        Args:
            df:          OHLCV DataFrame (columns: open, high, low, close, volume)
            symbol:      Deriv symbol string
            timeframe:   e.g. 'M15'
            htf_trend:   Higher timeframe trend ('bullish'|'bearish'|'ranging')

        Returns:
            TradeSignal
        """
        if df is None or len(df) < 50:
            return TradeSignal(symbol=symbol, timeframe=timeframe,
                               signal="NO TRADE", reason="Insufficient data")

        # Add indicators
        df = add_emas(df)
        df = add_rsi(df)
        df = add_atr(df)

        last  = df.iloc[-1]
        atr   = last.get("atr", 0.001) or 0.001
        price = last["close"]

        # Core analysis
        ms        = detect_market_structure(df)
        obs       = find_order_blocks(df)
        fvgs      = find_fvg(df)
        liq       = detect_liquidity_sweep(df)
        pd_zone   = get_premium_discount(df)
        pa        = detect_price_action(df)
        support, resistance = get_support_resistance(df)

        # ── Scoring ────────────────────────────────────────────────────────
        scores = self._score(last, ms, obs, fvgs, liq, pd_zone, pa, df)
        confidence = sum(
            scores[k] * SCORING_WEIGHTS[k] * 100
            for k in SCORING_WEIGHTS
        )

        # ── Determine bias ─────────────────────────────────────────────────
        bull_score = (
            scores["market_structure"] if ms["trend"] == "bullish" else 0
        ) + (1 if liq.get("direction") == "bullish" else 0)

        bear_score = (
            scores["market_structure"] if ms["trend"] == "bearish" else 0
        ) + (1 if liq.get("direction") == "bearish" else 0)

        if confidence < CONFIDENCE_THRESHOLD:
            signal = "NO TRADE"
            reason = (f"Confidence {confidence:.1f}% below threshold "
                      f"{CONFIDENCE_THRESHOLD}%. Conditions not aligned.")
            return TradeSignal(
                symbol=symbol, timeframe=timeframe, signal=signal,
                confidence=round(confidence, 2), trend=ms["trend"],
                support=support[:3], resistance=resistance[:3],
                reason=reason, scores=scores
            )

        # Direction decision
        if ms["trend"] == "bullish" and bull_score >= bear_score:
            signal = "BUY"
        elif ms["trend"] == "bearish" and bear_score >= bull_score:
            signal = "SELL"
        else:
            signal = "NO TRADE"
            reason = "No clear directional alignment."
            return TradeSignal(
                symbol=symbol, timeframe=timeframe, signal=signal,
                confidence=round(confidence, 2), trend=ms["trend"],
                support=support[:3], resistance=resistance[:3],
                reason=reason, scores=scores
            )

        # HTF conflict warning
        warning = ""
        if htf_trend != "ranging" and htf_trend != ms["trend"]:
            warning = (f"⚠️ LTF {signal} signal conflicts with HTF "
                       f"{htf_trend.upper()} trend. Use caution.")

        # ── Entry / SL / TP ───────────────────────────────────────────────
        entry, sl, tp1, tp2, tp3, rr = self._calc_levels(
            signal, price, atr, ms, obs, support, resistance
        )

        reason = self._build_reason(signal, ms, liq, pa, obs, fvgs, pd_zone,
                                     last, confidence, htf_trend)

        return TradeSignal(
            symbol=symbol, timeframe=timeframe, signal=signal,
            entry=round(entry, 5), sl=round(sl, 5),
            tp1=round(tp1, 5), tp2=round(tp2, 5), tp3=round(tp3, 5),
            rr=round(rr, 2), confidence=round(confidence, 2),
            trend=ms["trend"], support=[round(s, 5) for s in support[:3]],
            resistance=[round(r, 5) for r in resistance[:3]],
            reason=reason, warning=warning, scores=scores
        )

    # ─── SCORING ────────────────────────────────────────────────────────────

    def _score(self, last, ms, obs, fvgs, liq, pd_zone, pa, df) -> dict:
        """Return normalised [0–1] score for each component."""
        scores = {}

        # 1. Market Structure (25%)
        ms_score = 0.0
        if ms["bos"]:  ms_score += 0.4
        if ms["hh"] and ms["hl"]: ms_score += 0.4
        if ms["lh"] and ms["ll"]: ms_score += 0.4
        if ms["choch"]: ms_score = min(ms_score + 0.2, 1.0)
        scores["market_structure"] = min(ms_score, 1.0)

        # 2. SMC Confirmation (25%)
        smc_score = 0.0
        if obs:  smc_score += 0.35
        if fvgs: smc_score += 0.35
        if pd_zone["zone"] in ("premium", "discount"): smc_score += 0.3
        scores["smc_confirmation"] = min(smc_score, 1.0)

        # 3. RSI Confirmation (15%)
        rsi = last.get("rsi", 50)
        if pd.isna(rsi): rsi = 50
        if ms["trend"] == "bullish" and rsi < 50: rsi_score = 1.0
        elif ms["trend"] == "bullish" and 50 <= rsi <= 65: rsi_score = 0.6
        elif ms["trend"] == "bearish" and rsi > 50: rsi_score = 1.0
        elif ms["trend"] == "bearish" and 35 <= rsi <= 50: rsi_score = 0.6
        else: rsi_score = 0.2
        scores["rsi_confirmation"] = rsi_score

        # 4. EMA Trend (15%)
        ef = last.get("ema_fast")
        es = last.get("ema_slow")
        cl = last["close"]
        if ef and es and not pd.isna(ef) and not pd.isna(es):
            if ms["trend"] == "bullish" and ef > es and cl > ef:
                ema_score = 1.0
            elif ms["trend"] == "bearish" and ef < es and cl < ef:
                ema_score = 1.0
            elif ef > es or ef < es:
                ema_score = 0.5
            else:
                ema_score = 0.1
        else:
            ema_score = 0.3
        scores["ema_trend"] = ema_score

        # 5. Liquidity Sweep (10%)
        scores["liquidity_sweep"] = 1.0 if liq["sweep"] else 0.0

        # 6. Price Action (10%)
        pa_score = 0.0
        if ms["trend"] == "bullish":
            if pa["pin_bar_bull"] or pa["engulf_bull"] or pa["rejection_bull"]:
                pa_score = 1.0
        elif ms["trend"] == "bearish":
            if pa["pin_bar_bear"] or pa["engulf_bear"] or pa["rejection_bear"]:
                pa_score = 1.0
        scores["price_action"] = pa_score

        return scores

    # ─── LEVELS ─────────────────────────────────────────────────────────────

    def _calc_levels(self, signal, price, atr, ms, obs, support, resistance):
        """Calculate entry, SL, TP1/2/3 and R:R."""
        if signal == "BUY":
            entry = price
            # SL below nearest swing low or 1.5×ATR
            sl_lvl = ms["swing_lows"][-1] if ms["swing_lows"] else price - 1.5 * atr
            sl     = min(sl_lvl, price - 1.5 * atr)
            sl     = min(sl, price - 0.5 * atr)   # at least 0.5 ATR away
        else:  # SELL
            entry = price
            sl_lvl = ms["swing_highs"][-1] if ms["swing_highs"] else price + 1.5 * atr
            sl     = max(sl_lvl, price + 1.5 * atr)
            sl     = max(sl, price + 0.5 * atr)

        sl_dist = abs(entry - sl)
        tp1 = entry + self.tp_multipliers[0] * sl_dist * (1 if signal == "BUY" else -1)
        tp2 = entry + self.tp_multipliers[1] * sl_dist * (1 if signal == "BUY" else -1)
        tp3 = entry + self.tp_multipliers[2] * sl_dist * (1 if signal == "BUY" else -1)
        rr  = self.tp_multipliers[1]   # use TP2 as headline R:R

        return entry, sl, tp1, tp2, tp3, rr

    # ─── REASON TEXT ─────────────────────────────────────────────────────────

    def _build_reason(self, signal, ms, liq, pa, obs, fvgs, pd_zone,
                      last, confidence, htf_trend) -> str:
        parts = []
        parts.append(f"Signal: {signal} | Confidence: {confidence:.1f}%")
        parts.append(f"Market Structure: {ms['trend'].upper()} | "
                     f"BOS: {'Yes' if ms['bos'] else 'No'} | "
                     f"CHOCH: {'Yes' if ms['choch'] else 'No'}")
        if liq["sweep"]:
            parts.append(f"Liquidity Sweep detected ({liq['direction']})")
        if obs:
            parts.append(f"{len(obs)} Order Block(s) identified near price")
        if fvgs:
            parts.append(f"{len(fvgs)} Fair Value Gap(s) present")
        parts.append(f"Zone: {pd_zone['zone'].upper()} "
                     f"(EQ: {pd_zone['equilibrium']:.5f})")
        rsi_val = last.get("rsi", "N/A")
        if not pd.isna(rsi_val):
            parts.append(f"RSI: {rsi_val:.1f}")
        pa_signals = [k for k, v in pa.items() if v]
        if pa_signals:
            parts.append("Price Action: " + ", ".join(pa_signals))
        parts.append(f"HTF Trend Context: {htf_trend.upper()}")
        return " | ".join(parts)
          
