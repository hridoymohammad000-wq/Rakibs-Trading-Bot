from pybit.unified_trading import HTTP
import pandas as pd
import math
import time
import sys
import os

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(__file__))
)

from utils.logger import logger
from config.settings import (
    API_KEY,
    API_SECRET,
    USE_TESTNET,
    USE_DEMO,
)

_client = HTTP(
    testnet=USE_TESTNET,
    demo=USE_DEMO,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

INTERVAL_MS = {
    "15": 15 * 60 * 1000,
    "60": 60 * 60 * 1000,
}

COLUMNS = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
]

NUMERIC_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
]


def get_ohlcv(
    symbol: str,
    interval: str = "15",
    limit: int = 500
) -> pd.DataFrame:
    """Fetch and validate complete Bybit futures candles."""

    try:
        interval = str(interval)

        if interval not in INTERVAL_MS:
            raise ValueError(
                "Only 15M and 1H intervals are supported"
            )

        if not isinstance(limit, int) or not 1 <= limit <= 998:
            raise ValueError("limit must be 1-998")

        response = _client.get_kline(
            category="linear",
            symbol=symbol.upper(),
            interval=interval,
            limit=limit + 2,
        )

        if response.get("retCode") != 0:
            raise ValueError(
                f"Bybit API: {response.get('retMsg')}"
            )

        rows = response.get("result", {}).get("list")

        if not isinstance(rows, list) or not rows:
            raise ValueError("No candle data returned")

        df = pd.DataFrame(rows, columns=COLUMNS)

        df["timestamp"] = pd.to_numeric(
            df["timestamp"], errors="raise"
        ).astype("int64")

        for col in NUMERIC_COLUMNS:
            df[col] = pd.to_numeric(
                df[col], errors="raise"
            )

        if df[NUMERIC_COLUMNS].isna().any().any():
            raise ValueError("Missing numeric values")

        for row in df[NUMERIC_COLUMNS].itertuples(
            index=False, name=None
        ):
            if not all(math.isfinite(v) for v in row):
                raise ValueError("Invalid numeric values")

        if (
            (df[["open", "high", "low", "close"]] <= 0)
            .any().any()
        ):
            raise ValueError("Invalid OHLC prices")

        if (df[["volume", "turnover"]] < 0).any().any():
            raise ValueError("Negative volume or turnover")

        now_ms = time.time_ns() // 1_000_000
        duration = INTERVAL_MS[interval]

        # Remove unfinished candles
        df = df[
            df["timestamp"] + duration <= now_ms
        ].copy()

        df = df.sort_values("timestamp")

        if df["timestamp"].duplicated().any():
            raise ValueError("Duplicate timestamps detected")

        df = df.tail(limit).copy()

        if len(df) != limit:
            raise ValueError(
                f"Expected {limit}, received {len(df)}"
            )

        # Check consecutive candle timestamps
        gaps = df["timestamp"].diff().iloc[1:]

        if (gaps != duration).any():
            raise ValueError("Missing candle / data gap")

        if (
            (df["high"] < df[["open", "low", "close"]].max(axis=1))
            .any()
        ):
            raise ValueError("Invalid candle high")

        if (
            (df["low"] > df[["open", "high", "close"]].min(axis=1))
            .any()
        ):
            raise ValueError("Invalid candle low")

        # Preserve original naive UTC timestamp format
        df["timestamp"] = pd.to_datetime(
            df["timestamp"], unit="ms"
        )

        return df.reset_index(drop=True)

    except Exception as e:
        logger.error(
            f"get_ohlcv({symbol}, {interval}): {e}"
        )
        return pd.DataFrame(columns=COLUMNS)


def get_multi_timeframe_ohlcv(
    symbol: str,
    limit: int = 500
) -> dict:
    """Fetch both required strategy timeframes."""

    return {
        "15m": get_ohlcv(symbol, "15", limit),
        "1h": get_ohlcv(symbol, "60", limit),
    }


def get_usdt_perpetuals(limit: int = 50) -> list:
    """Fetch active USDT linear perpetual symbols."""

    try:
        resp = _client.get_instruments_info(
            category="linear",
            status="Trading",
        )

        pairs = [
            i["symbol"]
            for i in resp["result"]["list"]
            if i["symbol"].endswith("USDT")
        ]

        logger.info(
            f"{len(pairs)} pairs found, returning top {limit}"
        )

        return pairs[:limit]

    except Exception as e:
        logger.error(f"get_usdt_perpetuals: {e}")
        return []
