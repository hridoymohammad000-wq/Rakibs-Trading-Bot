from dotenv import load_dotenv
import os

load_dotenv()

# ─── Bybit API ───────────────────────────────────────────────────────────
API_KEY     = os.getenv("BYBIT_API_KEY", "")
API_SECRET  = os.getenv("BYBIT_API_SECRET", "")
USE_TESTNET = False
USE_DEMO = True

# ─── Telegram ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Market Parameters ───────────────────────────────────────────────────
TIMEFRAME     = "15"    # Bybit interval string (minutes)
SCAN_INTERVAL = 60      # seconds between scan cycles
TOP_N_PAIRS   = 5       # scanner top-N shortlist

# ─── Risk Management ─────────────────────────────────────────────────────
RISK_PER_TRADE  = 0.01  # 1% per trade
MAX_OPEN_TRADES = 3
LEVERAGE        = 5

# ─── Indicator Settings ──────────────────────────────────────────────────
EMA_FAST       = 9
EMA_SLOW       = 21
EMA_TREND      = 200
ATR_PERIOD     = 14
ADX_PERIOD     = 14
ADX_THRESHOLD  = 25
SWING_LOOKBACK = 10
