"""
database.py - SQLite persistence layer for signals, trades, account history.
"""

import sqlite3
import os
import pandas as pd
from datetime import datetime
from config import DB_PATH


def _conn() -> sqlite3.Connection:
    """Return a thread-safe connection with WAL mode enabled."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """Create all tables if they do not exist."""
    con = _conn()
    cur = con.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT    NOT NULL,
        symbol      TEXT    NOT NULL,
        timeframe   TEXT    NOT NULL,
        signal      TEXT    NOT NULL,
        entry       REAL,
        sl          REAL,
        tp1         REAL,
        tp2         REAL,
        tp3         REAL,
        rr          REAL,
        confidence  REAL,
        trend       TEXT,
        reason      TEXT
    );

    CREATE TABLE IF NOT EXISTS trades (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id TEXT,
        ts_open     TEXT    NOT NULL,
        ts_close    TEXT,
        symbol      TEXT    NOT NULL,
        direction   TEXT    NOT NULL,
        lot_size    REAL,
        entry       REAL,
        sl          REAL,
        tp1         REAL,
        tp2         REAL,
        tp3         REAL,
        exit_price  REAL,
        profit      REAL,
        status      TEXT    DEFAULT 'OPEN',
        confidence  REAL,
        reason      TEXT
    );

    CREATE TABLE IF NOT EXISTS account_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT    NOT NULL,
        balance     REAL,
        equity      REAL,
        margin_used REAL
    );

    CREATE TABLE IF NOT EXISTS daily_summary (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        date        TEXT    UNIQUE NOT NULL,
        trades      INTEGER DEFAULT 0,
        wins        INTEGER DEFAULT 0,
        losses      INTEGER DEFAULT 0,
        gross_pnl   REAL    DEFAULT 0,
        net_pnl     REAL    DEFAULT 0,
        win_rate    REAL    DEFAULT 0
    );
    """)
    con.commit()
    con.close()


# ─── SIGNALS ────────────────────────────────────────────────────────────────

def insert_signal(sig: dict) -> None:
    con = _conn()
    con.execute("""
        INSERT INTO signals (ts,symbol,timeframe,signal,entry,sl,tp1,tp2,tp3,rr,confidence,trend,reason)
        VALUES (:ts,:symbol,:timeframe,:signal,:entry,:sl,:tp1,:tp2,:tp3,:rr,:confidence,:trend,:reason)
    """, sig)
    con.commit()
    con.close()


def get_signals(limit: int = 100) -> pd.DataFrame:
    con = _conn()
    df = pd.read_sql(f"SELECT * FROM signals ORDER BY id DESC LIMIT {limit}", con)
    con.close()
    return df


# ─── TRADES ─────────────────────────────────────────────────────────────────

def insert_trade(trade: dict) -> int:
    con = _conn()
    cur = con.execute("""
        INSERT INTO trades (contract_id,ts_open,symbol,direction,lot_size,entry,sl,tp1,tp2,tp3,status,confidence,reason)
        VALUES (:contract_id,:ts_open,:symbol,:direction,:lot_size,:entry,:sl,:tp1,:tp2,:tp3,:status,:confidence,:reason)
    """, trade)
    row_id = cur.lastrowid
    con.commit()
    con.close()
    return row_id


def close_trade(row_id: int, exit_price: float, profit: float) -> None:
    con = _conn()
    con.execute("""
        UPDATE trades
        SET ts_close=?, exit_price=?, profit=?, status='CLOSED'
        WHERE id=?
    """, (datetime.utcnow().isoformat(), exit_price, profit, row_id))
    con.commit()
    con.close()


def get_open_trades() -> pd.DataFrame:
    con = _conn()
    df = pd.read_sql("SELECT * FROM trades WHERE status='OPEN' ORDER BY id DESC", con)
    con.close()
    return df


def get_closed_trades(limit: int = 100) -> pd.DataFrame:
    con = _conn()
    df = pd.read_sql(
        f"SELECT * FROM trades WHERE status='CLOSED' ORDER BY id DESC LIMIT {limit}", con)
    con.close()
    return df


# ─── ACCOUNT HISTORY ────────────────────────────────────────────────────────

def insert_account_snapshot(balance: float, equity: float, margin: float = 0.0) -> None:
    con = _conn()
    con.execute(
        "INSERT INTO account_history (ts,balance,equity,margin_used) VALUES (?,?,?,?)",
        (datetime.utcnow().isoformat(), balance, equity, margin)
    )
    con.commit()
    con.close()


def get_account_history(limit: int = 500) -> pd.DataFrame:
    con = _conn()
    df = pd.read_sql(
        f"SELECT * FROM account_history ORDER BY id DESC LIMIT {limit}", con)
    con.close()
    return df


# ─── DAILY SUMMARY ──────────────────────────────────────────────────────────

def upsert_daily_summary(date_str: str, win: bool, pnl: float) -> None:
    con = _conn()
    con.execute("""
        INSERT INTO daily_summary (date,trades,wins,losses,gross_pnl,net_pnl)
        VALUES (?,1,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            trades    = trades + 1,
            wins      = wins   + excluded.wins,
            losses    = losses + excluded.losses,
            gross_pnl = gross_pnl + excluded.gross_pnl,
            net_pnl   = net_pnl   + excluded.net_pnl,
            win_rate  = CAST(wins AS REAL) / trades * 100
    """, (date_str, int(win), int(not win), pnl, pnl))
    con.commit()
    con.close()


def get_today_summary() -> dict:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    con = _conn()
    row = con.execute(
        "SELECT * FROM daily_summary WHERE date=?", (today,)).fetchone()
    con.close()
    if row:
        return dict(row)
    return {"date": today, "trades": 0, "wins": 0, "losses": 0,
            "gross_pnl": 0.0, "net_pnl": 0.0, "win_rate": 0.0}
                            
