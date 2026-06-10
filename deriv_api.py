"""
deriv_api.py - Async Deriv WebSocket API wrapper.
Handles authentication, candle data, contract execution, and position monitoring.
"""

import json
import asyncio
import websockets
import pandas as pd
from datetime import datetime
from typing import Optional, Callable, Dict
from logger import log_error, log_event
from config import DERIV_WS_URL, DERIV_APP_ID, TIMEFRAMES, LOOKBACK_CANDLES


class DerivAPI:
    """
    Async WebSocket client for Deriv.
    Usage:
        api = DerivAPI(token="YOUR_TOKEN")
        await api.connect()
        balance = await api.get_balance()
    """

    def __init__(self, token: str = "", app_id: str = DERIV_APP_ID):
        self.token      = token
        self.app_id     = app_id
        self.ws         = None
        self._req_id    = 0
        self._pending:  Dict[int, asyncio.Future] = {}
        self._listener_task = None
        self.connected  = False
        self.account_info: dict = {}

    # ─── CONNECTION ─────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect and authenticate. Returns True on success."""
        try:
            url = f"{DERIV_WS_URL}?app_id={self.app_id}"
            self.ws = await websockets.connect(url, ping_interval=30, ping_timeout=10)
            self._listener_task = asyncio.create_task(self._listen())
            self.connected = True
            log_event("info", "✅ WebSocket connected to Deriv")

            if self.token:
                auth = await self._send({"authorize": self.token})
                if auth.get("error"):
                    log_error("Auth failed", Exception(auth["error"]["message"]))
                    return False
                self.account_info = auth.get("authorize", {})
                log_event("info", f"✅ Authenticated: {self.account_info.get('email', 'unknown')}")
            return True

        except Exception as e:
            log_error("Connection failed", e)
            self.connected = False
            return False

    async def disconnect(self) -> None:
        """Cleanly close the WebSocket."""
        self.connected = False
        if self._listener_task:
            self._listener_task.cancel()
        if self.ws:
            await self.ws.close()
        log_event("info", "🔌 Disconnected from Deriv")

    # ─── MESSAGING ──────────────────────────────────────────────────────────

    async def _send(self, payload: dict, timeout: float = 15.0) -> dict:
        """Send a request and await the matching response."""
        self._req_id += 1
        req_id = self._req_id
        payload["req_id"] = req_id

        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        try:
            await self.ws.send(json.dumps(payload))
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            log_error(f"Request timeout: {list(payload.keys())}")
            return {"error": {"message": "timeout"}}
        except Exception as e:
            self._pending.pop(req_id, None)
            log_error("Send error", e)
            return {"error": {"message": str(e)}}

    async def _listen(self) -> None:
        """Background task – routes incoming messages to waiting futures."""
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                    req_id = msg.get("req_id")
                    if req_id and req_id in self._pending:
                        fut = self._pending.pop(req_id)
                        if not fut.done():
                            fut.set_result(msg)
                except Exception as e:
                    log_error("Message parse error", e)
        except Exception as e:
            if self.connected:
                log_error("WebSocket listener crashed", e)
                self.connected = False

    # ─── ACCOUNT ────────────────────────────────────────────────────────────

    async def get_balance(self) -> dict:
        """Return balance dict with balance, currency, loginid."""
        resp = await self._send({"balance": 1, "account": "current"})
        return resp.get("balance", {})

    async def get_open_contracts(self) -> list:
        """Return list of open contract dicts."""
        resp = await self._send({"portfolio": 1})
        return resp.get("portfolio", {}).get("contracts", [])

    # ─── MARKET DATA ────────────────────────────────────────────────────────

    async def get_candles(self, symbol: str, granularity: int,
                          count: int = LOOKBACK_CANDLES) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles for a symbol.

        Args:
            symbol:      e.g. 'frxAUDUSD'
            granularity: seconds per candle (use TIMEFRAMES dict)
            count:       number of candles

        Returns:
            DataFrame with columns: time, open, high, low, close, volume
        """
        resp = await self._send({
            "ticks_history": symbol,
            "style":         "candles",
            "granularity":   granularity,
            "count":         count,
            "end":           "latest",
        })

        if resp.get("error"):
            log_error(f"get_candles({symbol})", Exception(resp["error"]["message"]))
            return None

        candles = resp.get("candles", [])
        if not candles:
            return None

        df = pd.DataFrame(candles)
        df.rename(columns={"epoch": "time"}, inplace=True)
        df["time"]   = pd.to_datetime(df["time"], unit="s", utc=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df.sort_values("time", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    async def get_tick(self, symbol: str) -> Optional[float]:
        """Return the latest bid price for a symbol."""
        resp = await self._send({"ticks": symbol})
        return resp.get("tick", {}).get("bid")

    # ─── TRADING ────────────────────────────────────────────────────────────

    async def buy_contract(self, symbol: str, direction: str,
                           amount: float, duration: int = 5,
                           duration_unit: str = "m",
                           barrier: str = None) -> dict:
        """
        Open a Rise/Fall (digital) contract.

        Args:
            symbol:        Deriv symbol
            direction:     'BUY' or 'SELL'
            amount:        stake amount in account currency
            duration:      contract duration number
            duration_unit: 't' ticks, 's' seconds, 'm' minutes, 'h' hours, 'd' days
            barrier:       optional barrier string

        Returns:
            Contract response dict or error dict.
        """
        contract_type = "CALL" if direction == "BUY" else "PUT"
        payload: dict = {
            "buy": 1,
            "price": amount,
            "parameters": {
                "contract_type": contract_type,
                "symbol":        symbol,
                "duration":      duration,
                "duration_unit": duration_unit,
                "basis":         "stake",
                "currency":      self.account_info.get("currency", "USD"),
            }
        }
        if barrier:
            payload["parameters"]["barrier"] = barrier

        resp = await self._send(payload, timeout=20.0)
        if resp.get("error"):
            log_error(f"buy_contract({symbol},{direction})",
                      Exception(resp["error"]["message"]))
        return resp

    async def sell_contract(self, contract_id: int, price: float = 0) -> dict:
        """
        Sell (close) an open contract at market or given price.

        Args:
            contract_id: The contract's integer ID
            price:       Minimum sell price (0 = market)
        """
        resp = await self._send({"sell": contract_id, "price": price}, timeout=20.0)
        if resp.get("error"):
            log_error(f"sell_contract({contract_id})",
                      Exception(resp["error"]["message"]))
        return resp

    async def get_contract_info(self, contract_id: int) -> dict:
        """Fetch live contract details (profit, status, entry/exit)."""
        resp = await self._send({"proposal_open_contract": 1,
                                  "contract_id": contract_id})
        return resp.get("proposal_open_contract", {})

    # ─── HELPERS ────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Send a ping to keep connection alive."""
        resp = await self._send({"ping": 1})
        return resp.get("ping") == "pong"

    @staticmethod
    def granularity(tf_label: str) -> int:
        """Convert timeframe label to granularity seconds."""
        return TIMEFRAMES.get(tf_label, 300)
        
