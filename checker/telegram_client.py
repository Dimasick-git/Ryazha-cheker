"""Telegram Bot API client with retry and exponential backoff."""

import re
import time
from typing import Any, Dict, List, Optional

import requests

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_MAX_LENGTH = 4096


class TelegramClient:
    def __init__(self, token: str, chat_id: str, topic_id: Optional[int] = None):
        self.token = token
        self.chat_id = chat_id
        self.topic_id = topic_id  # Support for Telegram topics/threads
        self.base = f"{TELEGRAM_API}/bot{token}"
        self.session = requests.Session()

    def validate(self) -> bool:
        """Verify the bot token via getMe."""
        try:
            resp = self.session.get(f"{self.base}/getMe", timeout=15)
            data = resp.json()
            if data.get("ok"):
                bot = data["result"]
                print(f"OK bot=@{bot['username']} id={bot['id']}")
                return True
            print(f"ERR getMe: {data.get('description')}")
            return False
        except Exception as e:
            print(f"ERR getMe-exc: {e}")
            return False

    def send(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        reply_markup: Optional[Dict] = None,
    ) -> bool:
        """Send a message, automatically splitting parts longer than 4096 chars."""
        parts = self._split(text)
        print(f"SEND parts={len(parts)} chat={self.chat_id}")
        all_ok = True
        for i, part in enumerate(parts, 1):
            # Buttons are sent only with the last part
            current_markup = reply_markup if i == len(parts) else None
            ok = self._send_part(part, parse_mode, disable_web_page_preview, current_markup)
            if ok:
                print(f"  OK part={i}/{len(parts)} bytes={len(part)}")
            else:
                print(f"  RETRY part={i}/{len(parts)} fallback=plain")
                # Fallback: strip HTML tags and send plain text
                plain = self._strip_html(part)
                ok = self._send_part(plain, parse_mode=None)
                if ok:
                    print(f"  OK part={i}/{len(parts)} fallback=plain")
                else:
                    print(f"  FAIL part={i}/{len(parts)} all-attempts-exhausted")
                    all_ok = False

            if i < len(parts):
                time.sleep(0.5)  # avoid flooding

        return all_ok

    # Alias used in some call sites
    send_message = send

    def _send_part(
        self,
        text: str,
        parse_mode: Optional[str],
        disable_web_page_preview: bool = True,
        reply_markup: Optional[Dict] = None,
    ) -> bool:
        """Send one message part with exponential backoff retry (1s, 2s, 4s — max 3 retries)."""
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

        delay = 1  # exponential backoff: 1s → 2s → 4s
        for attempt in range(3):
            try:
                resp = self.session.post(
                    f"{self.base}/sendMessage",
                    json=payload,
                    timeout=30,
                )
                data = resp.json()

                if data.get("ok"):
                    return True

                desc = data.get("description", "unknown error")
                print(f"  WARN telegram: {desc}")

                # Hints for common errors
                if "chat not found" in desc.lower():
                    print("  hint: verify CHAT_ID (send /start to bot)")
                elif "blocked" in desc.lower():
                    print("  hint: user blocked the bot")
                elif "parse" in desc.lower():
                    print("  hint: HTML parse error; retry as plain")
                elif "too many requests" in desc.lower():
                    retry_after = data.get("parameters", {}).get("retry_after", delay)
                    print(f"   Flood control — waiting {retry_after}s")
                    time.sleep(retry_after)
                    delay = retry_after
                    continue

                return False

            except requests.exceptions.Timeout:
                print(f"  Timeout Telegram (attempt {attempt + 1}/3) — retry in {delay}s")
                time.sleep(delay)
                delay *= 2
            except requests.exceptions.ConnectionError as e:
                print(f"  RETRY conn attempt={attempt + 1}/3 err={e} backoff={delay}s")
                time.sleep(delay)
                delay *= 2
            except Exception as e:
                print(f"  ERR telegram-exc: {e}")
                return False

        print("  FAIL _send_part: all-attempts-exhausted")
        return False

    @staticmethod
    def _split(text: str, limit: int = TELEGRAM_MAX_LENGTH) -> List[str]:
        """Split text into parts of at most `limit` characters, cutting on newlines.

        Guard against empty string after lstrip to avoid appending empty parts.
        """
        if len(text) <= limit:
            return [text]

        parts = []
        while text:
            if len(text) <= limit:
                parts.append(text)
                break
            # Find last newline within the allowed range
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
