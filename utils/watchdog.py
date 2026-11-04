"""Runtime watchdog for scheduler liveness and account safety rules."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from notifications.telegram import send_alert
from utils.logger import logger


WATCHDOG_TIMEOUT = timedelta(minutes=20)
MAX_OPEN_POSITIONS = 3
API_ERROR_ALERT_THRESHOLD = 3

last_successful_scan_time = datetime.now(timezone.utc)

_liveness_alert_active = False
_consecutive_api_errors = 0
_api_alert_active = False
_slot_violation_active = False
_unprotected_symbols = set()


def _utc_now(now=None):
    if now is None:
        return datetime.now(timezone.utc)

    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)

    return now.astimezone(timezone.utc)


def _send_watchdog_alert(message):
    try:
        sent = send_alert(message)

        if not sent:
            logger.error(
                "Watchdog Telegram alert could not be delivered"
            )

        return sent
    except Exception as error:
        logger.error(
            f"Watchdog Telegram alert failed: {error}"
        )
        return False


def record_scan_success(now=None):
    """Record a completed scan cycle and clear a liveness alarm."""
    global last_successful_scan_time
    global _liveness_alert_active

    last_successful_scan_time = _utc_now(now)
    _liveness_alert_active = False
    return last_successful_scan_time


def check_watchdog_health(now=None):
    """Alert once when no scan has completed for over 20 minutes."""
    global _liveness_alert_active

    current_time = _utc_now(now)
    inactive_for = current_time - last_successful_scan_time
    healthy = inactive_for <= WATCHDOG_TIMEOUT

    if not healthy and not _liveness_alert_active:
        message = (
            "⚠️ [WATCHDOG ALERT] Bot missed scheduled 15M cycle! "
            "Inactive for > 20 mins."
        )

        if _send_watchdog_alert(message):
            _liveness_alert_active = True

    return {
        "healthy": healthy,
        "last_successful_scan_time": last_successful_scan_time,
        "inactive_seconds": max(0.0, inactive_for.total_seconds()),
    }


def record_api_success():
    """Reset the consecutive Bybit API failure state."""
    global _consecutive_api_errors
    global _api_alert_active

    _consecutive_api_errors = 0
    _api_alert_active = False


def record_api_failure():
    """Record a Bybit API error and alert after more than three."""
    global _consecutive_api_errors
    global _api_alert_active

    _consecutive_api_errors += 1

    if (
        _consecutive_api_errors > API_ERROR_ALERT_THRESHOLD
        and not _api_alert_active
    ):
        message = (
            "⚠️ [WATCHDOG ALERT] Bybit API continuous "
            "connectivity failure."
        )

        if _send_watchdog_alert(message):
            _api_alert_active = True

    return _consecutive_api_errors


def _positive_decimal(value):
    try:
        number = Decimal(str(value or "0"))
        return number.is_finite() and number > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def validate_account_invariants(client):
    """Validate open-position limits and active stop-loss protection."""
    global _slot_violation_active
    global _unprotected_symbols

    try:
        response = client.get_positions(
            category="linear",
            settleCoin="USDT",
            limit=200,
        )

        if response.get("retCode") != 0:
            raise RuntimeError(
                response.get("retMsg")
                or "Bybit positions request failed"
            )

        positions = (
            response.get("result", {})
            .get("list", [])
        )

        open_positions = [
            position
            for position in positions
            if _positive_decimal(position.get("size"))
        ]

        open_count = len(open_positions)
        slot_violation = open_count > MAX_OPEN_POSITIONS

        if slot_violation and not _slot_violation_active:
            message = (
                "🚨 [RULE VIOLATION] Max 3 trade slots exceeded! "
                f"Found {open_count} open positions."
            )

            if _send_watchdog_alert(message):
                _slot_violation_active = True
        elif not slot_violation:
            _slot_violation_active = False

        current_unprotected = {
            str(position.get("symbol") or "UNKNOWN")
            for position in open_positions
            if not _positive_decimal(position.get("stopLoss"))
        }

        reported_unprotected = (
            current_unprotected & _unprotected_symbols
        )

        for symbol in sorted(
            current_unprotected - reported_unprotected
        ):
            message = (
                "🚨 [RISK ALERT] Unprotected open position on "
                f"{symbol} without Stop Loss!"
            )

            if _send_watchdog_alert(message):
                reported_unprotected.add(symbol)

        _unprotected_symbols = reported_unprotected
        record_api_success()

        return {
            "valid": not slot_violation and not current_unprotected,
            "open_positions": open_count,
            "unprotected_symbols": sorted(current_unprotected),
        }

    except Exception as error:
        failures = record_api_failure()
        logger.warning(
            "Watchdog account validation failed "
            f"({failures} consecutive): {error}"
        )
        return {
            "valid": False,
            "error": str(error),
            "consecutive_api_errors": failures,
        }
