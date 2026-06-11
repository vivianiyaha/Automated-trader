"""
logger.py - Real-time trade logger writing to in-memory list, CSV, and SQLite.
"""

import os
import csv
import logging
from datetime import datetime
from typing import List
from config import LOG_DIR, LOG_CSV_PATH

# Ensure log directories exist
os.makedirs(LOG_DIR, exist_ok=True)

# Python logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(f"{LOG_DIR}/system.log"),
        logging.StreamHandler()
    ]
)
system_logger = logging.getLogger("DerivAITrader")

# In-memory log buffer (shown in UI)
_log_buffer: List[dict] = []
MAX_BUFFER = 500


def _write_csv(entry: dict) -> None:
    """Append a log entry to the CSV file."""
    file_exists = os.path.isfile(LOG_CSV_PATH)
    with open(LOG_CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=entry.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(entry)


def log_event(level: str, message: str, extra: dict = None) -> dict:
    """
    Central log function. Adds to buffer, CSV, and Python logger.
    Returns the log entry dict.
    """
    entry = {
        "ts":      datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "level":   level.upper(),
        "message": message,
        **(extra or {})
    }

    # In-memory ring buffer
    _log_buffer.append(entry)
    if len(_log_buffer) > MAX_BUFFER:
        _log_buffer.pop(0)

    # CSV
    try:
        _write_csv(entry)
    except Exception as e:
        system_logger.warning(f"CSV write failed: {e}")

    # Python logger
    getattr(system_logger, level.lower(), system_logger.info)(message)
    return entry


def log_signal(symbol: str, signal: str, confidence: float, reason: str) -> None:
    log_event("info", f"📊 SIGNAL | {symbol} | {signal} | {confidence:.1f}% confidence",
              {"symbol": symbol, "signal": signal, "confidence": confidence})


def log_trade_open(symbol: str, direction: str, entry: float, sl: float, lot: float) -> None:
    log_event("info", f"🟢 TRADE OPEN | {symbol} | {direction} @ {entry:.5f} | SL {sl:.5f} | Lot {lot}",
              {"symbol": symbol, "direction": direction, "entry": entry, "sl": sl, "lot": lot})


def log_trade_close(symbol: str, exit_price: float, profit: float) -> None:
    emoji = "✅" if profit >= 0 else "❌"
    log_event("info", f"{emoji} TRADE CLOSE | {symbol} | Exit {exit_price:.5f} | P&L {profit:+.2f}",
              {"symbol": symbol, "exit": exit_price, "profit": profit})


def log_risk_halt(reason: str) -> None:
    log_event("warning", f"🚨 RISK HALT | {reason}", {"reason": reason})


def log_error(msg: str, exc: Exception = None) -> None:
    full = f"{msg}: {exc}" if exc else msg
    log_event("error", f"🔴 ERROR | {full}")


def get_logs(limit: int = 100) -> List[dict]:
    """Return the most recent log entries."""
    return list(reversed(_log_buffer[-limit:]))


def clear_logs() -> None:
    """Clear in-memory buffer."""
    _log_buffer.clear()
               
