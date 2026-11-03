import pandas as pd

from analysis.indicators import (
    add_ema,
    add_swing_highs_lows,
)


def analyze_market_structure(
    df: pd.DataFrame,
    lookback: int = 3
) -> dict:
    """Analyze EMA and confirmed market swing structure."""

    required = {"timestamp", "high", "low", "close"}

    if not required.issubset(df.columns):
        raise ValueError("Required OHLC columns missing")

    if not isinstance(lookback, int) or lookback < 1:
        raise ValueError("Invalid swing lookback")

    if len(df) < max(50, 2 * lookback + 1):
        raise ValueError("Insufficient candle data")

    work = (
        df.copy()
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    if work[list(required)].isna().any().any():
        raise ValueError("Missing market data values")

    if work["timestamp"].duplicated().any():
        raise ValueError("Duplicate timestamps")

    # EMA Calculation
    work["ema20"] = add_ema(work, 20)
    work["ema50"] = add_ema(work, 50)

    # Swing detection
    pivots = add_swing_highs_lows(
        work,
        lookback=lookback
    )

    # A pivot is known only after lookback future
    # candles have closed. Shift to confirmation bar.
    confirmed_highs = (
        pivots["swing_high"]
        .shift(lookback)
        .dropna()
        .tolist()
    )

    confirmed_lows = (
        pivots["swing_low"]
        .shift(lookback)
        .dropna()
        .tolist()
    )

    high_type = "UNKNOWN"
    low_type = "UNKNOWN"

    if len(confirmed_highs) >= 2:
        previous = confirmed_highs[-2]
        current = confirmed_highs[-1]

        if current > previous:
            high_type = "HH"
        elif current < previous:
            high_type = "LH"
        else:
            high_type = "EH"

    if len(confirmed_lows) >= 2:
        previous = confirmed_lows[-2]
        current = confirmed_lows[-1]

        if current > previous:
            low_type = "HL"
        elif current < previous:
            low_type = "LL"
        else:
            low_type = "EL"

    trend = "UNCONFIRMED"

    if high_type == "HH" and low_type == "HL":
        trend = "UPTREND"

    elif high_type == "LH" and low_type == "LL":
        trend = "DOWNTREND"

    elif (
        high_type != "UNKNOWN"
        and low_type != "UNKNOWN"
    ):
        trend = "MIXED_OR_SIDEWAYS"

    return {
        "timestamp_utc": str(work["timestamp"].iloc[-1]),
        "trend": trend,
        "high_structure": high_type,
        "low_structure": low_type,
        "ema20": float(work["ema20"].iloc[-1]),
        "ema50": float(work["ema50"].iloc[-1]),
        "last_swing_high": (
            float(confirmed_highs[-1])
            if confirmed_highs else None
        ),
        "last_swing_low": (
            float(confirmed_lows[-1])
            if confirmed_lows else None
        ),
        "confirmed_high_count": len(confirmed_highs),
        "confirmed_low_count": len(confirmed_lows),
    }

# ============================================================
# ICT FAIR VALUE GAP + ORDER BLOCK DETECTION
# Confirmed closed candles only
# ============================================================

FVG_COLUMNS = [
    "direction", "confirmed_at", "displacement_at",
    "bottom", "top", "gap_size", "body_to_median",
    "confirmation_index", "displacement_index",
]

OB_COLUMNS = [
    "direction", "origin_at", "confirmed_at",
    "zone_low", "zone_high",
    "fvg_bottom", "fvg_top", "origin_index",
]


def _prepare_ict_candles(df: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}

    if not required.issubset(df.columns):
        raise ValueError("Required OHLC columns missing")

    work = (
        df.copy()
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    if (
        work["timestamp"].isna().any()
        or work["timestamp"].duplicated().any()
    ):
        raise ValueError("Invalid or duplicate timestamps")

    columns = ["open", "high", "low", "close"]

    for column in columns:
        work[column] = pd.to_numeric(
            work[column], errors="raise"
        )

    if work[columns].isna().any().any():
        raise ValueError("Missing OHLC values")

    if (work[columns] <= 0).any().any():
        raise ValueError("Invalid OHLC prices")

    if (
        work["high"]
        < work[["open", "low", "close"]].max(axis=1)
    ).any():
        raise ValueError("Invalid candle high")

    if (
        work["low"]
        > work[["open", "high", "close"]].min(axis=1)
    ).any():
        raise ValueError("Invalid candle low")

    return work


def detect_fvg(
    df: pd.DataFrame,
    displacement_factor: float = 1.5,
    min_gap_atr: float = 0.1,
) -> pd.DataFrame:
    """
    Detect bullish/bearish 3-candle FVG.

    Requires strong middle candle displacement.
    FVG confirmed after third candle closes.
    """

    if displacement_factor <= 0 or min_gap_atr < 0:
        raise ValueError("Invalid FVG thresholds")

    work = _prepare_ict_candles(df)

    if len(work) < 23:
        return pd.DataFrame(columns=FVG_COLUMNS)

    body = (
        work["close"] - work["open"]
    ).abs()

    prior_body_median = (
        body.shift(1)
        .rolling(20, min_periods=20)
        .median()
    )

    previous_close = work["close"].shift(1)

    true_range = pd.concat([
        work["high"] - work["low"],
        (work["high"] - previous_close).abs(),
        (work["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)

    atr = true_range.rolling(
        14, min_periods=14
    ).mean()

    found = []

    for i in range(22, len(work)):
        middle = i - 1

        baseline = prior_body_median.iloc[middle]
        middle_body = body.iloc[middle]

        candle_range = (
            work.at[middle, "high"]
            - work.at[middle, "low"]
        )

        local_atr = atr.iloc[middle]

        if (
            pd.isna(baseline)
            or baseline <= 0
            or pd.isna(local_atr)
            or local_atr <= 0
            or candle_range <= 0
            or middle_body < displacement_factor * baseline
            or middle_body / candle_range < 0.6
        ):
            continue

        if (
            work.at[middle, "close"]
            > work.at[middle, "open"]
            and work.at[i, "low"]
            > work.at[i - 2, "high"]
        ):
            direction = "BULLISH"
            bottom = work.at[i - 2, "high"]
            top = work.at[i, "low"]

        elif (
            work.at[middle, "close"]
            < work.at[middle, "open"]
            and work.at[i, "high"]
            < work.at[i - 2, "low"]
        ):
            direction = "BEARISH"
            bottom = work.at[i, "high"]
            top = work.at[i - 2, "low"]

        else:
            continue

        gap = top - bottom

        if gap < min_gap_atr * local_atr:
            continue

        found.append({
            "direction": direction,
            "confirmed_at": work.at[i, "timestamp"],
            "displacement_at": work.at[middle, "timestamp"],
            "bottom": float(bottom),
            "top": float(top),
            "gap_size": float(gap),
            "body_to_median": float(
                middle_body / baseline
            ),
            "confirmation_index": i,
            "displacement_index": middle,
        })

    return pd.DataFrame(
        found,
        columns=FVG_COLUMNS
    )


def detect_order_blocks(
    df: pd.DataFrame,
    max_lookback: int = 5,
    displacement_factor: float = 1.5,
    min_gap_atr: float = 0.1,
) -> pd.DataFrame:
    """
    Detect potential bullish/bearish Order Blocks.

    Last opposite candle before displacement.
    Requires a confirmed FVG.

    This returns OB candidates, not trade signals.
    """

    if (
        not isinstance(max_lookback, int)
        or max_lookback < 1
    ):
        raise ValueError("Invalid OB lookback")

    work = _prepare_ict_candles(df)

    fvgs = detect_fvg(
        work,
        displacement_factor=displacement_factor,
        min_gap_atr=min_gap_atr,
    )

    found = []
    seen = set()

    for fvg in fvgs.itertuples(index=False):
        middle = int(fvg.displacement_index)

        earliest = max(
            0,
            middle - max_lookback
        )

        for j in range(
            middle - 1,
            earliest - 1,
            -1
        ):
            if fvg.direction == "BULLISH":
                opposite = (
                    work.at[j, "close"]
                    < work.at[j, "open"]
                )
            else:
                opposite = (
                    work.at[j, "close"]
                    > work.at[j, "open"]
                )

            if not opposite:
                continue

            key = (fvg.direction, j)

            if key not in seen:
                seen.add(key)

                found.append({
                    "direction": fvg.direction,
                    "origin_at": work.at[j, "timestamp"],
                    "confirmed_at": fvg.confirmed_at,
                    "zone_low": float(
                        work.at[j, "low"]
                    ),
                    "zone_high": float(
                        work.at[j, "high"]
                    ),
                    "fvg_bottom": float(fvg.bottom),
                    "fvg_top": float(fvg.top),
                    "origin_index": j,
                })

            break

    return pd.DataFrame(
        found,
        columns=OB_COLUMNS
    )

# ============================================================
# RANGE + LIQUIDITY DETECTION ENGINE
# Input: validated, closed OHLCV candles
# ============================================================


def _range_result(reason):
    return {
        "valid": False,
        "reason": reason,
        "range_high": None,
        "range_low": None,
        "equilibrium": None,
        "bsl_zone": None,
        "ssl_zone": None,
        "high_touches": 0,
        "low_touches": 0,
        "tolerance": None,
        "evaluated_1h_start": None,
    }


def _find_range_edge(
    series, tolerance, min_touches,
    separation, highest
):
    pivots = [
        (int(i), float(value))
        for i, value in series.items()
        if pd.notna(value)
    ]

    candidates = sorted(
        {value for _, value in pivots},
        reverse=highest,
    )

    for candidate in candidates:
        nearby = sorted(
            [
                (i, value)
                for i, value in pivots
                if abs(value - candidate) <= tolerance
            ]
        )

        touches = []

        for i, value in nearby:
            if not touches or i - touches[-1][0] > separation:
                touches.append((i, value))

        if len(touches) >= min_touches:
            level = float(
                pd.Series(
                    [v for _, v in touches]
                ).median()
            )
            return level, [i for i, _ in touches]

    return None, []


def detect_1h_range(
    df: pd.DataFrame,
    lookback: int = 120,
    pivot_lookback: int = 3,
    tolerance_atr: float = 0.30,
    min_height_atr: float = 3.0,
    min_touches: int = 2,
) -> dict:
    """
    Detect a potential 1H horizontal range.

    Uses previous candles to build levels.
    Reserves latest closed candle for sweep check.
    """

    if (
        lookback < 30
        or pivot_lookback < 1
        or tolerance_atr <= 0
        or min_height_atr <= 0
        or min_touches < 2
    ):
        raise ValueError("Invalid range parameters")

    work = _prepare_ict_candles(df)

    if len(work) < lookback + 1:
        return _range_result("INSUFFICIENT_DATA")

    timestamps = pd.to_datetime(
        work["timestamp"], utc=True
    )

    gaps = timestamps.diff().dropna()

    if not (gaps == pd.Timedelta(hours=1)).all():
        raise ValueError("1H candle gap detected")

    # Exclude newest candle from range construction.
    history = work.iloc[
        -(lookback + 1):-1
    ].reset_index(drop=True)

    prev_close = history["close"].shift()

    true_range = pd.concat([
        history["high"] - history["low"],
        (history["high"] - prev_close).abs(),
        (history["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = float(
        true_range.rolling(14).mean().iloc[-1]
    )

    if pd.isna(atr) or atr <= 0:
        return _range_result("INVALID_ATR")

    tolerance = atr * tolerance_atr

    swings = add_swing_highs_lows(
        history,
        lookback=pivot_lookback,
    )

    # Only fully confirmed pivot candles.
    pivots = swings.iloc[
        pivot_lookback:-pivot_lookback
    ]

    high, high_indices = _find_range_edge(
        pivots["swing_high"],
        tolerance,
        min_touches,
        pivot_lookback,
        True,
    )

    low, low_indices = _find_range_edge(
        pivots["swing_low"],
        tolerance,
        min_touches,
        pivot_lookback,
        False,
    )

    if high is None or low is None:
        return _range_result("NOT_ENOUGH_EDGE_TOUCHES")

    height = high - low

    if height <= 0 or height < atr * min_height_atr:
        return _range_result("RANGE_TOO_SMALL")

    # Check whether price has travelled
    # between the two boundaries.
    events = sorted(
        [(i, "H") for i in high_indices]
        + [(i, "L") for i in low_indices]
    )

    sequence = []

    for _, side in events:
        if not sequence or sequence[-1] != side:
            sequence.append(side)

    if len(sequence) < 3:
        return _range_result("NO_RANGE_ROTATION")

    outside = (
        (history["close"] > high + tolerance)
        | (history["close"] < low - tolerance)
    )

    outside_ratio = float(outside.mean())

    if outside_ratio > 0.05:
        return _range_result("TOO_MANY_OUTSIDE_CLOSES")

    # Avoid old ranges that have recently broken.
    if outside.tail(4).any():
        return _range_result("RECENT_BREAKOUT")

    equilibrium = (high + low) / 2

    return {
        "valid": True,
        "reason": "VALID_RANGE_CANDIDATE",
        "range_high": high,
        "range_low": low,
        "equilibrium": equilibrium,
        "range_height": height,
        "bsl_zone": {
            "bottom": high,
            "top": high + tolerance,
        },
        "ssl_zone": {
            "bottom": low - tolerance,
            "top": low,
        },
        "high_touches": len(high_indices),
        "low_touches": len(low_indices),
        "tolerance": tolerance,
        "atr": atr,
        "outside_close_ratio": outside_ratio,
        "evaluated_1h_start": str(
            work["timestamp"].iloc[-1]
        ),
    }


def detect_1h_liquidity_grab(
    df: pd.DataFrame,
    range_info: dict,
) -> dict:
    """
    Check the latest closed 1H candle.

    BSL grab: wick above range, body inside.
    SSL grab: wick below range, body inside.
    """

    result = {
        "status": "NO_SWEEP",
        "direction": None,
        "sweep_1h_start": None,
        "sweep_extreme": None,
    }

    if not range_info.get("valid"):
        result["status"] = "NO_VALID_RANGE"
        return result

    work = _prepare_ict_candles(df)

    if work.empty:
        return result

    latest = work.iloc[-1]

    if str(latest["timestamp"]) != range_info.get(
        "evaluated_1h_start"
    ):
        raise ValueError("Range and candle data mismatch")

    high = range_info["range_high"]
    low = range_info["range_low"]

    minimum_sweep = max(
        range_info["tolerance"] * 0.10,
        high * 0.000001,
    )

    body_inside = (
        min(latest["open"], latest["close"]) >= low
        and max(latest["open"], latest["close"]) <= high
    )

    bsl = (
        latest["high"] > high + minimum_sweep
        and body_inside
    )

    ssl = (
        latest["low"] < low - minimum_sweep
        and body_inside
    )

    if bsl and ssl:
        result["status"] = "AMBIGUOUS_DOUBLE_SWEEP"

    elif bsl:
        result.update({
            "status": "BSL_GRAB",
            "direction": "BEARISH",
            "sweep_1h_start": str(latest["timestamp"]),
            "sweep_extreme": float(latest["high"]),
        })

    elif ssl:
        result.update({
            "status": "SSL_GRAB",
            "direction": "BULLISH",
            "sweep_1h_start": str(latest["timestamp"]),
            "sweep_extreme": float(latest["low"]),
        })

    elif (
        latest["close"] > high
        or latest["close"] < low
    ):
        result["status"] = "BREAKOUT_POSSIBLE"

    return result


def confirm_15m_range_setup(
    df: pd.DataFrame,
    sweep_info: dict,
    max_confirmation_bars: int = 12,
) -> dict:
    """
    Confirm 15M FVG after a completed 1H sweep.

    A later candle must retest and reject FVG.
    Analysis only: no order or trade execution.
    """

    result = {
        "status": "NO_CONFIRMED_SWEEP",
        "direction": None,
        "fvg_bottom": None,
        "fvg_top": None,
        "fvg_confirmed_at": None,
        "retest_at": None,
    }

    if sweep_info.get("status") not in (
        "BSL_GRAB", "SSL_GRAB"
    ):
        return result

    if max_confirmation_bars < 3:
        raise ValueError("Invalid confirmation window")

    work = _prepare_ict_candles(df)

    times = pd.to_datetime(
        work["timestamp"], utc=True
    )

    if len(work) < 23:
        result["status"] = "INSUFFICIENT_15M_DATA"
        return result

    if not (
        times.diff().dropna() == pd.Timedelta(minutes=15)
    ).all():
        raise ValueError("15M candle gap detected")

    sweep_end = (
        pd.Timestamp(
            sweep_info["sweep_1h_start"],
            tz="UTC",
        )
        + pd.Timedelta(hours=1)
    )

    last_closed_at = (
        times.iloc[-1]
        + pd.Timedelta(minutes=15)
    )

    if last_closed_at <= sweep_end:
        result["status"] = "WAIT_FOR_15M"
        return result

    if (
        last_closed_at - sweep_end
        > pd.Timedelta(minutes=15 * max_confirmation_bars)
    ):
        result["status"] = "SWEEP_EXPIRED"
        return result

    fvgs = detect_fvg(work)

    direction = sweep_info["direction"]

    if fvgs.empty:
        result["status"] = "WAIT_FOR_FVG"
        return result

    matches = fvgs[
        fvgs["direction"] == direction
    ].copy()

    if matches.empty:
        result["status"] = "WAIT_FOR_FVG"
        return result

    fvg_times = pd.to_datetime(
        matches["confirmed_at"], utc=True
    )

    matches = matches[
        fvg_times >= sweep_end
    ]

    if matches.empty:
        result["status"] = "WAIT_FOR_FVG"
        return result

    fvg = matches.iloc[-1]

    result.update({
        "status": "WAIT_FOR_RETEST",
        "direction": direction,
        "fvg_bottom": float(fvg["bottom"]),
        "fvg_top": float(fvg["top"]),
        "fvg_confirmed_at": str(
            fvg["confirmed_at"]
        ),
    })

    fvg_close_time = (
        pd.to_datetime(
            fvg["confirmed_at"], utc=True
        )
        + pd.Timedelta(minutes=15)
    )

    later = work.loc[
        times >= fvg_close_time
    ]

    for candle in later.itertuples():
        touched = (
            candle.low <= fvg["top"]
            and candle.high >= fvg["bottom"]
        )

        if direction == "BULLISH":
            rejected = (
                candle.close > candle.open
                and candle.close > fvg["top"]
            )
        else:
            rejected = (
                candle.close < candle.open
                and candle.close < fvg["bottom"]
            )

        if touched and rejected:
            result["status"] = "15M_RETEST_CONFIRMED"
            result["retest_at"] = str(
                candle.timestamp
            )
            break

    return result
