# ⚡ Deriv AI Auto Trader

A production-ready Streamlit trading bot for Deriv (Binary.com) implementing
**ICT**, **Smart Money Concepts (SMC)**, and **Price Action** strategies across
12 Forex & Crypto pairs with full risk management and SQLite logging.

---

## 📁 Project Structure

```
deriv_ai_trader/
├── app.py              ← Streamlit dashboard (main entry point)
├── config.py           ← All constants, pairs, timeframes, weights
├── deriv_api.py        ← WebSocket API client + Demo mock
├── strategy.py         ← AI analysis & signal generation engine
├── risk_manager.py     ← Position sizing & daily limit controls
├── trade_executor.py   ← Trading loop orchestrator
├── indicators.py       ← TA indicators + ICT/SMC detectors
├── database.py         ← SQLite persistence layer
├── logger.py           ← Structured logging (file + memory buffer)
├── utils.py            ← Shared helpers
├── requirements.txt    ← Python dependencies
├── data/               ← SQLite database (auto-created)
└── logs/               ← Log files & CSV trade log (auto-created)
```

---

## 🚀 Installation

### 1. Prerequisites
- Python 3.11+ (3.12 recommended)
- pip

### 2. Clone / extract the project
```bash
cd deriv_ai_trader
```

### 3. Create a virtual environment (recommended)
```bash
python -m venv venv
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate.bat       # Windows
```

### 4. Install dependencies
```bash
pip install -r requirements.txt
```

---

## ▶️ Running the App

```bash
streamlit run app.py
```

The dashboard opens automatically at **http://localhost:8501**

---

## 🔑 Getting a Deriv API Token

1. Log in at [app.deriv.com](https://app.deriv.com)
2. Go to **Settings → API Token**
3. Create a token with: **Read**, **Trade**, **Payments** permissions
4. Paste the token into the sidebar — or use **Demo Mode** (no token needed)

---

## ⚙️ Configuration

All defaults are in `config.py`:

| Setting | Default | Description |
|---|---|---|
| `MIN_CONFIDENCE` | 75% | Minimum AI confidence to trade |
| `EMA_FAST` | 50 | Fast EMA period |
| `EMA_SLOW` | 200 | Slow EMA period |
| `RSI_PERIOD` | 14 | RSI period |
| `ATR_PERIOD` | 14 | ATR period |
| `SCAN_INTERVAL` | 60s | Seconds between market scans |
| `TP_RR_RATIOS` | 1.5/2.5/4.0 | TP1/2/3 R:R multiples |

---

## 🧠 AI Scoring System

The engine scores 6 factors and weights them to produce a 0–100% confidence score:

| Factor | Weight |
|---|---|
| Market Structure (HH/HL/LH/LL, BOS, CHOCH) | 25% |
| SMC (Order Blocks, FVG, Premium/Discount) | 25% |
| RSI 14 | 15% |
| EMA 50/200 Trend | 15% |
| Liquidity Sweep | 10% |
| Price Action (Pin Bar, Engulfing) | 10% |

**Trades only open when Confidence ≥ 75%**

---

## ⚠️ Risk Disclaimer

> This software is for **educational and research purposes only**.
> Automated trading carries substantial risk of loss.
> Never trade with money you cannot afford to lose.
> Past performance does not guarantee future results.
> Always test in Demo mode before using real funds.

---

## 📊 Supported Markets

**Forex:** NZD/USD · AUD/CHF · AUD/USD · AUD/NZD · AUD/CAD · NZD/CHF · NZD/CAD · NZD/JPY · CAD/CHF

**Crypto:** LTC/USD · XRP/USD · BCH/USD

---

## 🛠 Troubleshooting

| Issue | Solution |
|---|---|
| `ModuleNotFoundError: ta` | `pip install ta` |
| `websocket not found` | `pip install websocket-client` |
| Connection timeout | Check token permissions or use Demo mode |
| Chart blank | Ensure selected pairs are active on Deriv |
