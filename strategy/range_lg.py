"""Range + Liquidity Grab strategy. Signal only."""

import math
import pandas as pd

from analysis.indicators import add_atr
from analysis.structure import (
    detect_1h_range,
    detect_1h_liquidity_grab,
    confirm_15m_range_setup,
)


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


def _validate(df, minutes):
    required = {
        "timestamp", "open", "high", "low", "close"
    }

    if not isinstance(df, pd.DataFrame):
        raise ValueError("DataFrame required")

    if not required.issubset(df.columns):
        raise ValueError("Required OHLC columns missing")

    if len(df) < 500:
        raise ValueError("500 closed candles required")

    work = df.copy()

    work["timestamp"] = pd.to_datetime(
        work["timestamp"], utc=True
    )

    work = work.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    columns = ["open", "high", "low", "close"]

    for col in columns:
        work[col] = pd.to_numeric(
            work[col], errors="raise"
        )

    if work[columns].isna().any().any():
        raise ValueError("Missing prices")

    if not all(
        math.isfinite(float(v))
        for v in work[columns].to_numpy().ravel()
    ):
        raise ValueError("Non-finite prices")

    if (work[columns] <= 0).any().any():
        raise ValueError("Non-positive prices")

    if (
        work["timestamp"].isna().any()
        or work["timestamp"].duplicated().any()
    ):
        raise ValueError("Invalid timestamps")

    expected = pd.Timedelta(minutes=minutes)

    if not work["timestamp"].diff().dropna().eq(
        expected
    ).all():
        raise ValueError("Candle gap or wrong timeframe")

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


def generate_range_signal(
    h1_df: pd.DataFrame,
    m15_df: pd.DataFrame,
) -> dict:
    """
    1H Range + Liquidity Sweep + 15M FVG Retest.

    Returns BUY, SELL or NO_TRADE.
    Does not place orders.
    """

    try:
        h1 = _validate(h1_df, 60)
        m15 = _validate(m15_df, 15)

        h1_end = (
            h1["timestamp"].iloc[-1]
            + pd.Timedelta(hours=1)
        )

        m15_end = (
            m15["timestamp"].iloc[-1]
            + pd.Timedelta(minutes=15)
        )

        # Require synchronized closed candles.
        if not (
            h1_end <= m15_end
            < h1_end + pd.Timedelta(hours=1)
        ):
            return _no_trade(
                "TIMEFRAMES_NOT_ALIGNED"
            )

        atr15 = float(
            add_atr(m15, 14).iloc[-1]
        )

        if (
            not math.isfinite(atr15)
            or atr15 <= 0
        ):
            return _no_trade("INVALID_ATR")

        current = m15.iloc[-1]

        # Check recent completed 1H candles.
        # A sweep may occur before the most recent
        # 1H candle while 15M retest happens later.
        reasons = []

        first = max(121, len(h1) - 3)

        for end_index in range(
            len(h1), first - 1, -1
        ):
            history = h1.iloc[:end_index].copy()

            range_info = detect_1h_range(history)

            if not range_info["valid"]:
                reasons.append(
                    "NO_VALID_RANGE:"
                    + range_info["reason"]
                )
                continue

            sweep = detect_1h_liquidity_grab(
                history, range_info
            )

            if sweep["status"] not in (
                "BSL_GRAB", "SSL_GRAB"
            ):
                reasons.append(sweep["status"])
                continue

            confirmation = confirm_15m_range_setup(
                m15, sweep
            )

            if confirmation["status"] != (
                "15M_RETEST_CONFIRMED"
            ):
                reasons.append(
                    confirmation["status"]
                )
                continue

            retest_time = pd.to_datetime(
                confirmation["retest_at"],
                utc=True,
            )

            # Prevent an old confirmed retest
            # from generating a fresh signal.
            if retest_time != current.timestamp:
                reasons.append("STALE_15M_RETEST")
                continue

            side = (
                1 if sweep["status"] == "SSL_GRAB"
                else -1
            )

            direction = (
                "BUY" if side == 1 else "SELL"
            )

            high = float(range_info["range_high"])
            low = float(range_info["range_low"])
            eq = float(range_info["equilibrium"])

            fvg_bottom = float(
                confirmation["fvg_bottom"]
            )

            fvg_top = float(
                confirmation["fvg_top"]
            )

            if not (
                low < fvg_bottom < fvg_top < high
            ):
                reasons.append(
                    "FVG_OUTSIDE_1H_RANGE"
                )
                continue

            # Potential limit entry at FVG midpoint.
            entry = (
                fvg_bottom + fvg_top
            ) / 2

            current_close = float(current.close)

            if side == 1:
                if not (
                    low < entry < eq
                    and current_close > entry
                    and current_close < high
                ):
                    reasons.append(
                        "INVALID_BUY_ENTRY_ZONE"
                    )
                    continue

            else:
                if not (
                    eq < entry < high
                    and current_close < entry
                    and current_close > low
                ):
                    reasons.append(
                        "INVALID_SELL_ENTRY_ZONE"
                    )
                    continue

            sweep_extreme = float(
                sweep["sweep_extreme"]
            )

            # ATR based buffer for crypto futures.
            buffer = atr15 * 0.10

            if side == 1:
                stop = min(
                    sweep_extreme, low
                ) - buffer

                tp1 = eq
                tp2 = high

                risk = entry - stop

            else:
                stop = max(
                    sweep_extreme, high
                ) + buffer

                tp1 = eq
                tp2 = low

                risk = stop - entry

            if (
                not math.isfinite(risk)
                or risk <= 0
            ):
                reasons.append("INVALID_STOP_LOSS")
                continue

            tp1_rr = (
                side * (tp1 - entry) / risk
            )

            tp2_rr = (
                side * (tp2 - entry) / risk
            )

            if (
                not math.isfinite(tp1_rr)
                or not math.isfinite(tp2_rr)
                or tp1_rr <= 0
                or tp2_rr < 2.0
            ):
                reasons.append(
                    "INSUFFICIENT_RISK_REWARD"
                )
                continue

            return {
                "signal": direction,
                "setup": "RANGE_LIQUIDITY_GRAB",
                "reason": (
                    "1H range + liquidity sweep "
                    "+ 15M FVG retest"
                ),
                "entry_type": "LIMIT_CANDIDATE",
                "entry": round(entry, 8),
                "stop_loss": round(stop, 8),
                "tp1": round(tp1, 8),
                "tp2": round(tp2, 8),
                "risk_reward": round(tp2_rr, 2),
                "tp1_risk_reward": round(
                    tp1_rr, 2
                ),
                "range_high": high,
                "range_low": low,
                "equilibrium": eq,
                "bsl_zone": range_info["bsl_zone"],
                "ssl_zone": range_info["ssl_zone"],
                "sweep_status": sweep["status"],
                "sweep_extreme": sweep_extreme,
                "sweep_at_utc": sweep[
                    "sweep_1h_start"
                ],
                "fvg_bottom": fvg_bottom,
                "fvg_top": fvg_top,
                "retest_at_utc": str(
                    current.timestamp
                ),
            }

        return _no_trade(
            reasons[0] if reasons
            else "NO_VALID_RANGE_SETUP"
        )

    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
    ) as error:
        return _no_trade(
            f"INVALID_MARKET_DATA: {error}"
        )
