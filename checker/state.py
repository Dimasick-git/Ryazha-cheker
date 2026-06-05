"""State persistence: atomic JSON reads/writes and cold-start protection."""

import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# ATOMIC WRITE
# ──────────────────────────────────────────────────────────────

def _atomic_json_write(path: str, data: Any) -> None:
    """Write JSON atomically via a temp file (protects against write interruptions)."""
    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=dir_name, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, path)  # atomic on POSIX
    except Exception:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


# ──────────────────────────────────────────────────────────────
# LAST-CHECK STATE  (last_check.json)
# ──────────────────────────────────────────────────────────────

def _load_check_state() -> Dict[str, Any]:
    """Read last_check.json once and return a validated dict."""
    try:
        if os.path.exists("last_check.json"):
            with open("last_check.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        log.error("Error reading last_check.json: %s", e)
    return {}


def load_last_check_date() -> Optional[str]:
    """Load the last-check date from the JSON state file (raw ISO string)."""
    return _load_check_state().get("last_check_date")


def load_last_message_hash() -> Optional[str]:
    """Load the hash of the last sent message (for deduplication)."""
    return _load_check_state().get("last_message_hash")


def _update_check_state(**updates: Any) -> None:
    """Update one or more fields in last_check.json in a single atomic write."""
    try:
        data = _load_check_state()
        data.update(updates)
        _atomic_json_write("last_check.json", data)
    except Exception as e:
        log.error("Error updating last_check.json: %s", e)


def save_last_message_hash(h: str) -> None:
    """Update the hash of the last sent message."""
    _update_check_state(last_message_hash=h)


def save_last_check_date(date_str: str) -> None:
    """Save the last-check date to the JSON state file (raw ISO string)."""
    _update_check_state(last_check_date=date_str)


# ──────────────────────────────────────────────────────────────
# REPOSITORY STATES  (repo_states_<username>.json)
# ──────────────────────────────────────────────────────────────

def load_all_repository_states(username: str) -> Tuple[Dict[str, Any], bool]:
    """Load all repository states from file in a single read.

    Returns ``(states, is_cold_start)``.  ``is_cold_start`` is True when the
    state file is missing or empty — the caller should record the current state
    as a baseline without sending notifications to avoid flooding.
    """
    t0 = time.monotonic()
    try:
        state_file = f"repo_states_{username}.json"
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if not isinstance(data, dict):
                log.warning(
                    "repo_states_%s.json: expected dict, got %s. Resetting state.",
                    username, type(data).__name__,
                )
                return {}, True
            valid = {k: v for k, v in data.items() if isinstance(v, dict)}
            log.info(
                "State index loaded: %d repos in %.1f ms (from %s)",
                len(valid), elapsed_ms, state_file,
            )
            if not valid:
                return {}, True
            return valid, False
        return {}, True
    except Exception as e:
        log.error("Error loading repository states: %s", e)
        return {}, True


def save_all_repository_states(username: str, states: Dict[str, Any]) -> None:
    """Save all repository states in a single atomic write."""
    try:
        state_file = f"repo_states_{username}.json"
        _atomic_json_write(state_file, states)
        log.debug("Saved state for %d repos to %s", len(states), state_file)
    except Exception as e:
        log.error("Error saving repository states: %s", e)
