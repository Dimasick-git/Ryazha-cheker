"""AI-powered commit summarization using Anthropic Claude API.

Cache is persisted to disk (ai_summary_cache.json) so summaries survive
between GitHub Actions runs and avoid redundant API calls.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from typing import Optional, Tuple

try:
    import anthropic as _anthropic_mod
    _anthropic_available = True
except ImportError:
    _anthropic_mod = None
    _anthropic_available = False

log = logging.getLogger(__name__)

_CACHE_TTL = 86400        # 24 hours — summaries stay valid for a full day
_CACHE_MAX = 500          # max entries in cache file
_CACHE_FILE = "ai_summary_cache.json"

# In-memory layer (loaded from disk on first use)
_cache: dict[str, Tuple[str, float]] = {}
_cache_loaded = False
_async_client = None

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "claude-haiku-4-5-20251001")

_SYSTEM_PROMPT = (
    "Ты — краткий суммаризатор GitHub-активности. "
    "По списку коммитов и изменённых файлов напиши 1-2 предложения на русском языке: "
    "что изменилось и почему это важно для пользователей. "
    "Будь конкретным. Не перечисляй SHA-хэши. Не используй markdown."
)


# ──────────────────────────────────────────────────────────────
# DISK CACHE
# ──────────────────────────────────────────────────────────────

def _load_cache() -> None:
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    if not os.path.exists(_CACHE_FILE):
        return
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        now = time.time()
        # raw format: {key: [summary, timestamp]}
        _cache = {
            k: (v[0], v[1])
            for k, v in raw.items()
            if isinstance(v, list) and len(v) == 2 and now - v[1] < _CACHE_TTL
        }
        log.debug("AI summary cache loaded: %d valid entries from %s", len(_cache), _CACHE_FILE)
    except Exception as e:
        log.warning("Failed to load AI summary cache: %s", e)
        _cache = {}


def _save_cache() -> None:
    try:
        # Evict expired entries before saving
        now = time.time()
        active = {k: v for k, v in _cache.items() if now - v[1] < _CACHE_TTL}
        # Trim to max size by age
        if len(active) > _CACHE_MAX:
            sorted_keys = sorted(active, key=lambda k: active[k][1])
            for old in sorted_keys[:len(active) - _CACHE_MAX]:
                active.pop(old, None)
        serializable = {k: [v[0], v[1]] for k, v in active.items()}

        dir_ = os.path.dirname(os.path.abspath(_CACHE_FILE)) or "."
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False)
        os.replace(tmp, _CACHE_FILE)
        log.debug("AI summary cache saved: %d entries to %s", len(active), _CACHE_FILE)
    except Exception as e:
        log.warning("Failed to save AI summary cache: %s", e)


def _get_cached(key: str) -> Optional[str]:
    _load_cache()
    entry = _cache.get(key)
    if entry and time.time() - entry[1] < _CACHE_TTL:
        return entry[0]
    _cache.pop(key, None)
    return None


async def _set_cached_async(key: str, value: str) -> None:
    _load_cache()
    _cache[key] = (value, time.time())
    await asyncio.to_thread(_save_cache)


# ──────────────────────────────────────────────────────────────
# CLIENT
# ──────────────────────────────────────────────────────────────

def _get_async_client():
    global _async_client
    if _async_client is None and _anthropic_available and ANTHROPIC_API_KEY:
        _async_client = _anthropic_mod.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _async_client


def is_available() -> bool:
    return bool(ANTHROPIC_API_KEY and _anthropic_available)


# ──────────────────────────────────────────────────────────────
# SUMMARIZE
# ──────────────────────────────────────────────────────────────

async def summarize_commits(repo_name: str, commits: list) -> Optional[str]:
    """Summarize commits for a repo. Returns Russian summary or None if unavailable.

    Results are cached to disk for 24 hours so repeated runs for the same
    commits do not consume API quota.

    Uses AsyncAnthropic to avoid blocking the event loop during API calls.
    Cache key includes the model name so swapping AI_MODEL invalidates stale entries.
    """
    if not commits or not is_available():
        return None

    commit_shas = "".join(c.get("sha", "")[:8] for c in commits[:5])
    cache_key = hashlib.sha256(f"{AI_MODEL}:{repo_name}:{commit_shas}".encode()).hexdigest()[:16]

    cached = _get_cached(cache_key)
    if cached:
        log.debug("AI cache hit for %s (key=%s)", repo_name, cache_key)
        return cached

    lines = [f"Репозиторий: {repo_name}", "Новые коммиты:"]
    for c in commits[:5]:
        sha = c.get("sha", "")[:7]
        msg = (c.get("message") or "").split("\n")[0][:120]
        files = [f.get("filename", "") for f in c.get("files", [])[:4]]
        line = f"  [{sha}] {msg}"
        if files:
            line += f" | файлы: {', '.join(files)}"
        lines.append(line)

    prompt = "\n".join(lines) + "\n\nКратко на русском (1-2 предложения):"

    try:
        client = _get_async_client()
        if client is None:
            return None
        resp = await client.messages.create(
            model=AI_MODEL,
            max_tokens=160,
            system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        result = resp.content[0].text.strip() if resp.content else None
        if result:
            await _set_cached_async(cache_key, result)
            log.info("AI summary generated for %s (key=%s)", repo_name, cache_key)
        return result
    except Exception as e:
        log.warning("AI summarization failed for %s: %s", repo_name, e)
        return None


_RELEASE_SYSTEM_PROMPT = (
    "Ты — краткий суммаризатор релизов GitHub. "
    "По тегу, описанию и названиям файлов релиза напиши 1-2 предложения на русском: "
    "что изменилось и что нового для пользователей. "
    "Будь конкретным и практичным. Не используй markdown."
)


async def summarize_release(repo_name: str, release: dict) -> Optional[str]:
    """Summarize a GitHub release. Returns Russian summary or None if unavailable.

    Cache key is based on the release tag + repo_name so identical releases
    are never re-summarized across runs.
    """
    if not release or not is_available():
        return None

    tag = release.get("tag_name") or release.get("tag") or ""
    if not tag:
        return None

    cache_key = hashlib.sha256(f"{AI_MODEL}:release:{repo_name}:{tag}".encode()).hexdigest()[:16]
    cached = _get_cached(cache_key)
    if cached:
        log.debug("AI release cache hit for %s@%s (key=%s)", repo_name, tag, cache_key)
        return cached

    name = release.get("name") or tag
    body = (release.get("body") or "").strip()[:600]
    assets = release.get("assets", [])
    asset_names = ", ".join(a.get("name", "") for a in assets[:6] if a.get("name"))

    lines = [
        f"Репозиторий: {repo_name}",
        f"Релиз: {tag} — {name}",
    ]
    if body:
        lines.append(f"Описание: {body}")
    if asset_names:
        lines.append(f"Файлы: {asset_names}")

    prompt = "\n".join(lines) + "\n\nКратко на русском (1-2 предложения):"

    try:
        client = _get_async_client()
        if client is None:
            return None
        resp = await client.messages.create(
            model=AI_MODEL,
            max_tokens=160,
            system=[{"type": "text", "text": _RELEASE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        result = resp.content[0].text.strip() if resp.content else None
        if result:
            await _set_cached_async(cache_key, result)
            log.info("AI release summary generated for %s@%s (key=%s)", repo_name, tag, cache_key)
        return result
    except Exception as e:
        log.warning("AI release summarization failed for %s@%s: %s", repo_name, tag, e)
        return None
