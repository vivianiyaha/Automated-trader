"""
logger.py — Structured logging with console + file + in-memory log for Streamlit display.
"""

import logging
import os
import csv
from datetime import datetime
from collections import deque
from config import LOG_DIR, LOG_FILE, CSV_LOG_FILE

# ── In-memory ring buffer exposed to Streamlit ────────────
_log_buffer: deque = deque(maxlen=500)


def _setup_logger() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("DerivTrader")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s — %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    # File handler
    fh = logging.FileHandler(LOG_FILE)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


_logger = _setup_logger()


def _push(level: str, msg: str) -> None:
    """Add an entry to the Streamlit-visible buffer."""
    _log_buffer.appendleft({
        "time":    datetime.utcnow().strftime("%H:%M:%S"),
        "level":   level,
        "message": msg,
    })


def get_logs() -> list:
    """Return a copy of the in-memory log buffer for display."""
    return list(_log_buffer)


def clear_logs() -> None:
    _log_buffer.clear()


# ── Public helpers ─────────────────────────────────────────

def info(msg: str)  -> None: _logger.info(msg);    _push("INFO",    msg)
def warn(msg: str)  -> None: _logger.warning(msg); _push("WARN",    msg)
def error(msg: str) -> None: _logger.error(msg);   _push("ERROR",   msg)
def debug(msg: str) -> None: _logger.debug(msg);   _push("DEBUG",   msg)
def trade(msg: str) -> None: _logger.info(msg);    _push("TRADE",   msg)
def signal(msg: str)-> None: _logger.info(msg);    _push("SIGNAL",  msg)


# ── CSV trade log ──────────────────────────────────────────

def log_trade_csv(data: dict) -> None:
    """Append a trade event to the CSV log file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    file_exists = os.path.isfile(CSV_LOG_FILE)
    with open(CSV_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(data)
