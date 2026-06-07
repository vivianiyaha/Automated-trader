"""
database.py — SQLite persistence layer.
Creates and manages tables: signals, trades, account_history, daily_summary.
"""

import sqlite3
import os
import pandas as pd
from datetime import datetime, date
from config import DB_PATH


def _connect() -> sqlite3.Connection:
    """Return a thread-safe SQLite connection."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create all tables if they do not exist."""
    conn = _connect()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS signals (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp     TEXT    NOT NULL,
        symbol        TEXT    NOT NULL,
        timeframe     TEXT    NOT NULL,
        signal        TEXT    NOT NULL,   -- BUY | SELL | NO TRADE
        entry         REAL,
        stop_loss     REAL,
        tp1           REAL,
        tp2           REAL,
        tp3           REAL,
        confidence    REAL,
        trend         TEXT,
        trade_reason  TEXT,
        rr_ratio      REAL,
        acted_on      INTEGER DEFAULT 0   -- 1 if trade was opened
    );

    CREATE TABLE IF NOT EXISTS trades (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id   TEXT,
        timestamp     TEXT    NOT NULL,
        symbol        TEXT    NOT NULL,
        direction     TEXT    NOT NULL,   -- BUY | SELL
        entry_price   REAL,
        stop_loss     REAL,
        tp1           REAL,
        tp2           REAL,
        tp3           REAL,
        lot_size      REAL,
        confidence    REAL,
        trade_reason  TEXT,
        status        TEXT    DEFAULT 'OPEN',  -- OPEN | CLOSED | CANCELLED
        exit_price    REAL,
        exit_time     TEXT,
        pnl           REAL,
        exit_reason   TEXT
    );

    CREATE TABLE IF NOT EXISTS account_history (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT    NOT NULL,
        balance   REAL,
        equity    REAL,
        margin    REAL
    );

    CREATE TABLE IF NOT EXISTS daily_summary (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         TEXT    UNIQUE NOT NULL,
        trades_total INTEGER DEFAULT 0,
        trades_won   INTEGER DEFAULT 0,
        trades_lost  INTEGER DEFAULT 0,
        gross_pnl    REAL    DEFAULT 0.0,
        net_pnl      REAL    DEFAULT 0.0,
        win_rate     REAL    DEFAULT 0.0,
        max_drawdown REAL    DEFAULT 0.0
    );
    """)

    conn.commit()
    conn.close()


# ── Signal Operations ─────────────────────────────────────

def save_signal(data: dict) -> int:
    """Insert a signal record and return its row id."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO signals
            (timestamp, symbol, timeframe, signal, entry, stop_loss,
             tp1, tp2, tp3, confidence, trend, trade_reason, rr_ratio)
        VALUES
            (:timestamp, :symbol, :timeframe, :signal, :entry, :stop_loss,
             :tp1, :tp2, :tp3, :confidence, :trend, :trade_reason, :rr_ratio)
    """, data)
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_signals(limit: int = 100) -> pd.DataFrame:
    """Fetch the most recent signals as a DataFrame."""
    conn = _connect()
    df = pd.read_sql_query(
        "SELECT * FROM signals ORDER BY id DESC LIMIT ?", conn, params=(limit,)
    )
    conn.close()
    return df


# ── Trade Operations ──────────────────────────────────────

def save_trade(data: dict) -> int:
    """Insert a new open trade record."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO trades
            (contract_id, timestamp, symbol, direction, entry_price,
             stop_loss, tp1, tp2, tp3, lot_size, confidence, trade_reason)
        VALUES
            (:contract_id, :timestamp, :symbol, :direction, :entry_price,
             :stop_loss, :tp1, :tp2, :tp3, :lot_size, :confidence, :trade_reason)
    """, data)
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def close_trade(trade_id: int, exit_price: float, exit_reason: str, pnl: float) -> None:
    """Mark a trade as closed and record exit details."""
    conn = _connect()
    conn.execute("""
        UPDATE trades
        SET status='CLOSED', exit_price=?, exit_time=?, pnl=?, exit_reason=?
        WHERE id=?
    """, (exit_price, datetime.utcnow().isoformat(), pnl, exit_reason, trade_id))
    conn.commit()
    conn.close()
    _update_daily_summary()


def get_open_trades() -> pd.DataFrame:
    """Fetch all currently open trades."""
    conn = _connect()
    df = pd.read_sql_query(
        "SELECT * FROM trades WHERE status='OPEN' ORDER BY id DESC", conn
    )
    conn.close()
    return df


def get_closed_trades(limit: int = 200) -> pd.DataFrame:
    """Fetch recently closed trades."""
    conn = _connect()
    df = pd.read_sql_query(
        "SELECT * FROM trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?",
        conn, params=(limit,)
    )
    conn.close()
    return df


# ── Account History ───────────────────────────────────────

def save_account_snapshot(balance: float, equity: float, margin: float = 0.0) -> None:
    """Record an account snapshot."""
    conn = _connect()
    conn.execute("""
        INSERT INTO account_history (timestamp, balance, equity, margin)
        VALUES (?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), balance, equity, margin))
    conn.commit()
    conn.close()


def get_account_history(limit: int = 200) -> pd.DataFrame:
    conn = _connect()
    df = pd.read_sql_query(
        "SELECT * FROM account_history ORDER BY id DESC LIMIT ?",
        conn, params=(limit,)
    )
    conn.close()
    return df


# ── Daily Summary ─────────────────────────────────────────

def _update_daily_summary() -> None:
    """Recompute today's daily summary from closed trades."""
    today = date.today().isoformat()
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as won,
               SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as lost,
               SUM(pnl) as gross
        FROM trades
        WHERE status='CLOSED' AND DATE(exit_time)=?
    """, (today,))
    row = cur.fetchone()

    total = row["total"] or 0
    won   = row["won"]   or 0
    lost  = row["lost"]  or 0
    gross = row["gross"] or 0.0
    win_rate = (won / total * 100) if total > 0 else 0.0

    conn.execute("""
        INSERT INTO daily_summary (date, trades_total, trades_won, trades_lost,
                                   gross_pnl, net_pnl, win_rate)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            trades_total=excluded.trades_total,
            trades_won=excluded.trades_won,
            trades_lost=excluded.trades_lost,
            gross_pnl=excluded.gross_pnl,
            net_pnl=excluded.net_pnl,
            win_rate=excluded.win_rate
    """, (today, total, won, lost, gross, gross, win_rate))
    conn.commit()
    conn.close()


def get_today_summary() -> dict:
    """Return today's summary as a plain dict."""
    today = date.today().isoformat()
    conn  = _connect()
    cur   = conn.cursor()
    cur.execute("SELECT * FROM daily_summary WHERE date=?", (today,))
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"trades_total": 0, "trades_won": 0, "trades_lost": 0,
            "gross_pnl": 0.0, "win_rate": 0.0}
