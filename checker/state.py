"""State persistence: atomic JSON reads/writes and cold-start protection."""

import json
import os
import tempfile
from typing import Any, Dict, Optional


# Set to True when the state file is missing or empty (first run / expired cache).
# In that case the current state is recorded as a baseline without sending
# notifications — protection against notification floods.
IS_COLD_START: bool = False


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
        print(f"Error reading last_check.json: {e}")
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
        print(f"Error updating last_check.json: {e}")


def save_last_message_hash(h: str) -> None:
    """Update the hash of the last sent message."""
    _update_check_state(last_message_hash=h)


def save_last_check_date(date_str: str) -> None:
    """Save the last-check date to the JSON state file (raw ISO string)."""
    _update_check_state(last_check_date=date_str)
    print(f"Last check date updated: {date_str}")


# ──────────────────────────────────────────────────────────────
# REPOSITORY STATES  (repo_states_<username>.json)
# ──────────────────────────────────────────────────────────────

def load_all_repository_states(username: str) -> Dict[str, Any]:
    """Load all repository states from file in a single read.

    If the file is missing or empty, sets the global IS_COLD_START flag so the
    first run (or a run after cache expiry) does not flood with notifications.
    Also logs the index rebuild time for diagnostics.
    """
    global IS_COLD_START
    import time as _time
    t0 = _time.monotonic()
    try:
        state_file = f"repo_states_{username}.json"
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            elapsed_ms = (_time.monotonic() - t0) * 1000
            if not isinstance(data, dict):
                print(
                    f"WARN repo_states_{username}.json: expected dict, "
                    f"got {type(data).__name__}. Resetting state."
                )
                IS_COLD_START = True
                return {}
            valid = {k: v for k, v in data.items() if isinstance(v, dict)}
            print(
                f"State index loaded: {len(valid)} repos in {elapsed_ms:.1f} ms "
                f"(from {state_file})"
            )
            if not valid:
                # File exists but contains no valid entries
                IS_COLD_START = True
            return valid
        # File missing — first run or cache reset
        IS_COLD_START = True
        return {}
    except Exception as e:
        print(f"Error loading repository states: {e}")
        IS_COLD_START = True
        return {}


def save_all_repository_states(username: str, states: Dict[str, Any]) -> None:
    """Save all repository states in a single atomic write."""
    try:
        state_file = f"repo_states_{username}.json"
        _atomic_json_write(state_file, states)
    except Exception as e:
        print(f"Error saving repository states: {e}")
