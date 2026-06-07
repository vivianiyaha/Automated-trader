"""
deriv_api.py — Async WebSocket client for the Deriv Binary.com API.
Handles authentication, candle streaming, trade placement, and account monitoring.
"""

import asyncio
import json
import time
import threading
from datetime import datetime
from typing import Optional, Callable

import websocket

from config import DERIV_WS_URL, TIMEFRAMES, CANDLE_COUNT
from utils import candles_to_df
import logger

import pandas as pd


class DerivAPI:
    """
    Thread-safe Deriv WebSocket client.

    Usage:
        api = DerivAPI(token="YOUR_TOKEN")
        api.connect()                        # blocking; run in thread
        candles = api.get_candles("frxAUDUSD", "M15")
        contract_id = api.buy_contract(...)
        api.disconnect()
    """

    def __init__(self, token: str):
        self.token        = token
        self._ws          = None
        self._connected   = False
        self._pending     = {}          # req_id → Event + result store
        self._req_id      = 1
        self._lock        = threading.Lock()
        self.account_info = {}
        self.balance      = 0.0
        self.currency     = "USD"
        self._on_tick: Optional[Callable] = None

    # ── Connection ─────────────────────────────────────────

    def connect(self) -> bool:
        """Open WebSocket and authenticate. Returns True on success."""
        try:
            self._ws = websocket.WebSocketApp(
                DERIV_WS_URL,
                on_open    = self._on_open,
                on_message = self._on_message,
                on_error   = self._on_error,
                on_close   = self._on_close,
            )
            t = threading.Thread(target=self._ws.run_forever, daemon=True)
            t.start()

            # Wait for connection
            for _ in range(20):
                if self._connected:
                    break
                time.sleep(0.5)

            if not self._connected:
                logger.error("DerivAPI: connection timeout")
                return False

            return self._authorize()
        except Exception as e:
            logger.error(f"DerivAPI connect error: {e}")
            return False

    def disconnect(self) -> None:
        if self._ws:
            self._ws.close()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Auth ───────────────────────────────────────────────

    def _authorize(self) -> bool:
        resp = self._send_sync({"authorize": self.token})
        if resp and "authorize" in resp:
            info = resp["authorize"]
            self.account_info = info
            self.balance      = float(info.get("balance", 0))
            self.currency     = info.get("currency", "USD")
            logger.info(f"Authorized — account: {info.get('loginid')} | "
                        f"balance: {self.balance} {self.currency}")
            return True
        logger.error(f"Authorization failed: {resp}")
        return False

    def refresh_balance(self) -> float:
        """Fetch latest balance from API."""
        resp = self._send_sync({"balance": 1, "subscribe": 0})
        if resp and "balance" in resp:
            self.balance = float(resp["balance"]["balance"])
        return self.balance

    # ── Market Data ────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: str,
                    count: int = CANDLE_COUNT) -> pd.DataFrame:
        """
        Fetch historical OHLC candles for a symbol / timeframe.
        Returns a DataFrame or empty DataFrame on failure.
        """
        granularity = TIMEFRAMES.get(timeframe, 900)
        end         = int(time.time())
        start       = end - granularity * count

        req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count":        count,
            "end":          "latest",
            "start":        start,
            "style":        "candles",
            "granularity":  granularity,
        }
        resp = self._send_sync(req, timeout=15)
        if resp and "candles" in resp:
            return candles_to_df(resp["candles"])
        logger.warn(f"get_candles failed for {symbol}/{timeframe}: {resp}")
        return pd.DataFrame()

    def get_tick(self, symbol: str) -> Optional[float]:
        """Fetch the latest spot price."""
        resp = self._send_sync({"ticks": symbol, "subscribe": 0})
        if resp and "tick" in resp:
            return float(resp["tick"]["quote"])
        return None

    def get_active_symbols(self) -> list:
        """Return list of active tradeable symbols."""
        resp = self._send_sync({"active_symbols": "brief", "product_type": "basic"})
        if resp and "active_symbols" in resp:
            return resp["active_symbols"]
        return []

    # ── Trade Execution ────────────────────────────────────

    def buy_contract(self, symbol: str, direction: str,
                     duration: int, duration_unit: str,
                     stake: float, basis: str = "stake") -> Optional[dict]:
        """
        Open a RISE (BUY) or FALL (SELL) binary contract.

        Args:
            symbol        — e.g. 'frxAUDUSD'
            direction     — 'BUY' | 'SELL'
            duration      — contract duration integer
            duration_unit — 't'|'s'|'m'|'h'|'d'
            stake         — amount to stake
            basis         — 'stake' | 'payout'

        Returns contract dict or None.
        """
        contract_type = "CALL" if direction == "BUY" else "PUT"

        # Step 1: get a price proposal
        prop_req = {
            "proposal": 1,
            "amount":   stake,
            "basis":    basis,
            "contract_type": contract_type,
            "currency": self.currency,
            "duration": duration,
            "duration_unit": duration_unit,
            "symbol":   symbol,
        }
        prop = self._send_sync(prop_req, timeout=10)
        if not prop or "proposal" not in prop:
            logger.error(f"Proposal failed: {prop}")
            return None

        proposal_id = prop["proposal"]["id"]

        # Step 2: buy the proposal
        buy_req = {"buy": proposal_id, "price": stake}
        resp    = self._send_sync(buy_req, timeout=10)
        if resp and "buy" in resp:
            contract = resp["buy"]
            logger.trade(f"Contract opened: {contract.get('contract_id')} "
                         f"| {direction} {symbol} stake={stake}")
            return contract
        logger.error(f"Buy failed: {resp}")
        return None

    def sell_contract(self, contract_id: str, price: float = 0) -> Optional[dict]:
        """Early-sell an open contract."""
        resp = self._send_sync({"sell": contract_id, "price": price})
        if resp and "sell" in resp:
            logger.trade(f"Contract {contract_id} sold at {resp['sell'].get('sold_for')}")
            return resp["sell"]
        logger.error(f"Sell failed for {contract_id}: {resp}")
        return None

    def get_open_contracts(self) -> list:
        """Return a list of all currently open contracts."""
        resp = self._send_sync({"portfolio": 1})
        if resp and "portfolio" in resp:
            return resp["portfolio"].get("contracts", [])
        return []

    def get_profit_table(self, limit: int = 50) -> list:
        """Return a recent profit/loss history."""
        resp = self._send_sync({
            "profit_table": 1,
            "description":  1,
            "limit":        limit,
            "sort":         "DESC",
        })
        if resp and "profit_table" in resp:
            return resp["profit_table"].get("transactions", [])
        return []

    def check_server_time(self) -> int:
        """Return Deriv server Unix timestamp."""
        resp = self._send_sync({"time": 1})
        if resp and "time" in resp:
            return resp["time"]
        return int(time.time())

    # ── Internal Messaging ─────────────────────────────────

    def _next_id(self) -> int:
        with self._lock:
            rid = self._req_id
            self._req_id += 1
        return rid

    def _send_sync(self, payload: dict, timeout: float = 8.0) -> Optional[dict]:
        """Send a request and block until response or timeout."""
        if not self._connected:
            return None

        rid  = self._next_id()
        evt  = threading.Event()
        self._pending[rid] = {"event": evt, "result": None}
        payload["req_id"] = rid

        try:
            self._ws.send(json.dumps(payload))
        except Exception as e:
            logger.error(f"WS send error: {e}")
            self._pending.pop(rid, None)
            return None

        evt.wait(timeout=timeout)
        entry = self._pending.pop(rid, {})
        return entry.get("result")

    # ── WebSocket Callbacks ────────────────────────────────

    def _on_open(self, ws) -> None:
        self._connected = True
        logger.info("DerivAPI WebSocket connected")

    def _on_message(self, ws, raw: str) -> None:
        try:
            msg = json.loads(raw)
            rid = msg.get("req_id")
            if rid and rid in self._pending:
                self._pending[rid]["result"] = msg
                self._pending[rid]["event"].set()
            elif "tick" in msg and self._on_tick:
                self._on_tick(msg["tick"])
        except Exception as e:
            logger.error(f"WS message parse error: {e}")

    def _on_error(self, ws, err) -> None:
        logger.error(f"DerivAPI WS error: {err}")
        self._connected = False

    def _on_close(self, ws, code, msg) -> None:
        self._connected = False
        logger.warn(f"DerivAPI WS closed (code={code})")


# ── Mock / Demo mode ──────────────────────────────────────

class MockDerivAPI:
    """
    Offline simulation API for testing without a real Deriv token.
    Generates synthetic OHLCV data and simulates trade outcomes.
    """

    def __init__(self):
        self.is_connected = True
        self.balance      = 10_000.0
        self.currency     = "USD"
        self.account_info = {"loginid": "DEMO123", "balance": self.balance}
        self._contracts   = {}
        self._cid         = 1000

    def connect(self) -> bool:
        logger.info("MockAPI: running in DEMO mode")
        return True

    def disconnect(self) -> None:
        pass

    def refresh_balance(self) -> float:
        return self.balance

    def get_candles(self, symbol: str, timeframe: str,
                    count: int = CANDLE_COUNT) -> pd.DataFrame:
        """Generate synthetic OHLCV candles."""
        import numpy as np
        np.random.seed(abs(hash(symbol + timeframe)) % 10_000)
        base  = 1.1000 if "USD" in symbol else 0.6500
        close = base + np.cumsum(np.random.randn(count) * 0.0003)
        open_ = np.roll(close, 1);  open_[0] = close[0]
        high  = np.maximum(open_, close) + np.abs(np.random.randn(count)) * 0.0002
        low   = np.minimum(open_, close) - np.abs(np.random.randn(count)) * 0.0002
        vol   = np.random.randint(100, 2000, size=count).astype(float)
        now   = int(time.time())
        gran  = TIMEFRAMES.get(timeframe, 900)
        times = [now - (count - i) * gran for i in range(count)]

        return pd.DataFrame({
            "time": times, "open": open_, "high": high,
            "low": low, "close": close, "volume": vol,
            "datetime": pd.to_datetime(times, unit="s", utc=True),
        })

    def get_tick(self, symbol: str) -> float:
        import numpy as np
        return round(1.10 + np.random.randn() * 0.001, 5)

    def buy_contract(self, symbol, direction, duration,
                     duration_unit, stake, basis="stake") -> dict:
        cid = str(self._cid); self._cid += 1
        contract = {
            "contract_id":    cid,
            "symbol":         symbol,
            "direction":      direction,
            "stake":          stake,
            "entry_spot":     self.get_tick(symbol),
            "buy_price":      stake,
            "transaction_id": cid,
        }
        self._contracts[cid] = contract
        self.balance -= stake
        logger.trade(f"[DEMO] Contract {cid} opened: {direction} {symbol} ${stake}")
        return contract

    def sell_contract(self, contract_id, price=0) -> dict:
        import numpy as np
        contract = self._contracts.pop(contract_id, {})
        pnl      = round(np.random.uniform(-1, 2) * contract.get("stake", 10), 2)
        self.balance += contract.get("stake", 10) + pnl
        logger.trade(f"[DEMO] Contract {contract_id} closed — P&L: {pnl:+.2f}")
        return {"sold_for": contract.get("stake", 10) + pnl, "pnl": pnl}

    def get_open_contracts(self) -> list:
        return list(self._contracts.values())

    def get_profit_table(self, limit=50) -> list:
        return []

    def check_server_time(self) -> int:
        return int(time.time())

    def get_active_symbols(self) -> list:
        return []
