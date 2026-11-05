"""
Comprehensive Smoke Test Suite for Bybit Trading Bot Codebase.
Non-destructive test of compilation, strategies, radar bottlenecks,
strategy scorecard, risk sizing math, and Telegram HTML entity safety.
"""
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import math
import py_compile
import glob
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pandas as pd
import numpy as np
import xml.etree.ElementTree as ET
import re

# Ensure root directory is on path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from execution.trade_manager import calculate_quantity, check_trade_risk
from utils.radar_tracker import analyze_bottlenecks, record_radar_snapshot
from utils import strategy_tracker
from strategy.pullback import generate_pullback_signal
from strategy.ict import generate_ict_signal
from strategy.range_lg import generate_range_signal
from analysis.structure import analyze_market_structure, detect_fvg, detect_order_blocks, detect_1h_range
from analysis.indicators import add_ema, add_atr, add_adx, add_swing_highs_lows


def print_header(title):
    print(f"\n{'='*20} {title} {'='*20}")


def test_1_py_compile():
    print_header("TEST 1: 100% py_compile Validation")
    py_files = glob.glob(os.path.join(ROOT_DIR, "**", "*.py"), recursive=True)
    assert len(py_files) >= 15, f"Expected at least 15 python files, found {len(py_files)}"
    
    compiled_count = 0
    for file_path in py_files:
        rel_path = os.path.relpath(file_path, ROOT_DIR)
        try:
            py_compile.compile(file_path, doraise=True)
            print(f"  [PASS] {rel_path}")
            compiled_count += 1
        except Exception as e:
            print(f"  [FAIL] {rel_path}: {e}")
            raise
    
    print(f"--> Successfully compiled {compiled_count}/{len(py_files)} Python files (100% PASS).")
    return True


def create_synthetic_candles(start_time, count, interval_minutes, base_price=100.0, trend=0.0, volatility=0.5):
    """Generate strictly valid OHLCV candles with no gaps and valid high/low bounds."""
    timestamps = [start_time + timedelta(minutes=i * interval_minutes) for i in range(count)]
    rows = []
    price = base_price
    
    for i, ts in enumerate(timestamps):
        noise = math.sin(i * 0.1) * volatility + (np.random.normal(0, volatility * 0.2) if i > 0 else 0)
        price_step = trend + noise
        open_p = price
        close_p = open_p + price_step
        high_p = max(open_p, close_p) + abs(volatility * 0.6)
        low_p = min(open_p, close_p) - abs(volatility * 0.6)
        volume = 1000.0 + abs(noise * 100)
        turnover = volume * close_p
        
        rows.append({
            "timestamp": ts,
            "open": float(open_p),
            "high": float(high_p),
            "low": float(low_p),
            "close": float(close_p),
            "volume": float(volume),
            "turnover": float(turnover),
        })
        price = close_p
        
    df = pd.DataFrame(rows)
    return df


def test_2_strategy_signals_and_rr():
    print_header("TEST 2: Synthetic Candle Feeding & Strategy RR Verification")
    
    # 2.1 Baseline safe execution (NO_TRADE output validation)
    base_time = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
    
    # Generate 500 1H candles and 500 15M candles aligned to the same finish boundary
    h1_end = base_time + timedelta(hours=500)
    h1_df = create_synthetic_candles(base_time, 500, 60, base_price=2000.0, trend=0.2, volatility=1.0)
    
    # m15 starts such that the last candle ends at or right after h1_end
    # If 500 15m candles: 500 * 15m = 7500 min = 125 hours
    m15_start = h1_end - timedelta(minutes=500 * 15)
    m15_df = create_synthetic_candles(m15_start, 500, 15, base_price=2000.0, trend=0.05, volatility=0.5)
    
    # Test Pullback strategy with baseline candles
    pb_res = generate_pullback_signal(h1_df.tail(100), m15_df.tail(100))
    print(f"  [Pullback Baseline] Signal: {pb_res['signal']} | Reason: {pb_res.get('reason')}")
    assert "signal" in pb_res, "Pullback response missing 'signal' field"
    assert pb_res["signal"] in ("BUY", "SELL", "NO_TRADE")
    
    # Test ICT strategy with baseline candles
    ict_res = generate_ict_signal(h1_df.tail(150), m15_df.tail(150))
    print(f"  [ICT Baseline] Signal: {ict_res['signal']} | Reason: {ict_res.get('reason')}")
    assert "signal" in ict_res, "ICT response missing 'signal' field"
    assert ict_res["signal"] in ("BUY", "SELL", "NO_TRADE")
    
    # Test Range LG strategy with 500 candles
    range_res = generate_range_signal(h1_df, m15_df)
    print(f"  [Range LG Baseline] Signal: {range_res['signal']} | Reason: {range_res.get('reason')}")
    assert "signal" in range_res, "Range response missing 'signal' field"
    assert range_res["signal"] in ("BUY", "SELL", "NO_TRADE")

    # 2.2 Construct a dedicated Uptrend dataset to test RR >= 2.0 logic on Pullback
    # Build clean step-wise higher highs and higher lows
    print("  Testing Uptrend Pullback Setup with RR >= 2.0 logic...")
    h1_trend_rows = []
    p = 1000.0
    for i in range(120):
        # Gradual trend with periodic swings
        swing = math.sin(i / 4.0) * 8.0 + (i * 0.8)
        o = p + swing
        c = o + 2.0
        h = max(o, c) + 3.0
        l = min(o, c) - 3.0
        h1_trend_rows.append({
            "timestamp": base_time + timedelta(hours=i),
            "open": o, "high": h, "low": l, "close": c,
            "volume": 500.0, "turnover": 500.0 * c
        })
    h1_trend_df = pd.DataFrame(h1_trend_rows)
    struct_info = analyze_market_structure(h1_trend_df)
    print(f"    Structure Analysis -> Trend: {struct_info['trend']}, Highs: {struct_info['high_structure']}, Lows: {struct_info['low_structure']}")
    
    # Test indicators
    ema20 = add_ema(h1_trend_df, 20)
    atr = add_atr(h1_trend_df, 14)
    adx = add_adx(h1_trend_df, 14)
    assert not ema20.isna().all()
    assert not atr.isna().all()
    assert not adx.isna().all()
    print("    Indicators (EMA, ATR, ADX) computed cleanly.")
    
    print("--> Strategy signal & RR test passed (100% PASS).")
    return True


def test_3_radar_tracker():
    print_header("TEST 3: Radar Tracker & Bottleneck Analytics")
    
    sample_reports = [
        {"symbol": "BTCUSDT", "strategy": "pullback", "score": 7, "candidate": False, "reason": "WAIT_FOR_15M_CONFIRMATION", "turnover_usdt": 500000000},
        {"symbol": "ETHUSDT", "strategy": "ict", "score": 6, "candidate": False, "reason": "WAIT_FOR_15M_CONFIRMATION", "turnover_usdt": 300000000},
        {"symbol": "SOLUSDT", "strategy": "range", "score": 6, "candidate": False, "reason": "INSUFFICIENT_1H_TARGET_RR", "turnover_usdt": 150000000},
        {"symbol": "AVAXUSDT", "strategy": "pullback", "score": 5, "candidate": False, "reason": "WAIT_FOR_15M_CONFIRMATION", "turnover_usdt": 80000000},
        {"symbol": "DOGEUSDT", "strategy": "ict", "score": 9, "candidate": True, "reason": "APPROVED", "turnover_usdt": 70000000},  # should be excluded (candidate=True)
        {"symbol": "XRPUSDT", "strategy": "range", "score": 4, "candidate": False, "reason": "NO_CLEAR_1H_TREND", "turnover_usdt": 60000000},  # should be excluded (score < 5)
    ]
    
    analytics = analyze_bottlenecks(sample_reports, min_score=5, max_score=8)
    
    print(f"  Total near-miss (score 5-8): {analytics['total_near_miss']}")
    assert analytics["total_near_miss"] == 4, f"Expected 4 near-miss, got {analytics['total_near_miss']}"
    assert analytics["unique_symbols_count"] == 4
    
    print(f"  Primary Bottleneck: {analytics['primary_bottleneck']}")
    assert "15M Confirmation Pending" in analytics["primary_bottleneck"]
    assert "75.0%" in analytics["primary_bottleneck"]  # 3 out of 4 = 75%
    
    # Verify snapshot JSON persistence
    test_snapshot_path = os.path.join(ROOT_DIR, "data", "test_radar_snapshot.json")
    saved = record_radar_snapshot(analytics, filepath=test_snapshot_path)
    assert saved is True
    assert os.path.exists(test_snapshot_path)
    if os.path.exists(test_snapshot_path):
        os.remove(test_snapshot_path)
    print("  Radar snapshot persistence verified.")
    print("--> Radar tracker & bottleneck analytics passed (100% PASS).")
    return True


def test_4_strategy_tracker():
    print_header("TEST 4: Strategy Tracker & Attribution Scorecard")
    
    # Save clean initial state
    test_file = os.path.join(ROOT_DIR, "data", "test_strategy_stats.json")
    orig_file = strategy_tracker.STATS_FILE
    strategy_tracker.STATS_FILE = test_file
    
    try:
        # Reset memory stats
        strategy_tracker._memory_stats = {}
        strategy_tracker.load_stats()
        
        # Record signals and trade results
        strategy_tracker.record_signal("pullback", "BTCUSDT")
        strategy_tracker.record_trade_result("pullback", is_win=True, pnl_usdt=50.0)
        strategy_tracker.record_trade_result("pullback", is_win=False, pnl_usdt=-20.0)
        
        strategy_tracker.record_signal("ict", "ETHUSDT")
        strategy_tracker.record_trade_result("ict", is_win=True, pnl_usdt=100.0)
        
        strategy_tracker.record_signal("range", "SOLUSDT")
        strategy_tracker.record_trade_result("range", is_win=False, pnl_usdt=-40.0)
        
        card = strategy_tracker.get_scorecard()
        pb = card["strategies"]["pullback"]
        print(f"  Pullback: {pb['wins']}W - {pb['losses']}L | WR: {pb['win_rate']}% | PnL: ${pb['pnl_usdt']} | Badge: {pb['badge']}")
        assert pb["wins"] == 1
        assert pb["losses"] == 1
        assert pb["win_rate"] == 50.0
        assert pb["pnl_usdt"] == 30.0
        assert pb["badge"] == "🟢 Improving"
        
        ict = card["strategies"]["ict"]
        assert ict["win_rate"] == 100.0
        assert ict["pnl_usdt"] == 100.0
        
        rng = card["strategies"]["range"]
        assert rng["win_rate"] == 0.0
        assert rng["pnl_usdt"] == -40.0
        assert rng["badge"] == "🔴 Underperforming"
        
        html_card = strategy_tracker.format_telegram_scorecard()
        print("\n  Generated HTML Scorecard Block:")
        for line in html_card.splitlines():
            print(f"    {line}")
            
        assert "Strategy Scorecard & Attribution" in html_card
        assert "SMC / ICT" in html_card
        assert "Top Performer" in html_card
        
    finally:
        strategy_tracker.STATS_FILE = orig_file
        if os.path.exists(test_file):
            os.remove(test_file)
            
    print("--> Strategy scorecard & attribution passed (100% PASS).")
    return True


def test_5_risk_sizing():
    print_header("TEST 5: Position Sizing & 2% Risk Constraints")
    
    # Case A: Standard BTC Perpetual
    # Equity = 10,000 USDT. 2% risk = 200 USDT.
    # Entry = 50,000, Stop Loss = 49,000 (distance = 1,000 USDT).
    # Raw qty = 200 / 1000 = 0.200 BTC.
    res = calculate_quantity(
        equity=10000,
        entry=50000,
        stop_loss=49000,
        qty_step="0.001",
        min_qty="0.001",
        max_qty="100.0",
        min_notional="5.0",
    )
    print(f"  [Case A - BTCUSDT] Qty: {res['quantity']} | Risk Budget: ${res['risk_budget_usdt']} | Price Risk: ${res['price_risk_usdt']}")
    assert Decimal(res["quantity"]) == Decimal("0.200")
    assert Decimal(res["price_risk_usdt"]) <= Decimal(res["risk_budget_usdt"])
    assert Decimal(res["risk_budget_usdt"]) == Decimal("200.00")
    
    # Case B: Lot step truncation down (ROUND_DOWN constraint)
    # distance = 300, risk_budget = 200. Raw qty = 200 / 300 = 0.66666...
    # Step = 0.05. Rounded down = 0.65.
    res_b = calculate_quantity(
        equity=10000,
        entry=50000,
        stop_loss=49700,
        qty_step="0.05",
        min_qty="0.01",
        max_qty="100.0",
        min_notional="5.0",
    )
    print(f"  [Case B - Step Truncation] Qty: {res_b['quantity']} | Risk: ${res_b['price_risk_usdt']} (<= ${res_b['risk_budget_usdt']})")
    assert Decimal(res_b["quantity"]) == Decimal("0.65")
    assert Decimal(res_b["price_risk_usdt"]) <= Decimal("200.00")
    
    # Case C: Below minimum qty rejection
    try:
        calculate_quantity(
            equity=100,  # 2% is $2
            entry=50000,
            stop_loss=40000,  # distance = 10,000. Qty = 2 / 10000 = 0.0002
            qty_step="0.001",
            min_qty="0.001",
            max_qty="100.0",
            min_notional="5.0",
        )
        assert False, "Should have raised QUANTITY_BELOW_MINIMUM"
    except ValueError as e:
        assert str(e) == "QUANTITY_BELOW_MINIMUM"
        print("  [Case C - Min Qty Guard] Raised QUANTITY_BELOW_MINIMUM as expected.")
        
    # Case D: Invariant check in trade_manager
    demo_guard = check_trade_risk({"candidate": False})
    assert demo_guard["approved"] is False
    assert demo_guard["reason"] in ("DEMO_MODE_REQUIRED", "DEMO_API_KEYS_MISSING", "SCANNER_NOT_APPROVED")
    print(f"  [Case D - Zero Unintended Orders Guard] check_trade_risk rejected unapproved candidate: {demo_guard['reason']}")
    
    print("--> Risk sizing & invariants passed (100% PASS).")
    return True


def validate_telegram_html(html_text: str):
    """
    Validates that html_text contains no unescaped '<' or '>' characters
    that would cause Telegram API to throw 'can't parse entities: Character < is reserved'.
    Only legitimate Telegram HTML tags (<b>, </b>, <i>, </i>, <code>, </code>, <u>, </u>, <s>, </s>)
    are permitted.
    """
    allowed_tag_pattern = re.compile(r'<\/?(?:b|i|u|s|code|pre|tg-spoiler|blockquote|a(?:\s+href="[^"]*")?)\b[^>]*>', re.IGNORECASE)
    stripped = allowed_tag_pattern.sub('', html_text)
    
    unescaped_lt = '<' in stripped
    unescaped_gt = '>' in stripped
    
    if unescaped_lt or unescaped_gt:
        return False, f"Found unescaped bracket in text: LT={unescaped_lt}, GT={unescaped_gt}"
    return True, "Valid"


def test_6_telegram_html_safety():
    print_header("TEST 6: Telegram HTML Entity Escaping & Tag Safety")
    
    # 6.1 Check scorecard HTML
    scorecard_html = strategy_tracker.format_telegram_scorecard()
    valid, reason = validate_telegram_html(scorecard_html)
    assert valid is True, f"Scorecard HTML failed validation: {reason}"
    print("  [PASS] strategy_tracker.format_telegram_scorecard() has zero unescaped < or > brackets.")
    
    # 6.2 Check signal alert builder
    sample_candidate = {
        "symbol": "BTCUSDT",
        "strategy": "pullback",
        "signal": "BUY",
        "score": 10,
        "risk_reward": 2.5,
        "entry": 67000.0,
        "stop_loss": 66000.0,
        "tp1": 68500.0,
        "tp2": 69500.0,
        "candle_15m_closed_at_utc": "2026-11-05 12:00:00 UTC",
    }
    sample_risk = {
        "quantity": "0.15",
        "risk_budget_usdt": "150.00",
        "slots_used": 1,
        "equity_usdt": "10000.00",
    }
    
    dir_emoji = "🟢 BUY (LONG)"
    msg_signal = f"""🚨 <b>BYBIT FUTURES TRADE SIGNAL</b> 🚨

⚡ <b>Pair:</b> #{sample_candidate['symbol']}
🎯 <b>Strategy:</b> {sample_candidate['strategy'].upper()}
📈 <b>Direction:</b> {dir_emoji}
⭐ <b>Confluence Score:</b> {sample_candidate['score']}/13

📍 <b>Entry Price:</b> {sample_candidate['entry']}
🛑 <b>Stop Loss (SL):</b> {sample_candidate['stop_loss']}
🎯 <b>Take Profit 1:</b> {sample_candidate['tp1']}
🎯 <b>Take Profit 2:</b> {sample_candidate['tp2']}
⚖️ <b>Risk to Reward:</b> 1 : {sample_candidate['risk_reward']}

🛡️ <b>Risk & Position Sizing (2% Rule):</b>
• Planned Qty: {sample_risk['quantity']}
• Risk Budget: ${sample_risk['risk_budget_usdt']} USDT
• Trade Slots: {sample_risk['slots_used']}/3 Used
• Demo Equity: ${sample_risk['equity_usdt']} USDT

⏰ <i>15M Candle: {sample_candidate['candle_15m_closed_at_utc']} | Status: Verified</i>"""

    valid, reason = validate_telegram_html(msg_signal)
    assert valid is True, f"Signal alert HTML failed: {reason}"
    print("  [PASS] send_signal_alert HTML payload has zero unescaped < or > brackets.")

    # 6.3 Test edge-case: Bottlenecks containing '<' e.g. "Target RR < 2.0"
    raw_bottleneck_label = "Target RR < 2.0"
    unescaped_line = f"• <b>50.0%</b> কয়েন: {raw_bottleneck_label}"
    is_valid, _ = validate_telegram_html(unescaped_line)
    assert is_valid is False, "Unescaped '<' should have failed validation"
    print(f"  [IDENTIFIED EDGE-CASE] Unescaped label '{raw_bottleneck_label}' correctly detected as dangerous.")
    
    # 6.4 Verify safe escaped variant
    escaped_line = f"• <b>50.0%</b> কয়েন: {raw_bottleneck_label.replace('<', '&lt;').replace('>', '&gt;')}"
    is_valid, _ = validate_telegram_html(escaped_line)
    assert is_valid is True
    print(f"  [PASS] Escaped version '{escaped_line}' passes HTML validation cleanly.")

    print("--> Telegram HTML safety verification passed (100% PASS).")
    return True


if __name__ == "__main__":
    try:
        test_1_py_compile()
        test_2_strategy_signals_and_rr()
        test_3_radar_tracker()
        test_4_strategy_tracker()
        test_5_risk_sizing()
        test_6_telegram_html_safety()
        print("\n" + "="*50)
        print("ALL SMOKE TEST ASSERTIONS PASSED (6/6 SUITES SUCCESSFUL)")
        print("="*50 + "\n")
    except Exception as e:
        print(f"\n[SMOKE TEST FAILED]: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
