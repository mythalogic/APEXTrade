# APEX: Quick Reference Guide
## One-Pager for Developers & Stakeholders

---

## What is APEX?

**APEX** is an AI-powered day trading platform that:
- **Generates** trading strategies each morning using Claude API (based on market conditions)
- **Monitors** performance in real-time and detects when strategies underperform
- **Regenerates** strategies automatically when drift is detected (win rate drops, drawdown exceeds limits)
- **Executes** trades via Alpaca API with hard risk controls (daily loss limit, kill switch)
- **Alerts** user via Telegram on every strategy change and trade

**Not:** A financial advisory service, multi-user platform, or options/crypto trader  
**Is:** Personal trading tool with human oversight and AI-generated strategies

---

## The Problem APEX Solves

| Problem | APEX Solution |
|---------|---------------|
| Trader can't monitor 8h/day | Automated strategy regeneration every 30 min if underperforming |
| Static bots fail in new market conditions | AI continuously adapts strategy to current regime |
| "Black box" trading bot | Plain-English strategy display + reasoning from Claude |
| Information overload | System filters market data, Claude synthesizes into strategy |
| Manual intervention required | Drift detection triggers automatic strategy refresh |

---

## Core Flows (Plain English)

### Daily Loop
```
6:00am  → System wakes up
        → Fetch overnight data & market context
        → Call Claude: "Generate strategy for today"
        → Claude generates strategy v1
        → Save to DB, send Telegram alert

9:30am  → Market opens
        → Every 5 min: check if entry signal fires → place order
        → Continuously: monitor exits & stops
        → Every 30 min: check if strategy is working (win rate, drawdown)
        
11:42am → Drift detected! Win rate dropped 62% → 41%
        → Pause trading
        → Call Claude: "Market changed, new strategy needed"
        → Claude generates strategy v2
        → Resume trading with v2
        → Send Telegram alert explaining what changed & why

4:00pm  → Market closes
        → Close any remaining open positions
        → Send end-of-day summary
        → System sleeps until tomorrow 6am
```

### Kill Switch (Emergency Stop)
```
User clicks red button on dashboard
        ↓
Confirmation dialog: "All trades will close"
        ↓
User confirms
        ↓
Database flag: kill_switch_active = TRUE
        ↓
All services check flag → return immediately (no trading)
        ↓
Cancel all open orders
        ↓
Optionally: close all positions (user choice)
        ↓
Send critical Telegram alert
        ↓
System halted until user resets flag
```

---

## Key Numbers to Remember

| Metric | Value | Purpose |
|--------|-------|---------|
| Strategy update frequency | Pre-market + every 30 min | Detect & adapt to drift |
| Data fetch cycle | Every 5 min (market hours) | Keep indicators fresh |
| Drift check cycle | Every 30 min | Performance monitoring |
| Win rate threshold | > 50% (go-live criterion) | Strategy must beat coin flip |
| Max drawdown | < 10% (paper) / < 8% (live) | Risk control |
| Daily loss limit | -2% of capital | Stop-out level |
| Position size limit | 2% of capital per trade | Risk per trade |
| Alert delivery SLA | < 60 sec | Time critical |
| System uptime target | 99.5% market hours | High reliability |
| Paper trading duration | 4 weeks minimum | Validation before live |

---

## Technology Stack (Simplified)

**Backend:**
- Python 3.11 (all core logic)
- FastAPI (REST API for dashboard)
- SQLite (database)
- APScheduler (run jobs every 5/30 min)
- Anthropic Claude API (strategy generation)
- Alpaca API (price data + order execution)
- Telegram Bot API (alerts)

**Frontend:**
- React 18 + Vite
- Tailwind CSS (styling)
- Recharts (graphs)
- Zustand (state management)

**Infrastructure:**
- Ubuntu 22.04 on Hetzner or DigitalOcean
- 2 vCPU, 2GB RAM (~$8–20 AUD/month)
- systemd for auto-restart

---

## Database Schema (7 Tables)

```
strategies       → Every AI-generated strategy version with full context
trades           → Every completed trade (entry, exit, P&L)
positions        → Current open positions (synced from Alpaca every 60s)
ohlcv_bars       → 5-min price bars with pre-computed indicators
drift_events     → Audit log of every drift detection & strategy regeneration
alerts_log       → Every alert sent (status: pending/sent/failed)
system_state     → Global state (active strategy, kill switch, trading mode)
```

**Key insight:** Full audit trail stored. Can replay any day's trading.

---

## Features (v1.0)

### P0 (Go-live blockers)
✅ AI Strategy Engine (Claude generates strategy daily)  
✅ Live Drift Detection (win rate, drawdown, P&L monitoring)  
✅ Strategy Auto-Update (regenerate on drift)  
✅ Alert System (Telegram on every change)  
✅ Broker Execution (Alpaca API orders)  
✅ Risk Manager (daily loss limit, kill switch)  
✅ Kill Switch (immediate halt)  

### P1 (Strongly recommended)
✅ Web Dashboard (active strategy, positions, P&L, alerts)  
✅ Strategy Version Log (audit trail)  
✅ Paper Trading Mode (full simulation)  

### Future (v2.0+)
❌ Multi-strategy support  
❌ Human approval workflow  
❌ Backtesting UI  
❌ Mobile app  
❌ Multi-asset (ASX, crypto, forex)  

---

## 16-Week Implementation Roadmap

| Sprint | Weeks | Goal | Key Deliverable | Blocker Risk |
|--------|-------|------|-----------------|--------------|
| S1 | 1–2 | Data Pipeline | DB populating with OHLCV bars | Alpaca connectivity |
| S2 | 3–4 | Indicator + Strategy Engine | First AI strategy generated | Claude API reliability |
| S3 | 5–6 | Execution Engine | First paper trade placed | Alpaca order API |
| S4 | 7–8 | Drift Detector + Alerts | First drift alert sent | Telegram bot setup |
| S5 | 9–10 | Kill Switch + Risk Manager | Kill switch tested & working | Race condition bugs |
| S6 | 11–12 | Web Dashboard | Dashboard live on VPS | Frontend complexity |
| S7 | 13–14 | Integration + Backtesting | Backtest pass/fail decision | Backtest overfitting |
| S8 | 15–16 | Paper Trading Validation | Go/no-go assessment | Performance in live conditions |

**Go-live decision:** Only if ALL 8 go-live criteria met (win rate >50%, drawdown <10%, uptime >98%, etc.)

---

## Design Gaps Requiring Clarification (Before Sprint 1)

| # | Gap | Impact | Status |
|---|-----|--------|--------|
| 1 | Claude API JSON validation edge cases | Could break strategy parsing | Design in progress |
| 2 | Alpaca paper account availability | Blocks initial development | Verify before Sprint 1 |
| 3 | Market context JSON completeness (news sentiment) | Claude may lack context | Define schema before Sprint 2 |
| 4 | Dead zone handling (no signals for 3+ hours) | May need strategy regen | Add logic before Sprint 4 |
| 5 | Position sizing algorithm details | Critical for risk manager | Finalize before Sprint 3 |
| 6 | Alert retry logic for failed Telegram | May miss critical alerts | Implement Sprint 4 |

**→ See APEX_GAP_ANALYSIS.md for detailed breakdown**

---

## Success Criteria (Go-Live Checklist)

After 4 weeks of paper trading, APEX goes live ONLY if ALL true:

- [ ] Win rate > 50% of trades
- [ ] Max 5-day drawdown < 10% of capital
- [ ] Daily loss limit never exceeded (100% compliance)
- [ ] Kill switch tested and working cleanly
- [ ] Alert delivery < 60 sec on 95% of alerts
- [ ] System uptime > 98% market hours
- [ ] Dashboard metrics accurate vs Alpaca broker
- [ ] Zero unplanned crashes during validation period

**If ANY criterion fails:** Return to paper trading, revise, re-validate.

---

## Critical Safety Features (Non-Bypassable)

1. **Daily Loss Limit** – Enforced BEFORE every order (if breached → auto-kill switch)
2. **Position Size Limit** – Enforced at order creation (system cannot exceed max % per trade)
3. **Kill Switch** – Global flag in database (all services check before any action)
4. **Strategy Fallback** – If Claude API returns malformed JSON → use last valid strategy (never crash to no-rules)
5. **Drift Detection** – If strategy underperforms → auto-regenerate (no waiting for manual intervention)

---

## Deploy to VPS (Quick Steps)

```bash
# On VPS (Ubuntu 22.04):
1. git clone <private-repo>
2. python -m venv .venv && source .venv/bin/activate
3. pip install -r requirements.txt
4. cp .env.template .env  (fill in API keys)
5. python scripts/init_db.py  (create database)
6. systemctl start apex (or: python main.py)
7. Open browser: http://localhost:8000 (or https://yourdomain.com)
```

**Security:** Never commit .env, use environment variables on production VPS.

---

## Key Commands (Developer Reference)

```python
# Initialize database
python scripts/init_db.py

# Run system connectivity check
python scripts/system_check.py

# Start APEX
python main.py

# Run backtest
python backtest_runner.py --start 2020-01-01 --end 2024-12-31

# Trigger kill switch (manual)
python apex.py --kill

# View logs
tail -f /var/log/apex/trading.log
```

---

## Monitoring Checklist (Daily During Paper Trading)

- [ ] Dashboard loads (all metrics up to date)
- [ ] Strategy showing correctly (today's AI-generated rules visible)
- [ ] Trades logged (check trade log for entries/exits)
- [ ] Alerts received (Telegram alerts arriving for trades/drift)
- [ ] No crashes (check logs for errors)
- [ ] Positions synced (positions table matches Alpaca)
- [ ] P&L accurate (daily_pnl matches net trades)

**If any alert:** Check logs (`tail -f /var/log/apex/`), restart services if needed.

---

## Common Pitfalls to Avoid

❌ **Don't:** Go live before 4 weeks of paper trading  
✅ **Do:** Validate backtest assumptions in paper trading first

❌ **Don't:** Use live trading API keys during development  
✅ **Do:** Always use paper trading account until ready

❌ **Don't:** Change strategy generation prompt without re-backtesting  
✅ **Do:** Always backtest new prompts on 2020–2024 data

❌ **Don't:** Disable kill switch or daily loss limit  
✅ **Do:** Keep all circuit breakers enabled always

❌ **Don't:** Rely on Telegram as only alert channel  
✅ **Do:** Monitor dashboard regularly, have email alert backup

❌ **Don't:** Forget to backup database  
✅ **Do:** Daily backup to cloud storage (AWS S3, etc.)

---

## Support & Resources

### Documentation
- `APEX_PRODUCT_DESIGN.md` – Complete system design (10K+ words)
- `APEX_GAP_ANALYSIS.md` – Issues & clarifications needed
- This quick reference → You are here

### Code Structure
```
apex/
├── main.py                 # Entry point
├── requirements.txt        # Dependencies
├── .env.template           # Config template
├── scripts/
│   ├── init_db.py         # Database initialization
│   ├── system_check.py    # API connectivity test
│   └── backtest_runner.py # Historical backtesting
├── apex/
│   ├── data_pipeline.py   # Fetch OHLCV, indicators
│   ├── strategy_engine.py # Claude API strategy generation
│   ├── execution_engine.py # Order placement & management
│   ├── drift_monitor.py   # Performance tracking
│   ├── risk_manager.py    # Position sizing, kill switch
│   ├── alert_manager.py   # Telegram alerts
│   └── database.py        # SQLAlchemy ORM models
├── dashboard/
│   ├── backend/           # FastAPI REST API
│   └── frontend/          # React Vite SPA
└── tests/
    ├── test_indicators.py
    ├── test_strategy.py
    └── test_integration.py
```

### External Resources
- Alpaca API docs: https://docs.alpaca.markets
- Claude API docs: https://docs.anthropic.com
- FastAPI docs: https://fastapi.tiangolo.com
- React docs: https://react.dev

---

## Key Contacts / Questions?

**For product design questions:**
→ See APEX_PRODUCT_DESIGN.md section [Design Gaps & Considerations]

**For development blockers:**
→ See APEX_GAP_ANALYSIS.md section [Critical Issues]

**For risk/trading questions:**
→ See APEX_PRODUCT_DESIGN.md section [Risk Analysis]

---

**Last Updated:** June 26, 2026  
**Status:** Ready for Sprint 1  
**Next Step:** Resolve design gaps, then begin development  
**Questions?** Review APEX_PRODUCT_DESIGN.md for comprehensive details
