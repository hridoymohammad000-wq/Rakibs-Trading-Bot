"""Run the scanner after each 15M UTC candle close."""

import argparse
import time
from datetime import datetime, timedelta, timezone

from analysis.scanner import scan_market
from execution.trade_manager import check_trade_risk
from notifications.telegram import send_alert


def next_scan_time(now):
    """Next UTC 15-minute boundary plus 10 seconds."""

    if now.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")

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


def scan_once(last_alert_key=None):
    print("\n=== BYBIT DEMO SCANNER ===", flush=True)

    result = scan_market(max_pairs=50)

    print(
        "Scanned:", result["scanned_pairs"],
        "| Failed:", result["failed_pairs"],
        "| Candidates:", len(result["candidates"]),
        flush=True,
    )

    # Fail closed if any pair scan failed.
    if result["failed_pairs"] > 0:
        print(
            "[BLOCK] Scanner errors detected",
            flush=True,
        )
        return last_alert_key

    if not result["top_candidates"]:
        print("NO QUALIFIED SIGNAL", flush=True)
        return last_alert_key

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
            print(
                "[BLOCK]",
                candidate["symbol"],
                risk["reason"],
                flush=True,
            )
            continue

        message = "\n".join([
            "BYBIT DEMO - SIGNAL ONLY",
            "",
            f"Pair: {candidate['symbol']}",
            f"Strategy: {candidate['strategy']}",
            f"Signal: {candidate['signal']}",
            f"Score: {candidate['score']}/13",
            "",
            f"Entry: {candidate['entry']}",
            f"SL: {candidate['stop_loss']}",
            f"TP1: {candidate['tp1']}",
            f"TP2: {candidate['tp2']}",
            f"RR: {candidate['risk_reward']}",
            "",
            f"Planned Qty: {risk['quantity']}",
            f"Open Positions: {risk['open_positions']}",
            f"Pending Entries: {risk['pending_entries']}",
            f"Trade Slots: {risk['slots_used']}/3",
            f"Risk Budget USDT: {risk['risk_budget_usdt']}",
            f"Price Risk USDT: {risk['price_risk_usdt']}",
            "",
            "No order placed.",
        ])

        try:
            send_alert(message)

            print(
                "[INFO] Telegram send function called",
                candidate["symbol"],
                flush=True,
            )

            # Delivery must be verified in Telegram.
            return key

        except Exception as error:
            print(
                "[ERROR] Telegram alert:",
                type(error).__name__,
                flush=True,
            )

            return last_alert_key

    print("NO RISK-APPROVED SIGNAL", flush=True)
    return last_alert_key


def run_scheduler():
    print("15M SCHEDULER STARTED")
    print("BYBIT DEMO - SIGNAL ONLY")
    print("CTRL+C to stop")

    last_boundary = None
    last_alert_key = None

    while True:
        target = next_scan_time(
            datetime.now(timezone.utc)
        )

        print(
            "Next scan UTC:",
            target.isoformat(),
            flush=True,
        )

        remaining = (
            target - datetime.now(timezone.utc)
        ).total_seconds()

        time.sleep(max(0, remaining))

        boundary = target - timedelta(seconds=10)

        if boundary == last_boundary:
            continue

        last_boundary = boundary

        try:
            last_alert_key = scan_once(
                last_alert_key
            )

        except Exception as error:
            print(
                "[ERROR] Scan:",
                type(error).__name__,
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--once",
        action="store_true",
    )

    args = parser.parse_args()

    if args.once:
        scan_once()
    else:
        try:
            run_scheduler()
        except KeyboardInterrupt:
            print("\nSCHEDULER STOPPED")
