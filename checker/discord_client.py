"""Discord webhook client for Ryazha-cheker notifications."""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests

DISCORD_MAX_EMBED_DESC = 4096
DISCORD_MAX_CONTENT = 2000

log = logging.getLogger(__name__)


class DiscordWebhookClient:
    """Send GitHub activity notifications to a Discord channel via webhook."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.session = requests.Session()

    def send(self, text: str, username: str = "Ryazha-cheker") -> bool:
        """Send a message to Discord, auto-splitting if needed.

        Converts Telegram HTML to Discord markdown before sending.
        Returns True if all parts were delivered successfully.
        """
        md = self._html_to_discord(text)
        parts = self._split(md)
        log.info("Discord: sending %d part(s)", len(parts))
        all_ok = True
        for i, part in enumerate(parts, 1):
            ok = self._send_part(part, username=username)
            if not ok:
                log.error("Discord: part %d/%d failed", i, len(parts))
                all_ok = False
            if i < len(parts):
                time.sleep(0.5)
        return all_ok

    def send_embed(
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
        return self._send_part("", username=username, embeds=[embed])

    # ── internal ──────────────────────────────────────────────────────────────

    def _send_part(
        self,
        content: str,
        username: str = "Ryazha-cheker",
        embeds: Optional[List[Dict]] = None,
    ) -> bool:
        payload: Dict[str, Any] = {"username": username}
        if content:
            payload["content"] = content[:DISCORD_MAX_CONTENT]
        if embeds:
            payload["embeds"] = embeds

        delays = [2, 4]
        for attempt in range(3):
            try:
                resp = self.session.post(
                    self.webhook_url,
                    json=payload,
                    timeout=30,
                )
                if resp.status_code == 204:
                    return True
                if resp.status_code == 429:
                    wait = delays[min(attempt, len(delays) - 1)]
                    try:
                        retry_after = resp.json().get("retry_after", wait * 1000) / 1000
                        wait = max(wait, retry_after)
                    except Exception:
                        pass
                    log.warning("Discord 429 rate-limit, waiting %.1fs", wait)
                    if attempt < 2:
                        time.sleep(wait)
                        continue
                    return False
                if resp.status_code >= 500:
                    wait = delays[min(attempt, len(delays) - 1)]
                    log.warning("Discord %d server error, waiting %ds", resp.status_code, wait)
                    if attempt < 2:
                        time.sleep(wait)
                        continue
                    return False
                log.error("Discord webhook error %d: %s", resp.status_code, resp.text[:200])
                return False
            except requests.exceptions.Timeout:
                wait = delays[min(attempt, len(delays) - 1)]
                log.warning("Discord timeout attempt %d/3, retry in %ds", attempt + 1, wait)
                if attempt < 2:
                    time.sleep(wait)
            except requests.exceptions.ConnectionError as exc:
                wait = delays[min(attempt, len(delays) - 1)]
                log.warning("Discord connection error attempt %d/3: %s", attempt + 1, exc)
                if attempt < 2:
                    time.sleep(wait)
            except Exception as exc:
                log.error("Discord unexpected error: %s", exc)
                return False

        log.error("Discord: all attempts exhausted")
        return False

    @staticmethod
    def _html_to_discord(text: str) -> str:
        """Convert Telegram HTML markup to Discord markdown."""
        # Bold
        text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
        # Italic
        text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)
        # Code (inline)
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
        # Pre (block)
        text = re.sub(r"<pre>(.*?)</pre>", r"```\n\1\n```", text, flags=re.DOTALL)
        # Links: <a href="url">label</a> → [label](url)
        text = re.sub(r'<a href=[\'"]([^\'"]+)[\'"]>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL)
        # Strip remaining tags
        text = re.sub(r"<[^>]+>", "", text)
        # Unescape HTML entities
        text = (
            text.replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"')
                .replace("&#39;", "'")
                .replace("&#x27;", "'")
        )
        return text

    @staticmethod
    def _split(text: str, limit: int = DISCORD_MAX_CONTENT) -> List[str]:
        """Split text into chunks of at most `limit` characters, cutting on newlines."""
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
