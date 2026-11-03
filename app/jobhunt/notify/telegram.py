"""Telegram notification module for job-hunt."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def redact_token(text: str) -> str:
    """Redacts any Telegram bot token in text."""
    if not text:
        return ""
    return re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot<REDACTED>", str(text))


def escape_md(text: Any) -> str:
    """Escapes Telegram MarkdownV2 reserved characters."""
    if text is None:
        return ""
    s = str(text)
    # Characters that must be escaped in MarkdownV2 outside of formatting tags:
    # _ * [ ] ( ) ~ ` > # + - = | { } . ! \
    escape_chars = r"_*[]()~`>#+-=|{}.!\\"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", s)


class TelegramNotifier:
    """Sends job notifications and text messages to Telegram via Bot API."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token.strip()
        self.chat_id = str(chat_id).strip()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def send_text(self, text: str) -> bool:
        """Send plain text (for test messages)."""
        if not self.bot_token or not self.chat_id:
            logger.error("Telegram bot token or chat ID is missing")
            return False

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code in (401, 403):
                logger.error(f"Telegram API authentication error {resp.status_code}: {resp.text}")
                print(f"Telegram API authentication error {resp.status_code}: {resp.text}")
                return False
            if resp.status_code != 200:
                logger.error(f"Telegram send failed ({resp.status_code}): {resp.text}")
                print(f"Telegram send failed ({resp.status_code}): {resp.text}")
                return False
            return True

    async def send_job(self, job: Dict[str, Any], company_name: str) -> bool:
        """Send one job as a formatted Telegram message in MarkdownV2."""
        if not self.bot_token or not self.chat_id:
            logger.error("Telegram bot token or chat ID is missing")
            return False

        score = job.get("score", 0)
        tier = (job.get("tier") or "drop").lower()
        tier_emojis = {
            "high": "🔥",
            "medium": "⭐",
            "low": "💡",
        }
        tier_emoji = tier_emojis.get(tier, "❓")

        company = escape_md(company_name or "Unknown")
        title = escape_md(job.get("title") or "")
        loc = escape_md(job.get("location_raw") or "Unknown")

        posted_at = job.get("posted_at")
        if posted_at:
            if isinstance(posted_at, str):
                try:
                    posted_at = datetime.fromisoformat(posted_at)
                except Exception:
                    posted_at = None
            if posted_at and posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)

        if posted_at:
            now = datetime.now(timezone.utc)
            delta = now - posted_at
            if delta.days <= 0:
                hrs = delta.seconds // 3600
                if hrs <= 0:
                    mins = max(delta.seconds // 60, 1)
                    ago_str = f"{mins}m ago"
                else:
                    ago_str = f"{hrs}h ago"
            elif delta.days == 1:
                ago_str = "1d ago"
            else:
                ago_str = f"{delta.days}d ago"
        else:
            ago_str = "recently"

        posted_ago = escape_md(ago_str)
        job_kind = escape_md(job.get("job_kind") or "other")
        role_family = escape_md(job.get("role_family") or "other")
        source = escape_md(job.get("source") or "unknown")

        raw_url = job.get("apply_url") or ""
        safe_url = raw_url.replace("\\", "\\\\").replace(")", r"\)")

        pipe = r"\|"
        msg = (
            f"🔥 *{score}* {pipe} {tier_emoji} {company}\n"
            f"*{title}*\n"
            f"📍 {loc}  {pipe}  🕐 {posted_ago}\n"
            f"🏷 {job_kind}  ·  {role_family}\n"
            f"🔗 [Apply]({safe_url})\n"
            f"📋 Source: {source}"
        )

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": msg,
            "parse_mode": "MarkdownV2",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code in (401, 403):
                logger.error(f"Telegram API authentication error {resp.status_code}: {resp.text}")
                print(f"Telegram API authentication error {resp.status_code}: {resp.text}")
                return False
            if resp.status_code != 200:
                logger.error(f"Telegram send failed ({resp.status_code}): {resp.text}")
                print(f"Telegram send failed ({resp.status_code}): {resp.text}")
                return False
            return True

