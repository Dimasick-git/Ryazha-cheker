#!/usr/bin/env python3
"""
GitHub Repository Monitor — entry point.

Parses CLI arguments, instantiates GitHubMonitor, and calls run().
All logic lives in the checker/ package.
"""

import logging
import os
import sys

from checker.formatter import escape_html
from checker.telegram_client import TelegramClient
from checker.monitor import GitHubMonitor


def _setup_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


# ──────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _setup_logging()
    log = logging.getLogger("main")

    _monitor_ref = None
    try:
        _monitor_ref = GitHubMonitor()
        _monitor_ref.run()
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        log.exception("Unexpected error: %s", e)

        # Notify Telegram about the crash
        _tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        _tg_chat  = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if _tg_token and _tg_chat and _monitor_ref is not None:
            try:
                error_msg = (
                    f"🚨 <b>Ryazha-cheker crashed!</b>\n\n"
                    f"<code>{escape_html(type(e).__name__)}: "
                    f"{escape_html(str(e)[:500])}</code>"
                )
                _monitor_ref.telegram.send(error_msg)
            except Exception as notify_err:
                log.warning("ERR crash-notify: %s", notify_err)
        elif _tg_token and _tg_chat:
            # Monitor didn't finish initialising — create a minimal client
            try:
                _tmp_tg = TelegramClient(_tg_token, _tg_chat)
                error_msg = (
                    f"🚨 <b>Ryazha-cheker crashed!</b>\n\n"
                    f"<code>{escape_html(type(e).__name__)}: "
                    f"{escape_html(str(e)[:500])}</code>"
                )
                _tmp_tg.send(error_msg)
            except Exception as notify_err:
                log.warning("ERR crash-notify: %s", notify_err)

        raise  # re-raise: GitHub Actions marks the job as failed
