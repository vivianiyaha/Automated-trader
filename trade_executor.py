"""
trade_executor.py - Orchestrates signal analysis, trade entry, and position management.
Runs as a background async loop that can be started / stopped from the Streamlit UI.
"""

import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional

from config import (HIGHER_TIMEFRAMES, EXECUTION_TIMEFRAMES,
                     ALL_PAIRS, PAIR_DISPLAY)
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
    Call start() to begin; stop() to halt gracefully.
    """

    SCAN_INTERVAL = 60   # seconds between full market scans

    def __init__(self, api: DerivAPI, risk_mgr: RiskManager,
                 selected_pairs: List[str] = None,
                 exec_timeframe: str = "M15",
                 stake_amount: float = 10.0):
        self.api          = api
        self.risk_mgr     = risk_mgr
        self.strategy     = StrategyEngine()
        self.pairs        = selected_pairs or ALL_PAIRS
        self.exec_tf      = exec_timeframe
        self.stake        = stake_amount
        self._running     = False
        self._task        = None
        self._last_signals: Dict[str, TradeSignal] = {}
        self._open_ids:     Dict[str, int]  = {}  # symbol → db row id
        self._contract_ids: Dict[str, int]  = {}  # symbol → deriv contract id

        init_db()

    # ─── CONTROL ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the async event loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log_event("info", "🚀 TradeExecutor started")

    def stop(self) -> None:
        """Signal the loop to stop after the current iteration."""
        self._running = False
        log_event("info", "🛑 TradeExecutor stop requested")

    async def close_all_trades(self) -> None:
        """Force-close all open positions on Deriv."""
        for symbol, contract_id in list(self._contract_ids.items()):
            await self._close_position(symbol, contract_id, reason="Manual close-all")

    # ─── MAIN LOOP ──────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Core async trading loop – runs until stop() is called."""
        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                log_error("Loop cycle error", e)
            await asyncio.sleep(self.SCAN_INTERVAL)

        log_event("info", "✅ TradeExecutor loop exited cleanly")

    async def _scan_cycle(self) -> None:
        """One full market scan cycle."""
        # Update balance
        bal_info = await self.api.get_balance()
        balance = float(bal_info.get("balance", 0))
        if balance:
            self.risk_mgr.update_balance(balance)
            insert_account_snapshot(balance, balance)

        # Monitor open positions first
        await self._monitor_positions()

        # Check trading is still allowed
        allowed, reason = self.risk_mgr.can_trade()
        if not allowed:
            log_event("warning", f"⏸ Trading paused: {reason}")
            return

        # Analyse each selected pair
        for symbol in self.pairs:
            if not self._running:
                break
            await self._analyse_and_trade(symbol)
            await asyncio.sleep(1)   # polite rate limiting

    # ─── ANALYSIS & ENTRY ───────────────────────────────────────────────────

    async def _analyse_and_trade(self, symbol: str) -> None:
        """Fetch data, generate signal, and enter trade if criteria met."""
        try:
            # Higher timeframe for trend bias
            htf_trend = await self._get_htf_trend(symbol)

            # Execution timeframe analysis
            gran = self.api.granularity(self.exec_tf)
            df   = await self.api.get_candles(symbol, gran)
            if df is None:
                return

            sig: TradeSignal = self.strategy.analyse(df, symbol, self.exec_tf, htf_trend)
            self._last_signals[symbol] = sig

            # Persist signal
            insert_signal(sig.to_dict())
            log_signal(PAIR_DISPLAY.get(symbol, symbol),
                       sig.signal, sig.confidence, sig.reason)

            # Trade entry conditions
            if sig.signal in ("BUY", "SELL") and symbol not in self._contract_ids:
                allowed, reason = self.risk_mgr.can_trade()
                if allowed:
                    await self._enter_trade(symbol, sig)

        except Exception as e:
            log_error(f"analyse_and_trade({symbol})", e)

    async def _enter_trade(self, symbol: str, sig: TradeSignal) -> None:
        """Place a contract on Deriv and record it."""
        sl_dist = abs(sig.entry - sig.sl) if sig.entry and sig.sl else 0.001
        lot     = self.risk_mgr.position_size(sl_dist)

        resp = await self.api.buy_contract(
            symbol=symbol,
            direction=sig.signal,
            amount=self.stake,
            duration=15,
            duration_unit="m",
        )

        if resp.get("error"):
            log_error(f"Entry failed for {symbol}: {resp['error']['message']}")
            return

        buy_info    = resp.get("buy", {})
        contract_id = buy_info.get("contract_id")
        entry_price = float(buy_info.get("start_price", sig.entry or 0))

        if not contract_id:
            log_error(f"No contract_id returned for {symbol}")
            return

        # Record
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
        self.risk_mgr.record_trade_open()

        log_trade_open(PAIR_DISPLAY.get(symbol, symbol),
                       sig.signal, entry_price, sig.sl or 0, lot)

    # ─── MONITORING & EXIT ──────────────────────────────────────────────────

    async def _monitor_positions(self) -> None:
        """Check every open contract for TP / SL / reversal exit."""
        for symbol, contract_id in list(self._contract_ids.items()):
            try:
                info = await self.api.get_contract_info(contract_id)
                status = info.get("status", "open")

                if status == "sold" or info.get("is_expired"):
                    profit = float(info.get("profit", 0))
                    exit_p = float(info.get("sell_price", info.get("entry_tick", 0)))
                    await self._finalise_trade(symbol, contract_id, exit_p, profit)
                    continue

                # CHOCH / reversal exit
                sig = self._last_signals.get(symbol)
                if sig:
                    new_sig = self._last_signals.get(symbol)
                    if new_sig and new_sig.signal not in (sig.signal, "NO TRADE"):
                        await self._close_position(symbol, contract_id,
                                                    reason="Opposite signal / CHOCH")
            except Exception as e:
                log_error(f"monitor_positions({symbol})", e)

    async def _close_position(self, symbol: str, contract_id: int,
                               reason: str = "") -> None:
        """Sell a contract at market."""
        resp = await self.api.sell_contract(contract_id, price=0)
        if resp.get("error"):
            log_error(f"Close failed ({symbol}): {resp['error']['message']}")
            return
        sell_info  = resp.get("sell", {})
        profit     = float(sell_info.get("profit", 0))
        exit_price = float(sell_info.get("price", 0))
        await self._finalise_trade(symbol, contract_id, exit_price, profit,
                                    reason=reason)

    async def _finalise_trade(self, symbol: str, contract_id: int,
                               exit_price: float, profit: float,
                               reason: str = "") -> None:
        """Update DB, risk manager, and logs after a position closes."""
        row_id = self._open_ids.pop(symbol, None)
        self._contract_ids.pop(symbol, None)

        if row_id:
            close_trade(row_id, exit_price, profit)

        self.risk_mgr.record_trade_close(profit)
        upsert_daily_summary(
            datetime.utcnow().strftime("%Y-%m-%d"),
            win=profit >= 0,
            pnl=profit
        )
        log_trade_close(PAIR_DISPLAY.get(symbol, symbol), exit_price, profit)

    # ─── HIGHER TIMEFRAME TREND ──────────────────────────────────────────────

    async def _get_htf_trend(self, symbol: str, tf: str = "H1") -> str:
        """Quickly determine HTF trend for context."""
        try:
            gran = self.api.granularity(tf)
            df   = await self.api.get_candles(symbol, gran, count=50)
            if df is None:
                return "ranging"
            from indicators import add_emas, detect_market_structure
            df = add_emas(df)
            ms = detect_market_structure(df)
            return ms["trend"]
        except Exception:
            return "ranging"

    # ─── ACCESSORS ──────────────────────────────────────────────────────────

    @property
    def last_signals(self) -> Dict[str, TradeSignal]:
        return dict(self._last_signals)

    @property
    def is_running(self) -> bool:
        return self._running
      
