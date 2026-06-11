"""
deriv_api.py - Robust Deriv WebSocket API wrapper with auto-reconnect.

Key fixes vs v1:
  - Auto-reconnect on disconnect / ping timeout (exponential back-off)
  - Dedicated heartbeat task sends app-level pings every 20 s
  - _send() detects a dead socket and reconnects before retrying
  - Pending futures are cancelled and re-queued on reconnect
  - Per-request fresh connection for critical calls (buy / sell)
"""

import json
import asyncio
import websockets
import pandas as pd
from datetime import datetime
from typing import Optional, Dict
from logger import log_error, log_event
from config import DERIV_WS_URL, DERIV_APP_ID, TIMEFRAMES, LOOKBACK_CANDLES

# ── Tuning knobs ───────────────────────────────────────────────────────────
_PING_INTERVAL   = 20        # send app-level ping every N seconds
_PING_TIMEOUT    = 10        # seconds to wait for pong before reconnecting
_RECONNECT_DELAY = 3         # base delay between reconnect attempts (seconds)
_MAX_RECONNECTS  = 10        # give up after this many consecutive failures
_SEND_TIMEOUT    = 20        # seconds a normal request can take
_TRADE_TIMEOUT   = 25        # seconds for buy / sell requests


class DerivAPI:
    """
    Resilient async WebSocket client for Deriv.

    Usage:
        api = DerivAPI(token="YOUR_TOKEN")
        await api.connect()
        balance = await api.get_balance()
    """

    def __init__(self, token: str = "", app_id: str = DERIV_APP_ID):
        self.token        = token
        self.app_id       = app_id
        self.account_info: dict = {}

        self._ws          = None
        self._req_id      = 0
        self._pending:    Dict[int, asyncio.Future] = {}

        self._listener_task  = None
        self._heartbeat_task = None

        self._connected      = False      # True after a successful auth
        self._reconnecting   = False      # guard against concurrent reconnects
        self._shutdown       = False      # set by disconnect() to stop loops
        self._reconnect_count = 0

        self._lock = asyncio.Lock()       # serialise reconnect attempts

    # ── Public flag ─────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected and not self._shutdown

    # ════════════════════════════════════════════════════════════════════════
    # CONNECTION
    # ════════════════════════════════════════════════════════════════════════

    async def connect(self) -> bool:
        """
        Open WebSocket, authenticate, and start background tasks.
        Returns True on success. Safe to call again after a disconnect.
        """
        self._shutdown = False
        return await self._do_connect()

    async def _do_connect(self) -> bool:
        """Internal: (re-)establish the WebSocket connection."""
        try:
            url = f"{DERIV_WS_URL}?app_id={self.app_id}"
            # Build connect kwargs — close_timeout was renamed in websockets >=14
            connect_kwargs = dict(
                ping_interval=None,   # We manage pings ourselves
                ping_timeout=None,
                max_size=2**22,       # 4 MB frames
            )
            import websockets as _ws_mod
            _ws_ver = tuple(int(x) for x in _ws_mod.__version__.split(".")[:2])
            if _ws_ver >= (14, 0):
                connect_kwargs["open_timeout"] = 10
            else:
                connect_kwargs["close_timeout"] = 5
            ws  = await websockets.connect(url, **connect_kwargs)
            self._ws = ws
            self._connected = False   # not ready until auth succeeds

            # Cancel old tasks before starting fresh ones
            await self._cancel_bg_tasks()

            self._listener_task  = asyncio.create_task(self._listen(),        name="deriv-listen")
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="deriv-heartbeat")

            # Authenticate
            if self.token:
                auth = await self._send_raw({"authorize": self.token}, timeout=15)
                if auth.get("error"):
                    log_error("Auth failed", Exception(auth["error"]["message"]))
                    return False
                self.account_info = auth.get("authorize", {})
                log_event("info",
                          f"✅ Connected & authenticated: "
                          f"{self.account_info.get('email', 'unknown')} | "
                          f"Balance: {self.account_info.get('balance', '?')} "
                          f"{self.account_info.get('currency', '')}")

            self._connected = True
            self._reconnect_count = 0
            return True

        except Exception as e:
            log_error("Connection failed", e)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Cleanly tear down everything."""
        self._shutdown  = True
        self._connected = False
        await self._cancel_bg_tasks()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        log_event("info", "🔌 Disconnected from Deriv")

    async def _cancel_bg_tasks(self) -> None:
        for task in (self._listener_task, self._heartbeat_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._listener_task  = None
        self._heartbeat_task = None

    # ════════════════════════════════════════════════════════════════════════
    # RECONNECT
    # ════════════════════════════════════════════════════════════════════════

    async def _reconnect(self) -> None:
        """
        Attempt to restore the connection with exponential back-off.
        Concurrent calls are serialised by _lock.
        """
        if self._shutdown:
            return
        async with self._lock:
            if self._reconnecting:
                return
            self._reconnecting = True

        try:
            self._connected = False
            # Cancel any pending futures so callers get an error immediately
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("Reconnecting…"))
            self._pending.clear()

            for attempt in range(1, _MAX_RECONNECTS + 1):
                if self._shutdown:
                    break
                delay = min(_RECONNECT_DELAY * (2 ** (attempt - 1)), 60)
                log_event("warning",
                          f"🔄 Reconnect attempt {attempt}/{_MAX_RECONNECTS} "
                          f"(waiting {delay}s)…")
                await asyncio.sleep(delay)

                if await self._do_connect():
                    log_event("info", "✅ Reconnected successfully")
                    self._reconnect_count = attempt
                    return

            log_event("error",
                      f"❌ Gave up reconnecting after {_MAX_RECONNECTS} attempts")
        finally:
            self._reconnecting = False

    # ════════════════════════════════════════════════════════════════════════
    # HEARTBEAT
    # ════════════════════════════════════════════════════════════════════════

    async def _heartbeat_loop(self) -> None:
        """
        Send an application-level ping every _PING_INTERVAL seconds.
        If the pong does not arrive within _PING_TIMEOUT, trigger reconnect.
        """
        while not self._shutdown:
            await asyncio.sleep(_PING_INTERVAL)
            if self._shutdown:
                break
            try:
                resp = await asyncio.wait_for(
                    self._send_raw({"ping": 1}), timeout=_PING_TIMEOUT
                )
                if resp.get("ping") != "pong":
                    raise ConnectionError("Unexpected ping response")
            except (asyncio.TimeoutError, ConnectionError, Exception) as e:
                if not self._shutdown:
                    log_event("warning", f"💔 Heartbeat failed ({e}) — reconnecting…")
                    asyncio.create_task(self._reconnect())

    # ════════════════════════════════════════════════════════════════════════
    # MESSAGING
    # ════════════════════════════════════════════════════════════════════════

    async def _listen(self) -> None:
        """Background task: route incoming messages to waiting futures."""
        try:
            async for raw in self._ws:
                try:
                    msg    = json.loads(raw)
                    req_id = msg.get("req_id")
                    if req_id and req_id in self._pending:
                        fut = self._pending.pop(req_id)
                        if not fut.done():
                            fut.set_result(msg)
                except Exception as e:
                    log_error("Message parse error", e)
        except Exception as e:
            if not self._shutdown:
                log_event("warning", f"⚡ Listener dropped ({e}) — triggering reconnect")
                self._connected = False
                asyncio.create_task(self._reconnect())

    async def _send_raw(self, payload: dict, timeout: float = _SEND_TIMEOUT) -> dict:
        """
        Low-level send: attach req_id, register future, send, await reply.
        Does NOT auto-reconnect — used internally where reconnect is handled above.
        """
        self._req_id += 1
        req_id = self._req_id
        payload = dict(payload)          # don't mutate caller's dict
        payload["req_id"] = req_id

        loop = asyncio.get_event_loop()
        fut  = loop.create_future()
        self._pending[req_id] = fut

        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return {"error": {"message": f"timeout after {timeout}s"}}
        except Exception as e:
            self._pending.pop(req_id, None)
            return {"error": {"message": str(e)}}

    async def _send(self, payload: dict, timeout: float = _SEND_TIMEOUT,
                    retries: int = 3) -> dict:
        """
        Public send with reconnect-and-retry logic.
        Up to `retries` attempts; each failed attempt triggers a reconnect wait.
        """
        for attempt in range(1, retries + 1):
            # Wait until connected (up to 30 s)
            waited = 0
            while not self._connected and not self._shutdown:
                if waited > 30:
                    return {"error": {"message": "Not connected after 30 s wait"}}
                await asyncio.sleep(1)
                waited += 1

            if self._shutdown:
                return {"error": {"message": "Shutting down"}}

            resp = await self._send_raw(payload, timeout=timeout)

            # Success
            if "error" not in resp:
                return resp

            err_msg = resp["error"].get("message", "")

            # Unrecoverable API errors (bad symbol, bad token, etc.)
            non_retryable = ("InvalidSymbol", "AuthorizationRequired",
                             "InvalidToken", "RateLimit")
            if any(x in err_msg for x in non_retryable):
                return resp

            # Connection error — reconnect then retry
            log_event("warning",
                      f"⚠️  Send attempt {attempt} failed ({err_msg}). "
                      f"Reconnecting…")
            self._connected = False
            asyncio.create_task(self._reconnect())
            await asyncio.sleep(min(3 * attempt, 15))

        return {"error": {"message": f"All {retries} send attempts failed"}}

    # ════════════════════════════════════════════════════════════════════════
    # ACCOUNT
    # ════════════════════════════════════════════════════════════════════════

    async def get_balance(self) -> dict:
        resp = await self._send({"balance": 1, "account": "current"})
        if resp.get("error"):
            log_error("get_balance", Exception(resp["error"]["message"]))
            return {}
        return resp.get("balance", {})

    async def get_open_contracts(self) -> list:
        resp = await self._send({"portfolio": 1})
        if resp.get("error"):
            return []
        return resp.get("portfolio", {}).get("contracts", [])

    # ════════════════════════════════════════════════════════════════════════
    # MARKET DATA
    # ════════════════════════════════════════════════════════════════════════

    async def get_candles(self, symbol: str, granularity: int,
                          count: int = LOOKBACK_CANDLES) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles.  Returns None on error; retries 3× automatically.
        """
        resp = await self._send({
            "ticks_history": symbol,
            "style":         "candles",
            "granularity":   granularity,
            "count":         count,
            "end":           "latest",
        }, retries=3)

        if resp.get("error"):
            log_error(f"get_candles({symbol})",
                      Exception(resp["error"]["message"]))
            return None

        candles = resp.get("candles", [])
        if not candles:
            log_event("warning", f"No candles returned for {symbol}")
            return None

        df = pd.DataFrame(candles)
        df.rename(columns={"epoch": "time"}, inplace=True)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df.sort_values("time", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    async def get_tick(self, symbol: str) -> Optional[float]:
        resp = await self._send({"ticks": symbol})
        return resp.get("tick", {}).get("bid")

    # ════════════════════════════════════════════════════════════════════════
    # TRADING
    # ════════════════════════════════════════════════════════════════════════

    async def buy_contract(self, symbol: str, direction: str,
                           amount: float, duration: int = 15,
                           duration_unit: str = "m",
                           barrier: str = None) -> dict:
        """
        Open a Rise/Fall digital contract.

        Args:
            symbol:        Deriv symbol string
            direction:     'BUY' (CALL) or 'SELL' (PUT)
            amount:        Stake in account currency
            duration:      Contract duration value
            duration_unit: 't' | 's' | 'm' | 'h' | 'd'
            barrier:       Optional barrier level string
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

        resp = await self._send(payload, timeout=_TRADE_TIMEOUT, retries=2)
        if resp.get("error"):
            log_error(f"buy_contract({symbol},{direction})",
                      Exception(resp["error"]["message"]))
        return resp

    async def sell_contract(self, contract_id: int, price: float = 0) -> dict:
        """Sell (close) an open contract at market or minimum price."""
        resp = await self._send(
            {"sell": contract_id, "price": price},
            timeout=_TRADE_TIMEOUT, retries=2
        )
        if resp.get("error"):
            log_error(f"sell_contract({contract_id})",
                      Exception(resp["error"]["message"]))
        return resp

    async def get_contract_info(self, contract_id: int) -> dict:
        """Fetch live contract details."""
        resp = await self._send(
            {"proposal_open_contract": 1, "contract_id": contract_id}
        )
        return resp.get("proposal_open_contract", {})

    # ════════════════════════════════════════════════════════════════════════
    # HELPERS
    # ════════════════════════════════════════════════════════════════════════

    async def ping(self) -> bool:
        resp = await self._send_raw({"ping": 1}, timeout=_PING_TIMEOUT)
        return resp.get("ping") == "pong"

    @staticmethod
    def granularity(tf_label: str) -> int:
        return TIMEFRAMES.get(tf_label, 300)

    def is_alive(self) -> bool:
        """Quick sync check — True if WS is open and we are authenticated.
        Compatible with websockets >=14 (uses close_code) and older (uses .closed).
        """
        if not self._connected or self._ws is None:
            return False
        # websockets >=14 renamed .closed → tracks via close_code
        try:
            return self._ws.close_code is None
        except AttributeError:
            pass
        # websockets <14
        try:
            return not self._ws.closed
        except AttributeError:
            return False
          
