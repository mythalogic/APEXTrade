# APEX: AI Day Trading Platform
## Comprehensive Product Design Document

**Version:** 1.0  
**Date:** June 2026  
**Status:** Ready for Development  
**Build Duration:** 16 weeks (4 months)  

---

## Table of Contents
1. [Executive Summary](#executive-summary)
2. [Problem & Solution](#problem--solution)
3. [System Architecture](#system-architecture)
4. [Core Features & Workflows](#core-features--workflows)
5. [Database Design](#database-design)
6. [User Interface](#user-interface)
7. [Technical Stack](#technical-stack)
8. [Implementation Roadmap](#implementation-roadmap)
9. [Risk Analysis](#risk-analysis)
10. [Success Criteria](#success-criteria)
11. [Design Gaps & Considerations](#design-gaps--considerations)

---

## Executive Summary

**APEX** is an AI-powered adaptive day trading platform that autonomously generates, monitors, and updates trading strategies in real time. Unlike static rule-based trading bots, APEX continuously evaluates market regime conditions and alerts users whenever the strategy changes—combining AI intelligence with human oversight and safety controls.

### Core Value Proposition
- **AI Strategy Generation:** Claude API generates market-specific strategies pre-market each day
- **Real-Time Drift Detection:** Automatically detects when strategies underperform and regenerates new rules
- **Transparency & Control:** Full visibility into active strategy, with human-controlled kill switch and risk limits
- **Autonomous Execution:** Alpaca API integration for automatic order placement and management
- **Comprehensive Monitoring:** Web dashboard + Telegram alerts for real-time awareness

### Target User
Technically literate individual trader comfortable with:
- Basic trading concepts (entries, exits, stops, position sizing)
- Setting risk parameters once
- Reviewing AI-generated strategies
- Operating a cloud-based system
- Paper trading before live deployment

### Market Scope (v1.0)
- **Assets:** US equities only (SPY, QQQ, NVDA, etc.)
- **Trading Hours:** NYSE/NASDAQ (9:30am–4:00pm EST)
- **Deployment:** Single trader account on VPS
- **Mode:** Paper trading primary, live trading optional

---

## Problem & Solution

### Problem Statement

| Problem | Impact | Current Workaround |
|---------|--------|-------------------|
| Manual strategy management | Trader cannot monitor 8 hours/day → missed signals, late exits | Constant vigilance |
| Static bots fail in new regimes | Momentum strategy works in bull market, fails in volatility | Manual bot updates |
| No transparency | Trader doesn't understand why bot enters/exits → loses trust | Black-box distrust |
| Information overload | Too much market data, no actionable synthesis | Manual filtering |
| No adaptive feedback loop | Poor performance continues until manual intervention | Reactive fixes |

### APEX Solution

| Problem | APEX Solution |
|---------|---------------|
| Manual monitoring | Automated strategy regeneration every 30 min if underperforming |
| Static rules | AI continuously adapts strategy to current market regime |
| No transparency | Plain-English strategy display + reasoning from Claude |
| Data overload | Curated market context fed to Claude, not trader |
| Reactive fixes | Drift detection triggers automatic strategy refresh in <2 min |

---

## System Architecture

### Logical Services (5 loosely coupled components)

```
┌─────────────────────────────────────────────────────────────────┐
│                       APEX Platform                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐  ┌──────────────────┐                   │
│  │  DATA PIPELINE   │  │ STRATEGY ENGINE  │                   │
│  │  (Every 5 min)   │  │  (Pre-market +   │                   │
│  │                  │  │   Drift trigger) │                   │
│  │ • Fetch OHLCV    │  │                  │                   │
│  │ • Compute RSI,   │  │ • Call Claude    │                   │
│  │   MACD, EMA, ATR │  │ • Validate JSON  │                   │
│  │ • Fetch news     │  │ • Save to DB     │                   │
│  │ • Update DB      │  │ • Version log    │                   │
│  └────────┬─────────┘  └────────┬─────────┘                   │
│           │                     │                             │
│           └─────────────────────┼─────────────────────────────┘
│                                 │                             │
│      ┌──────────────────────────┴──────────────────────┐      │
│      │         SHARED DATABASE (SQLite)               │      │
│      │  • strategies (AI-generated versions)           │      │
│      │  • trades (completed trade log)                 │      │
│      │  • positions (current open positions)           │      │
│      │  • ohlcv_bars (5-min price data)                │      │
│      │  • drift_events (performance triggers)          │      │
│      │  • alerts_log (all sent alerts)                 │      │
│      │  • system_state (global kill switch, mode)      │      │
│      └────────────────┬─────────────────────────────────┘      │
│                       │                                         │
│  ┌────────────────────┴──────────────┐                         │
│  │                                   │                         │
│  ▼                                   ▼                         │
│ ┌──────────────────┐  ┌──────────────────┐                    │
│ │ EXECUTION ENGINE │  │  DRIFT MONITOR   │                    │
│ │ (Continuous)     │  │ (Every 30 min)   │                    │
│ │                  │  │                  │                    │
│ │ • Scan signals   │  │ • Check win rate │                    │
│ │ • Place orders   │  │ • Check drawdown │                    │
│ │ • Manage stops   │  │ • Check P&L      │                    │
│ │ • Log trades     │  │ • Trigger regen  │                    │
│ └──────────────────┘  │ • Send alerts    │                    │
│                       └──────────────────┘                    │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │        WEB DASHBOARD (FastAPI + React)                   │  │
│  │  • Active strategy display                              │  │
│  │  • Real-time positions & P&L                            │  │
│  │  • Trade log with history                               │  │
│  │  • Alert feed                                           │  │
│  │  • Kill switch control                                  │  │
│  │  • Settings management                                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
                ▼              ▼              ▼
          ┌──────────┐   ┌──────────┐  ┌────────────┐
          │ Alpaca   │   │ Claude   │  │ Telegram   │
          │ API      │   │ API      │  │ Bot        │
          └──────────┘   └──────────┘  └────────────┘
```

### Service Details

| Service | Language | Runs When | Key Responsibility |
|---------|----------|-----------|-------------------|
| **Data Pipeline** | Python 3.11 | Every 5 min (market hours) | Fetch OHLCV, compute indicators, build market context |
| **Strategy Engine** | Python + Claude API | 6:00am EST + drift trigger | Generate AI strategy, validate, version, save |
| **Execution Engine** | Python | Continuous (market hours) | Scan signals, place orders, manage positions |
| **Drift Monitor** | Python | Every 30 min (market hours) | Track win rate/drawdown, trigger strategy regen |
| **Web Dashboard** | FastAPI + React | Always on | Monitor system, control kill switch, view history |

---

## Core Features & Workflows

### Feature Set (v1.0)

#### P0 - Critical (Go-live blockers)
1. **AI Strategy Engine** - Claude generates strategy daily
2. **Live Drift Detection** - Win rate, drawdown, P&L monitoring
3. **Strategy Auto-Update** - Regenerate on drift trigger
4. **Alert System** - Telegram/email on every strategy change
5. **Broker Execution** - Alpaca API order placement & management
6. **Risk Manager** - Enforce daily loss limit, max position size, kill switch
7. **Kill Switch** - Immediate halt to all trading

#### P1 - High (Strongly recommended)
8. **Web Dashboard** - Active strategy, positions, P&L, trade history
9. **Strategy Version Log** - Audit trail of every strategy generated
10. **Paper Trading Mode** - Full simulation without real money

---

### Workflow 1: Daily Operating Loop

```
DAY START (6:00am EST)
    │
    ├─→ APScheduler triggers pre-market job
    │
    ├─→ Check: Is today a trading day? (Alpaca Calendar API)
    │
    ├─→ Fetch overnight data:
    │   • Last 24h news headlines
    │   • SPY/QQQ overnight prices
    │   • VIX level
    │
    ├─→ Compute market context:
    │   • Technical indicators (RSI, MACD, EMA, ATR, volume ratio)
    │   • Market regime (trending/ranging/volatile)
    │   • Sector performance (XLK, XLF, XLE, XLV)
    │   • Last strategy performance (win rate, P&L)
    │
    ├─→ Call Claude API:
    │   • Inject: market context JSON
    │   • Receive: strategy JSON
    │   • Model: claude-sonnet-4-6
    │   • Temperature: 0.3 (deterministic)
    │   • Max tokens: 1000
    │
    ├─→ Validate strategy JSON:
    │   • Parse: strategy_type, tickers, entry_signal, exit_signal, stop_loss_pct, etc.
    │   • Fallback: if malformed, use last valid strategy
    │
    ├─→ Save strategy v1 to DB:
    │   • Store full JSON
    │   • Store market context snapshot
    │   • Store raw Claude response
    │   • Set status: active
    │
    ├─→ Send pre-market alert (Telegram):
    │   "APEX – Morning Strategy | SPY, QQQ
    │    Strategy v4 | Mean Reversion | Confidence: Medium
    │    Entry: RSI(14) < 35 on 15-min bar
    │    Exit: RSI > 55 OR +2% target
    │    Stop Loss: 1.5% | Max Position: 2% | Max Trades: 5"
    │
MARKET OPENS (9:30am)
    │
    ├─→ Every 5 minutes:
    │   ├─→ Fetch OHLCV bars from Alpaca
    │   ├─→ Update technical indicators in DB
    │   └─→ Evaluate entry conditions vs. active strategy
    │
    ├─→ On entry signal:
    │   ├─→ Check position limits (not already max size)
    │   ├─→ Check daily loss limit (not already at max loss)
    │   ├─→ Place market buy order via Alpaca
    │   ├─→ Log trade entry to DB
    │   └─→ Send Telegram alert: "Entry: SPY 100 shares @ $450"
    │
    ├─→ Continuously:
    │   ├─→ Monitor open positions for exit signal
    │   ├─→ Monitor positions for stop loss hit
    │   └─→ Update unrealized P&L in positions table
    │
    ├─→ On exit signal:
    │   ├─→ Place market sell order
    │   ├─→ Update trade record (exit_price, exit_reason, net_pnl)
    │   ├─→ Send Telegram alert: "Exit: SPY 100 @ $455, +$500 P&L"
    │   └─→ Close position record
    │
    ├─→ Every 30 minutes (drift check):
    │   ├─→ Calculate win rate (last 10 trades)
    │   ├─→ Calculate drawdown (peak to trough)
    │   ├─→ Calculate daily P&L delta
    │   │
    │   ├─→ IF win_rate < (baseline - 15%):
    │   │   ├─→ Log drift event: "win_rate_drop"
    │   │   ├─→ Pause trading
    │   │   ├─→ Call Claude API with drift context
    │   │   ├─→ Save new strategy v2, mark v1 as superseded
    │   │   ├─→ Send alert: "STRATEGY UPDATED: Win rate dropped from 62% to 41%"
    │   │   └─→ Resume trading with v2
    │   │
    │   ├─→ ELSE IF drawdown > 5% OR daily_loss > 2%:
    │   │   ├─→ Log drift event
    │   │   ├─→ Auto-trigger kill switch
    │   │   └─→ Send critical alert
    │   │
    │   └─→ ELSE: continue trading
    │
MARKET CLOSE (4:00pm EST)
    │
    ├─→ Close any remaining open positions (market close order)
    │
    ├─→ Generate end-of-day summary:
    │   • Total trades: 5
    │   • Win rate: 60% (3/5)
    │   • Net P&L: +$847
    │   • Max drawdown: 2.3%
    │   • Strategy updates: 1 (drift trigger)
    │
    ├─→ Send EOD alert (Telegram + Email):
    │   "APEX – End of Day | Tue 17 Jun 2026
    │    Trades: 5 | Win: 60% | P&L: +$847
    │    Strategy: Mean Reversion v4 (active: 6:00-16:00)
    │    Strategy: Momentum v5 (active: 11:42-16:00)"
    │
    ├─→ Archive session record to strategy_history table
    │
    └─→ System enters sleep until 6:00am EST next day
```

---

### Workflow 2: Strategy Regeneration (Drift Trigger)

```
Drift Condition Fires
    │
    ├─→ Trigger: Win rate < (baseline - 15%)
    │           OR drawdown > 5%
    │           OR VIX spike > 20%
    │
    ├─→ Log drift event to drift_events table
    │
    ├─→ PAUSE TRADING:
    │   ├─→ Signal scanner pauses (stops scanning for entries)
    │   ├─→ Position manager pauses (stops monitoring exits)
    │   └─→ System state: paused
    │
    ├─→ Refresh market context:
    │   ├─→ Fetch latest prices
    │   ├─→ Fetch latest news
    │   ├─→ Recompute indicators
    │   ├─→ Get last strategy performance stats
    │   └─→ Include drift reason in context
    │
    ├─→ Call Claude API:
    │   ├─→ System prompt: "Strategy generator. Market has shifted. Adapt."
    │   ├─→ User message: JSON context + drift metrics + last strategy performance
    │   ├─→ Receive: new strategy JSON
    │
    ├─→ Validate new strategy:
    │   ├─→ Parse JSON
    │   ├─→ Check: stop_loss_pct <= 5%
    │   ├─→ Check: max_position_pct <= 20%
    │   ├─→ Check: max_trades_day > 0
    │   └─→ Fallback: if invalid, use last valid strategy
    │
    ├─→ Save new strategy v2 to DB:
    │   ├─→ Create new strategies row
    │   ├─→ Mark old strategy v1: status='superseded', superseded_by=v2
    │   └─→ Set v2: status='active', trigger_reason='drift_winrate'
    │
    ├─→ Send alert (Telegram – URGENT):
    │   "APEX – STRATEGY UPDATED | 11:42am EST
    │    Drift: Win rate dropped from 62% to 41% (threshold: -15%)
    │    Previous: Mean Reversion v4 (12 trades, -$43 net)
    │    New: Momentum v5 | Tickers: SPY, NVDA
    │    Entry: EMA(9) > EMA(21) with volume > 1.5x avg
    │    Exit: EMA(9) < EMA(21) OR +3% target
    │    Why: VIX moved 14→19. Trend conditions improving."
    │
    ├─→ RESUME TRADING:
    │   ├─→ Signal scanner resumes (scanning with v2 rules)
    │   ├─→ Position manager resumes (monitoring exits)
    │   └─→ System state: active
    │
    └─→ Continue trading day with new strategy
```

---

### Workflow 3: Kill Switch (Emergency Stop)

```
Kill Switch Trigger (4 possible ways)
    │
    ├─→ Option 1: User clicks red button on dashboard
    │   ├─→ Confirmation modal: "All trades will be closed. Confirm?"
    │   └─→ Click "CONFIRM"
    │
    ├─→ Option 2: Telegram command
    │   ├─→ Send: /kill
    │   ├─→ Bot responds: "Please confirm: /kill_confirm"
    │   └─→ Send: /kill_confirm
    │
    ├─→ Option 3: Daily loss limit breached
    │   ├─→ Risk manager detects: daily_pnl <= -2% of capital
    │   └─→ Auto-trigger (no confirmation needed)
    │
    ├─→ Option 4: Manual SSH to server
    │   └─→ Run: python apex.py --kill
    │
EXECUTION
    │
    ├─→ Set DB flag: system_state.kill_switch_active = True
    │
    ├─→ All services check flag before ANY action:
    │   ├─→ Data pipeline: STOP
    │   ├─→ Strategy engine: STOP
    │   ├─→ Execution engine: STOP
    │   ├─→ Drift monitor: STOP
    │   └─→ (Dashboard continues running for control)
    │
    ├─→ Cancel all open orders:
    │   ├─→ Query Alpaca API: list all open orders
    │   ├─→ For each order: call Alpaca cancel_order()
    │   └─→ Log each cancellation
    │
    ├─→ Close all open positions (OPTIONAL – user choice):
    │   ├─→ Query positions table: all active positions
    │   ├─→ For each position: market sell order
    │   └─→ Wait for fills, log results
    │
    ├─→ Send critical alert (Telegram):
    │   "APEX – KILL SWITCH ACTIVATED
    │    Triggered: Manual (dashboard)
    │    Time: 14:32 EST
    │    Open orders: 2 (CANCELLED)
    │    Open positions: 1 QQQ 100 @ $400 (CLOSED @ $401)
    │    Status: SYSTEM HALTED"
    │
    ├─→ System halted:
    │   ├─→ No new orders can be placed
    │   ├─→ Existing positions frozen
    │   ├─→ Dashboard shows SYSTEM HALTED banner
    │   └─→ User can review and reset from dashboard
    │
RESET
    │
    ├─→ User reviews situation, decides all is OK
    │
    ├─→ Click "RESUME" on dashboard
    │
    ├─→ System sets: system_state.kill_switch_active = False
    │
    └─→ All services resume normal operation at next scheduled job
```

---

### Workflow 4: User First-Time Setup

```
STEP 1: Install and Configure
    │
    ├─→ git clone https://github.com/user/apex.git (private repo)
    ├─→ cd apex
    ├─→ python -m venv .venv
    ├─→ source .venv/bin/activate (or .venv\Scripts\activate on Windows)
    ├─→ pip install -r requirements.txt
    │
STEP 2: Set up Environment Variables
    │
    ├─→ Create .env file:
    │   ALPACA_API_KEY=PK_xxxxxxxxxxxxx
    │   ALPACA_API_SECRET=xxxxxxxxxxxxx
    │   ALPACA_BASE_URL=https://paper-api.alpaca.markets (for paper trading)
    │   ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxx
    │   TELEGRAM_BOT_TOKEN=123456:ABCdefghijklmnop
    │   TELEGRAM_CHAT_ID=987654321
    │   FLASK_SECRET_KEY=some-random-secret
    │
STEP 3: Initialize Database
    │
    ├─→ python scripts/init_db.py
    │   ├─→ Creates SQLite database
    │   ├─→ Creates 7 tables (strategies, trades, positions, ohlcv_bars, drift_events, alerts_log, system_state)
    │   └─→ Sets default system_state row
    │
STEP 4: Configure Risk Rules
    │
    ├─→ Open dashboard or config file
    ├─→ Set: daily_loss_limit_pct = 2.0% (default)
    ├─→ Set: max_position_size_pct = 2.0% (default)
    ├─→ Set: target tickers = ["SPY", "QQQ"] (default)
    ├─→ Set: trading_mode = "paper" (default, NOT live)
    │
STEP 5: Select Trading Mode
    │
    ├─→ Paper Trading (RECOMMENDED for first 4 weeks):
    │   └─→ Real data, simulated orders, no real money
    │
    ├─→ Live Trading (ONLY after paper validation):
    │   ├─→ Update ALPACA_BASE_URL = https://api.alpaca.markets
    │   ├─→ Dashboard shows "LIVE" badge in red
    │   └─→ Real money trading enabled
    │
STEP 6: Run System Check
    │
    ├─→ python scripts/system_check.py
    │   ├─→ Test Alpaca API connection
    │   ├─→ Test Claude API connection
    │   ├─→ Test Telegram bot
    │   ├─→ Test database connectivity
    │   └─→ Print results
    │
STEP 7: Start APEX
    │
    ├─→ python main.py
    │
    ├─→ System boots:
    │   ├─→ Load last strategy from DB
    │   ├─→ Start APScheduler
    │   ├─→ Start FastAPI dashboard server
    │   ├─→ Print: "APEX started. Dashboard running on http://localhost:8000"
    │   └─→ Wait for 6:00am EST (or run pre-market job immediately if before 4:30pm)
    │
STEP 8: Receive First Alert
    │
    ├─→ At 6:00am EST (or immediately):
    │   ├─→ Pre-market job fires
    │   ├─→ Claude generates first strategy
    │   ├─→ Telegram alert sent: "APEX – Morning Strategy | v1 | Mean Reversion | ..."
    │   └─→ Strategy saved to DB
    │
STEP 9: Monitor Dashboard
    │
    ├─→ Open browser: http://localhost:8000
    │
    ├─→ View:
    │   ├─→ Active Strategy Panel (v1 Mean Reversion rules displayed)
    │   ├─→ Metric cards: Daily P&L ($0), Win Rate (—), Open Positions (0), System Status (Active)
    │   ├─→ Trade log (empty)
    │   ├─→ Alert feed (showing morning strategy alert)
    │   └─→ Strategy history accordion
    │
    └─→ READY TO TRADE
```

---

## Database Design

### Schema Overview (7 Tables)

#### Table 1: strategies
**Stores every AI-generated strategy version. The audit log of the system.**

```sql
CREATE TABLE strategies (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  version         INTEGER NOT NULL,                    -- v1, v2, v3, ...
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  strategy_type   TEXT NOT NULL,                       -- 'mean_reversion' | 'momentum' | 'news_driven'
  tickers         TEXT NOT NULL,                       -- JSON: ["SPY","QQQ"]
  entry_signal    TEXT NOT NULL,                       -- "RSI(14) < 35 on 15-min bar"
  exit_signal     TEXT NOT NULL,                       -- "RSI > 55 OR +2% profit target"
  stop_loss_pct   REAL NOT NULL,                       -- 1.5
  max_position_pct REAL NOT NULL,                      -- 2.0
  max_trades_day  INTEGER NOT NULL,                    -- 5
  avoid_times     TEXT,                                -- JSON: ["09:30-10:00", "15:45-16:00"]
  reasoning       TEXT NOT NULL,                       -- "Low-vol ranging expected..."
  confidence      TEXT NOT NULL,                       -- 'low' | 'medium' | 'high'
  market_context  TEXT NOT NULL,                       -- Full JSON context fed to Claude
  raw_llm_output  TEXT NOT NULL,                       -- Raw Claude API response
  trigger_reason  TEXT,                                -- 'pre_market' | 'drift_winrate' | 'drift_drawdown'
  status          TEXT NOT NULL DEFAULT 'active',      -- 'active' | 'superseded' | 'error'
  superseded_at   DATETIME,
  superseded_by   INTEGER REFERENCES strategies(id)    -- FK to v2 (if superseded)
);

INDEX: idx_strategies_status ON strategies(status);
```

**Example Rows:**
```
id=1, version=1, created_at=2026-06-17 06:00:00, strategy_type='mean_reversion', 
tickers='["SPY","QQQ"]', entry_signal='RSI < 35', exit_signal='RSI > 55', 
confidence='medium', status='superseded', superseded_by=2

id=2, version=2, created_at=2026-06-17 11:42:00, strategy_type='momentum', 
tickers='["SPY","NVDA"]', entry_signal='EMA(9) > EMA(21)', exit_signal='EMA(9) < EMA(21)', 
confidence='medium', status='active', trigger_reason='drift_winrate'
```

#### Table 2: trades
**Every completed trade. Created on entry, updated on exit.**

```sql
CREATE TABLE trades (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id     INTEGER NOT NULL REFERENCES strategies(id),
  broker_order_id TEXT NOT NULL UNIQUE,                -- "8dcf45d5-4d7c-4e9f-a7f8-1b2d3e4f5g6h"
  symbol          TEXT NOT NULL,                       -- "SPY"
  side            TEXT NOT NULL,                       -- 'buy' | 'sell'
  qty             REAL NOT NULL,                       -- 100.0
  entry_price     REAL NOT NULL,                       -- 450.25
  entry_time      DATETIME NOT NULL,                   -- 2026-06-17 10:15:00
  exit_price      REAL,                                -- 453.50 (NULL if still open)
  exit_time       DATETIME,                            -- 2026-06-17 11:30:00
  exit_reason     TEXT,                                -- 'exit_signal' | 'stop_loss' | 'market_close'
  gross_pnl       REAL,                                -- (453.50 - 450.25) * 100 = 325
  commission      REAL DEFAULT 0,                      -- 0 (Alpaca paper has no commission)
  net_pnl         REAL,                                -- 325 - 0 = 325
  slippage        REAL,                                -- Difference between signal price and execution
  status          TEXT NOT NULL DEFAULT 'open'         -- 'open' | 'closed'
);

INDEX: idx_trades_symbol ON trades(symbol);
INDEX: idx_trades_status ON trades(status);
```

**Example Rows:**
```
id=1, strategy_id=1, symbol='SPY', side='buy', qty=100, entry_price=450.25, 
entry_time=2026-06-17 10:15:00, exit_price=453.50, exit_time=2026-06-17 11:30:00, 
exit_reason='exit_signal', net_pnl=325, status='closed'

id=2, strategy_id=1, symbol='QQQ', side='buy', qty=50, entry_price=380.00, 
entry_time=2026-06-17 10:45:00, exit_price=NULL, status='open'  (still trading)
```

#### Table 3: positions
**Real-time snapshot of open positions. Synced from Alpaca every 60 seconds.**

```sql
CREATE TABLE positions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol          TEXT NOT NULL UNIQUE,                -- "SPY"
  qty             REAL NOT NULL,                       -- 50.0
  side            TEXT NOT NULL,                       -- 'long' | 'short'
  avg_entry_price REAL NOT NULL,                       -- 450.25
  current_price   REAL NOT NULL,                       -- 453.50
  market_value    REAL NOT NULL,                       -- 450.25 * 50 = 22512.50
  unrealized_pnl  REAL NOT NULL,                       -- (453.50 - 450.25) * 50 = 162.50
  unrealized_pnl_pct REAL NOT NULL,                    -- 162.50 / 22512.50 * 100 = 0.72%
  stop_loss_price REAL,                                -- 444.00 (1.5% below entry)
  last_synced     DATETIME NOT NULL                    -- 2026-06-17 10:45:30
);
```

#### Table 4: ohlcv_bars
**5-minute price bars with pre-computed technical indicators.**

```sql
CREATE TABLE ohlcv_bars (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol          TEXT NOT NULL,                       -- "SPY"
  timestamp       DATETIME NOT NULL,                   -- 2026-06-17 10:15:00
  open            REAL NOT NULL,                       -- 450.00
  high            REAL NOT NULL,                       -- 451.50
  low             REAL NOT NULL,                       -- 449.75
  close           REAL NOT NULL,                       -- 450.25
  volume          INTEGER NOT NULL,                    -- 1250000
  vwap            REAL,                                -- Volume-weighted average price
  rsi_14          REAL,                                -- 45.2 (14-period RSI)
  ema_9           REAL,                                -- 450.10
  ema_21          REAL,                                -- 449.50
  ema_50          REAL,                                -- 448.00
  macd            REAL,                                -- 0.60
  macd_signal     REAL,                                -- 0.55
  atr_14          REAL,                                -- 1.20 (Average True Range)
  volume_ratio    REAL,                                -- 1.05 (current vol / 20-day avg)
  UNIQUE(symbol, timestamp)
);

INDEX: idx_ohlcv_symbol_time ON ohlcv_bars(symbol, timestamp DESC);
```

#### Table 5: drift_events
**Audit log of every drift detection that triggers strategy regeneration.**

```sql
CREATE TABLE drift_events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  detected_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  strategy_id     INTEGER NOT NULL REFERENCES strategies(id),  -- Triggered from v1
  drift_type      TEXT NOT NULL,                       -- 'win_rate' | 'drawdown' | 'daily_loss' | 'vix_spike'
  metric_baseline REAL NOT NULL,                       -- Expected: 62.0 (win rate %)
  metric_actual   REAL NOT NULL,                       -- Actual: 41.0 (win rate %)
  threshold       REAL NOT NULL,                       -- Threshold: -15%
  action_taken    TEXT NOT NULL,                       -- 'regenerate' | 'pause' | 'kill'
  new_strategy_id INTEGER REFERENCES strategies(id)    -- FK to v2
);

INDEX: idx_drift_strategy ON drift_events(strategy_id);
```

**Example Row:**
```
id=1, detected_at=2026-06-17 11:42:00, strategy_id=1, drift_type='win_rate', 
metric_baseline=62.0, metric_actual=41.0, threshold=-15.0, 
action_taken='regenerate', new_strategy_id=2
```

#### Table 6: alerts_log
**Every alert sent to user (Telegram, email, or both).**

```sql
CREATE TABLE alerts_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  sent_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  alert_type      TEXT NOT NULL,                       -- 'morning_strategy' | 'strategy_update' | 'trade' | 'warning' | 'kill' | 'eod' | 'error'
  channel         TEXT NOT NULL,                       -- 'telegram' | 'email' | 'both'
  content         TEXT NOT NULL,                       -- Full message text
  status          TEXT NOT NULL DEFAULT 'pending',     -- 'pending' | 'sent' | 'failed'
  error_message   TEXT                                 -- If failed, error details
);

INDEX: idx_alerts_type ON alerts_log(alert_type, sent_at DESC);
```

#### Table 7: system_state
**Single-row table storing global system state (enforced by CHECK constraint).**

```sql
CREATE TABLE system_state (
  id                    INTEGER PRIMARY KEY DEFAULT 1,
  active_strategy_id    INTEGER REFERENCES strategies(id),  -- Currently active strategy version
  trading_mode          TEXT NOT NULL DEFAULT 'paper',      -- 'paper' | 'live'
  kill_switch_active    BOOLEAN NOT NULL DEFAULT 0,         -- 0 = enabled, 1 = halted
  kill_switch_reason    TEXT,                               -- Why kill switch was triggered
  kill_switch_at        DATETIME,                           -- When kill switch was triggered
  system_status         TEXT NOT NULL DEFAULT 'idle',       -- 'idle' | 'pre_market' | 'active' | 'paused' | 'killed'
  daily_pnl             REAL NOT NULL DEFAULT 0,            -- Current session P&L
  daily_trade_count     INTEGER NOT NULL DEFAULT 0,         -- Trades today
  daily_loss_limit_pct  REAL NOT NULL DEFAULT 2.0,          -- Risk limit: 2% of capital
  max_position_size_pct REAL NOT NULL DEFAULT 2.0,          -- Max position: 2% of capital
  last_updated          DATETIME NOT NULL,
  CHECK (id = 1)  -- Enforces single row
);
```

### Data Flow Through System

```
1. DATA FETCHED (Every 5 min)
   Alpaca API → OHLCV bars → ohlcv_bars table
   Alpha Vantage → News → market_context
   Alpaca Account → Positions → positions table (synced every 60s)

2. STRATEGY GENERATED (6am pre-market + drift trigger)
   Market context + Last strategy performance → Claude API → strategy JSON
   → Validate and save to strategies table (status='active')
   → Previous strategy marked (status='superseded')

3. TRADING HAPPENS (9:30am–4:00pm EST)
   Entry signal detected → Order placed via Alpaca
   → Trade record created in trades table (status='open')
   → Position synced to positions table
   Exit signal detected → Order placed via Alpaca
   → Trade record updated (exit_price, exit_reason, net_pnl, status='closed')
   → Position removed from positions table

4. DRIFT MONITORING (Every 30 min)
   Last 10 trades → Calculate win_rate, drawdown, P&L
   → Check vs. thresholds → Log to drift_events table
   → If breach: regenerate strategy (loop back to 2)

5. ALERTS SENT (Various triggers)
   Morning, trade, drift, EOD → Format message → Send Telegram
   → Log to alerts_log table (status='sent')

6. DASHBOARD VIEWS (Real-time)
   Query active_strategy_id from system_state
   → Get active strategy from strategies table
   → Get open positions from positions table
   → Calculate daily P&L from trades table (today's closed trades)
   → Show in web dashboard
```

---

## User Interface

### Design Philosophy
- **Information Hierarchy:** Critical alerts above everything
- **Dark Theme:** Default, reduces eye strain
- **Semantic Colors:** Green = profit/buy, Red = loss/sell, Amber = warning
- **Status at Glance:** Color-coded P&L visible in <5 seconds
- **Mobile-Aware:** Metric bar stacks on mobile, kill switch always visible

### Color System

| Element | Hex Code | Usage |
|---------|----------|-------|
| Background Primary | #0D1117 | Main page background |
| Background Secondary | #161B22 | Cards, panels, sidebar |
| Background Tertiary | #21262D | Table rows, input fields |
| Signal Green | #00D4AA | Profit, buy signal, active strategy, healthy |
| Signal Red | #FF4757 | Loss, sell signal, drift alert, kill switch, error |
| Signal Amber | #FFA502 | Warning, low confidence, approaching loss limit |
| Accent Red | #C0392B | Primary brand color, headings |
| Text Primary | #E8EDF2 | Main text on dark |
| Text Muted | #7A8694 | Secondary labels, timestamps |

### Main Dashboard Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ APEX Trading  |  🟢 PAPER  |  System: Active  |  Logout             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐          │
│  │  Daily P&L    │  │   Win Rate     │  │    Positions   │          │
│  │  +$847.50     │  │      60%       │  │        1       │          │
│  │  (green)      │  │   (green)      │  │                │          │
│  └────────────────┘  └────────────────┘  └────────────────┘          │
│  ┌────────────────┐                                                  │
│  │ System Status  │                                                  │
│  │   🟢 Active    │                                                  │
│  └────────────────┘                                                  │
│                                                                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  LEFT (60%)                      │  RIGHT (40%)                      │
│  ──────────────────────────────── ────────────────────────────────   │
│                                   │                                  │
│  ACTIVE STRATEGY                  │  ACTIVE STRATEGY PANEL           │
│  ┌─────────────────────────────┐ │  ┌─────────────────────┐        │
│  │ SPY, QQQ | Mean Reversion   │ │  │ Mean Reversion v4   │        │
│  │ Entry: RSI(14) < 35         │ │  │ Confidence: Medium  │        │
│  │ Exit: RSI > 55 OR +2%       │ │  │ Tickers: SPY, QQQ  │        │
│  │ Stop: 1.5% | Max: 2%        │ │  │ Max trades: 5       │        │
│  │ Max trades: 5               │ │  │ Last update: 06:00  │        │
│  │                              │ │  └─────────────────────┘        │
│  │ Confidence: Medium ⚠️        │ │                                  │
│  │ Updated: 6:00 AM (active)    │ │  ALERT FEED                     │
│  └─────────────────────────────┘ │  ┌─────────────────────┐        │
│                                   │  │ 11:42 🔴 DRIFT      │        │
│  POSITIONS TABLE                  │  │ Strategy updated    │        │
│  ┌─────────────────────────────┐ │  │ Win rate dropped    │        │
│  │ Symbol │ Qty │ P&L │ %     │ │  │ 62%→41%             │        │
│  ├────────┼─────┼─────┼───────┤ │  │                     │        │
│  │ SPY    │ 50  │ +160│ +0.72%│ │  │ 10:30 📊 TRADE     │        │
│  │        │     │    │(🟢)    │ │  │ Exit: SPY +$500     │        │
│  ├────────┼─────┼─────┼───────┤ │  │                     │        │
│  │ QQQ    │ 100 │ -85 │ -0.22%│ │  │ 06:00 📋 MORNING   │        │
│  │        │     │    │(🔴)    │ │  │ Strategy v4 active  │        │
│  └─────────────────────────────┘ │  └─────────────────────┘        │
│                                   │                                  │
│  TRADE LOG (Paginated)            │  KILL SWITCH                    │
│  ┌─────────────────────────────┐ │  ┌─────────────────────┐        │
│  │ Time  │ Symbol │ Side │ P&L │ │  │   🛑 HALT ALL      │        │
│  ├───────┼────────┼──────┼─────┤ │  │   TRADING          │        │
│  │ 11:30 │ SPY    │ Sell │ +500│ │  │                     │        │
│  ├───────┼────────┼──────┼─────┤ │  │  (Fixed button,     │        │
│  │ 10:45 │ QQQ    │ Buy  │  — │ │  │   always visible)   │        │
│  ├───────┼────────┼──────┼─────┤ │  └─────────────────────┘        │
│  │ 10:15 │ SPY    │ Buy  │ +160│ │                                  │
│  └─────────────────────────────┘ │                                  │
│                                   │                                  │
│  ◀ 1 2 3 ▶  (pagination)           │                                  │
│                                   │                                  │
│  STRATEGY HISTORY (Accordion)      │                                  │
│  ┌─────────────────────────────┐ │                                  │
│  │ ▶ v4 Mean Reversion (active)│ │                                  │
│  │ ▼ v3 Momentum (superseded)  │ │                                  │
│  │   Active: 10:00–11:42       │ │                                  │
│  │   Trades: 8 | Win: 75%      │ │                                  │
│  │   Drift reason: win_rate    │ │                                  │
│  └─────────────────────────────┘ │                                  │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### Key UX Rules (from design brief)

1. **Kill Switch Always Visible:** Never hidden, fixed position bottom-right
2. **Units on All Numbers:** 2.3% not 2.3, $143 not 143, 100 shares
3. **Color Coding:** Green = profit/healthy, Red = loss/alert, Amber = warning
4. **Staleness Indicator:** Active strategy shows time since last update
5. **Drift Alert Styling:** Red left border on alert feed, prominent
6. **Trading Mode Badge:** "PAPER" badge always visible (or "LIVE" in red)
7. **No Jarring Refreshes:** Subtle update indicators, not page flashes
8. **Mobile Responsive:** Metric bar stacks 2×2 on mobile, kill switch remains fixed

### Alert Templates

#### Morning Strategy Alert (Telegram)
```
APEX – Morning Strategy  |  Tuesday 17 Jun 2026

Strategy v4  |  Mean Reversion  |  Confidence: Medium
Tickers: SPY, QQQ
Entry: RSI(14) < 35 on 15-min bar
Exit: RSI > 55 OR +2% profit target
Stop loss: 1.5% | Max position: 2% | Max trades: 5
Why: Low-vol ranging session expected. Mean reversion favored over trend.
```

#### Strategy Update Alert (Telegram – URGENT)
```
APEX – STRATEGY UPDATED  |  11:42am EST

Drift detected: Win rate dropped from 62% to 41% (threshold: -15%)
Previous: Mean Reversion v4 (12 trades, -$43 net)
New: Momentum v5 | Tickers: SPY, NVDA
Entry: EMA(9) crosses above EMA(21) with volume > 1.5x avg
Exit: EMA(9) crosses below EMA(21) OR +3% target
Why: VIX moved from 14 to 19. Trend conditions improving.
```

#### Trade Alert (Telegram)
```
Entry: SPY 100 shares @ $450.25
Exit: SPY 100 shares @ $453.50 | +$325 P&L | Strategy v4
```

#### Kill Switch Alert (Telegram – CRITICAL)
```
🛑 KILL SWITCH ACTIVATED  |  14:32 EST

Triggered: Manual (dashboard)
Orders cancelled: 2
Positions closed: 1 (QQQ 100 @ $401)
Status: SYSTEM HALTED
```

#### End-of-Day Report (Telegram)
```
APEX – End of Day  |  Tue 17 Jun 2026

Trades: 5 | Win Rate: 60% | Net P&L: +$847
Drawdown: 2.3% | Max Loss: -$123

Strategies active:
  v4 Mean Reversion: 6:00–11:42 (8 trades)
  v5 Momentum: 11:42–16:00 (2 trades)
```

---

## Technical Stack

### Backend (Python 3.11)

| Component | Purpose | Library |
|-----------|---------|---------|
| **Web Framework** | REST API for dashboard | FastAPI |
| **Task Scheduler** | Cron jobs (data fetch, strategy gen, drift check) | APScheduler |
| **Database** | Persistent storage v1.0 | SQLite + SQLAlchemy ORM |
| **Environment** | Config management | python-dotenv |
| **API Clients** | Market data, LLM, alerts | requests, alpaca-py, anthropic, python-telegram-bot |
| **Technical Indicators** | RSI, MACD, EMA, ATR, volume | ta-lib or pandas-ta |
| **JSON Parsing** | Strategy validation | json |
| **Logging** | Observability | logging |
| **Process Manager** | Auto-restart on crash | systemd (Linux) or PM2 |

### Frontend (React 18 + Vite)

| Component | Purpose | Library |
|-----------|---------|---------|
| **Build Tool** | Fast development & production build | Vite |
| **Styling** | Utility-first CSS | Tailwind CSS |
| **Charts** | P&L charts, equity curves | Recharts |
| **State Management** | Global state (kill switch, alerts) | Zustand |
| **API Client** | HTTP requests to backend | Axios |
| **UI Components** | Buttons, modals, tables | Custom + Tailwind |
| **Real-time Updates** | WebSocket or polling | Socket.io or native fetch |

### External APIs

| API | Purpose | Rate Limit |
|-----|---------|-----------|
| **Alpaca Markets** | Price data, order execution | 200 req/min (free tier) |
| **Anthropic Claude** | Strategy generation | Tier 1: 50k tokens/min |
| **Alpha Vantage** | News + sentiment | 25 req/day (free tier) |
| **Telegram Bot** | Alert delivery | 30 msg/sec |
| **yfinance** | Historical data (dev/test) | Best-effort |

### Infrastructure

| Component | Specification |
|-----------|---------------|
| **VPS Provider** | Hetzner or DigitalOcean |
| **OS** | Ubuntu 22.04 LTS |
| **CPU** | 2 vCPU (recommended) |
| **RAM** | 2 GB (recommended) |
| **Storage** | 40 GB SSD |
| **Network** | < 50ms latency to NYSE (US-East region) |
| **Monthly Cost** | ~$8–20 AUD |

---

## Implementation Roadmap

### 16-Week Sprint Plan (8 x 2-week sprints)

#### Sprint 1 (Week 1–2): Environment & Data Pipeline
**Goal:** Data flowing into database  
**Key Deliverables:**
- Python 3.11 venv, dependencies installed
- GitHub private repo with .gitignore
- Alpaca paper account + API keys
- data_fetcher.py: fetch OHLCV bars every 5 min
- historical_backfill.py: pull 2 years of daily bars via yfinance
- SQLite schema created (7 tables)
- APScheduler configured for market hours
- **Milestone:** DB populating with valid OHLCV data every 5 min

**Blockers to Watch:**
- Alpaca API rate limits
- yfinance unofficial API reliability
- Timezone handling (EST vs local)

---

#### Sprint 2 (Week 3–4): Indicator Engine & Strategy Engine v1
**Goal:** First AI strategy generated  
**Key Deliverables:**
- indicators.py: RSI(14), MACD, EMA(9/21/50), ATR(14), volume ratio
- market_context.py: build JSON context for Claude
- strategy_engine.py: call Claude API, parse response
- strategy_store.py: save to DB, version numbering
- Prompt template: system + user message
- **Milestone:** First strategy JSON successfully generated and saved

**Blockers to Watch:**
- Claude API latency
- JSON parsing robustness (fallback to last strategy)
- Market context completeness

---

#### Sprint 3 (Week 5–6): Execution Engine
**Goal:** First paper trade placed  
**Key Deliverables:**
- signal_scanner.py: evaluate entry conditions every 5 min
- order_manager.py: place orders via Alpaca, handle fills
- position_manager.py: monitor exits, stop losses
- trades table logging
- avoid-times logic (no entries first/last 30 min)
- **Milestone:** End-to-end trade from signal to close

**Blockers to Watch:**
- Alpaca paper order execution reliability
- Position sync accuracy
- Slippage calculation

---

#### Sprint 4 (Week 7–8): Drift Detector & Alert System
**Goal:** First drift alert sent  
**Key Deliverables:**
- drift_monitor.py: win rate, drawdown, P&L tracking
- Drift thresholds: win rate –15%, drawdown > 5%, VIX spike > 20%
- Strategy regeneration trigger
- alert_manager.py: format and send Telegram
- Telegram bot: /kill, /status commands
- All alert templates
- **Milestone:** Drift detected, strategy regenerated, alert received in <60s

**Blockers to Watch:**
- Telegram bot token management
- Alert delivery reliability
- Drift calculation accuracy

---

#### Sprint 5 (Week 9–10): Kill Switch & Risk Manager
**Goal:** Kill switch tested  
**Key Deliverables:**
- risk_manager.py: daily loss limit check before every order
- Kill switch DB flag implementation
- All services respect kill flag
- Telegram /kill command with confirmation
- Automatic kill on daily loss limit breach
- system_health.py: API connection checks
- **Milestone:** Kill switch fires cleanly, orders cancelled, system halts

**Blockers to Watch:**
- Race conditions (order placement during kill)
- Position closure order execution
- DB consistency during kills

---

#### Sprint 6 (Week 11–12): Web Dashboard
**Goal:** Dashboard live on VPS  
**Key Deliverables:**
- FastAPI backend with JWT auth
- REST endpoints: /positions, /strategy, /trades, /alerts, /kill
- React + Vite + Tailwind frontend
- Metric cards (Daily P&L, Win Rate, Positions, Status)
- Strategy panel with plain-English rules
- Positions table with color-coded P&L
- Trade log with pagination
- Alert feed
- Kill switch button with confirmation modal
- Strategy history accordion
- VPS deployment + nginx reverse proxy
- **Milestone:** Dashboard accessible, all data synced

**Blockers to Watch:**
- WebSocket real-time updates
- JWT token expiry handling
- Dashboard–backend data sync accuracy
- Mobile responsiveness

---

#### Sprint 7 (Week 13–14): Integration & Backtesting
**Goal:** Backtest pass/fail decision  
**Key Deliverables:**
- End-to-end system integration (all services talking)
- Bug fixes from full-system run
- Backtrader installation and setup
- backtest_runner.py: implement strategy as Backtrader strategy
- Backtests on 2020–2024 data (COVID crash, 2021 bull, 2022 bear)
- Walk-forward test: train 2020–2022, test 2023–2024
- Performance report: Sharpe ratio, max drawdown, win rate, equity curve
- **Milestone:** Go/no-go decision (Sharpe > 0.8? Drawdown < 20%?)

**Blockers to Watch:**
- Backtest data quality
- Walk-forward test regime representativeness
- Strategy overfitting detection

**Decision Gate:** If Sharpe < 0.8 or max drawdown > 20%, revise prompt and re-test.

---

#### Sprint 8 (Week 15–16): Paper Trading Validation
**Goal:** Go-live decision  
**Key Deliverables:**
- 4 weeks of automated paper trading enabled
- Daily P&L, strategy updates, drift events logged
- Dashboard monitored every trading day
- Weekly vs backtest comparison
- Bug fixes from live operation
- **Milestone:** Formal go/no-go assessment against 8 criteria

**Validation Checklist:**
- [ ] Win rate > 50% over 4 weeks
- [ ] Max 5-day drawdown < 10% of paper capital
- [ ] Daily loss limit never exceeded
- [ ] Kill switch fired cleanly at least once
- [ ] Alert delivery < 60s on 95% of alerts
- [ ] System uptime > 98% during market hours
- [ ] Dashboard metrics accurate vs broker
- [ ] No crashes requiring manual intervention

**Decision:** If ALL criteria met, enable live trading. Otherwise, revise and re-paper trade.

---

## Risk Analysis

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|-----------|
| **Backtest Overfitting** | High | High | Walk-forward test, 4-week paper trade minimum, start with small capital |
| **Claude API Outage** | Low | Medium | Cache last valid strategy, execution continues with cached rules |
| **Alpaca API Rate Limit Hit** | Medium | Medium | Queue requests, exponential backoff, stay <200 req/min |
| **VPS Crash During Market Hours** | Low | High | systemd RestartAlways, reload last strategy from DB on restart |
| **Malformed Strategy JSON from Claude** | Medium | Low | JSON validation with fallback to last valid strategy, never crash to no-rules |
| **Daily Loss Limit Breach** | Medium | High | Auto-kill switch enforced before every order |
| **Regime Change Wipes Account** | Low | Critical | Hard daily loss limit, kill switch, start with minimum capital |
| **Zero Trades (Dead Zone)** | Medium | Low | Log as expected behavior in low-volatility, alert if > 3 hours |
| **Position Sync Out of Sync** | Low | Medium | Sync from Alpaca every 60s, reconcile on startup |
| **Telegram Bot Token Compromised** | Low | Medium | Use environment variable, rotate token quarterly |

### Mitigation Strategies

1. **Backtest Overfitting:** Walk-forward testing on different market regimes, 4-week paper validation minimum
2. **API Resilience:** Caching, exponential backoff, fallback to last known good state
3. **Data Consistency:** DB single row for system_state enforces atomicity, strategy versioning prevents gaps
4. **Crash Recovery:** systemd auto-restart, DB recovery on startup
5. **Risk Enforcement:** Risk limits checked BEFORE order placement, non-bypassable circuit breakers
6. **Transparency:** Full audit trail (strategies table, drift_events table, alerts_log table)

---

## Success Criteria

### Paper Trading Phase (4 weeks)

| Criterion | Target | Action if Fails |
|-----------|--------|-----------------|
| Win rate | > 50% of trades | Revise strategy prompt, re-paper trade |
| Max 5-day drawdown | < 10% of capital | Review drift thresholds, do not go live |
| Daily loss limit | Never exceeded (100% compliance) | Mandatory fix before live trading |
| Kill switch tested | Fired cleanly at least once | Run kill switch test, cannot skip |
| Alert delivery | < 60s on 95% of alerts | Debug Telegram, fix before live |
| System uptime | > 98% during market hours | Fix crashes, cannot go live unstable |
| Dashboard accuracy | All metrics match broker | Fix data sync, dashboard must be trusted |
| Strategy updates | Logged and triggered correctly | Verify drift detection accuracy |

### Live Trading Readiness

**GO-LIVE ONLY IF ALL of the following are true:**
1. Paper trading win rate > 50%
2. Max 5-day drawdown < 10%
3. Daily loss limit never breached
4. Kill switch tested and working
5. Alerts reliable (< 60s delivery 95% of time)
6. System uptime > 98%
7. Dashboard metrics accurate
8. Zero crashes requiring manual intervention

**If any criterion fails:** Return to paper trading phase, revise, re-validate.

---

## Design Gaps & Considerations

### Questions Requiring Further Clarification

1. **Strategy Approval Workflow**
   - **Current Design:** AI generates strategy, system trades immediately
   - **Consideration:** Should user approve strategy changes before live? (v2.0 feature)
   - **Recommendation:** Paper trade first to validate, then optional approval toggle for live trading

2. **Position Management During Drift**
   - **Current Design:** New strategy applies only to NEW entries; open positions stay until exit signal/stop loss
   - **Question:** Should open positions from old strategy be closed when new strategy generated?
   - **Recommendation:** Keep positions open (don't force close) to avoid realized losses from bad timing

3. **Multi-Asset Support**
   - **Current Design:** Only US equities (SPY, QQQ, NVDA)
   - **v2.0 Feature:** ASX stocks, crypto, forex
   - **Note:** Database schema already supports arbitrary tickers, just need new data sources

4. **Human Override Capability**
   - **Current Design:** Kill switch only override mechanism
   - **Consideration:** Should user be able to manually place/close trades while system running?
   - **Recommendation:** Add dashboard manual order feature in v2.0; v1.0 focus on trust-building

5. **News Sentiment Integration**
   - **Current Design:** Alpha Vantage headlines fetched, included in market context
   - **Consideration:** Reliability of free tier sentiment scoring?
   - **Recommendation:** Use headlines as context, not automated signal

6. **Backtesting Against Current News**
   - **Current Design:** Backtrader backtest uses only OHLCV data
   - **Limitation:** Cannot backtest how strategy would have reacted to different news on historical dates
   - **Recommendation:** Accept this limitation v1.0, add news replay in v2.0

7. **Performance Attribution**
   - **Current Design:** Track which strategy generated which trades, drift events
   - **Question:** Should each strategy version get a performance scorecard?
   - **Recommendation:** Yes, add "Strategy Performance" report in dashboard (v1.0 phase 2)

8. **Tax Reporting**
   - **Current Design:** All trades logged in trades table
   - **Consideration:** Trade taxes, wash sales, holding periods
   - **Recommendation:** Export trades table to accounting software (TurboTax, CoinTracker) as CSV

9. **Regulatory Compliance**
   - **Current Design:** Personal trading account only, no AFSL required (Australia)
   - **Jurisdiction:** Verify compliance in user's country before deployment
   - **Note:** Not a licensed financial product, user assumes all trading risk

10. **Scale to Multiple Strategies**
    - **Current Design:** One active strategy at a time
    - **v2.0 Feature:** Multiple concurrent strategies on different symbol subsets
    - **Database:** Already supports via strategy_id foreign key in trades

### Potential Issues to Monitor

**Issue #1: Claude API Cost Explosion**
- **Symptom:** Drift triggers too frequently, strategy regenerated 20+ times per day
- **Mitigation:** Add drift cooldown (minimum 30 min between regenerations), cost alerting
- **Fix:** Adjust drift thresholds upward if costs exceed budget

**Issue #2: Alpaca API Outage**
- **Symptom:** Cannot fetch prices or place orders
- **Mitigation:** System logs error, continues using last cached strategy
- **Recovery:** Monitor Alpaca status page, resume manually if needed

**Issue #3: Strategy Generates Contradictory Signals**
- **Symptom:** Entry condition and exit condition both true simultaneously
- **Mitigation:** JSON validation checks for logical contradictions, reject and use last strategy
- **Example:** "Entry: RSI > 70" and "Exit: RSI > 70" (same condition)

**Issue #4: Slippage Exceeds Stop Loss**
- **Symptom:** Market order slips below calculated stop loss
- **Mitigation:** Track slippage metric, alert user if > 0.5%, review position sizing
- **Note:** Paper trading has zero slippage; live trading will have real slippage

**Issue #5: Kill Switch Doesn't Fire During Network Outage**
- **Symptom:** Server loses internet, cannot cancel orders
- **Mitigation:** systemd auto-restart on recovery, manual SSH kill switch
- **Recovery:** Alpaca will manage open orders server-side

### Recommendations for Roadmap

**v1.0 (Current – 16 weeks):**
- Single AI strategy, auto-regenerated on drift
- Paper trading validation
- Basic dashboard with essential metrics
- Kill switch & risk enforcement
- Telegram alerts

**v2.0 (Future – 8–12 weeks):**
- Multi-strategy support (different symbol subsets)
- Human approval workflow for strategy updates
- Backtesting UI (run simulations from dashboard)
- Performance analytics (Sharpe ratio, rolling win rate charts)
- Mobile app with push notifications
- Email alerts + Slack integration
- Tax reporting export

**v3.0+ (Long-term):**
- Multi-asset support (ASX, crypto, forex)
- ML regime classification (scikit-learn / PyTorch)
- Portfolio-level risk management
- API for third-party integrations
- Multi-user SaaS platform

---

## Deployment Checklist (Pre-Live Trading)

- [ ] 4 weeks of paper trading completed
- [ ] All go-live criteria met (win rate, drawdown, uptime, etc.)
- [ ] Backtest Sharpe ratio > 0.8, max drawdown < 20%
- [ ] Kill switch tested and confirmed working
- [ ] Alerts tested (Telegram delivery < 60s)
- [ ] Dashboard showing accurate data vs Alpaca
- [ ] Real brokerage account created with minimum capital ($500–$1,000)
- [ ] Alpaca live API keys generated and stored in environment variables (not .env)
- [ ] VPS uptime monitoring enabled (UptimeRobot)
- [ ] Database backed up to cloud storage
- [ ] Logs archived to cloud storage
- [ ] Kill switch tested with live orders (no money traded, just order placement/cancellation)
- [ ] User has SSH access to VPS for manual intervention
- [ ] Telegram bot alerts confirmed working on live trading system
- [ ] Dashboard deployed behind HTTPS with self-signed cert (or Let's Encrypt)
- [ ] All services running under systemd with RestartAlways policy

---

## Conclusion

**APEX** is a sophisticated yet focused AI trading platform that combines Claude-powered strategy generation with robust risk management and human oversight. The 16-week implementation roadmap prioritizes:

1. **Safety First:** Risk limits, kill switch, and daily loss limits enforced before every decision
2. **Transparency:** Full audit trail of every strategy, trade, and drift event
3. **Adaptive Intelligence:** AI continuously updates strategy based on live market performance
4. **Observability:** Web dashboard and Telegram alerts keep user informed in real time
5. **Validation:** Rigorous backtest and paper trading phases before live deployment

The architecture is modular, allowing future expansion to multi-asset support, multi-strategy trading, and advanced analytics without major refactoring.

**Key Success Factor:** Treat this as a 4-month learning phase. Paper trading reveals what the system learns about market regimes, how often drift triggers, and whether the AI-generated strategies are robust. Only proceed to live trading if the system consistently outperforms backtest expectations over 4 full weeks.

---

**Document Prepared:** June 2026  
**Version:** 1.0  
**Status:** Ready for Sprint 1 kickoff  
**Estimated Delivery:** October 2026 (conditional on paper trading results)
