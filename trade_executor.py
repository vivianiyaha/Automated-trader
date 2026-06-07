"""
trade_executor.py — Orchestrates the full trading loop:
  connect → scan → signal → execute → monitor → close.
"""

import time
import threading
from datetime import datetime
from typing import Optional

from deriv_api import DerivAPI, MockDerivAPI
from strategy  import analyse_market
from risk_manager import RiskManager
from database import (save_signal, save_trade, close_trade,
                      get_open_trades, save_account_snapshot)
from utils import display_name, now_utc, round_price
from config import SCAN_INTERVAL, HIGHER_TF, EXEC_TF, TIMEFRAMES
import logger


class TradeExecutor:
    """
    Main bot controller.  Designed to run its loop in a background thread
    so that Streamlit can keep rendering while the bot operates.

    State is stored in:
      - SQLite (via database.py)
      - In-memory signal/trade lists for fast Streamlit display
    """

    def __init__(self, api_token: str, demo_mode: bool,
                 risk_manager: RiskManager,
                 selected_pairs: list[str],
                 exec_timeframe: str = "M15"):

        self.api_token      = api_token
        self.demo_mode      = demo_mode
        self.rm             = risk_manager
        self.pairs          = selected_pairs
        self.exec_tf        = exec_timeframe

        self.api: Optional[DerivAPI] = None
        self._running       = False
        self._thread        = None

        # In-memory caches for Streamlit display
        self.last_signals:  list = []
        self.active_trades: dict = {}    # contract_id → trade dict

    # ── Lifecycle ──────────────────────────────────────────

    def start(self) -> bool:
        """Connect to API and begin the scan/trade loop."""
        if self._running:
            return True

        self.api = MockDerivAPI() if self.demo_mode else DerivAPI(self.api_token)
        ok = self.api.connect()
        if not ok:
            logger.error("TradeExecutor: API connection failed")
            return False

        # Sync balance
        balance = self.api.refresh_balance()
        self.rm.update_balance(balance)
        save_account_snapshot(balance, balance)

        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"TradeExecutor started — {'DEMO' if self.demo_mode else 'LIVE'} mode")
        return True

    def stop(self) -> None:
        """Stop the loop gracefully."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("TradeExecutor stopped")

    def close_all(self) -> int:
        """Emergency close-all: sell every open contract."""
        closed = 0
        for cid, trade in list(self.active_trades.items()):
            result = self.api.sell_contract(cid)
            if result:
                pnl = result.get("pnl", 0.0)
                close_trade(trade["db_id"], result.get("sold_for", 0),
                            "Manual Close All", pnl)
                self.rm.record_trade_pnl(pnl)
                self.active_trades.pop(cid, None)
                closed += 1
        logger.trade(f"Close-all complete: {closed} contracts closed")
        return closed

    # ── Main Loop ──────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._scan_and_trade()
                self._monitor_open_trades()
                self._sync_balance()
            except Exception as e:
                logger.error(f"Loop error: {e}")
            time.sleep(SCAN_INTERVAL)

    def _scan_and_trade(self) -> None:
        """Run analysis on every selected pair and open trades if conditions are met."""
        open_count = len(self.active_trades)
        signals    = []

        for symbol in self.pairs:
            if not self._running:
                break
            try:
                sig = self._analyse_symbol(symbol)
                signals.append(sig)

                if sig["signal"] in ("BUY", "SELL"):
                    save_signal({
                        "timestamp":    sig["timestamp"],
                        "symbol":       symbol,
                        "timeframe":    self.exec_tf,
                        "signal":       sig["signal"],
                        "entry":        sig["entry"],
                        "stop_loss":    sig["stop_loss"],
                        "tp1":          sig["tp1"],
                        "tp2":          sig["tp2"],
                        "tp3":          sig["tp3"],
                        "confidence":   sig["confidence"],
                        "trend":        sig["trend"],
                        "trade_reason": sig["trade_reason"],
                        "rr_ratio":     sig["rr_ratio"],
                    })

                    allowed, reason = self.rm.can_open_trade(open_count)
                    if allowed:
                        self._execute_trade(sig)
                        open_count += 1
                    else:
                        logger.warn(f"Trade skipped [{symbol}]: {reason}")

            except Exception as e:
                logger.error(f"Analysis error [{symbol}]: {e}")

        self.last_signals = signals

    def _analyse_symbol(self, symbol: str) -> dict:
        """Fetch candles for exec + HTF and run strategy analysis."""
        df_exec = self.api.get_candles(symbol, self.exec_tf)
        df_h1   = self.api.get_candles(symbol, "H1",  count=200)
        df_h4   = self.api.get_candles(symbol, "H4",  count=100)
        return analyse_market(symbol, self.exec_tf, df_exec, df_h1, df_h4)

    def _execute_trade(self, sig: dict) -> None:
        """Place a contract based on a confirmed signal."""
        symbol    = sig["symbol"]
        direction = sig["signal"]
        entry     = sig["entry"] or 0.0
        sl        = sig["stop_loss"] or 0.0
        stake     = self.rm.stake_amount(entry, sl, symbol)

        # Duration: 15 minutes for M15 exec timeframe
        tf_secs   = TIMEFRAMES.get(self.exec_tf, 900)
        duration  = max(1, tf_secs // 60)   # in minutes

        contract = self.api.buy_contract(
            symbol        = symbol,
            direction     = direction,
            duration      = duration,
            duration_unit = "m",
            stake         = stake,
        )

        if not contract:
            logger.error(f"Trade execution failed for {symbol}")
            return

        cid = str(contract.get("contract_id", ""))
        trade_data = {
            "contract_id":  cid,
            "timestamp":    now_utc(),
            "symbol":       symbol,
            "direction":    direction,
            "entry_price":  entry,
            "stop_loss":    sl,
            "tp1":          sig["tp1"],
            "tp2":          sig["tp2"],
            "tp3":          sig["tp3"],
            "lot_size":     self.rm.lot_size,
            "confidence":   sig["confidence"],
            "trade_reason": sig["trade_reason"],
        }

        db_id = save_trade(trade_data)
        trade_data["db_id"] = db_id
        self.active_trades[cid] = trade_data

        logger.trade(f"TRADE OPENED | {direction} {display_name(symbol)} "
                     f"@ {entry:.5f} | SL {sl:.5f} | Stake ${stake:.2f} | "
                     f"Conf {sig['confidence']:.0f}%")

        logger.log_trade_csv({
            "time":      now_utc(),
            "event":     "OPEN",
            "symbol":    display_name(symbol),
            "direction": direction,
            "entry":     entry,
            "sl":        sl,
            "tp1":       sig["tp1"],
            "stake":     stake,
            "confidence":sig["confidence"],
        })

    # ── Trade Monitoring ───────────────────────────────────

    def _monitor_open_trades(self) -> None:
        """Check open contracts and close if TP / SL / reversal conditions met."""
        for cid, trade in list(self.active_trades.items()):
            try:
                tick = self.api.get_tick(trade["symbol"])
                if tick is None:
                    continue

                self._check_exit_conditions(cid, trade, tick)
            except Exception as e:
                logger.error(f"Monitor error [{cid}]: {e}")

    def _check_exit_conditions(self, cid: str, trade: dict, current_price: float) -> None:
        """Evaluate SL / TP / reversal exit rules."""
        direction = trade["direction"]
        entry     = trade["entry_price"]
        sl        = trade["stop_loss"]
        tp1       = trade["tp1"]
        tp3       = trade["tp3"]

        exit_reason = None

        if direction == "BUY":
            if current_price <= sl:
                exit_reason = "Stop Loss Hit"
            elif tp3 and current_price >= tp3:
                exit_reason = "TP3 Hit"
            elif tp1 and current_price >= tp1:
                exit_reason = "TP1 Hit"
        else:  # SELL
            if current_price >= sl:
                exit_reason = "Stop Loss Hit"
            elif tp3 and current_price <= tp3:
                exit_reason = "TP3 Hit"
            elif tp1 and current_price <= tp1:
                exit_reason = "TP1 Hit"

        if exit_reason:
            self._close_trade(cid, trade, current_price, exit_reason)

    def _close_trade(self, cid: str, trade: dict,
                     exit_price: float, reason: str) -> None:
        """Execute close and persist results."""
        result = self.api.sell_contract(cid)
        pnl    = 0.0
        if result:
            pnl = float(result.get("pnl", 0.0))
        else:
            # Estimate PnL from price movement
            if trade["direction"] == "BUY":
                pnl = (exit_price - trade["entry_price"]) * trade["lot_size"] * 100_000
            else:
                pnl = (trade["entry_price"] - exit_price) * trade["lot_size"] * 100_000
            pnl = round(pnl, 2)

        close_trade(trade["db_id"], exit_price, reason, pnl)
        self.rm.record_trade_pnl(pnl)
        self.active_trades.pop(cid, None)

        emoji = "🟢" if pnl > 0 else "🔴"
        logger.trade(f"TRADE CLOSED | {reason} | "
                     f"{display_name(trade['symbol'])} "
                     f"@ {exit_price:.5f} | P&L {emoji} {pnl:+.2f}")

        logger.log_trade_csv({
            "time":      now_utc(),
            "event":     "CLOSE",
            "symbol":    display_name(trade["symbol"]),
            "direction": trade["direction"],
            "exit":      exit_price,
            "pnl":       pnl,
            "reason":    reason,
        })

    # ── Balance Sync ──────────────────────────────────────

    def _sync_balance(self) -> None:
        balance = self.api.refresh_balance()
        self.rm.update_balance(balance)
        save_account_snapshot(balance, balance)

    # ── State for Dashboard ────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    def get_open_trades_list(self) -> list:
        return list(self.active_trades.values())

    def get_last_signals(self) -> list:
        return self.last_signals
