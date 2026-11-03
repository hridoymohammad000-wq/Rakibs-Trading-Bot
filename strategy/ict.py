"""ICT 1H + 15M signal engine. No order execution."""

import math
import pandas as pd

from analysis.indicators import add_atr
from analysis.structure import (
    analyze_market_structure,
    detect_fvg,
    detect_order_blocks,
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

    if (
        not isinstance(df, pd.DataFrame)
        or not required.issubset(df.columns)
    ):
        raise ValueError("Required OHLC DataFrame missing")

    if len(df) < 100:
        raise ValueError("At least 100 candles required")

    work = df.copy()

    work["timestamp"] = pd.to_datetime(
        work["timestamp"], utc=True
    )

    work = work.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    cols = ["open", "high", "low", "close"]

    for col in cols:
        work[col] = pd.to_numeric(
            work[col], errors="raise"
        )

    if work[cols].isna().any().any():
        raise ValueError("Missing OHLC values")

    if not all(
        math.isfinite(float(v))
        for v in work[cols].to_numpy().ravel()
    ):
        raise ValueError("Non-finite OHLC values")

    if (work[cols] <= 0).any().any():
        raise ValueError("Invalid prices")

    if (
        work["timestamp"].isna().any()
        or work["timestamp"].duplicated().any()
    ):
        raise ValueError("Invalid timestamps")

    expected = pd.Timedelta(minutes=minutes)

    gaps = work["timestamp"].diff().dropna()

    if not gaps.eq(expected).all():
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


def _find_sweeps(m15, side, atr, max_age=12):
    """
    Find recent SSL/BSL sweeps using prior
    equal lows or highs.

    Only earlier candles form the level.
    """

    last = len(m15) - 1

    for i in range(
        last - 3,
        max(24, last - max_age) - 1,
        -1,
    ):
        past = m15.iloc[i - 24:i]
        candle = m15.iloc[i]

        local_atr = float(atr.iloc[i])

        if (
            not math.isfinite(local_atr)
            or local_atr <= 0
        ):
            continue

        prices = (
            past["low"]
            if side == 1
            else past["high"]
        )

        level = float(
            prices.min()
            if side == 1
            else prices.max()
        )

        tolerance = local_atr * 0.15

        touch_indices = [
            j
            for j, value in enumerate(
                prices.to_numpy()
            )
            if abs(float(value) - level) <= tolerance
        ]

        separated = []

        for j in touch_indices:
            if (
                not separated
                or j - separated[-1] >= 3
            ):
                separated.append(j)

        if len(separated) < 2:
            continue

        minimum_sweep = max(
            local_atr * 0.05,
            level * 0.000001,
        )

        if side == 1:
            valid = (
                candle.low < level - minimum_sweep
                and candle.open > level
                and candle.close > level
            )
            extreme = float(candle.low)

        else:
            valid = (
                candle.high > level + minimum_sweep
                and candle.open < level
                and candle.close < level
            )
            extreme = float(candle.high)

        if valid:
            yield i, level, extreme


def _get_htf_zones(
    h1,
    side,
    sweep_start,
    max_age_hours=96,
):
    """
    Return 1H OB/FVG zones confirmed
    before the liquidity sweep.
    """

    direction = (
        "BULLISH" if side == 1 else "BEARISH"
    )

    zones = []

    cutoff = (
        sweep_start
        - pd.Timedelta(hours=max_age_hours)
    )

    datasets = (
        (
            "OB",
            detect_order_blocks(h1),
            "zone_low",
            "zone_high",
        ),
        (
            "FVG",
            detect_fvg(h1),
            "bottom",
            "top",
        ),
    )

    for kind, frame, low_col, high_col in datasets:

        if frame.empty:
            continue

        for zone in frame.itertuples(index=False):

            if zone.direction != direction:
                continue

            confirmed = pd.to_datetime(
                zone.confirmed_at,
                utc=True,
            )

            # 1H confirming candle must be closed.
            if not (
                cutoff <= confirmed
                and confirmed + pd.Timedelta(hours=1)
                <= sweep_start
            ):
                continue

            low = float(getattr(zone, low_col))
            high = float(getattr(zone, high_col))

            if low >= high:
                continue

            subsequent = h1.loc[
                (h1["timestamp"] > confirmed)
                & (
                    h1["timestamp"]
                    + pd.Timedelta(hours=1)
                    <= sweep_start
                )
            ]

            if (
                side == 1
                and (subsequent["close"] < low).any()
            ):
                continue

            if (
                side == -1
                and (subsequent["close"] > high).any()
            ):
                continue

            zones.append({
                "kind": kind,
                "low": low,
                "high": high,
                "confirmed": confirmed,
            })

    return sorted(
        zones,
        key=lambda z: z["confirmed"],
        reverse=True,
    )


def _fvg_retest(candle, fvg, side):
    """Latest 15M candle retests and rejects FVG."""

    touched = (
        candle.low <= fvg.top
        and candle.high >= fvg.bottom
    )

    if side == 1:
        return (
            touched
            and candle.close > candle.open
            and candle.close > fvg.top
        )

    return (
        touched
        and candle.close < candle.open
        and candle.close < fvg.bottom
    )


def generate_ict_signal(
    h1_df: pd.DataFrame,
    m15_df: pd.DataFrame,
) -> dict:
    """
    ICT signal-only engine.

    Steps:
    1. Confirm 1H direction.
    2. Find existing 1H OB/FVG.
    3. Detect 15M liquidity sweep.
    4. Confirm displacement + FVG.
    5. Match 15M OB inside 1H zone.
    6. Confirm FVG retest.
    7. Validate stop loss and RR.

    No order execution.
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

        if not (
            h1_end <= m15_end
            <= h1_end + pd.Timedelta(hours=1)
        ):
            return _no_trade(
                "TIMEFRAMES_NOT_ALIGNED"
            )

        structure = analyze_market_structure(h1)

        trend = structure["trend"]

        if trend == "UPTREND":
            side = 1

        elif trend == "DOWNTREND":
            side = -1

        else:
            return _no_trade(
                "NO_CLEAR_1H_TREND"
            )

        direction = (
            "BULLISH" if side == 1
            else "BEARISH"
        )

        atr = add_atr(m15, 14)

        last_atr = float(atr.iloc[-1])

        if (
            not math.isfinite(last_atr)
            or last_atr <= 0
        ):
            return _no_trade("INVALID_ATR")

        fvgs = detect_fvg(m15)
        obs = detect_order_blocks(m15)

        if fvgs.empty or obs.empty:
            return _no_trade(
                "WAIT_FOR_15M_FVG_OB"
            )

        current = m15.iloc[-1]
        last_idx = len(m15) - 1

        seen_sweep = False
        seen_zone = False

        for sweep_idx, level, extreme in _find_sweeps(
            m15, side, atr, max_age=12
        ):
            seen_sweep = True

            sweep = m15.iloc[sweep_idx]

            zones = [
                zone
                for zone in _get_htf_zones(
                    h1, side, sweep.timestamp
                )
                if (
                    sweep.low <= zone["high"]
                    and sweep.high >= zone["low"]
                )
            ]

            if not zones:
                continue

            seen_zone = True

            matching_fvgs = fvgs[
                (fvgs["direction"] == direction)
                & (
                    fvgs["confirmation_index"]
                    >= sweep_idx + 2
                )
                & (
                    fvgs["confirmation_index"]
                    < last_idx
                )
            ]

            if matching_fvgs.empty:
                continue

            for fvg in reversed(
                list(matching_fvgs.itertuples(index=False))
            ):
                fvg_idx = int(fvg.confirmation_index)

                if last_idx - fvg_idx > 12:
                    continue

                # Displacement after sweep only.
                if fvg.displacement_index <= sweep_idx:
                    continue

                if not _fvg_retest(
                    current, fvg, side
                ):
                    continue

                # Ignore repeatedly mitigated gaps.
                between = m15.iloc[
                    fvg_idx + 1:last_idx
                ]

                if not between.empty:
                    earlier_touch = (
                        (between["low"] <= fvg.top)
                        & (between["high"] >= fvg.bottom)
                    ).any()

                    if earlier_touch:
                        continue

                ob_matches = obs[
                    (obs["direction"] == direction)
                    & (
                        pd.to_datetime(
                            obs["confirmed_at"],
                            utc=True,
                        )
                        == pd.to_datetime(
                            fvg.confirmed_at,
                            utc=True,
                        )
                    )
                ]

                if ob_matches.empty:
                    continue

                for ob in ob_matches.itertuples(index=False):

                    # 15M OB must fit a 1H zone.
                    matching_zones = [
                        zone
                        for zone in zones
                        if (
                            ob.zone_low >= zone["low"]
                            and ob.zone_high <= zone["high"]
                        )
                    ]

                    if not matching_zones:
                        continue

                    entry = float(current.close)

                    buffer = last_atr * 0.10

                    if side == 1:
                        stop = extreme - buffer
                        risk = entry - stop
                        target = structure[
                            "last_swing_high"
                        ]
                    else:
                        stop = extreme + buffer
                        risk = stop - entry
                        target = structure[
                            "last_swing_low"
                        ]

                    if (
                        not math.isfinite(risk)
                        or risk <= 0
                        or target is None
                    ):
                        continue

                    target = float(target)

                    rr = (
                        side * (target - entry) / risk
                    )

                    if (
                        not math.isfinite(rr)
                        or rr < 2.0
                    ):
                        continue

                    tp1 = (
                        entry + side * 1.5 * risk
                    )

                    zone = matching_zones[0]

                    return {
                        "signal": (
                            "BUY" if side == 1
                            else "SELL"
                        ),
                        "setup": "ICT_LIQUIDITY_OB_FVG_RETEST",
                        "reason": (
                            "1H trend/zone + "
                            "15M sweep/FVG/OB/retest"
                        ),
                        "entry": round(entry, 8),
                        "stop_loss": round(stop, 8),
                        "tp1": round(tp1, 8),
                        "tp2": round(target, 8),
                        "risk_reward": round(rr, 2),
                        "sweep_level": round(level, 8),
                        "sweep_extreme": round(extreme, 8),
                        "sweep_at_utc": str(
                            sweep.timestamp
                        ),
                        "htf_zone_type": zone["kind"],
                        "htf_zone_low": zone["low"],
                        "htf_zone_high": zone["high"],
                        "ob_low": float(ob.zone_low),
                        "ob_high": float(ob.zone_high),
                        "fvg_bottom": float(fvg.bottom),
                        "fvg_top": float(fvg.top),
                        "confirmed_15m_start_utc": str(
                            current.timestamp
                        ),
                    }

        if not seen_sweep:
            return _no_trade(
                "WAIT_FOR_15M_LIQUIDITY_SWEEP"
            )

        if not seen_zone:
            return _no_trade(
                "SWEEP_NOT_AT_VALID_1H_OB_FVG"
            )

        return _no_trade(
            "WAIT_FOR_OB_FVG_RETEST_OR_RR"
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
