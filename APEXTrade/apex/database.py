"""
APEX Database - SQLAlchemy ORM Models
7 core tables matching the Backend Schema spec.
SQLite for v1.0, PostgreSQL-compatible design.
"""
import json
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List

from sqlalchemy import (
    create_engine, Column, Integer, Text, REAL, Boolean,
    DateTime, ForeignKey, UniqueConstraint, CheckConstraint, event, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

Base = declarative_base()
_engine = None
_SessionLocal = None


# ─── Database Initialization ─────────────────────────────────────────────────

def init_db(db_path: str = "apex.db") -> None:
    """Initialize database, create tables and indexes, seed system_state row."""
    global _engine, _SessionLocal

    _engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

    _create_indexes()
    _seed_system_state()


def _create_indexes() -> None:
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status)",
        "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)",
        "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)",
        "CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time ON ohlcv_bars(symbol, timestamp DESC)",
        "CREATE INDEX IF NOT EXISTS idx_drift_strategy ON drift_events(strategy_id)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts_log(alert_type, sent_at DESC)",
    ]
    with _engine.connect() as conn:
        for idx in indexes:
            conn.execute(text(idx))
        conn.commit()


def _seed_system_state() -> None:
    with get_db_session() as session:
        state = session.query(SystemState).filter_by(id=1).first()
        if not state:
            session.add(SystemState(
                id=1,
                trading_mode="paper",
                kill_switch_active=False,
                system_status="idle",
                daily_pnl=0.0,
                daily_trade_count=0,
                daily_loss_limit_pct=2.0,
                max_position_size_pct=2.0,
                last_updated=datetime.utcnow(),
            ))


@contextmanager
def get_db_session():
    """Context manager that yields a transactional session."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ─── ORM Models ───────────────────────────────────────────────────────────────

class Strategy(Base):
    """Every AI-generated strategy version. The core audit log."""
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    strategy_type = Column(Text, nullable=False)       # mean_reversion | momentum | news_driven
    tickers = Column(Text, nullable=False)             # JSON: ["SPY","QQQ"]
    entry_signal = Column(Text, nullable=False)        # Human-readable
    exit_signal = Column(Text, nullable=False)
    entry_conditions = Column(Text)                    # JSON: structured conditions for evaluator
    exit_conditions = Column(Text)                     # JSON: structured conditions for evaluator
    stop_loss_pct = Column(REAL, nullable=False)
    max_position_pct = Column(REAL, nullable=False)
    max_trades_day = Column(Integer, nullable=False)
    avoid_times = Column(Text)                         # JSON: ["09:30-10:00","15:45-16:00"]
    reasoning = Column(Text, nullable=False)
    confidence = Column(Text, nullable=False)          # low | medium | high
    market_context = Column(Text, nullable=False)      # Full JSON context fed to Claude
    raw_llm_output = Column(Text, nullable=False)      # Raw Claude response
    trigger_reason = Column(Text)                      # pre_market | drift_winrate | drift_drawdown | drift_vix
    status = Column(Text, nullable=False, default="active")   # active | superseded | error
    superseded_at = Column(DateTime)
    superseded_by = Column(Integer, ForeignKey("strategies.id"))

    trades = relationship("Trade", back_populates="strategy", foreign_keys="Trade.strategy_id")

    def get_tickers(self) -> List[str]:
        return json.loads(self.tickers) if self.tickers else []

    def get_entry_conditions(self) -> List[dict]:
        return json.loads(self.entry_conditions) if self.entry_conditions else []

    def get_exit_conditions(self) -> List[dict]:
        return json.loads(self.exit_conditions) if self.exit_conditions else []

    def get_avoid_times(self) -> List[str]:
        return json.loads(self.avoid_times) if self.avoid_times else []


class Trade(Base):
    """Every completed trade. Created on entry, updated on exit."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    broker_order_id = Column(Text, nullable=False, unique=True)
    symbol = Column(Text, nullable=False)
    side = Column(Text, nullable=False)                # buy | sell
    qty = Column(REAL, nullable=False)
    entry_price = Column(REAL, nullable=False)
    entry_time = Column(DateTime, nullable=False)
    exit_price = Column(REAL)                          # NULL if still open
    exit_time = Column(DateTime)
    exit_reason = Column(Text)                         # exit_signal | stop_loss | market_close | kill_switch
    gross_pnl = Column(REAL)
    commission = Column(REAL, default=0.0)
    net_pnl = Column(REAL)
    slippage = Column(REAL)                            # entry_price - signal_price
    status = Column(Text, nullable=False, default="open")   # open | closed

    strategy = relationship("Strategy", back_populates="trades", foreign_keys=[strategy_id])


class Position(Base):
    """Real-time open position snapshot. Synced from broker every 60 seconds."""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False, unique=True)
    qty = Column(REAL, nullable=False)
    side = Column(Text, nullable=False)                # long | short
    avg_entry_price = Column(REAL, nullable=False)
    current_price = Column(REAL, nullable=False)
    market_value = Column(REAL, nullable=False)
    unrealized_pnl = Column(REAL, nullable=False)
    unrealized_pnl_pct = Column(REAL, nullable=False)
    stop_loss_price = Column(REAL)
    last_synced = Column(DateTime, nullable=False)


class OHLCVBar(Base):
    """5-minute OHLCV bars with pre-computed technical indicators."""
    __tablename__ = "ohlcv_bars"
    __table_args__ = (UniqueConstraint("symbol", "timestamp"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    open = Column(REAL, nullable=False)
    high = Column(REAL, nullable=False)
    low = Column(REAL, nullable=False)
    close = Column(REAL, nullable=False)
    volume = Column(Integer, nullable=False)
    vwap = Column(REAL)
    rsi_14 = Column(REAL)
    ema_9 = Column(REAL)
    ema_21 = Column(REAL)
    ema_50 = Column(REAL)
    macd = Column(REAL)
    macd_signal = Column(REAL)
    atr_14 = Column(REAL)
    volume_ratio = Column(REAL)


class DriftEvent(Base):
    """Audit log of every drift detection trigger."""
    __tablename__ = "drift_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    drift_type = Column(Text, nullable=False)          # win_rate | drawdown | daily_loss | vix_spike
    metric_baseline = Column(REAL, nullable=False)
    metric_actual = Column(REAL, nullable=False)
    threshold = Column(REAL, nullable=False)
    action_taken = Column(Text, nullable=False)        # regenerate | pause | kill
    new_strategy_id = Column(Integer, ForeignKey("strategies.id"))


class AlertLog(Base):
    """Every alert sent via Telegram or email."""
    __tablename__ = "alerts_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sent_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    alert_type = Column(Text, nullable=False)
    channel = Column(Text, nullable=False)             # telegram | email | both
    content = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="pending")  # pending | sent | failed
    error_message = Column(Text)


class SystemState(Base):
    """Single-row table: global system state. Enforced by CHECK constraint."""
    __tablename__ = "system_state"
    __table_args__ = (CheckConstraint("id = 1"),)

    id = Column(Integer, primary_key=True, default=1)
    active_strategy_id = Column(Integer, ForeignKey("strategies.id"))
    trading_mode = Column(Text, nullable=False, default="paper")   # paper | live
    kill_switch_active = Column(Boolean, nullable=False, default=False)
    kill_switch_reason = Column(Text)
    kill_switch_at = Column(DateTime)
    system_status = Column(Text, nullable=False, default="idle")
    # idle | pre_market | active | paused | killed
    daily_pnl = Column(REAL, nullable=False, default=0.0)
    daily_trade_count = Column(Integer, nullable=False, default=0)
    daily_loss_limit_pct = Column(REAL, nullable=False, default=2.0)
    max_position_size_pct = Column(REAL, nullable=False, default=2.0)
    last_updated = Column(DateTime, nullable=False, default=datetime.utcnow)
