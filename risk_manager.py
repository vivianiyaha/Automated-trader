"""
risk_manager.py — Position sizing, daily limits, and drawdown controls.
"""

from datetime import date
from config import DEFAULT_RISK_PCT, DEFAULT_DAILY_LOSS, DEFAULT_MAX_TRADES, TP_RR_RATIOS
from utils import calc_position_size, pip_value
import logger


class RiskManager:
    """
    Manages per-trade risk, daily limits, and maximum positions.

    All state is kept in memory; the database is queried to determine daily P&L.
    """

    def __init__(self,
                 balance:        float = 10_000.0,
                 risk_pct:       float = DEFAULT_RISK_PCT,
                 daily_loss_pct: float = DEFAULT_DAILY_LOSS,
                 daily_profit_pct: float = 10.0,
                 max_trades:     int   = DEFAULT_MAX_TRADES,
                 lot_size:       float = 0.01):

        self.balance          = balance
        self.risk_pct         = risk_pct          # % per trade
        self.daily_loss_pct   = daily_loss_pct    # halt if reached
        self.daily_profit_pct = daily_profit_pct  # optional profit lock
        self.max_trades       = max_trades
        self.lot_size         = lot_size

        # Daily tracking
        self._session_start_balance = balance
        self._daily_pnl             = 0.0
        self._today                 = date.today()
        self._halted                = False

    # ── State updates ──────────────────────────────────────

    def update_balance(self, new_balance: float) -> None:
        """Call after every trade close or account snapshot."""
        self._daily_pnl  = new_balance - self._session_start_balance
        self.balance     = new_balance
        self._check_limits()

    def record_trade_pnl(self, pnl: float) -> None:
        self._daily_pnl += pnl
        self.balance    += pnl
        self._check_limits()

    def reset_session(self) -> None:
        """Reset daily tracking at session start or manual reset."""
        self._session_start_balance = self.balance
        self._daily_pnl             = 0.0
        self._today                 = date.today()
        self._halted                = False
        logger.info("RiskManager: session reset")

    # ── Gate checks ────────────────────────────────────────

    def can_open_trade(self, open_trade_count: int) -> tuple[bool, str]:
        """
        Returns (allowed, reason).
        Called before every new trade attempt.
        """
        if self._halted:
            return False, "Trading halted — daily loss limit reached"

        if open_trade_count >= self.max_trades:
            return False, f"Max open trades ({self.max_trades}) reached"

        daily_loss_limit = self.balance * (self.daily_loss_pct / 100)
        if self._daily_pnl <= -daily_loss_limit:
            self._halted = True
            logger.warn("RiskManager: daily loss limit hit — trading halted")
            return False, "Daily loss limit reached"

        daily_profit_target = self._session_start_balance * (self.daily_profit_pct / 100)
        if self._daily_pnl >= daily_profit_target:
            logger.info("RiskManager: daily profit target reached")
            return False, "Daily profit target reached — no new trades"

        return True, "OK"

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    # ── Position sizing ────────────────────────────────────

    def position_size(self, entry: float, stop_loss: float, symbol: str) -> float:
        """
        Calculate position size using the risk-per-trade formula.
        Falls back to the user-configured lot size if calculation returns < 0.01.
        """
        sl_distance = abs(entry - stop_loss)
        pip_val     = pip_value(symbol, entry)
        size        = calc_position_size(self.balance, self.risk_pct, sl_distance, pip_val)
        if size < 0.01:
            size = self.lot_size
        logger.debug(f"Position size for {symbol}: {size} lots "
                     f"(risk {self.risk_pct}% of {self.balance:.2f})")
        return size

    def stake_amount(self, entry: float, stop_loss: float, symbol: str) -> float:
        """
        Return a stake amount in account currency suitable for binary/digital contracts.
        This is the $ amount at risk, floored to $1.
        """
        risk_amount = self.balance * (self.risk_pct / 100)
        return max(1.0, round(risk_amount, 2))

    # ── Internal ───────────────────────────────────────────

    def _check_limits(self) -> None:
        daily_loss_limit = self._session_start_balance * (self.daily_loss_pct / 100)
        if self._daily_pnl <= -daily_loss_limit and not self._halted:
            self._halted = True
            logger.warn(f"RiskManager: daily loss {self._daily_pnl:.2f} — HALT")

    # ── Summary ────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "balance":            round(self.balance, 2),
            "risk_pct":           self.risk_pct,
            "daily_pnl":          round(self._daily_pnl, 2),
            "daily_loss_limit":   round(self.balance * (self.daily_loss_pct / 100), 2),
            "daily_profit_target":round(self._session_start_balance * (self.daily_profit_pct / 100), 2),
            "max_trades":         self.max_trades,
            "halted":             self._halted,
        }
