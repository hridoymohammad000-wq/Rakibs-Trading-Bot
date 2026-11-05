"""Strategy Attribution and Performance Scorecard Tracker."""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Any

STATS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "strategy_stats.json",
)

_lock = threading.Lock()

DEFAULT_STATS = {
    "pullback": {
        "name": "Trend Pullback",
        "signals": 0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "pnl_usdt": 0.0,
        "last_signal_utc": None,
    },
    "ict": {
        "name": "SMC / ICT",
        "signals": 0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "pnl_usdt": 0.0,
        "last_signal_utc": None,
    },
    "range": {
        "name": "Range Liquidity Grab",
        "signals": 0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "pnl_usdt": 0.0,
        "last_signal_utc": None,
    },
}

_memory_stats: Dict[str, Any] = {}


def _normalize_strategy(strategy: str) -> str:
    s = (strategy or "").lower()
    if "pullback" in s:
        return "pullback"
    if "ict" in s or "smc" in s:
        return "ict"
    if "range" in s:
        return "range"
    return "pullback"


def load_stats() -> Dict[str, Any]:
    global _memory_stats
    with _lock:
        if _memory_stats:
            return _memory_stats

        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, "r", encoding="utf-8") as f:
                    _memory_stats = json.load(f)
                    # Ensure all default strategies exist
                    for k, v in DEFAULT_STATS.items():
                        if k not in _memory_stats:
                            _memory_stats[k] = dict(v)
                    return _memory_stats
            except Exception as e:
                print(f"[STRATEGY TRACKER LOAD ERROR]: {e}")

        _memory_stats = {k: dict(v) for k, v in DEFAULT_STATS.items()}
        return _memory_stats


def save_stats():
    with _lock:
        try:
            os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
            with open(STATS_FILE, "w", encoding="utf-8") as f:
                json.dump(_memory_stats, f, indent=2)
        except Exception as e:
            print(f"[STRATEGY TRACKER SAVE ERROR]: {e}")


def record_signal(strategy: str, symbol: str):
    """Record a qualified signal dispatch."""
    load_stats()
    key = _normalize_strategy(strategy)
    with _lock:
        if key in _memory_stats:
            _memory_stats[key]["signals"] += 1
            _memory_stats[key]["last_signal_utc"] = datetime.now(timezone.utc).isoformat()
            _memory_stats[key]["last_symbol"] = symbol
    save_stats()


def record_trade_result(strategy: str, is_win: bool, pnl_usdt: float = 0.0):
    """Record a closed trade win/loss and PnL."""
    load_stats()
    key = _normalize_strategy(strategy)
    with _lock:
        if key in _memory_stats:
            _memory_stats[key]["trades"] += 1
            if is_win:
                _memory_stats[key]["wins"] += 1
            else:
                _memory_stats[key]["losses"] += 1
            _memory_stats[key]["pnl_usdt"] = round(_memory_stats[key]["pnl_usdt"] + pnl_usdt, 2)
    save_stats()


def get_scorecard() -> Dict[str, Any]:
    """Calculate Win Rates and Performance Badges."""
    stats = load_stats()
    scorecard = {}
    
    top_performer = None
    best_pnl = -999999
    underperformer = None
    worst_pnl = 999999

    for key, data in stats.items():
        total_closed = data.get("wins", 0) + data.get("losses", 0)
        win_rate = round((data.get("wins", 0) / total_closed * 100), 1) if total_closed > 0 else None
        pnl = data.get("pnl_usdt", 0.0)

        # Badge assignment
        if pnl > 0 or (win_rate and win_rate >= 50.0):
            badge = "🟢 Improving"
        elif pnl < 0 or (win_rate and win_rate < 50.0):
            badge = "🔴 Underperforming"
        else:
            badge = "🟡 Neutral"

        scorecard[key] = {
            "name": data.get("name", key),
            "signals": data.get("signals", 0),
            "trades": data.get("trades", 0),
            "wins": data.get("wins", 0),
            "losses": data.get("losses", 0),
            "win_rate": win_rate,
            "pnl_usdt": pnl,
            "badge": badge,
        }

        if pnl > best_pnl and total_closed > 0:
            best_pnl = pnl
            top_performer = data.get("name", key)

        if pnl < worst_pnl and total_closed > 0 and pnl < 0:
            worst_pnl = pnl
            underperformer = data.get("name", key)

    return {
        "strategies": scorecard,
        "top_performer": top_performer or "Evaluating...",
        "underperformer": underperformer or "None",
    }


def format_telegram_scorecard() -> str:
    """Format an HTML scorecard block for Telegram."""
    card = get_scorecard()
    lines = ["📊 <b>Strategy Scorecard & Attribution:</b>"]
    
    for key, s in card["strategies"].items():
        wr_str = f"{s['win_rate']}%" if s['win_rate'] is not None else "N/A"
        pnl_str = f"+${s['pnl_usdt']:.2f}" if s['pnl_usdt'] >= 0 else f"-${abs(s['pnl_usdt']):.2f}"
        lines.append(
            f"• <b>{s['name']}:</b> {s['signals']} Sigs | {s['wins']}W - {s['losses']}L | WR: {wr_str} | PnL: {pnl_str} ({s['badge']})"
        )

    lines.append(f"🏆 <b>Top Performer:</b> {card['top_performer']}")
    if card["underperformer"] != "None":
        lines.append(f"⚠️ <b>Needs Tuning:</b> {card['underperformer']}")
        
    return "\n".join(lines)
