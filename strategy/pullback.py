"""1H direction + 15M pullback and breakout-retest signals."""

import math
import pandas as pd

from analysis.indicators import (
    add_atr,
    add_ema,
    add_swing_highs_lows,
)
from analysis.structure import analyze_market_structure


def _no_trade(reason):
    return {
        "signal": "NO_TRADE",
        "setup": None,
        "reason": reason,
        "entry": None,
        "stop_loss": None,
        "tp1": None,
        "tp2": None,
        "risk_reward": None,
    }


def _validate(df, minutes, minimum=60):
    required = {"timestamp", "open", "high", "low", "close"}

    if not isinstance(df, pd.DataFrame):
        raise ValueError("Input must be DataFrame")

    if not required.issubset(df.columns):
        raise ValueError("Required OHLC columns missing")

    if len(df) < minimum:
        raise ValueError("Insufficient closed candles")

    work = df.copy().sort_values(
        "timestamp"
    ).reset_index(drop=True)

    work["timestamp"] = pd.to_datetime(
        work["timestamp"], utc=True
    )

    cols = ["open", "high", "low", "close"]

    for col in cols:
        work[col] = pd.to_numeric(
            work[col], errors="raise"
        )

    if work[cols].isna().any().any():
        raise ValueError("Missing OHLC values")

    if not all(
        math.isfinite(float(value))
        for value in work[cols].to_numpy().ravel()
    ):
        raise ValueError("Non-finite prices")

    if work["timestamp"].duplicated().any():
        raise ValueError("Duplicate timestamps")

    expected = pd.Timedelta(minutes=minutes)

    if not work["timestamp"].diff().dropna().eq(expected).all():
        raise ValueError("Candle gap or wrong timeframe")

    if (work[cols] <= 0).any().any():
        raise ValueError("Non-positive prices")

    if (
        work["high"]
        < work[["open", "close", "low"]].max(axis=1)
    ).any():
        raise ValueError("Invalid candle high")

    if (
        work["low"]
        > work[["open", "close", "high"]].min(axis=1)
    ).any():
        raise ValueError("Invalid candle low")

    return work


def _reversal(previous, current, side):
    body = abs(float(current.close - current.open))
    candle_range = float(current.high - current.low)

    if candle_range <= 0:
        return False

    lower_wick = float(
        min(current.open, current.close) - current.low
    )
    upper_wick = float(
        current.high - max(current.open, current.close)
    )

    if side == 1:
        engulf = (
            previous.close < previous.open
            and current.close > current.open
            and current.open <= previous.close
            and current.close >= previous.open
        )
        pin = (
            current.close > current.open
            and lower_wick >= 2 * body
            and upper_wick <= max(
                body, candle_range * 0.15
            )
        )
    else:
        engulf = (
            previous.close > previous.open
            and current.close < current.open
            and current.open >= previous.close
            and current.close <= previous.open
        )
        pin = (
            current.close < current.open
            and upper_wick >= 2 * body
            and lower_wick <= max(
                body, candle_range * 0.15
            )
        )

    return bool(engulf or pin)


def _make_signal(m15, side, setup, atr15, target=None):
    current = m15.iloc[-1]
    entry = float(current.close)

    swings = add_swing_highs_lows(
        m15, lookback=3
    )

    col = "swing_low" if side == 1 else "swing_high"

    confirmed = swings[col].shift(3).dropna()

    if confirmed.empty:
        return _no_trade("NO_CONFIRMED_15M_SWING")

    swing = float(confirmed.iloc[-1])
    buffer = atr15 * 0.1

    if side == 1:
        stop = min(float(current.low), swing) - buffer
        risk = entry - stop
    else:
        stop = max(float(current.high), swing) + buffer
        risk = stop - entry

    if not math.isfinite(risk) or risk <= 0:
        return _no_trade("INVALID_STOP_LOSS")

    if target is not None:
        rr = side * (target - entry) / risk

        if rr < 2:
            return _no_trade(
                "INSUFFICIENT_1H_TARGET_RR"
            )

        tp2 = float(target)
    else:
        rr = 2.5
        tp2 = entry + side * 2.5 * risk

    return {
        "signal": "BUY" if side == 1 else "SELL",
        "setup": setup,
        "reason": "1H trend + 15M confirmation",
        "entry": entry,
        "stop_loss": round(stop, 8),
        "tp1": round(
            entry + side * 1.5 * risk, 8
        ),
        "tp2": round(tp2, 8),
        "risk_reward": round(rr, 2),
        "15m_candle_start_utc": str(
            current.timestamp
        ),
    }


def generate_pullback_signal(
    h1_df: pd.DataFrame,
    m15_df: pd.DataFrame,
) -> dict:
    """Return BUY, SELL or NO_TRADE. Never places orders."""

    try:
        h1 = _validate(h1_df, minutes=60)
        m15 = _validate(m15_df, minutes=15)

        h1_end = (
            h1["timestamp"].iloc[-1]
            + pd.Timedelta(hours=1)
        )
        m15_end = (
            m15["timestamp"].iloc[-1]
            + pd.Timedelta(minutes=15)
        )

        if not (
            h1_end <= m15_end
            <= h1_end + pd.Timedelta(hours=1)
        ):
            return _no_trade(
                "TIMEFRAMES_NOT_ALIGNED"
            )

        info = analyze_market_structure(h1)
        trend = info["trend"]

        if trend == "UPTREND":
            side = 1
        elif trend == "DOWNTREND":
            side = -1
        else:
            return _no_trade("NO_CLEAR_1H_TREND")

        h1_close = float(h1["close"].iloc[-1])
        ema20h = float(info["ema20"])
        ema50h = float(info["ema50"])

        if (
            side * (h1_close - ema50h) <= 0
            or side * (ema20h - ema50h) <= 0
        ):
            return _no_trade(
                "1H_EMA_FILTER_FAILED"
            )

        m15["ema20"] = add_ema(m15, 20)

        atr15 = float(
            add_atr(m15, 14).iloc[-1]
        )

        if not math.isfinite(atr15) or atr15 <= 0:
            return _no_trade("INVALID_ATR")

        current = m15.iloc[-1]
        previous = m15.iloc[-2]

        if not _reversal(previous, current, side):
            return _no_trade(
                "WAIT_FOR_15M_CONFIRMATION"
            )

        # Previous 1H swing level.
        previous_structure = analyze_market_structure(
            h1.iloc[:-1]
        )

        key = (
            "last_swing_high"
            if side == 1
            else "last_swing_low"
        )

        level = previous_structure[key]

        h1_last = h1.iloc[-1]
        h1_prev = h1.iloc[-2]

        # Setup B: Breakout + Retest
        if level is not None:
            crossed = (
                side * (
                    float(h1_last.close) - level
                ) > 0
                and side * (
                    float(h1_prev.close) - level
                ) <= 0
            )

            retested = (
                float(current.low)
                <= level + atr15 * 0.15
                and float(current.high)
                >= level - atr15 * 0.15
                and side * (
                    float(current.close) - level
                ) > 0
            )

            if (
                crossed
                and retested
                and current.timestamp >= h1_end
            ):
                return _make_signal(
                    m15, side,
                    "BREAKOUT_RETEST", atr15
                )

        # Setup A: EMA20 Pullback
        ema20 = float(current.ema20)

        touched = (
            min(
                float(previous.low),
                float(current.low)
            ) <= ema20 + atr15 * 0.15
            and max(
                float(previous.high),
                float(current.high)
            ) >= ema20 - atr15 * 0.15
        )

        rebound = (
            side * (
                float(current.close) - ema20
            ) > 0
        )

        if touched and rebound:
            target = info[key]

            if target is None:
                return _no_trade("NO_1H_TARGET")

            return _make_signal(
                m15, side,
                "EMA20_PULLBACK",
                atr15,
                float(target),
            )

        return _no_trade(
            "WAIT_FOR_PULLBACK_OR_RETEST"
        )

    except (ValueError, TypeError, KeyError) as error:
        return _no_trade(
            f"INVALID_MARKET_DATA: {error}"
        )
