import pandas as pd
import numpy as np


def add_ema(df: pd.DataFrame, period: int, col: str = "close") -> pd.Series:
    return df[col].ewm(span=period, adjust=False).mean()


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl   = df["high"] - df["low"]
    hpc  = (df["high"] - df["close"].shift()).abs()
    lpc  = (df["low"]  - df["close"].shift()).abs()
    tr   = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def add_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hi, lo, cl = df["high"], df["low"], df["close"]
    plus_dm    = hi.diff().clip(lower=0)
    minus_dm   = (-lo.diff()).clip(lower=0)
    plus_dm    = plus_dm.where(plus_dm > minus_dm, 0.0)
    minus_dm   = minus_dm.where(minus_dm > plus_dm, 0.0)
    atr        = add_atr(df, period)
    plus_di    = 100 * plus_dm.ewm(span=period, adjust=False).mean() / (atr + 1e-10)
    minus_di   = 100 * minus_dm.ewm(span=period, adjust=False).mean() / (atr + 1e-10)
    dx         = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    return dx.ewm(span=period, adjust=False).mean()


def add_swing_highs_lows(df: pd.DataFrame, lookback: int = 10) -> pd.DataFrame:
    df = df.copy()
    df["swing_high"] = df["high"][(df["high"] == df["high"].rolling(lookback * 2 + 1, center=True).max())]
    df["swing_low"]  = df["low"][(df["low"]  == df["low"].rolling(lookback * 2 + 1, center=True).min())]
    return df
