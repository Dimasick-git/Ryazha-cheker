"""Telegram Bot API client — fully async with httpx."""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_MAX_LENGTH = 4096

log = logging.getLogger(__name__)

_RETRY_DELAYS = [2, 4]


class TelegramClient:
    def __init__(self, token: str, chat_id: str, topic_id: Optional[int] = None):
        self.token = token
        self.chat_id = chat_id
        self.topic_id = topic_id
        self.base = f"{TELEGRAM_API}/bot{token}"

    async def validate_async(self) -> bool:
        """Verify the bot token via getMe."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.base}/getMe")
            data = resp.json()
            if data.get("ok"):
                bot = data["result"]
                log.info("Bot validated: @%s id=%d", bot["username"], bot["id"])
                return True
            log.error("getMe failed: %s", data.get("description"))
            return False
        except Exception as e:
            log.error("getMe exception: %s", e)
            return False

    async def send_async(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        reply_markup: Optional[Dict] = None,
    ) -> bool:
        """Send a message, automatically splitting parts longer than 4096 chars."""
        parts = self._split(text)
        log.info("Sending message: %d part(s) to chat=%s", len(parts), self.chat_id)
        all_ok = True
        async with httpx.AsyncClient(timeout=30) as client:
            for i, part in enumerate(parts, 1):
                current_markup = reply_markup if i == len(parts) else None
                ok = await self._send_part(client, part, parse_mode, disable_web_page_preview, current_markup)
                if ok:
                    log.debug("Part %d/%d sent (%d bytes)", i, len(parts), len(part))
                else:
                    log.warning("Part %d/%d failed HTML, retrying as plain text", i, len(parts))
                    plain = self._strip_html(part)
                    ok = await self._send_part(client, plain, parse_mode=None)
                    if ok:
                        log.info("Part %d/%d sent as plain text fallback", i, len(parts))
                    else:
                        log.error("Part %d/%d failed all attempts", i, len(parts))
                        all_ok = False

                if i < len(parts):
                    await asyncio.sleep(0.5)

        return all_ok

    # Alias for call sites that use the old name
    send = send_async
    send_message = send_async

    async def _send_part(
        self,
        client: httpx.AsyncClient,
        text: str,
        parse_mode: Optional[str],
        disable_web_page_preview: bool = True,
        reply_markup: Optional[Dict] = None,
    ) -> bool:
        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if self.topic_id:
            payload["message_thread_id"] = self.topic_id

        for attempt in range(3):
            try:
                resp = await client.post(f"{self.base}/sendMessage", json=payload)

                if resp.status_code == 429:
                    wait = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    try:
                        retry_after = int(resp.json().get("parameters", {}).get("retry_after", wait))
                        wait = max(wait, retry_after)
                    except Exception:
                        pass
                    log.warning("HTTP 429 rate-limit attempt=%d/3, waiting %ds", attempt + 1, wait)
                    if attempt < 2:
                        await asyncio.sleep(wait)
                        continue
                    log.error("_send_part: all attempts exhausted (429)")
                    return False

                if resp.status_code >= 500:
                    wait = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    log.warning("HTTP %d server error attempt=%d/3, waiting %ds",
                                resp.status_code, attempt + 1, wait)
                    if attempt < 2:
                        await asyncio.sleep(wait)
                        continue
                    log.error("_send_part: all attempts exhausted (5xx)")
                    return False

                data = resp.json()
                if data.get("ok"):
                    return True

                desc = data.get("description", "unknown error")
                log.warning("Telegram API error: %s", desc)

                if "chat not found" in desc.lower():
                    log.info("Hint: verify CHAT_ID (send /start to bot)")
                elif "blocked" in desc.lower():
                    log.info("Hint: user has blocked the bot")
                elif "parse" in desc.lower():
                    log.info("Hint: HTML parse error — will retry as plain text")
                elif "too many requests" in desc.lower():
                    retry_after = data.get("parameters", {}).get(
                        "retry_after", _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    )
                    log.warning("Flood control — waiting %ds", retry_after)
                    if attempt < 2:
                        await asyncio.sleep(retry_after)
                        continue

                return False

            except httpx.TimeoutException:
                wait = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                log.warning("Telegram timeout attempt=%d/3, retry in %ds", attempt + 1, wait)
                if attempt < 2:
                    await asyncio.sleep(wait)
            except httpx.ConnectError as e:
                wait = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                log.warning("Connection error attempt=%d/3 backoff=%ds: %s", attempt + 1, wait, e)
                if attempt < 2:
                    await asyncio.sleep(wait)
            except Exception as e:
                log.error("Telegram unexpected exception: %s", e)
                return False

        log.error("_send_part: all attempts exhausted")
        return False

    @staticmethod
    def _split(text: str, limit: int = TELEGRAM_MAX_LENGTH) -> List[str]:
        """Split text into parts of at most `limit` characters, cutting on newlines."""
        if len(text) <= limit:
            return [text]

        parts = []
        while text:
            if len(text) <= limit:
                parts.append(text)
                break
            cut = text.rfind("\n", 0, limit)
            if cut == -1:
                cut = limit
            parts.append(text[:cut])
            text = text[cut:].lstrip("\n")
            if not text:
                break

        return parts

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove HTML tags for plain-text fallback."""
        clean = re.sub(r"<[^>]+>", "", text)
        return (
            clean
            .replace("&amp;",  "&")
            .replace("&lt;",   "<")
            .replace("&gt;",   ">")
            .replace("&quot;", '"')
        )
