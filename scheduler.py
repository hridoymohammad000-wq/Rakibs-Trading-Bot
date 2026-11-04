"""Run the scanner after each 15M UTC candle close, plus Hourly and Daily Telegram Digests."""
import argparse
import sys
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import logger
from utils import watchdog
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


def get_live_account_state():
    """Fetch live Bybit Demo balance and open slots."""
    try:
        client = HTTP(testnet=USE_TESTNET, demo=USE_DEMO, api_key=API_KEY, api_secret=API_SECRET)
        wallet = client.get_wallet_balance(accountType="UNIFIED", coin="USDT")

        if wallet.get("retCode") != 0:
            raise RuntimeError(
                wallet.get("retMsg")
                or "Bybit wallet request failed"
            )

        accs = wallet.get("result", {}).get("list", [])
        if accs:
            for coin in accs[0].get("coin", []):
                if coin.get("coin") == "USDT":
                    eq = Decimal(coin.get("equity") or "0")
                    _metrics["current_equity"] = f"{eq:.2f}"
                    if _metrics["start_equity"] is None:
                        _metrics["start_equity"] = _metrics["current_equity"]

        invariants = watchdog.validate_account_invariants(client)

        if "open_positions" in invariants:
            _metrics["slots_used"] = invariants[
                "open_positions"
            ]
    except Exception as e:
        watchdog.record_api_failure()
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


def scan_once(last_alert_key=None):
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

    # Track highest score report for radar
    top_report = None
    if result.get("reports"):
        top_report = max(result["reports"], key=lambda r: r.get("score", 0))
        _metrics["last_top_radar"] = top_report

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
            _metrics["hourly_signals"] += 1
            _metrics["daily_signals"] += 1
            signal_sent = True
            print(f"[ALERT SENT] {candidate['symbol']} {candidate['signal']}", flush=True)
            return key

    # If no signal qualified, send quiet 15M ping
    if not signal_sent:
        print("NO QUALIFIED SIGNAL -> Sending 15M Scan Ping", flush=True)
        send_scan_ping(scanned_count=result["scanned_pairs"], top_radar=top_report)

    return last_alert_key


def check_and_send_hourly(current_time: datetime, last_hourly_mark: datetime):
    """Trigger hourly digest at the top of each hour (XX:00)."""
    watchdog.check_watchdog_health()
    current_hour_mark = current_time.replace(minute=0, second=0, microsecond=0)
    if last_hourly_mark is None or current_hour_mark > last_hourly_mark:
        # Update live equity and open slots
        eq, slots = get_live_account_state()
        macro = check_macro_market()
        top_watch = "None"
        if _metrics["last_top_radar"]:
            r = _metrics["last_top_radar"]
            top_watch = f"#{r['symbol']} ({r['strategy'].upper()} - Score {r['score']}/13)"

        send_hourly_digest(
            hour_utc=current_hour_mark.strftime("%Y-%m-%d %H:00"),
            scans_completed=_metrics["hourly_scans"],
            signals_count=_metrics["hourly_signals"],
            slots_used=slots,
            equity=eq,
            btc_summary=macro["btc"],
            eth_summary=macro["eth"],
            top_watch=top_watch,
        )
        print(f"[HOURLY DIGEST SENT] for {current_hour_mark.strftime('%H:00 UTC')}", flush=True)
        _metrics["hourly_scans"] = 0
        _metrics["hourly_signals"] = 0
        return current_hour_mark
    return last_hourly_mark


def check_and_send_daily(current_time: datetime, last_daily_mark: datetime):
    """Trigger daily summary at UTC 00:00."""
    current_day_mark = current_time.date()
    if last_daily_mark is None or current_day_mark > last_daily_mark:
        if last_daily_mark is not None:
            eq, _ = get_live_account_state()
            start_val = float(_metrics["start_equity"] or eq)
            curr_val = float(eq)
            pnl = curr_val - start_val
            pnl_pct = (pnl / start_val) * 100 if start_val > 0 else 0.0

            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            pct_str = f"+{pnl_pct:.2f}" if pnl_pct >= 0 else f"{pnl_pct:.2f}"

            send_daily_summary(
                date_str=str(last_daily_mark),
                start_equity=f"{start_val:.2f}",
                current_equity=f"{curr_val:.2f}",
                pnl_usdt=pnl_str,
                pnl_pct=pct_str,
                total_cycles=_metrics["daily_scans"],
                signals_generated=_metrics["daily_signals"],
            )
            print(f"[DAILY SUMMARY SENT] for {last_daily_mark}", flush=True)

        _metrics["daily_scans"] = 0
        _metrics["daily_signals"] = 0
        _metrics["start_equity"] = _metrics["current_equity"]
        return current_day_mark
    return last_daily_mark


def run_scheduler():
    print("=== 15M SCHEDULER STARTED ===")
    print("BYBIT DEMO - REAL-TIME SIGNALS & TELEGRAM ALERTS")
    print("Press CTRL+C to stop")

    # Initial account balance sync
    get_live_account_state()
    print(f"Bybit Demo Initial Balance Synced: ${_metrics['current_equity']} USDT", flush=True)

    last_boundary = None
    last_alert_key = None
    last_hourly_mark = None
    last_daily_mark = datetime.now(timezone.utc).date()

    while True:
        watchdog.check_watchdog_health()
        now = datetime.now(timezone.utc)
        target = next_scan_time(now)

        print(f"Current UTC: {now.strftime('%H:%M:%S')} | Next 15M scan UTC: {target.strftime('%H:%M:%S')}", flush=True)

        remaining = (target - datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            time.sleep(remaining)

        watchdog.check_watchdog_health()

        boundary = target - timedelta(seconds=10)
        if boundary == last_boundary:
            continue

        last_boundary = boundary

        # Run 15M Scan
        try:
            last_alert_key = scan_once(last_alert_key)
        except Exception as error:
            print(f"[ERROR] Scan: {type(error).__name__} - {error}", flush=True)

        # Check Hourly Digest
        now_after = datetime.now(timezone.utc)
        if now_after.minute < 15:
            last_hourly_mark = check_and_send_hourly(now_after, last_hourly_mark)

        # Check Daily Summary
        last_daily_mark = check_and_send_daily(now_after, last_daily_mark)


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
