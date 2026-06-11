# Deriv AI Automated Trader

A production-ready AI trading bot for the Deriv platform featuring Smart Money
Concepts (SMC), ICT analysis, and automated trade execution — all in a
professional Streamlit dashboard.

## Features

- **AI Signal Engine** — SMC, ICT Order Blocks, Fair Value Gaps, Liquidity Sweeps,
  Market Structure (HH/HL/BOS/CHOCH), Premium/Discount Zones
- **Multi-Timeframe Analysis** — HTF bias (H1/H4) + LTF execution (M5/M15)
- **Weighted Confidence Scoring** — Only trades when confidence ≥ 75%
- **Automated Execution** — Buy/Sell contracts via Deriv WebSocket API
- **Risk Management** — Position sizing, daily loss limits, max open trades
- **Live Dashboard** — Real-time charts, open/closed positions, signal history
- **Full Logging** — In-app log feed + CSV + SQLite database

---

## Project Structure

```
deriv_ai_trader/
├── app.py              ← Streamlit UI (main entry point)
├── config.py           ← API keys, constants, colour palette
├── deriv_api.py        ← Async Deriv WebSocket client
├── strategy.py         ← AI analysis & signal generation
├── risk_manager.py     ← Position sizing & risk controls
├── trade_executor.py   ← Main trading loop
├── indicators.py       ← Technical indicator calculations
├── database.py         ← SQLite persistence layer
├── logger.py           ← Real-time logging
├── utils.py            ← Plotly charts & helpers
├── requirements.txt
├── .env.example
├── data/               ← SQLite database (auto-created)
└── logs/               ← CSV & system logs (auto-created)
```

---

## Installation

### 1. Prerequisites

- Python 3.10 or 3.11 (recommended)
- pip

### 2. Clone or unzip the project

```bash
cd deriv_ai_trader
```

### 3. Create a virtual environment (recommended)

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure your Deriv API token

**Option A — .env file (recommended)**
```bash
cp .env.example .env
# Edit .env and paste your token:
# DERIV_API_TOKEN=your_token_here
```

**Option B — In the app**
Enter your API token directly in the sidebar API Configuration field.

#### How to get a Deriv API Token
1. Log in to [app.deriv.com](https://app.deriv.com)
2. Go to **Account Settings → API Token**
3. Create a token with **Trade** and **Read** permissions
4. For demo trading, use your Demo account token

---

## Running the Bot

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`

---

## Usage Guide

### Dashboard Overview

| Section | Description |
|---|---|
| Sidebar | Settings, pair selection, controls |
| KPI Row | Balance, equity, daily P&L, win rate |
| Live Chart | Candlesticks + EMA50/200 + RSI + signal levels |
| Current Signals | Live signal cards per pair |
| Open Positions | Table of active trades |
| Closed Trades | History with P&L colouring |
| Signal History | All generated signals with confidence |
| Trade Log | Real-time log feed |

### Starting the Bot

1. Enter your **Deriv API Token** in the sidebar
2. Select **Demo** or **Real** account type
3. Set your **stake amount**, **risk %**, and **daily loss limit**
4. Choose the **pairs** you want to trade
5. Select the **execution timeframe**
6. Click **▶ START**

The bot will:
- Connect to Deriv WebSocket API
- Scan all selected markets every 60 seconds
- Generate signals using the AI engine
- Execute trades automatically when confidence ≥ 75%
- Monitor and close positions based on TP/SL/CHOCH rules

### Supported Markets

**Forex:** NZD/USD, AUD/CHF, AUD/USD, AUD/NZD, AUD/CAD, NZD/CHF, NZD/CAD, NZD/JPY, CAD/CHF

**Crypto:** LTC/USD, XRP/USD, BCH/USD

---

## Strategy Details

### AI Confidence Scoring

| Component | Weight |
|---|---|
| Market Structure (HH/HL/BOS/CHOCH) | 25% |
| SMC (Order Blocks + FVG + Zones) | 25% |
| RSI Confirmation | 15% |
| EMA Trend (50/200) | 15% |
| Liquidity Sweep | 10% |
| Price Action Patterns | 10% |

Minimum confidence to trade: **75%**

### Entry Conditions (ALL must be met)
- Confidence score ≥ 75%
- HTF trend alignment
- Break of Structure (BOS) confirmed
- Liquidity sweep present
- RSI confirmation

### Exit Conditions
- Take Profit 1/2/3 reached
- Stop Loss hit
- Change of Character (CHOCH) detected
- Opposite signal generated

---

## Risk Management

- **Position Size** = (Balance × Risk%) / SL Distance
- **Daily Loss Halt**: Bot stops automatically if daily loss limit is hit
- **Max Open Trades**: Configurable cap on simultaneous positions
- All parameters adjustable in the sidebar without restarting

---

## Database

SQLite at `data/trader.db` — four tables:

- `signals` — every generated signal
- `trades` — every opened/closed trade with full details
- `account_history` — balance snapshots
- `daily_summary` — aggregated daily stats

---

## Important Notes

> **⚠️ RISK WARNING:** Automated trading involves significant financial risk.
> Always start with a **Demo account** to test and validate the strategy.
> Past performance does not guarantee future results. Never trade with money
> you cannot afford to lose. This software is provided for educational
> purposes — you are solely responsible for any trades placed.

- The bot uses Deriv's **Rise/Fall** (digital options) contract type
- For CFD/Multiplier contracts, the `buy_contract()` method in `deriv_api.py`
  will need to be updated with the appropriate `contract_type` parameter
- The default App ID `1089` is Deriv's public demo app — create your own
  at [api.deriv.com](https://api.deriv.com) for production use

---

## Troubleshooting

| Issue | Solution |
|---|---|
| "Could not connect to Deriv" | Check your token is valid and has Trade permission |
| Chart not loading | Ensure the bot is running and pairs are available |
| "Max open positions" warning | Reduce active pairs or increase Max Open Trades |
| WebSocket timeout | Check internet connection; app will auto-retry |
| Module not found | Run `pip install -r requirements.txt` again |
