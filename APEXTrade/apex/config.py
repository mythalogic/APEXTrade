"""
APEX Configuration
Loads all settings from environment variables via .env file.
"""
import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class APEXConfig:
    # ─── Alpaca Markets ────────────────────────────────────────────────────────
    alpaca_api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_api_secret: str = field(default_factory=lambda: os.getenv("ALPACA_API_SECRET", ""))
    alpaca_paper: bool = field(
        default_factory=lambda: os.getenv("ALPACA_PAPER", "true").lower() == "true"
    )

    # ─── Anthropic Claude ──────────────────────────────────────────────────────
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    claude_model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"))
    claude_max_tokens: int = 1000
    claude_temperature: float = 0.3

    # ─── Telegram Bot ──────────────────────────────────────────────────────────
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # ─── Alpha Vantage (News) ──────────────────────────────────────────────────
    alpha_vantage_key: str = field(default_factory=lambda: os.getenv("ALPHA_VANTAGE_KEY", "demo"))

    # ─── Dashboard ────────────────────────────────────────────────────────────
    dashboard_secret_key: str = field(
        default_factory=lambda: os.getenv("DASHBOARD_SECRET_KEY", "change-this-in-production")
    )
    dashboard_port: int = field(default_factory=lambda: int(os.getenv("DASHBOARD_PORT", "8000")))
    dashboard_username: str = field(default_factory=lambda: os.getenv("DASHBOARD_USERNAME", "admin"))
    dashboard_password: str = field(default_factory=lambda: os.getenv("DASHBOARD_PASSWORD", "apex2026"))

    # ─── Risk Settings ────────────────────────────────────────────────────────
    daily_loss_limit_pct: float = field(
        default_factory=lambda: float(os.getenv("DAILY_LOSS_LIMIT_PCT", "2.0"))
    )
    max_position_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_POSITION_PCT", "2.0"))
    )

    # ─── Trading Targets ──────────────────────────────────────────────────────
    default_tickers: List[str] = field(
        default_factory=lambda: [
            t.strip().upper()
            for t in os.getenv("DEFAULT_TICKERS", "SPY,QQQ,NVDA").split(",")
            if t.strip()
        ]
    )

    # ─── Database ────────────────────────────────────────────────────────────
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "apex.db"))

    # ─── Drift Thresholds ────────────────────────────────────────────────────
    drift_winrate_threshold: float = 15.0    # % drop from baseline to trigger regen
    drift_drawdown_threshold: float = 5.0    # % intraday drawdown triggers regen
    drift_vix_spike_threshold: float = 20.0  # % VIX spike triggers regen
    drift_min_trades: int = 5                # Minimum trades before winrate check
    drift_cooldown_minutes: int = 30         # Min minutes between strategy regenerations

    # ─── Market ───────────────────────────────────────────────────────────────
    market_timezone: str = "US/Eastern"
    market_open_hour: int = 9
    market_open_minute: int = 30
    market_close_hour: int = 16
    market_close_minute: int = 0
    avoid_open_minutes: int = 30             # No new entries in first 30 min
    avoid_close_minutes: int = 15            # No new entries in last 15 min


# Singleton instance
config = APEXConfig()
