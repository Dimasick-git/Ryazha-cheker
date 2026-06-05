#!/usr/bin/env python3
"""
GitHub Repository Monitor — entry point.

Parses CLI arguments, instantiates GitHubMonitor, and calls run().
All logic lives in the checker/ package.
"""

import os
import sys

from checker.formatter import escape_html
from checker.telegram_client import TelegramClient
from checker.monitor import GitHubMonitor


# ──────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _monitor_ref = None
    try:
        _monitor_ref = GitHubMonitor()
        _monitor_ref.run()
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print(f"\nUnexpected error: {e}")
        traceback.print_exc()

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
                print(f"ERR crash-notify: {notify_err}")
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
                print(f"ERR crash-notify: {notify_err}")

        raise  # re-raise: GitHub Actions marks the job as failed
