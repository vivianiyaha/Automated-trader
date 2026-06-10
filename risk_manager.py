"""
risk_manager.py - Position sizing, daily limits, and risk controls.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict
from logger import log_risk_halt


@dataclass
class RiskSettings:
    risk_pct:       float = 1.0    # % of balance per trade
    daily_loss_pct: float = 5.0    # max daily drawdown %
    max_open:       int   = 5      # max simultaneous positions
    lot_size:       float = 0.01   # manual override (0 = auto)


@dataclass
class SessionStats:
    date:           str   = field(default_factory=lambda: str(date.today()))
    start_balance:  float = 0.0
    current_balance:float = 0.0
    daily_pnl:      float = 0.0
    trades_today:   int   = 0
    wins_today:     int   = 0
    open_positions: int   = 0
    halted:         bool  = False
    halt_reason:    str   = ""


class RiskManager:
    """
    Central risk manager.
    All trading decisions pass through can_trade() before execution.
    """

    def __init__(self, settings: RiskSettings = None):
        self.settings = settings or RiskSettings()
        self.session  = SessionStats()

    # ─── SESSION ────────────────────────────────────────────────────────────

    def init_session(self, balance: float) -> None:
        """Call once when the bot starts or balance is fetched."""
        self.session = SessionStats(
            date=str(date.today()),
            start_balance=balance,
            current_balance=balance,
        )

    def update_balance(self, new_balance: float) -> None:
        self.session.current_balance = new_balance
        self.session.daily_pnl = new_balance - self.session.start_balance
        self._check_halt()

    def record_trade_open(self) -> None:
        self.session.open_positions += 1
        self.session.trades_today   += 1

    def record_trade_close(self, profit: float) -> None:
        self.session.open_positions = max(0, self.session.open_positions - 1)
        if profit >= 0:
            self.session.wins_today += 1
        self.update_balance(self.session.current_balance + profit)

    def reset_session(self, balance: float) -> None:
        self.init_session(balance)

    # ─── GATE ────────────────────────────────────────────────────────────────

    def can_trade(self) -> tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).
        Call before placing any new trade.
        """
        if self.session.halted:
            return False, f"Trading halted: {self.session.halt_reason}"

        if self.session.open_positions >= self.settings.max_open:
            return False, f"Max open positions ({self.settings.max_open}) reached"

        daily_loss_limit = self.settings.daily_loss_pct / 100 * self.session.start_balance
        if self.session.daily_pnl <= -daily_loss_limit:
            self._halt(f"Daily loss limit {self.settings.daily_loss_pct}% hit")
            return False, self.session.halt_reason

        return True, "OK"

    # ─── POSITION SIZING ────────────────────────────────────────────────────

    def position_size(self, sl_distance: float, tick_value: float = 1.0) -> float:
        """
        Auto position size formula:
            size = (balance × risk_pct) / (sl_distance × tick_value)
        Falls back to manual lot size if sl_distance is 0 or manual lot set.
        """
        if self.settings.lot_size > 0:
            return self.settings.lot_size

        if sl_distance <= 0:
            return 0.01

        risk_amount = self.session.current_balance * (self.settings.risk_pct / 100)
        size = risk_amount / (sl_distance * tick_value)
        # Cap at reasonable limits
        size = max(0.01, min(size, 10.0))
        return round(size, 2)

    # ─── STATS ──────────────────────────────────────────────────────────────

    @property
    def win_rate(self) -> float:
        if self.session.trades_today == 0:
            return 0.0
        return round(self.session.wins_today / self.session.trades_today * 100, 1)

    @property
    def daily_pnl_pct(self) -> float:
        if self.session.start_balance == 0:
            return 0.0
        return round(self.session.daily_pnl / self.session.start_balance * 100, 2)

    # ─── INTERNAL ───────────────────────────────────────────────────────────

    def _check_halt(self) -> None:
        limit = self.settings.daily_loss_pct / 100 * self.session.start_balance
        if self.session.daily_pnl <= -limit and not self.session.halted:
            self._halt(f"Daily loss limit {self.settings.daily_loss_pct}% reached")

    def _halt(self, reason: str) -> None:
        self.session.halted     = True
        self.session.halt_reason = reason
        log_risk_halt(reason)
        
