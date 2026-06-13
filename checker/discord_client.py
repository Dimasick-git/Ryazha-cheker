"""Discord webhook client — fully async with httpx."""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from .formatter import split_message

DISCORD_MAX_EMBED_DESC = 4096
DISCORD_MAX_CONTENT = 2000

log = logging.getLogger(__name__)

_RETRY_DELAYS = [2, 4]


class DiscordWebhookClient:
    """Send GitHub activity notifications to a Discord channel via webhook."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, text: str, username: str = "Ryazha-cheker") -> bool:
        """Send a message to Discord, auto-splitting if needed."""
        md = self._html_to_discord(text)
        parts = self._split(md)
        log.info("Discord: sending %d part(s)", len(parts))
        all_ok = True
        async with httpx.AsyncClient(timeout=30) as client:
            for i, part in enumerate(parts, 1):
                ok = await self._send_part(client, part, username=username)
                if not ok:
                    log.error("Discord: part %d/%d failed", i, len(parts))
                    all_ok = False
                if i < len(parts):
                    await asyncio.sleep(0.5)
        return all_ok

    async def send_embed(
        self,
        title: str,
        description: str,
        color: int = 0x6366F1,
        url: Optional[str] = None,
        fields: Optional[List[Dict[str, Any]]] = None,
        username: str = "Ryazha-cheker",
    ) -> bool:
        """Send a rich embed message to Discord."""
        embed: Dict[str, Any] = {
            "title": title[:256],
            "description": description[:DISCORD_MAX_EMBED_DESC],
            "color": color,
        }
        if url:
            embed["url"] = url
        if fields:
            embed["fields"] = [
                {
                    "name": f.get("name", "")[:256],
                    "value": f.get("value", "")[:1024],
                    "inline": f.get("inline", False),
                }
                for f in fields[:25]
            ]
        async with httpx.AsyncClient(timeout=30) as client:
            return await self._send_part(client, "", username=username, embeds=[embed])

    async def _send_part(
        self,
        client: httpx.AsyncClient,
        content: str,
        username: str = "Ryazha-cheker",
        embeds: Optional[List[Dict]] = None,
    ) -> bool:
        payload: Dict[str, Any] = {"username": username}
        if content:
            payload["content"] = content[:DISCORD_MAX_CONTENT]
        if embeds:
            payload["embeds"] = embeds

        for attempt in range(3):
            try:
                resp = await client.post(self.webhook_url, json=payload)
                if resp.status_code == 204:
                    return True
                if resp.status_code == 429:
                    wait = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    try:
                        retry_after = resp.json().get("retry_after", wait * 1000) / 1000
                        wait = max(wait, retry_after)
                    except Exception:
                        pass
                    log.warning("Discord 429 rate-limit, waiting %.1fs", wait)
                    if attempt < 2:
                        await asyncio.sleep(wait)
                        continue
                    return False
                if resp.status_code >= 500:
                    wait = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    log.warning("Discord %d server error, waiting %ds", resp.status_code, wait)
                    if attempt < 2:
                        await asyncio.sleep(wait)
                        continue
                    return False
                log.error("Discord webhook error %d: %s", resp.status_code, resp.text[:200])
                return False
            except httpx.TimeoutException:
                wait = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                log.warning("Discord timeout attempt %d/3, retry in %ds", attempt + 1, wait)
                if attempt < 2:
                    await asyncio.sleep(wait)
            except httpx.ConnectError as exc:
                wait = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                log.warning("Discord connection error attempt %d/3: %s", attempt + 1, exc)
                if attempt < 2:
                    await asyncio.sleep(wait)
            except Exception as exc:
                log.error("Discord unexpected error: %s", exc)
                return False

        log.error("Discord: all attempts exhausted")
        return False

    @staticmethod
    def _html_to_discord(text: str) -> str:
        """Convert Telegram HTML markup to Discord markdown."""
        text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
        text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
        text = re.sub(r"<pre>(.*?)</pre>", r"```\n\1\n```", text, flags=re.DOTALL)
        text = re.sub(r'<a href=[\'"]([^\'"]+)[\'"]>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", text)
        return (
            text
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&#x27;", "'")
        )

    @staticmethod
    def _split(text: str, limit: int = DISCORD_MAX_CONTENT) -> List[str]:
        return split_message(text, limit)
