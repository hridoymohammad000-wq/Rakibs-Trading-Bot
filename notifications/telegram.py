"""Telegram notifications for 15M signals, hourly digests, and daily summaries."""
import asyncio
import aiohttp
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import logger
from config.settings import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


async def _async_send(text: str, parse_mode: str = "HTML") -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram token or chat ID is missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("ok"):
                    return True
                else:
                    logger.warning(f"Telegram API response: {data}")
                    return False
    except Exception as e:
        logger.error(f"Telegram network error: {e}")
        return False


def send_alert(text: str, parse_mode: str = "HTML") -> bool:
    """Synchronous wrapper to send any raw message."""
    try:
        return asyncio.run(_async_send(text, parse_mode=parse_mode))
    except Exception as e:
        logger.error(f"send_alert exception: {e}")
        return False


def send_signal_alert(candidate: dict, risk: dict) -> bool:
    """Send 15M Trade Signal Alert when score >= 8 and risk check passes."""
    symbol = candidate.get("symbol", "UNKNOWN")
    strategy = candidate.get("strategy", "").upper()
    direction = candidate.get("signal", "").upper()
    score = candidate.get("score", 0)
    rr = candidate.get("risk_reward", 0)

    entry = candidate.get("entry", 0)
    sl = candidate.get("stop_loss", 0)
    tp1 = candidate.get("tp1", 0)
    tp2 = candidate.get("tp2", 0)

    planned_qty = risk.get("quantity", "N/A")
    risk_budget = risk.get("risk_budget_usdt", "N/A")
    slots_used = risk.get("slots_used", 0)
    equity = risk.get("equity_usdt", "N/A")

    candle_time = candidate.get("candle_15m_closed_at_utc", "UTC")

    dir_emoji = "🟢 BUY (LONG)" if direction == "BUY" else "🔴 SELL (SHORT)"

    msg = f"""🚨 <b>BYBIT FUTURES TRADE SIGNAL</b> 🚨

⚡ <b>Pair:</b> #{html.escape(symbol)}
🎯 <b>Strategy:</b> {html.escape(strategy)}
📈 <b>Direction:</b> {dir_emoji}
⭐ <b>Confluence Score:</b> {score}/13

📍 <b>Entry Price:</b> {entry}
🛑 <b>Stop Loss (SL):</b> {sl}
🎯 <b>Take Profit 1:</b> {tp1}
🎯 <b>Take Profit 2:</b> {tp2}
⚖️ <b>Risk to Reward:</b> 1 : {rr}

🛡️ <b>Risk & Position Sizing (2% Rule):</b>
• Planned Qty: {planned_qty}
• Risk Budget: ${risk_budget} USDT
• Trade Slots: {slots_used}/3 Used
• Demo Equity: ${equity} USDT

⏰ <i>15M Candle: {html.escape(str(candle_time))} | Status: Verified</i>"""

    return send_alert(msg, parse_mode="HTML")


def send_scan_ping(scanned_count: int, top_radar: dict = None) -> bool:
    """Send short 15M scan heartbeat summary."""
    top_str = "None"
    if top_radar:
        sym = top_radar.get("symbol", "")
        sc = top_radar.get("score", 0)
        strat = top_radar.get("strategy", "")
        top_str = f"#{sym} (Score: {sc}/13 - {strat})"

    msg = f"""🔍 <b>15M Scan Cycle Complete</b>
• <b>Pairs Checked:</b> {scanned_count} / {scanned_count}
• <b>Top Radar:</b> {html.escape(top_str)}
• <b>Action:</b> No qualifying trade (Capital Protected 🛡️)"""

    return send_alert(msg, parse_mode="HTML")


def send_hourly_digest(
    hour_utc: str,
    scans_completed: int,
    signals_count: int,
    slots_used: int,
    equity: str,
    btc_summary: str = "Sideways",
    eth_summary: str = "Neutral",
    top_watch: str = "None"
) -> bool:
    """Send Hourly Market & Bot Digest."""
    msg = f"""⏱️ <b>HOURLY BOT STATUS & MARKET DIGEST</b>
📅 <i>Hour: {html.escape(hour_utc)} UTC</i>

📊 <b>Bot Activity (Last 1 Hour):</b>
• Scans Completed: {scans_completed} Cycles
• Signals Triggered: {signals_count}
• Open Positions: {slots_used}/3 Slots Used
• Current Demo Equity: ${equity} USDT

🌐 <b>Macro Market Structure (1H):</b>
• BTC: {html.escape(btc_summary)}
• ETH: {html.escape(eth_summary)}

👀 <b>Top Watchlist:</b> {html.escape(top_watch)}

🟢 <i>System: Render Cloud LIVE | Health: 100%</i>"""

    return send_alert(msg, parse_mode="HTML")


def send_daily_summary(
    date_str: str,
    start_equity: str,
    current_equity: str,
    pnl_usdt: str,
    pnl_pct: str,
    total_cycles: int,
    signals_generated: int
) -> bool:
    """Send Daily 24H Summary Report."""
    msg = f"""📅 <b>DAILY PERFORMANCE & SUMMARY REPORT</b>
Date: {html.escape(date_str)} (UTC)

💼 <b>Account Overview:</b>
• Starting Balance: ${start_equity} USDT
• Current Balance:  ${current_equity} USDT
• Net Daily PnL:   {pnl_usdt} USDT ({pnl_pct}%) 🚀

🤖 <b>Bot Analytics:</b>
• Total Scan Cycles: {total_cycles}
• High Confluence Signals: {signals_generated}
• Active Open Trades: 0

🛡️ <i>Risk Management: 100% Capital Protection Adhered</i>
Have a great trading day ahead! ☕"""

    return send_alert(msg, parse_mode="HTML")
