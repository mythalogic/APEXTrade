"""
APEX Web Dashboard — FastAPI REST Backend
JWT-authenticated endpoints serving the React frontend.

Endpoints:
  GET  /api/status          — System state snapshot
  GET  /api/strategy        — Active strategy detail
  GET  /api/strategies      — Strategy history (all versions)
  GET  /api/positions       — Current open positions
  GET  /api/trades          — Trade log (paginated)
  GET  /api/alerts          — Alert feed (last 50)
  GET  /api/drift           — Recent drift events
  POST /api/kill            — Activate kill switch
  POST /api/resume          — Reset kill switch
  PUT  /api/settings        — Update risk settings
  POST /api/auth/login      — Get JWT token
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List

import jwt
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apex.database import (
    get_db_session, SystemState, Strategy, Trade, Position,
    AlertLog, DriftEvent
)

logger = logging.getLogger(__name__)

app = FastAPI(title="APEX Dashboard API", version="1.0.0")

# Allow React dev server in development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_security = HTTPBearer()

# Config is injected on startup (set by APEXTrade.py)
_config = None


def set_config(config) -> None:
    global _config
    _config = config


# ─── Auth ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@app.post("/api/auth/login", response_model=TokenResponse)
def login(req: LoginRequest):
    if not _config:
        raise HTTPException(status_code=503, detail="Service not ready")
    if req.username != _config.dashboard_username or req.password != _config.dashboard_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    payload = {
        "sub": req.username,
        "exp": datetime.utcnow() + timedelta(hours=24),
    }
    token = jwt.encode(payload, _config.dashboard_secret_key, algorithm="HS256")
    return TokenResponse(access_token=token)


def _require_auth(credentials: HTTPAuthorizationCredentials = Depends(_security)):
    if not _config:
        raise HTTPException(status_code=503, detail="Service not ready")
    try:
        jwt.decode(credentials.credentials, _config.dashboard_secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ─── System Status ────────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status(_: None = Depends(_require_auth)):
    with get_db_session() as session:
        state = session.query(SystemState).filter_by(id=1).first()
        if not state:
            raise HTTPException(status_code=503, detail="System not initialised")
        return {
            "trading_mode": state.trading_mode,
            "system_status": state.system_status,
            "kill_switch_active": state.kill_switch_active,
            "kill_switch_reason": state.kill_switch_reason,
            "kill_switch_at": _fmt_dt(state.kill_switch_at),
            "daily_pnl": state.daily_pnl,
            "daily_trade_count": state.daily_trade_count,
            "daily_loss_limit_pct": state.daily_loss_limit_pct,
            "max_position_size_pct": state.max_position_size_pct,
            "active_strategy_id": state.active_strategy_id,
            "last_updated": _fmt_dt(state.last_updated),
        }


# ─── Strategy ─────────────────────────────────────────────────────────────────

@app.get("/api/strategy")
def get_active_strategy(_: None = Depends(_require_auth)):
    with get_db_session() as session:
        state = session.query(SystemState).filter_by(id=1).first()
        if not state or not state.active_strategy_id:
            return {"strategy": None}
        s = session.query(Strategy).filter_by(id=state.active_strategy_id).first()
        if not s:
            return {"strategy": None}
        return {"strategy": _strategy_to_dict(s)}


@app.get("/api/strategies")
def get_strategy_history(limit: int = 50, _: None = Depends(_require_auth)):
    with get_db_session() as session:
        strategies = (
            session.query(Strategy)
            .order_by(Strategy.created_at.desc())
            .limit(limit)
            .all()
        )
        return {"strategies": [_strategy_to_dict(s) for s in strategies]}


# ─── Positions ────────────────────────────────────────────────────────────────

@app.get("/api/positions")
def get_positions(_: None = Depends(_require_auth)):
    with get_db_session() as session:
        positions = session.query(Position).all()
        return {
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "side": p.side,
                    "avg_entry_price": p.avg_entry_price,
                    "current_price": p.current_price,
                    "market_value": p.market_value,
                    "unrealized_pnl": p.unrealized_pnl,
                    "unrealized_pnl_pct": round(p.unrealized_pnl_pct, 4),
                    "stop_loss_price": p.stop_loss_price,
                    "last_synced": _fmt_dt(p.last_synced),
                }
                for p in positions
            ]
        }


# ─── Trades ───────────────────────────────────────────────────────────────────

@app.get("/api/trades")
def get_trades(page: int = 1, per_page: int = 20, _: None = Depends(_require_auth)):
    offset = (page - 1) * per_page
    with get_db_session() as session:
        total = session.query(Trade).count()
        trades = (
            session.query(Trade)
            .order_by(Trade.entry_time.desc())
            .offset(offset)
            .limit(per_page)
            .all()
        )
        return {
            "total": total,
            "page": page,
            "per_page": per_page,
            "trades": [
                {
                    "id": t.id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "qty": t.qty,
                    "entry_price": t.entry_price,
                    "entry_time": _fmt_dt(t.entry_time),
                    "exit_price": t.exit_price,
                    "exit_time": _fmt_dt(t.exit_time),
                    "exit_reason": t.exit_reason,
                    "net_pnl": t.net_pnl,
                    "status": t.status,
                    "strategy_id": t.strategy_id,
                }
                for t in trades
            ],
        }


# ─── Alerts ───────────────────────────────────────────────────────────────────

@app.get("/api/alerts")
def get_alerts(limit: int = 50, _: None = Depends(_require_auth)):
    with get_db_session() as session:
        alerts = (
            session.query(AlertLog)
            .order_by(AlertLog.sent_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "alerts": [
                {
                    "id": a.id,
                    "sent_at": _fmt_dt(a.sent_at),
                    "alert_type": a.alert_type,
                    "channel": a.channel,
                    "content": a.content,
                    "status": a.status,
                }
                for a in alerts
            ]
        }


# ─── Drift Events ─────────────────────────────────────────────────────────────

@app.get("/api/drift")
def get_drift_events(limit: int = 20, _: None = Depends(_require_auth)):
    with get_db_session() as session:
        events = (
            session.query(DriftEvent)
            .order_by(DriftEvent.detected_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "events": [
                {
                    "id": e.id,
                    "detected_at": _fmt_dt(e.detected_at),
                    "strategy_id": e.strategy_id,
                    "drift_type": e.drift_type,
                    "metric_baseline": e.metric_baseline,
                    "metric_actual": e.metric_actual,
                    "threshold": e.threshold,
                    "action_taken": e.action_taken,
                    "new_strategy_id": e.new_strategy_id,
                }
                for e in events
            ]
        }


# ─── Kill Switch ─────────────────────────────────────────────────────────────

class KillRequest(BaseModel):
    reason: Optional[str] = "Manual — dashboard"
    close_positions: bool = False


@app.post("/api/kill")
def activate_kill_switch(req: KillRequest, _: None = Depends(_require_auth)):
    """Activate kill switch. Optionally close all open positions."""
    from apex.risk_manager import RiskManager
    if not _config:
        raise HTTPException(status_code=503, detail="Service not ready")

    rm = RiskManager(_config)
    rm.activate_kill_switch(reason=req.reason or "Manual — dashboard")

    orders_cancelled = 0
    positions_closed = 0

    try:
        from apex.execution_engine import ExecutionEngine
        from apex.alert_manager import AlertManager
        am = AlertManager(_config)
        ee = ExecutionEngine(_config, rm, am)
        orders_cancelled = ee.cancel_all_open_orders()
        if req.close_positions:
            positions_closed = ee.close_all_positions(reason="kill_switch")
        am.send_kill_switch_alert(
            req.reason or "Manual — dashboard", orders_cancelled, positions_closed
        )
    except Exception as exc:
        logger.error(f"Kill switch execution error: {exc}")

    return {
        "status": "kill_switch_activated",
        "orders_cancelled": orders_cancelled,
        "positions_closed": positions_closed,
    }


@app.post("/api/resume")
def reset_kill_switch(_: None = Depends(_require_auth)):
    """Reset kill switch and resume normal operation."""
    from apex.risk_manager import RiskManager
    if not _config:
        raise HTTPException(status_code=503, detail="Service not ready")
    rm = RiskManager(_config)
    rm.reset_kill_switch()
    return {"status": "kill_switch_reset", "system_status": "idle"}


# ─── Settings ─────────────────────────────────────────────────────────────────

class SettingsRequest(BaseModel):
    daily_loss_limit_pct: Optional[float] = None
    max_position_size_pct: Optional[float] = None
    trading_mode: Optional[str] = None


@app.put("/api/settings")
def update_settings(req: SettingsRequest, _: None = Depends(_require_auth)):
    from apex.risk_manager import RiskManager
    if not _config:
        raise HTTPException(status_code=503, detail="Service not ready")
    rm = RiskManager(_config)
    try:
        rm.update_risk_settings(
            daily_loss_limit_pct=req.daily_loss_limit_pct,
            max_position_size_pct=req.max_position_size_pct,
        )
        if req.trading_mode:
            rm.set_trading_mode(req.trading_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "settings_updated"}


# ─── Health Check (no auth) ───────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_dt(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _strategy_to_dict(s: Strategy) -> dict:
    return {
        "id": s.id,
        "version": s.version,
        "created_at": _fmt_dt(s.created_at),
        "strategy_type": s.strategy_type,
        "tickers": s.get_tickers(),
        "entry_signal": s.entry_signal,
        "exit_signal": s.exit_signal,
        "entry_conditions": s.get_entry_conditions(),
        "exit_conditions": s.get_exit_conditions(),
        "stop_loss_pct": s.stop_loss_pct,
        "max_position_pct": s.max_position_pct,
        "max_trades_day": s.max_trades_day,
        "avoid_times": s.get_avoid_times(),
        "reasoning": s.reasoning,
        "confidence": s.confidence,
        "trigger_reason": s.trigger_reason,
        "status": s.status,
        "superseded_at": _fmt_dt(s.superseded_at),
        "superseded_by": s.superseded_by,
    }
