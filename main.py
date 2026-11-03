import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import schedule, time
from utils.logger import logger
from data.market_data import get_usdt_perpetuals, get_ohlcv
from config.settings import SCAN_INTERVAL, TIMEFRAME, TOP_N_PAIRS


def run_scan() -> None:
    logger.info("══════════ Scan Cycle শুরু ══════════")
    pairs = get_usdt_perpetuals(limit=20)
    if not pairs:
        logger.warning("কোনো pair পাওয়া যায়নি — পরের cycle এ retry হবে।")
        return
    logger.info(f"{len(pairs)} pairs লোড → top {TOP_N_PAIRS}: {pairs[:TOP_N_PAIRS]}")

    # Phase 1 complete হলে এখানে analysis → scanner → execution call আসবে
    # sample: একটা pair এর OHLCV টেস্ট করা
    df = get_ohlcv(pairs[0], interval=TIMEFRAME, limit=100)
    if not df.empty:
        logger.info(f"{pairs[0]} last candle → close={df['close'].iloc[-1]:.4f}")

    logger.info("══════════ Scan Cycle শেষ ══════════")


if __name__ == "__main__":
    logger.info("🤖 Rakibs Trading Bot চালু হচ্ছে...")
    run_scan()   # immediate first run
    schedule.every(SCAN_INTERVAL).seconds.do(run_scan)
    while True:
        schedule.run_pending()
        time.sleep(1)
