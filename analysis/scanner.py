"""Read-only Bybit Futures multi-pair signal scanner."""

import math
import time
from datetime import datetime, timezone

import pandas as pd
from pybit.unified_trading import HTTP

from data.market_data import get_multi_timeframe_ohlcv

from analysis.indicators import (
    add_adx,
    add_atr,
    add_ema,
)

from analysis.structure import (
    analyze_market_structure,
    detect_1h_range,
    detect_1h_liquidity_grab,
    detect_fvg,
)

from strategy.pullback import generate_pullback_signal
from strategy.ict import generate_ict_signal
from strategy.range_lg import generate_range_signal


# Public market-data requests only.
# No API keys and no order methods are used here.
_public = HTTP(testnet=False)

WEIGHTS = {
    "volume": 1,
    "volatility": 1,
    "structure": 2,
    "key_level": 2,
    "liquidity_grab": 3,
    "fvg": 2,
    "confluence": 2,
}


def _ok(response, label):
    if response.get("retCode") != 0:
        raise RuntimeError(
            f"{label}: {response.get('retMsg')}"
        )
    return response.get("result", {})


def _active_usdt_perpetuals():
    symbols = set()
    cursor = None
    seen = set()

    while True:
        params = {
            "category": "linear",
            "status": "Trading",
            "limit": 1000,
        }

        if cursor:
            params["cursor"] = cursor

        result = _ok(
            _public.get_instruments_info(**params),
            "instruments",
        )

        for item in result.get("list", []):
            if (
                item.get("contractType")
                == "LinearPerpetual"
                and item.get("status") == "Trading"
                and item.get("quoteCoin") == "USDT"
                and item.get("symbol", "").endswith("USDT")
            ):
                symbols.add(item["symbol"])

        cursor = result.get("nextPageCursor") or None

        if not cursor:
            break

        if cursor in seen:
            raise RuntimeError(
                "Repeated instruments page cursor"
            )

        seen.add(cursor)

    return symbols


def _universe(
    min_turnover_usdt,
    max_spread_pct,
    max_pairs,
):
    active = _active_usdt_perpetuals()

    tickers = _ok(
        _public.get_tickers(category="linear"),
        "tickers",
    )

    filtered = []

    for item in tickers.get("list", []):
        symbol = item.get("symbol", "")

        if symbol not in active:
            continue

        try:
            turnover = float(item["turnover24h"])
            bid = float(item["bid1Price"])
            ask = float(item["ask1Price"])

            if not all(
                math.isfinite(v)
                for v in (turnover, bid, ask)
            ):
                continue

            if (
                turnover <= min_turnover_usdt
                or bid <= 0
                or ask < bid
            ):
                continue

            spread_pct = (
                100.0 * (ask - bid)
                / ((bid + ask) / 2.0)
            )

            if spread_pct > max_spread_pct:
                continue

            filtered.append({
                "symbol": symbol,
                "turnover_usdt": turnover,
                "spread_pct": spread_pct,
            })

        except (
            KeyError,
            TypeError,
            ValueError,
            ZeroDivisionError,
        ):
            continue

    filtered.sort(
        key=lambda row: row["turnover_usdt"],
        reverse=True,
    )

    if max_pairs is None:
        return filtered

    return filtered[:max_pairs]


def _score(evidence):
    return sum(
        weight
        for name, weight in WEIGHTS.items()
        if evidence[name]
    )


def _scan_symbol(
    pair,
    min_turnover_usdt,
    min_atr_pct,
    max_atr_pct,
    max_age_minutes,
    min_score,
):
    symbol = pair["symbol"]

    candles = get_multi_timeframe_ohlcv(
        symbol, 500
    )

    h1 = candles["1h"]
    m15 = candles["15m"]

    if (
        h1.empty
        or m15.empty
        or len(h1) != 500
        or len(m15) != 500
    ):
        raise ValueError(
            "500 closed candles not available"
        )

    h1_close = (
        pd.to_datetime(
            h1["timestamp"].iloc[-1], utc=True
        )
        + pd.Timedelta(hours=1)
    )

    m15_close = (
        pd.to_datetime(
            m15["timestamp"].iloc[-1], utc=True
        )
        + pd.Timedelta(minutes=15)
    )

    now = pd.Timestamp.now(tz="UTC")

    if not (
        h1_close <= m15_close
        < h1_close + pd.Timedelta(hours=1)
    ):
        raise ValueError(
            "Timeframes are not aligned"
        )

    if (
        m15_close > now
        or now - m15_close
        > pd.Timedelta(minutes=max_age_minutes)
    ):
        raise ValueError(
            "Market data is stale or future-dated"
        )

    structure = analyze_market_structure(h1)

    adx = float(add_adx(h1).iloc[-1])
    atr = float(add_atr(m15).iloc[-1])

    atr_pct = (
        100.0 * atr
        / float(m15["close"].iloc[-1])
    )

    if (
        not all(
            math.isfinite(v)
            for v in (adx, atr, atr_pct)
        )
        or atr <= 0
    ):
        raise ValueError("Invalid ATR or ADX")

    volatility_ok = (
        min_atr_pct <= atr_pct <= max_atr_pct
    )

    range_info = detect_1h_range(h1)

    sweep = detect_1h_liquidity_grab(
        h1, range_info
    )

    regime = "NONE"

    if (
        structure["trend"] in (
            "UPTREND", "DOWNTREND"
        )
        and adx > 20
    ):
        regime = "TREND"

    elif (
        structure["trend"]
        == "MIXED_OR_SIDEWAYS"
        and range_info["valid"]
        and adx < 25
    ):
        regime = "RANGE"

    fvgs = detect_fvg(m15)

    latest = m15.iloc[-1]

    ema20 = float(
        add_ema(m15, 20).iloc[-1]
    )

    is_near_ema = (
        float(latest.low)
        <= ema20 + 0.20 * atr
        and float(latest.high)
        >= ema20 - 0.20 * atr
    )

    near_range_edge = bool(
        range_info["valid"]
        and (
            abs(
                float(latest.close)
                - range_info["range_high"]
            ) <= 0.5 * float(range_info["atr"])
            or
            abs(
                float(latest.close)
                - range_info["range_low"]
            ) <= 0.5 * float(range_info["atr"])
        )
    )

    signals = {
        "pullback": generate_pullback_signal(
            h1, m15
        ),
        "ict": generate_ict_signal(
            h1, m15
        ),
        "range": generate_range_signal(
            h1, m15
        ),
    }

    rows = []

    for name, signal in signals.items():
        side = signal.get("signal")

        matched = side in ("BUY", "SELL")

        relevant_regime = (
            regime == "RANGE"
            if name == "range"
            else regime == "TREND"
        )

        expected_side = (
            "BUY"
            if structure["trend"] == "UPTREND"
            else "SELL"
            if structure["trend"] == "DOWNTREND"
            else None
        )

        if (
            name != "range"
            and matched
            and side != expected_side
        ):
            relevant_regime = False

        recent_fvg = False

        if not fvgs.empty:
            direction = (
                "BULLISH"
                if side == "BUY"
                else "BEARISH"
                if side == "SELL"
                else None
            )

            if direction:
                recent = fvgs[
                    (fvgs["direction"] == direction)
                    & (
                        fvgs["confirmation_index"]
                        >= len(m15) - 9
                    )
                ]
                recent_fvg = not recent.empty

        evidence = {
            "volume": (
                pair["turnover_usdt"]
                > min_turnover_usdt
            ),
            "volatility": volatility_ok,
            "structure": relevant_regime,
            "key_level": (
                matched
                or (
                    near_range_edge
                    if name == "range"
                    else is_near_ema
                    if name == "pullback"
                    else False
                )
            ),
            "liquidity_grab": (
                (
                    matched
                    and name in ("ict", "range")
                )
                or (
                    name == "range"
                    and sweep["status"] in (
                        "BSL_GRAB", "SSL_GRAB"
                    )
                )
            ),
            "fvg": (
                recent_fvg
                or (
                    matched
                    and name in ("ict", "range")
                )
            ),
            "confluence": (
                matched and relevant_regime
            ),
        }

        score = _score(evidence)

        rr = signal.get("risk_reward")

        valid_rr = (
            isinstance(rr, (int, float))
            and math.isfinite(rr)
            and rr >= 2
        )

        # Each strategy owns its setup requirements.  The shared
        # evidence score is useful for comparison and reporting, but
        # it must not make one strategy depend on another strategy's
        # signals (for example, requiring an ICT liquidity grab for a
        # valid pullback setup).
        candidate = bool(
            matched
            and relevant_regime
            and volatility_ok
            and valid_rr
        )

        rows.append({
            "symbol": symbol,
            "strategy": name,
            "signal": side,
            "reason": signal.get("reason"),
            "score": score,
            "max_score": 13,
            "candidate": candidate,
            "risk_reward": rr,
            "entry": signal.get("entry"),
            "stop_loss": signal.get("stop_loss"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "turnover_usdt": pair["turnover_usdt"],
            "spread_pct": round(
                pair["spread_pct"], 5
            ),
            "atr_pct": round(atr_pct, 4),
            "adx": round(adx, 2),
            "regime": regime,
            "evidence": evidence,
            "candle_15m_closed_at_utc": str(
                m15_close
            ),
        })

    return rows


def scan_market(
    max_pairs=50,
    min_turnover_usdt=50_000_000,
    max_spread_pct=0.15,
    min_atr_pct=0.10,
    max_atr_pct=10.0,
    min_score=8,
    max_age_minutes=30,
    delay_seconds=0.12,
):
    """
    Read-only multi-pair scanner.

    max_pairs=50: top 50 volume pairs.
    max_pairs=None: all qualified pairs.

    Returns ranked strategy reports.
    Does not place any orders.
    """

    if (
        max_pairs is not None
        and (
            not isinstance(max_pairs, int)
            or max_pairs < 1
        )
    ):
        raise ValueError(
            "max_pairs must be positive or None"
        )

    if not (
        0 < min_turnover_usdt
        and 0 <= max_spread_pct < 100
    ):
        raise ValueError(
            "Invalid volume or spread threshold"
        )

    if not (
        0 < min_atr_pct < max_atr_pct
        and 0 <= min_score <= 13
    ):
        raise ValueError(
            "Invalid ATR or score threshold"
        )

    if (
        max_age_minutes < 15
        or delay_seconds < 0
    ):
        raise ValueError(
            "Invalid freshness or delay threshold"
        )

    pairs = _universe(
        min_turnover_usdt,
        max_spread_pct,
        max_pairs,
    )

    reports = []
    errors = []

    for index, pair in enumerate(pairs):
        try:
            reports.extend(
                _scan_symbol(
                    pair,
                    min_turnover_usdt,
                    min_atr_pct,
                    max_atr_pct,
                    max_age_minutes,
                    min_score,
                )
            )

        except Exception as exc:
            errors.append({
                "symbol": pair["symbol"],
                "error": str(exc),
            })

        if (
            delay_seconds
            and index < len(pairs) - 1
        ):
            time.sleep(delay_seconds)

    reports.sort(
        key=lambda row: (
            -row["score"],
            -row["turnover_usdt"],
        )
    )

    candidates = [
        row for row in reports
        if row["candidate"]
    ]

    # Give every independently qualified strategy a route to the
    # scheduler instead of letting the shared evidence score crowd a
    # strategy out of the three dispatch slots.  Candidates are already
    # score-ranked, so this selects the strongest row for each strategy.
    top_candidates = []
    selected_strategies = set()
    selected_candidates = set()

    for row in candidates:
        strategy = row["strategy"]

        if strategy in selected_strategies:
            continue

        top_candidates.append(row)
        selected_strategies.add(strategy)
        selected_candidates.add((row["symbol"], strategy))

        if len(top_candidates) == 3:
            break

    # If fewer than three strategies qualified, fill the remaining
    # slots with the next highest-ranked candidates from other symbols.
    selected_symbols = {
        row["symbol"] for row in top_candidates
    }

    for row in candidates:
        if len(top_candidates) == 3:
            break

        if (
            (row["symbol"], row["strategy"])
            in selected_candidates
            or row["symbol"] in selected_symbols
        ):
            continue

        top_candidates.append(row)
        selected_candidates.add(
            (row["symbol"], row["strategy"])
        )
        selected_symbols.add(row["symbol"])

    return {
        "mode": "READ_ONLY",
        "scanned_pairs": len(pairs),
        "successful_pairs": len({
            r["symbol"] for r in reports
        }),
        "failed_pairs": len(errors),
        "candidates": candidates,
        "top_candidates": top_candidates,
        "reports": reports,
        "errors": errors,
        "scanned_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
    }
