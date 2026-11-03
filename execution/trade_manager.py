"""Bybit Demo read-only risk and trade-slot validation."""

from decimal import Decimal, ROUND_DOWN
from pybit.unified_trading import HTTP

from config.settings import (
    API_KEY,
    API_SECRET,
    USE_TESTNET,
    USE_DEMO,
)

MAX_OPEN_TRADES = 3
RISK_PER_TRADE = Decimal("0.02")


def _decimal(value):
    number = Decimal(str(value))

    if not number.is_finite():
        raise ValueError("INVALID_NUMBER")

    return number


def _reject(reason):
    return {
        "approved": False,
        "reason": reason,
    }


def calculate_quantity(
    equity,
    entry,
    stop_loss,
    qty_step,
    min_qty,
    max_qty,
    min_notional,
):
    """Calculate quantity from a maximum 2% stop-distance risk."""

    equity = _decimal(equity)
    entry = _decimal(entry)
    stop = _decimal(stop_loss)
    step = _decimal(qty_step)
    minimum = _decimal(min_qty)
    maximum = _decimal(max_qty)
    notional = _decimal(min_notional)

    if (
        equity <= 0
        or entry <= 0
        or stop <= 0
        or step <= 0
        or minimum <= 0
        or maximum < minimum
        or notional < 0
    ):
        raise ValueError("INVALID_RISK_PARAMETERS")

    distance = abs(entry - stop)

    if distance <= 0:
        raise ValueError("INVALID_STOP_DISTANCE")

    risk_budget = equity * RISK_PER_TRADE

    raw_quantity = risk_budget / distance

    quantity = min(
        raw_quantity,
        maximum,
    )

    quantity = (
        quantity / step
    ).to_integral_value(
        rounding=ROUND_DOWN
    ) * step

    if quantity < minimum:
        raise ValueError("QUANTITY_BELOW_MINIMUM")

    if quantity * entry < notional:
        raise ValueError("NOTIONAL_BELOW_MINIMUM")

    price_risk = quantity * distance

    if price_risk > risk_budget:
        raise ValueError("RISK_LIMIT_EXCEEDED")

    return {
        "quantity": str(quantity),
        "risk_budget_usdt": str(risk_budget),
        "price_risk_usdt": str(price_risk),
    }


def _get_pages(client, method, **params):
    items = []
    cursor = None
    visited = set()

    for _ in range(30):
        request = dict(params)

        if cursor:
            request["cursor"] = cursor

        response = getattr(
            client, method
        )(**request)

        if response.get("retCode") != 0:
            raise ValueError("BYBIT_ACCOUNT_READ_FAILED")

        result = response.get("result") or {}

        items.extend(result.get("list", []))

        cursor = result.get("nextPageCursor") or None

        if not cursor:
            return items

        if cursor in visited:
            raise ValueError("PAGINATION_ERROR")

        visited.add(cursor)

    raise ValueError("PAGINATION_LIMIT")


def check_trade_risk(candidate):
    """Demo account checks only. Never creates or changes orders."""

    if USE_TESTNET is not False or USE_DEMO is not True:
        return _reject("DEMO_MODE_REQUIRED")

    if not API_KEY or not API_SECRET:
        return _reject("DEMO_API_KEYS_MISSING")

    if not candidate.get("candidate"):
        return _reject("SCANNER_NOT_APPROVED")

    if candidate.get("signal") not in ("BUY", "SELL"):
        return _reject("NO_VALID_SIGNAL")

    if candidate.get("score", 0) < 8:
        return _reject("LOW_SCORE")

    try:
        entry = _decimal(candidate["entry"])
        stop = _decimal(candidate["stop_loss"])
        tp1 = _decimal(candidate["tp1"])
        tp2 = _decimal(candidate["tp2"])

        if candidate["signal"] == "BUY":
            valid = stop < entry < tp1 < tp2
        else:
            valid = tp2 < tp1 < entry < stop

        if not valid:
            return _reject("INVALID_ENTRY_SL_TP")

        actual_rr = abs(tp2 - entry) / abs(entry - stop)

        if actual_rr < 2:
            return _reject("RISK_REWARD_BELOW_2")

        client = HTTP(
            testnet=False,
            demo=True,
            api_key=API_KEY,
            api_secret=API_SECRET,
        )

        positions = _get_pages(
            client,
            "get_positions",
            category="linear",
            settleCoin="USDT",
            limit=200,
        )

        open_positions = sum(
            _decimal(p.get("size") or "0") > 0
            for p in positions
        )

        orders = _get_pages(
            client,
            "get_open_orders",
            category="linear",
            settleCoin="USDT",
            openOnly=0,
            limit=50,
        )

        pending_entries = sum(
            str(o.get("reduceOnly", False)).lower()
            != "true"
            and str(o.get("closeOnTrigger", False)).lower()
            != "true"
            and _decimal(o.get("leavesQty") or "0") > 0
            for o in orders
        )

        slots_used = (
            open_positions + pending_entries
        )

        if slots_used >= MAX_OPEN_TRADES:
            return _reject("MAX_3_TRADE_SLOTS_REACHED")

        wallet = client.get_wallet_balance(
            accountType="UNIFIED",
            coin="USDT",
        )

        if wallet.get("retCode") != 0:
            return _reject("WALLET_READ_FAILED")

        accounts = (
            wallet.get("result", {})
            .get("list", [])
        )

        if not accounts:
            return _reject("WALLET_EMPTY")

        usdt = next(
            (
                coin
                for coin in accounts[0].get("coin", [])
                if coin.get("coin") == "USDT"
            ),
            None,
        )

        if not usdt:
            return _reject("USDT_EQUITY_NOT_FOUND")

        equity = _decimal(usdt["equity"])

        info = client.get_instruments_info(
            category="linear",
            symbol=candidate["symbol"],
        )

        if info.get("retCode") != 0:
            return _reject("INSTRUMENT_READ_FAILED")

        instruments = (
            info.get("result", {})
            .get("list", [])
        )

        if not instruments:
            return _reject("INSTRUMENT_NOT_FOUND")

        instrument = instruments[0]

        if (
            instrument.get("contractType")
            != "LinearPerpetual"
            or instrument.get("settleCoin") != "USDT"
        ):
            return _reject("INVALID_INSTRUMENT")

        lot = instrument["lotSizeFilter"]

        sizing = calculate_quantity(
            equity=equity,
            entry=entry,
            stop_loss=stop,
            qty_step=lot["qtyStep"],
            min_qty=lot["minOrderQty"],
            max_qty=lot["maxOrderQty"],
            min_notional=lot.get(
                "minNotionalValue"
            ) or "0",
        )

        return {
            "approved": True,
            "reason": "DEMO_RISK_CHECK_PASSED",
            "open_positions": open_positions,
            "pending_entries": pending_entries,
            "slots_used": slots_used,
            "equity_usdt": str(equity),
            **sizing,
        }

    except Exception:
        return _reject("RISK_CHECK_FAILED")
