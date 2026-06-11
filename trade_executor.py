"""
trade_executor.py - Trading loop with connection-aware scan cycle.

v2 changes:
  - Waits for api.is_alive() before each scan; skips cycle if not connected
  - Longer inter-symbol delay to avoid Deriv rate limits
  - HTF candle fetch failures fall back to 'ranging' without crashing
  - Proper buy_proposal flow: get proposal first, then buy
  - Smarter CHOCH exit: compares signal direction, not object identity
  - Reconnect-aware monitor loop
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional

from config import ALL_PAIRS, PAIR_DISPLAY
from deriv_api import DerivAPI
from strategy import StrategyEngine, TradeSignal
from risk_manager import RiskManager, RiskSettings
from database import (init_db, insert_signal, insert_trade, close_trade,
                       get_open_trades, insert_account_snapshot,
                       upsert_daily_summary)
from logger import (log_event, log_signal, log_trade_open,
                     log_trade_close, log_error)


class TradeExecutor:
    """
    Main trading loop.
    Call start() in an async context; stop() to halt gracefully.
    """

    SCAN_INTERVAL     = 60    # seconds between full market scans
    SYMBOL_DELAY      = 2     # seconds between symbols (rate-limit friendly)
    MAX_WAIT_CONNECTED = 60   # seconds to wait for connection before skipping cycle

    def __init__(self, api: DerivAPI, risk_mgr: RiskManager,
                 selected_pairs: List[str] = None,
                 exec_timeframe: str = "M15",
                 stake_amount: float = 10.0):
        self.api      = api
        self.risk_mgr = risk_mgr
        self.strategy = StrategyEngine()
        self.pairs    = selected_pairs or ALL_PAIRS
        self.exec_tf  = exec_timeframe
        self.stake    = stake_amount

        self._running = False
        self._last_signals:  Dict[str, TradeSignal] = {}
        self._open_ids:      Dict[str, int] = {}   # symbol → db row id
        self._contract_ids:  Dict[str, int] = {}   # symbol → deriv contract id
        self._prev_direction: Dict[str, str] = {}  # symbol → last signal direction

        init_db()

    # ════════════════════════════════════════════════════════════════════════
    # CONTROL
    # ════════════════════════════════════════════════════════════════════════

    async def run(self) -> None:
        """
        Main entry point — call this as an asyncio task.
        Runs until stop() is called.
        """
        self._running = True
        log_event("info", "🚀 TradeExecutor started")

        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                log_error("Loop cycle error", e)
            # Wait between scans (interruptible)
            for _ in range(self.SCAN_INTERVAL):
                if not self._running:
                    break
                await asyncio.sleep(1)

        log_event("info", "✅ TradeExecutor loop exited cleanly")

    def stop(self) -> None:
        """Signal the loop to stop after the current cycle."""
        self._running = False
        log_event("info", "🛑 TradeExecutor stop requested")

    async def close_all_trades(self) -> None:
        """Force-close every open Deriv position."""
        for symbol, contract_id in list(self._contract_ids.items()):
            await self._close_position(symbol, contract_id, reason="Manual close-all")

    # ════════════════════════════════════════════════════════════════════════
    # SCAN CYCLE
    # ════════════════════════════════════════════════════════════════════════

    async def _scan_cycle(self) -> None:
        """One full market scan — balance update → monitor → analyse."""

        # ── Wait until connected ───────────────────────────────────────────
        waited = 0
        while not self.api.is_alive():
            if waited >= self.MAX_WAIT_CONNECTED:
                log_event("warning", "⚠️  Not connected — skipping scan cycle")
                return
            if waited == 0:
                log_event("info", "⏳ Waiting for Deriv connection…")
            await asyncio.sleep(2)
            waited += 2

        # ── Update balance ─────────────────────────────────────────────────
        try:
            bal_info = await self.api.get_balance()
            balance  = float(bal_info.get("balance", 0))
            if balance:
                self.risk_mgr.update_balance(balance)
                insert_account_snapshot(balance, balance)
        except Exception as e:
            log_error("Balance update failed", e)

        # ── Monitor open positions ─────────────────────────────────────────
        await self._monitor_positions()

        # ── Gate: are we allowed to open new trades? ───────────────────────
        allowed, reason = self.risk_mgr.can_trade()
        if not allowed:
            log_event("warning", f"⏸ Trading paused: {reason}")
            return

        # ── Analyse each pair ──────────────────────────────────────────────
        for symbol in self.pairs:
            if not self._running:
                break
            if not self.api.is_alive():
                log_event("warning", "⚡ Connection lost mid-scan — pausing")
                break
            await self._analyse_and_trade(symbol)
            await asyncio.sleep(self.SYMBOL_DELAY)

    # ════════════════════════════════════════════════════════════════════════
    # ANALYSIS & ENTRY
    # ════════════════════════════════════════════════════════════════════════

    async def _analyse_and_trade(self, symbol: str) -> None:
        """Fetch candles, generate signal, enter trade if conditions are met."""
        try:
            # Higher-timeframe trend (non-critical — safe fallback)
            htf_trend = await self._get_htf_trend(symbol)

            # Execution timeframe candles
            gran = self.api.granularity(self.exec_tf)
            df   = await self.api.get_candles(symbol, gran)
            if df is None or df.empty:
                log_event("warning", f"⚠️  No candle data for {symbol} — skipping")
                return

            # AI analysis
            sig: TradeSignal = self.strategy.analyse(df, symbol, self.exec_tf, htf_trend)
            self._last_signals[symbol] = sig

            # Persist
            insert_signal(sig.to_dict())
            log_signal(PAIR_DISPLAY.get(symbol, symbol),
                       sig.signal, sig.confidence, sig.reason)

            if sig.warning:
                log_event("warning", sig.warning)

            # Only enter if: actionable signal, not already in a trade for this pair
            if sig.signal in ("BUY", "SELL") and symbol not in self._contract_ids:
                allowed, deny_reason = self.risk_mgr.can_trade()
                if allowed:
                    await self._enter_trade(symbol, sig)
                else:
                    log_event("info", f"🚫 {PAIR_DISPLAY.get(symbol, symbol)}: {deny_reason}")

        except Exception as e:
            log_error(f"analyse_and_trade({symbol})", e)

    async def _enter_trade(self, symbol: str, sig: TradeSignal) -> None:
        """Place a contract and persist it."""
        sl_dist = abs((sig.entry or 0) - (sig.sl or 0))
        if sl_dist == 0:
            sl_dist = 0.001
        lot = self.risk_mgr.position_size(sl_dist)

        log_event("info",
                  f"📤 Placing {sig.signal} on {PAIR_DISPLAY.get(symbol, symbol)} | "
                  f"stake={self.stake} | confidence={sig.confidence:.1f}%")

        resp = await self.api.buy_contract(
            symbol=symbol,
            direction=sig.signal,
            amount=self.stake,
            duration=15,
            duration_unit="m",
        )

        if resp.get("error"):
            log_error(f"Entry failed ({symbol})",
                      Exception(resp["error"].get("message", "unknown")))
            return

        buy_info    = resp.get("buy", {})
        contract_id = buy_info.get("contract_id")
        entry_price = float(buy_info.get("start_price", sig.entry or 0))

        if not contract_id:
            log_error(f"No contract_id returned for {symbol} — "
                      f"raw response: {resp}")
            return

        # Record in DB
        row_id = insert_trade({
            "contract_id": str(contract_id),
            "ts_open":     datetime.utcnow().isoformat(),
            "symbol":      symbol,
            "direction":   sig.signal,
            "lot_size":    lot,
            "entry":       entry_price,
            "sl":          sig.sl,
            "tp1":         sig.tp1,
            "tp2":         sig.tp2,
            "tp3":         sig.tp3,
            "status":      "OPEN",
            "confidence":  sig.confidence,
            "reason":      sig.reason,
        })

        self._open_ids[symbol]    = row_id
        self._contract_ids[symbol] = contract_id
        self._prev_direction[symbol] = sig.signal
        self.risk_mgr.record_trade_open()

        log_trade_open(PAIR_DISPLAY.get(symbol, symbol),
                       sig.signal, entry_price, sig.sl or 0, lot)

    # ════════════════════════════════════════════════════════════════════════
    # MONITORING & EXIT
    # ════════════════════════════════════════════════════════════════════════

    async def _monitor_positions(self) -> None:
        """Check every open contract for expiry, TP/SL hit, or CHOCH exit."""
        for symbol, contract_id in list(self._contract_ids.items()):
            if not self.api.is_alive():
                break
            try:
                info   = await self.api.get_contract_info(contract_id)
                status = info.get("status", "open")

                # Contract settled on Deriv's side
                if status in ("sold", "won", "lost") or info.get("is_expired"):
                    profit = float(info.get("profit", 0))
                    exit_p = float(info.get("sell_price",
                                             info.get("exit_tick_display_value", 0)) or 0)
                    await self._finalise_trade(symbol, contract_id, exit_p, profit)
                    continue

                # CHOCH exit: current signal opposes the direction we entered
                prev_dir = self._prev_direction.get(symbol)
                new_sig  = self._last_signals.get(symbol)
                if (prev_dir and new_sig
                        and new_sig.signal != "NO TRADE"
                        and new_sig.signal != prev_dir):
                    log_event("info",
                              f"↩️  CHOCH detected on "
                              f"{PAIR_DISPLAY.get(symbol, symbol)} — closing")
                    await self._close_position(symbol, contract_id,
                                               reason="CHOCH / signal reversal")

            except Exception as e:
                log_error(f"monitor_positions({symbol})", e)

    async def _close_position(self, symbol: str, contract_id: int,
                               reason: str = "") -> None:
        """Market-sell a contract."""
        log_event("info",
                  f"🔴 Closing {PAIR_DISPLAY.get(symbol, symbol)} | reason: {reason}")
        resp = await self.api.sell_contract(contract_id, price=0)
        if resp.get("error"):
            log_error(f"Close failed ({symbol})",
                      Exception(resp["error"].get("message", "")))
            return
        sell_info  = resp.get("sell", {})
        profit     = float(sell_info.get("profit", 0))
        exit_price = float(sell_info.get("price", 0))
        await self._finalise_trade(symbol, contract_id, exit_price, profit,
                                    reason=reason)

    async def _finalise_trade(self, symbol: str, contract_id: int,
                               exit_price: float, profit: float,
                               reason: str = "") -> None:
        """Post-trade bookkeeping."""
        row_id = self._open_ids.pop(symbol, None)
        self._contract_ids.pop(symbol, None)
        self._prev_direction.pop(symbol, None)

        if row_id:
            close_trade(row_id, exit_price, profit)

        self.risk_mgr.record_trade_close(profit)
        upsert_daily_summary(
            datetime.utcnow().strftime("%Y-%m-%d"),
            win=profit >= 0,
            pnl=profit
        )
        log_trade_close(PAIR_DISPLAY.get(symbol, symbol), exit_price, profit)

    # ════════════════════════════════════════════════════════════════════════
    # HTF TREND
    # ════════════════════════════════════════════════════════════════════════

    async def _get_htf_trend(self, symbol: str, tf: str = "H1") -> str:
        """
        Fetch H1 candles and return trend string.
        Returns 'ranging' on any failure (non-critical).
        """
        try:
            gran = self.api.granularity(tf)
            df   = await self.api.get_candles(symbol, gran, count=50)
            if df is None or df.empty:
                return "ranging"
            from indicators import add_emas, detect_market_structure
            df = add_emas(df)
            ms = detect_market_structure(df)
            return ms["trend"]
        except Exception:
            return "ranging"

    # ════════════════════════════════════════════════════════════════════════
    # ACCESSORS
    # ════════════════════════════════════════════════════════════════════════

    @property
    def last_signals(self) -> Dict[str, TradeSignal]:
        return dict(self._last_signals)

    @property
    def is_running(self) -> bool:
        return self._running
                              
