import asyncio, aiohttp
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import logger
from config.settings import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


async def _send(text: str) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{_BASE}/sendMessage",
                                json={"chat_id": TELEGRAM_CHAT_ID,
                                      "text": text,
                                      "parse_mode": "HTML"}) as r:
            if r.status != 200:
                logger.warning(f"Telegram {r.status}: {await r.text()}")


def send_alert(text: str) -> None:
    """যেকোনো জায়গা থেকে call করা যাবে — sync wrapper।"""
    try:
        asyncio.run(_send(text))
        logger.info(f"Alert sent: {text[:60]}...")
    except Exception as e:
        logger.error(f"send_alert → {e}")
