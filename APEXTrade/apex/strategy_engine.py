"""
APEX Strategy Engine
- Calls Claude API to generate a structured trading strategy
- Validates strategy JSON (with fallback to last valid strategy)
- Versions and stores every strategy in the DB
- Sends morning strategy alert and EOD summary
"""
import json
import logging
from datetime import datetime, date
from typing import Optional, Dict, Any

from apex.database import get_db_session, Strategy, Trade, SystemState, DriftEvent
from apex.alert_manager import AlertManager

logger = logging.getLogger(__name__)

# ─── Claude System Prompt ────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are APEX Strategy Engine — an AI that generates day trading strategies for US equities.

CRITICAL: Return ONLY valid JSON. No markdown fences, no explanation outside JSON.

Required JSON format:
{
  "strategy_type": "mean_reversion" | "momentum" | "news_driven",
  "tickers": ["SYMBOL1", "SYMBOL2"],
  "entry_signal": "<human-readable entry description>",
  "exit_signal": "<human-readable exit description>",
  "entry_conditions": [
    /* Each condition must be one of these supported types:
       {"type":"indicator","indicator":"rsi_14|ema_9|ema_21|ema_50|macd|macd_signal|atr_14|volume_ratio","op":"lt|gt|lte|gte","val":<number>}
       {"type":"ema_cross_above","fast":"ema_9","slow":"ema_21"}
       {"type":"ema_cross_below","fast":"ema_9","slow":"ema_21"}
       {"type":"volume_ratio","op":"gt|lt","val":<number>}
    */
  ],
  "exit_conditions": [
    /* Same indicator types as entry, plus:
       {"type":"profit_pct","op":"gt","val":<number>}
       {"type":"loss_pct","op":"gt","val":<number>}
    */
  ],
  "stop_loss_pct": <number between 0.5 and 5.0>,
  "max_position_size_pct": <number between 0.5 and 10.0>,
  "max_trades_per_day": <integer 1-20>,
  "avoid_times": ["09:30-10:00","15:45-16:00"],
  "reasoning": "<concise explanation tied to the market context provided>",
  "confidence": "low" | "medium" | "high"
}

Rules:
- stop_loss_pct: 0.5–5.0. max_position_size_pct: 0.5–10.0.
- Always include avoid_times covering first 30 min and last 15 min of session.
- entry_conditions must have at least 1 item. exit_conditions must have at least 1 item.
- Confidence: low = uncertain/mixed signals, medium = moderate conviction, high = clear trend/signal.
- Only use tickers provided in the market context.
"""


class StrategyEngine:
    def __init__(self, config, alert_manager: AlertManager):
        self.config = config
        self.alert_manager = alert_manager
        self._anthropic_client = None
        self._init_claude()

    def _init_claude(self) -> None:
        try:
            import anthropic
            self._anthropic_client = anthropic.Anthropic(
                api_key=self.config.anthropic_api_key
            )
            logger.info("Claude client initialised.")
        except Exception as exc:
            logger.error(f"Claude init failed: {exc}")

    # ─── Strategy Generation ─────────────────────────────────────────────────

    def generate_strategy(self, market_context: Dict[str, Any],
                           trigger_reason: str = "pre_market") -> Optional[Strategy]:
        """
        Call Claude API with market context, parse and validate response,
        save to DB, send alert, and return the new Strategy ORM object.
        Falls back to last valid strategy on API failure or malformed JSON.
        """
        if self._anthropic_client is None:
            logger.error("Claude client not initialised.")
            return self._get_active_strategy()

        logger.info(f"Generating strategy via Claude (trigger: {trigger_reason})...")
        context_json = json.dumps(market_context, indent=2, default=str)

        user_message = (
            f"Market context for {market_context.get('date', date.today())}:\n\n"
            f"{context_json}\n\n"
            f"Trigger reason: {trigger_reason}\n\n"
            f"Generate a day trading strategy for the tickers listed in the market context. "
            f"Return ONLY JSON."
        )

        try:
            response = self._anthropic_client.messages.create(
                model=self.config.claude_model,
                max_tokens=self.config.claude_max_tokens,
                temperature=self.config.claude_temperature,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            raw_output = response.content[0].text.strip()
            logger.debug(f"Claude raw output: {raw_output[:200]}...")

        except Exception as exc:
            logger.error(f"Claude API call failed: {exc}")
            self.alert_manager.send_error_alert(f"Claude API error: {exc}")
            return self._get_active_strategy()  # Fallback

        # Parse and validate JSON
        strategy_data = self._parse_and_validate(raw_output)
        if strategy_data is None:
            logger.error("Strategy JSON invalid. Using last valid strategy.")
            return self._get_active_strategy()

        # Save to DB, supersede previous
        strategy = self._save_strategy(strategy_data, raw_output, context_json, trigger_reason)

        # Send morning alert
        if trigger_reason == "pre_market":
            self.alert_manager.send_morning_strategy(strategy)
        # Drift-triggered alerts are sent by drift_monitor.py after calling here

        return strategy

    # ─── JSON Parsing & Validation ───────────────────────────────────────────

    def _parse_and_validate(self, raw: str) -> Optional[Dict]:
        """Parse Claude response JSON and validate all required fields."""
        # Strip any accidental markdown code fences
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error(f"JSON decode error: {exc}. Raw: {raw[:300]}")
            return None

        required = [
            "strategy_type", "tickers", "entry_signal", "exit_signal",
            "entry_conditions", "exit_conditions", "stop_loss_pct",
            "max_position_size_pct", "max_trades_per_day", "reasoning", "confidence",
        ]
        for field in required:
            if field not in data:
                logger.error(f"Strategy JSON missing required field: '{field}'")
                return None

        # Type and range checks
        if data["strategy_type"] not in ("mean_reversion", "momentum", "news_driven"):
            logger.error(f"Invalid strategy_type: {data['strategy_type']}")
            return None
        if not isinstance(data["tickers"], list) or not data["tickers"]:
            logger.error("tickers must be a non-empty list")
            return None
        if not (0.5 <= float(data["stop_loss_pct"]) <= 5.0):
            logger.error(f"stop_loss_pct out of range: {data['stop_loss_pct']}")
            data["stop_loss_pct"] = max(0.5, min(5.0, float(data["stop_loss_pct"])))
        if not (0.5 <= float(data["max_position_size_pct"]) <= 10.0):
            data["max_position_size_pct"] = max(0.5, min(10.0, float(data["max_position_size_pct"])))
        if data["confidence"] not in ("low", "medium", "high"):
            data["confidence"] = "medium"
        if not isinstance(data.get("entry_conditions"), list) or not data["entry_conditions"]:
            logger.error("entry_conditions must be a non-empty list")
            return None
        if not isinstance(data.get("exit_conditions"), list) or not data["exit_conditions"]:
            logger.error("exit_conditions must be a non-empty list")
            return None

        # Validate individual conditions
        if not self._validate_conditions(data["entry_conditions"]):
            return None
        if not self._validate_conditions(data["exit_conditions"]):
            return None

        return data

    def _validate_conditions(self, conditions: list) -> bool:
        """Ensure each condition has valid structure and uses safe indicator names."""
        valid_indicators = {
            "rsi_14", "ema_9", "ema_21", "ema_50",
            "macd", "macd_signal", "atr_14", "volume_ratio",
        }
        valid_ops = {"lt", "gt", "lte", "gte"}
        valid_types = {
            "indicator", "ema_cross_above", "ema_cross_below",
            "volume_ratio", "profit_pct", "loss_pct",
        }

        for cond in conditions:
            if not isinstance(cond, dict):
                logger.error(f"Condition must be a dict: {cond}")
                return False
            ctype = cond.get("type")
            if ctype not in valid_types:
                logger.error(f"Invalid condition type: {ctype}")
                return False
            if ctype == "indicator":
                if cond.get("indicator") not in valid_indicators:
                    logger.error(f"Invalid indicator: {cond.get('indicator')}")
                    return False
                if cond.get("op") not in valid_ops:
                    logger.error(f"Invalid operator: {cond.get('op')}")
                    return False
            if ctype in ("ema_cross_above", "ema_cross_below"):
                if cond.get("fast") not in valid_indicators or cond.get("slow") not in valid_indicators:
                    logger.error(f"Invalid EMA cross condition: {cond}")
                    return False
        return True

    # ─── Strategy Storage ────────────────────────────────────────────────────

    def _save_strategy(self, data: Dict, raw_output: str,
                        context_json: str, trigger_reason: str) -> Strategy:
        """Save new strategy to DB, mark previous as superseded, update system_state."""
        with get_db_session() as session:
            # Determine next version number
            latest = (
                session.query(Strategy)
                .order_by(Strategy.version.desc())
                .first()
            )
            next_version = (latest.version + 1) if latest else 1

            # Mark previous active strategy as superseded
            previous_active = (
                session.query(Strategy)
                .filter_by(status="active")
                .first()
            )
            old_id = None
            if previous_active:
                old_id = previous_active.id
                previous_active.status = "superseded"
                previous_active.superseded_at = datetime.utcnow()

            # Create new strategy
            strategy = Strategy(
                version=next_version,
                created_at=datetime.utcnow(),
                strategy_type=data["strategy_type"],
                tickers=json.dumps(data["tickers"]),
                entry_signal=data["entry_signal"],
                exit_signal=data["exit_signal"],
                entry_conditions=json.dumps(data["entry_conditions"]),
                exit_conditions=json.dumps(data["exit_conditions"]),
                stop_loss_pct=float(data["stop_loss_pct"]),
                max_position_pct=float(data["max_position_size_pct"]),
                max_trades_day=int(data["max_trades_per_day"]),
                avoid_times=json.dumps(data.get("avoid_times", ["09:30-10:00", "15:45-16:00"])),
                reasoning=data["reasoning"],
                confidence=data["confidence"],
                market_context=context_json,
                raw_llm_output=raw_output,
                trigger_reason=trigger_reason,
                status="active",
            )
            session.add(strategy)
            session.flush()  # Get strategy.id

            if old_id:
                old_strat = session.query(Strategy).filter_by(id=old_id).first()
                if old_strat:
                    old_strat.superseded_by = strategy.id

            # Update system_state
            state = session.query(SystemState).filter_by(id=1).first()
            if state:
                state.active_strategy_id = strategy.id
                state.system_status = "active"
                state.last_updated = datetime.utcnow()

            session.flush()
            sid = strategy.id
            version = strategy.version

        logger.info(f"Strategy v{version} (id={sid}) saved — type: {data['strategy_type']}")
        # Re-fetch to return a clean (non-lazy) object
        return self._get_strategy_by_id(sid)

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _get_active_strategy(self) -> Optional[Strategy]:
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state and state.active_strategy_id:
                s = session.query(Strategy).filter_by(id=state.active_strategy_id).first()
                if s:
                    session.expunge(s)
                    return s
        return None

    def _get_strategy_by_id(self, strategy_id: int) -> Optional[Strategy]:
        with get_db_session() as session:
            s = session.query(Strategy).filter_by(id=strategy_id).first()
            if s:
                session.expunge(s)
                return s
        return None

    # ─── EOD Summary ─────────────────────────────────────────────────────────

    def send_eod_summary(self) -> None:
        """Calculate day stats and send end-of-day Telegram alert."""
        try:
            with get_db_session() as session:
                today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                trades = (
                    session.query(Trade)
                    .filter(Trade.exit_time >= today_start, Trade.status == "closed")
                    .all()
                )
                if not trades:
                    self.alert_manager.send_eod_report(0, 0.0, 0.0, 0.0, 0)
                    return

                wins = sum(1 for t in trades if t.net_pnl and t.net_pnl > 0)
                win_rate = wins / len(trades) * 100
                net_pnl = sum(t.net_pnl or 0 for t in trades)

                # Count strategy updates today
                updates = (
                    session.query(DriftEvent)
                    .filter(DriftEvent.detected_at >= today_start)
                    .count()
                )

                # Simple drawdown: worst intraday cumulative pnl
                running = 0.0
                peak = 0.0
                max_dd = 0.0
                for t in sorted(trades, key=lambda x: x.exit_time or datetime.utcnow()):
                    running += t.net_pnl or 0
                    if running > peak:
                        peak = running
                    dd = peak - running
                    if dd > max_dd:
                        max_dd = dd

                max_dd_pct = (max_dd / 100_000 * 100) if max_dd > 0 else 0.0

            self.alert_manager.send_eod_report(
                len(trades), win_rate, net_pnl, max_dd_pct, updates
            )
        except Exception as exc:
            logger.error(f"EOD summary failed: {exc}")
