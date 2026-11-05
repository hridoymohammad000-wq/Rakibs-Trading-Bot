"""Run the scanner after each 15M UTC candle close, plus Hourly and Daily Telegram Digests."""
import argparse
import html
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import logger
from utils import watchdog
from utils.radar_tracker import analyze_bottlenecks, record_radar_snapshot
from utils import strategy_tracker
from analysis.scanner import scan_market
from analysis.structure import analyze_market_structure
from data.market_data import get_ohlcv
from execution.trade_manager import check_trade_risk
from notifications.telegram import (
    send_alert,
    send_signal_alert,
    send_scan_ping,
    send_hourly_digest,
    send_daily_summary,
)
from pybit.unified_trading import HTTP
from config.settings import API_KEY, API_SECRET, USE_TESTNET, USE_DEMO

# Global scan lock to prevent concurrent scan execution between HTTP pings and timer loop
SCAN_LOCK = threading.Lock()

# Global session metrics
_metrics = {
    "hourly_scans": 0,
    "hourly_signals": 0,
    "daily_scans": 0,
    "daily_signals": 0,
    "start_equity": None,
    "current_equity": "1001.88",
    "slots_used": 0,
    "last_top_radar": None,
}

_last_hourly_mark = None
_last_daily_mark = datetime.now(timezone.utc).date()


def get_live_account_state():
    """Fetch live Bybit Demo balance and open slots."""
    try:
        client = HTTP(testnet=USE_TESTNET, demo=USE_DEMO, api_key=API_KEY, api_secret=API_SECRET)
        wallet = client.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        accs = wallet.get("result", {}).get("list", [])
        if accs:
            for coin in accs[0].get("coin", []):
                if coin.get("coin") == "USDT":
                    eq = Decimal(coin.get("equity") or "0")
                    _metrics["current_equity"] = f"{eq:.2f}"
                    if _metrics["start_equity"] is None:
                        _metrics["start_equity"] = _metrics["current_equity"]

        # Fetch open positions count
        pos = client.get_positions(category="linear", settleCoin="USDT", limit=50)
        open_pos = sum(
            Decimal(p.get("size") or "0") > 0
            for p in pos.get("result", {}).get("list", [])
        )
        _metrics["slots_used"] = open_pos
        watchdog.validate_account_invariants(client)
    except Exception as e:
        logger.debug(f"Account state read: {e}")

    return _metrics["current_equity"], _metrics["slots_used"]


def next_scan_time(now: datetime) -> datetime:
    """Next UTC 15-minute boundary plus 10 seconds."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    boundary = now.replace(
        minute=(now.minute // 15) * 15,
        second=0,
        microsecond=0,
    )
    target = boundary + timedelta(seconds=10)

    if target <= now:
        target += timedelta(minutes=15)

    return target


def check_macro_market():
    """Fetch quick 1H trend summary for BTC & ETH."""
    res = {"btc": "Sideways", "eth": "Neutral"}
    try:
        btc_df = get_ohlcv("BTCUSDT", interval="60", limit=60)
        if not btc_df.empty:
            info = analyze_market_structure(btc_df)
            res["btc"] = f"{info['trend']} (EMA20: {info['ema20']:.0f})"
    except Exception as e:
        logger.debug(f"Macro BTC read: {e}")

    try:
        eth_df = get_ohlcv("ETHUSDT", interval="60", limit=60)
        if not eth_df.empty:
            info = analyze_market_structure(eth_df)
            res["eth"] = f"{info['trend']} (EMA20: {info['ema20']:.1f})"
    except Exception as e:
        logger.debug(f"Macro ETH read: {e}")

    return res


def scan_once(last_alert_key=None, block: bool = False):
    acquired = SCAN_LOCK.acquire(blocking=block)
    if not acquired:
        print("[CONCURRENCY] Market scan already running in another thread/request. Skipping duplicate execution.", flush=True)
        return last_alert_key

    try:
        print("\n=== BYBIT DEMO SCANNER ===", flush=True)

        # Sync live account balance from Bybit Demo
        get_live_account_state()

        result = scan_market(max_pairs=50)
        watchdog.record_scan_success()

        _metrics["hourly_scans"] += 1
        _metrics["daily_scans"] += 1

        print(
            f"Scanned: {result['scanned_pairs']} | Failed: {result['failed_pairs']} | Candidates: {len(result['candidates'])}",
            flush=True,
        )

        if result["failed_pairs"] > 0 and result["scanned_pairs"] == 0:
            print("[BLOCK] Scanner errors detected", flush=True)
            return last_alert_key

        # Track highest score report for radar and analyze 5-8 bottlenecks
        top_report = None
        radar_analysis = None
        if result.get("reports"):
            top_report = max(result["reports"], key=lambda r: r.get("score", 0))
            _metrics["last_top_radar"] = top_report
            try:
                radar_analysis = analyze_bottlenecks(result["reports"])
                record_radar_snapshot(radar_analysis)
                _metrics["last_radar_analysis"] = radar_analysis
            except Exception as e:
                logger.debug(f"Radar analysis error: {e}")

        # If qualified candidates found:
        signal_sent = False
        if result.get("top_candidates"):
            for candidate in result["top_candidates"]:
                key = (
                    candidate["symbol"],
                    candidate["strategy"],
                    candidate["signal"],
                    candidate["candle_15m_closed_at_utc"],
                )

                if key == last_alert_key:
                    continue

                risk = check_trade_risk(candidate)

                if not risk["approved"]:
                    print(f"[BLOCK] {candidate['symbol']}: {risk['reason']}", flush=True)
                    continue

                _metrics["current_equity"] = risk.get("equity_usdt", _metrics["current_equity"])
                _metrics["slots_used"] = risk.get("slots_used", 0)

                # Send rich 15M Signal Alert
                send_signal_alert(candidate, risk)
                strategy_tracker.record_signal(candidate.get("strategy", ""), candidate.get("symbol", ""))
                _metrics["hourly_signals"] += 1
                _metrics["daily_signals"] += 1
                signal_sent = True
                print(f"[ALERT SENT] {candidate['symbol']} {candidate['signal']}", flush=True)
                return key

        # If no signal qualified, send quiet 15M ping
        if not signal_sent:
            print("NO QUALIFIED SIGNAL -> Sending 15M Scan Ping", flush=True)
            send_scan_ping(
                scanned_count=result["scanned_pairs"],
                top_radar=top_report,
                radar_analysis=radar_analysis,
            )

        # Check hourly & daily digests seamlessly
        now = datetime.now(timezone.utc)
        check_and_send_hourly(now)
        check_and_send_daily(now)

        return last_alert_key
    finally:
        SCAN_LOCK.release()


def check_and_send_hourly(current_time: datetime = None, last_hourly_mark: datetime = None):
    """Trigger hourly digest at the top of each hour (XX:00)."""
    global _last_hourly_mark
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    mark_to_check = last_hourly_mark if last_hourly_mark is not None else _last_hourly_mark

    watchdog.check_watchdog_health()
    current_hour_mark = current_time.replace(minute=0, second=0, microsecond=0)
    if mark_to_check is None or current_hour_mark > mark_to_check:
        eq, slots = get_live_account_state()
        macro = check_macro_market()
        top_watch = "None"
        if _metrics["last_top_radar"]:
            r = _metrics["last_top_radar"]
            top_watch = f"#{r['symbol']} ({r['strategy'].upper()} - Score {r['score']}/13)"

        scorecard_html = strategy_tracker.format_telegram_scorecard()
        bottlenecks_html = ""
        if _metrics.get("last_radar_analysis"):
            ra = _metrics["last_radar_analysis"]
            if ra.get("bottlenecks"):
                b_lines = ["🚧 <b>Pipeline Bottlenecks (কোথায় আটকাচ্ছে):</b>"]
                for b in ra["bottlenecks"][:3]:
                    b_lines.append(f"• <b>{b['pct']}%</b> কয়েন: {html.escape(str(b['label']))}")
                bottlenecks_html = "\n".join(b_lines)

        send_hourly_digest(
            hour_utc=current_hour_mark.strftime("%Y-%m-%d %H:00"),
            scans_completed=_metrics["hourly_scans"],
            signals_count=_metrics["hourly_signals"],
            slots_used=slots,
            equity=eq,
            btc_summary=macro["btc"],
            eth_summary=macro["eth"],
            top_watch=top_watch,
            scorecard_html=scorecard_html,
            bottlenecks_html=bottlenecks_html,
        )
        print(f"[HOURLY DIGEST SENT] for {current_hour_mark.strftime('%H:00 UTC')}", flush=True)
        _metrics["hourly_scans"] = 0
        _metrics["hourly_signals"] = 0
        _last_hourly_mark = current_hour_mark
        return current_hour_mark
    return mark_to_check


def check_and_send_daily(current_time: datetime = None, last_daily_mark = None):
    """Trigger daily summary at UTC 00:00."""
    global _last_daily_mark
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    mark_to_check = last_daily_mark if last_daily_mark is not None else _last_daily_mark

    current_day_mark = current_time.date()
    if mark_to_check is None or current_day_mark > mark_to_check:
        if mark_to_check is not None:
            eq, _ = get_live_account_state()
            start_val = float(_metrics["start_equity"] or eq)
            curr_val = float(eq)
            pnl = curr_val - start_val
            pnl_pct = (pnl / start_val) * 100 if start_val > 0 else 0.0

            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            pct_str = f"+{pnl_pct:.2f}" if pnl_pct >= 0 else f"{pnl_pct:.2f}"

            scorecard_html = strategy_tracker.format_telegram_scorecard()

            send_daily_summary(
                date_str=str(mark_to_check),
                start_equity=f"{start_val:.2f}",
                current_equity=f"{curr_val:.2f}",
                pnl_usdt=pnl_str,
                pnl_pct=pct_str,
                total_cycles=_metrics["daily_scans"],
                signals_generated=_metrics["daily_signals"],
                scorecard_html=scorecard_html,
            )
            print(f"[DAILY SUMMARY SENT] for {mark_to_check}", flush=True)

        _metrics["daily_scans"] = 0
        _metrics["daily_signals"] = 0
        _metrics["start_equity"] = _metrics["current_equity"]
        _last_daily_mark = current_day_mark
        return current_day_mark
    return mark_to_check


def run_scheduler():
    print("=== 15M SCHEDULER STARTED ===")
    print("BYBIT DEMO - REAL-TIME SIGNALS & TELEGRAM ALERTS")
    print("Press CTRL+C to stop")

    get_live_account_state()
    print(f"Bybit Demo Initial Balance Synced: ${_metrics['current_equity']} USDT", flush=True)

    last_boundary = None
    last_alert_key = None

    while True:
        now = datetime.now(timezone.utc)
        target = next_scan_time(now)

        print(f"Current UTC: {now.strftime('%H:%M:%S')} | Next 15M scan UTC: {target.strftime('%H:%M:%S')}", flush=True)

        remaining = (target - datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            time.sleep(remaining)

        boundary = target - timedelta(seconds=10)
        if boundary == last_boundary:
            continue

        last_boundary = boundary

        # Run 15M Scan
        try:
            last_alert_key = scan_once(last_alert_key)
        except Exception as error:
            print(f"[ERROR] Scan: {type(error).__name__} - {error}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.once:
        scan_once()
    else:
        try:
            run_scheduler()
        except KeyboardInterrupt:
            print("\nSCHEDULER STOPPED")

