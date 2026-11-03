from dotenv import load_dotenv
import os

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

# Bybit API
API_KEY     = os.getenv("BYBIT_API_KEY", "")
API_SECRET  = os.getenv("BYBIT_API_SECRET", "")
USE_TESTNET = os.getenv("USE_TESTNET", "False").lower() in ("true", "1")
USE_DEMO    = os.getenv("USE_DEMO", "True").lower() in ("true", "1")

# Telegram
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Market Parameters
TIMEFRAME     = "15"    # Bybit interval string (minutes)
SCAN_INTERVAL = 60      # seconds between scan cycles
TOP_N_PAIRS   = 50      # scanner top-N shortlist

# Risk Management
RISK_PER_TRADE  = 0.02  # 2% per trade
MAX_OPEN_TRADES = 3
LEVERAGE        = 5
