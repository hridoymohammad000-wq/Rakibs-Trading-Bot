"""Score 5-8 Near-Miss Radar and Bottleneck Analytics."""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Any

FRIENDLY_REASONS = {
    "WAIT_FOR_15M_CONFIRMATION": "15M Confirmation Pending",
    "INSUFFICIENT_1H_TARGET_RR": "Target RR < 2.0",
    "BREAKOUT_POSSIBLE": "Range Breakout Risk",
    "NO_CLEAR_1H_TREND": "No Clear 1H Trend",
    "WAIT_FOR_15M_LIQUIDITY_SWEEP": "Liquidity Sweep Missing",
    "PULLBACK_TOO_DEEP": "Pullback Too Deep",
    "NO_1H_OB": "No 1H Order Block",
    "NO_15M_DISPLACEMENT": "Displacement Missing",
    "REGIME_MISMATCH": "Regime Mismatch",
    "1H_EMA_FILTER_FAILED": "1H EMA Filter Not Aligned",
    "RISK_REWARD_BELOW_2": "RR Below 2.0",
    "LOW_VOLATILITY": "Low ATR Volatility",
    "HIGH_VOLATILITY": "High ATR Volatility",
}


def analyze_bottlenecks(
    reports: List[Dict[str, Any]],
    min_score: int = 5,
    max_score: int = 8,
) -> Dict[str, Any]:
    """
    Analyzes reports in the 5 to 8 score band to identify developing setups
    and quantify where setups are getting stuck (bottlenecks).
    """
    if not reports:
        return {
            "total_near_miss": 0,
            "top_pipeline": [],
            "bottlenecks": [],
            "primary_bottleneck": "None",
            "summary_line": "No pipeline candidates",
        }

    # Filter reports scoring between min_score and max_score that are not approved candidates
    near_miss = [
        r for r in reports
        if min_score <= r.get("score", 0) <= max_score and not r.get("candidate", False)
    ]

    total_count = len(near_miss)
    if total_count == 0:
        return {
            "total_near_miss": 0,
            "top_pipeline": [],
            "bottlenecks": [],
            "primary_bottleneck": "None",
            "summary_line": "0 in 5-8 score band",
        }

    # Deduplicate top pipeline by symbol (keep highest score per symbol)
    symbols_seen = {}
    for r in sorted(near_miss, key=lambda x: (x.get("score", 0), x.get("turnover_usdt", 0)), reverse=True):
        sym = r.get("symbol")
        if sym and sym not in symbols_seen:
            symbols_seen[sym] = {
                "symbol": sym,
                "strategy": r.get("strategy", ""),
                "score": r.get("score", 0),
                "reason": r.get("reason") or "UNKNOWN",
                "friendly_reason": FRIENDLY_REASONS.get(r.get("reason"), r.get("reason") or "Pending"),
                "turnover_usdt": r.get("turnover_usdt", 0),
            }

    top_pipeline = list(symbols_seen.values())[:5]

    # Calculate bottleneck frequency
    reason_counts: Dict[str, int] = {}
    for r in near_miss:
        raw_reason = r.get("reason") or "UNKNOWN"
        reason_counts[raw_reason] = reason_counts.get(raw_reason, 0) + 1

    bottlenecks = []
    for raw_r, cnt in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True):
        pct = round((cnt / total_count) * 100, 1)
        label = FRIENDLY_REASONS.get(raw_r, raw_r)
        bottlenecks.append({
            "raw_reason": raw_r,
            "label": label,
            "count": cnt,
            "pct": pct,
        })

    primary_bottleneck = f"{bottlenecks[0]['label']} ({bottlenecks[0]['pct']}%)" if bottlenecks else "None"

    # Compact summary for telegram pings
    top_sym = top_pipeline[0]["symbol"] if top_pipeline else "None"
    top_sc = top_pipeline[0]["score"] if top_pipeline else 0
    top_strat = top_pipeline[0]["strategy"].upper() if top_pipeline else ""
    summary_line = f"{total_count} in pipeline (Top: #{top_sym} {top_sc}/13 - {top_strat}) | Blocked by: {primary_bottleneck}"

    return {
        "total_near_miss": total_count,
        "unique_symbols_count": len(symbols_seen),
        "top_pipeline": top_pipeline,
        "bottlenecks": bottlenecks,
        "primary_bottleneck": primary_bottleneck,
        "summary_line": summary_line,
    }


def record_radar_snapshot(analysis: Dict[str, Any], filepath: str = None) -> bool:
    """Save recent radar snapshot for persistence and debugging."""
    if filepath is None:
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "radar_snapshot.json",
        )

    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        payload = {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "analysis": analysis,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return True
    except Exception as e:
        print(f"[RADAR TRACKER ERROR]: {e}")
        return False
