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

_RELEASE_SYSTEM_PROMPT = (
    "Ты — краткий суммаризатор релизов GitHub. "
    "По тегу, описанию и названиям файлов релиза напиши 1-2 предложения на русском: "
    "что изменилось и что нового для пользователей. "
    "Будь конкретным и практичным. Не используй markdown."
)

_PR_SYSTEM_PROMPT = (
    "Ты — краткий суммаризатор GitHub Pull Requests. "
    "По номерам, названиям и авторам PR напиши 1 предложение на русском: "
    "что предлагается изменить в этих PR. Не используй markdown. "
    "Если PR один — кратко его суть, если несколько — общая тема."
)

_WEEKLY_INSIGHTS_PROMPT = (
    "Ты — аналитик GitHub-активности. "
    "По статистике репозиториев за неделю напиши 2-3 предложения на русском: "
    "краткий вывод об активности проекта, что развивалось активнее всего и что важно отметить. "
    "Будь лаконичным и конкретным. Не используй markdown."
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


def _get_cached(key: str, ttl: int = _CACHE_TTL) -> Optional[str]:
    _load_cache()
    entry = _cache.get(key)
    if entry and time.time() - entry[1] < ttl:
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


_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.5


def _is_retryable(exc: Exception) -> bool:
    """Return True for transient API errors that warrant a retry."""
    if not _anthropic_available:
        return False
    if isinstance(exc, _anthropic_mod.RateLimitError):
        return True
    if isinstance(exc, _anthropic_mod.APIStatusError) and exc.status_code >= 500:
        return True
    if isinstance(exc, _anthropic_mod.APIConnectionError):
        return True
    return False


async def _call_claude(
    system_prompt: str,
    user_prompt: str,
    cache_key: str,
    max_tokens: int = 200,
    ttl: int = _CACHE_TTL,
    log_label: str = "",
) -> Optional[str]:
    """Shared Claude API call with cache, retry logic, and error handling."""
    cached = _get_cached(cache_key, ttl=ttl)
    if cached:
        log.debug("AI cache hit%s (key=%s)", f" for {log_label}" if log_label else "", cache_key)
        return cached

    client = _get_async_client()
    if client is None:
        return None

    delay = _RETRY_BASE_DELAY
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = await client.messages.create(
                model=AI_MODEL,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_prompt}],
            )
            result = resp.content[0].text.strip() if resp.content else None
            if result:
                await _set_cached_async(cache_key, result)
                log.info("AI summary generated%s (key=%s)", f" for {log_label}" if log_label else "", cache_key)
            return result
        except Exception as e:
            if _is_retryable(e) and attempt < _RETRY_ATTEMPTS:
                log.warning(
                    "Claude API attempt %d/%d failed%s (%s), retrying in %.1fs",
                    attempt, _RETRY_ATTEMPTS,
                    f" for {log_label}" if log_label else "",
                    type(e).__name__, delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue
            log.warning("Claude API call failed%s: %s", f" for {log_label}" if log_label else "", e)
            return None
    return None


# ──────────────────────────────────────────────────────────────
# SUMMARIZE COMMITS
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
    return await _call_claude(_SYSTEM_PROMPT, prompt, cache_key, max_tokens=200, log_label=repo_name)


# ──────────────────────────────────────────────────────────────
# SUMMARIZE RELEASE
# ──────────────────────────────────────────────────────────────

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
    return await _call_claude(
        _RELEASE_SYSTEM_PROMPT, prompt, cache_key, max_tokens=200, log_label=f"{repo_name}@{tag}"
    )


# ──────────────────────────────────────────────────────────────
# SUMMARIZE PR BATCH
# ──────────────────────────────────────────────────────────────

async def summarize_pr_batch(repo_name: str, prs: list) -> Optional[str]:
    """Summarize new open PRs for a repo. Returns Russian one-liner or None.

    Generates a brief description of what the PRs propose to change, cached
    by repo name and PR numbers so repeated runs don't re-query the API.
    """
    if not prs or not is_available():
        return None

    pr_key = "".join(str(p.get("number", "")) for p in prs[:4])
    cache_key = hashlib.sha256(f"{AI_MODEL}:prs:{repo_name}:{pr_key}".encode()).hexdigest()[:16]

    lines = [f"Репозиторий: {repo_name}", "Новые открытые PR:"]
    for p in prs[:4]:
        num = p.get("number", "?")
        title = (p.get("title") or "")[:100]
        author = p.get("author") or p.get("login") or ""
        entry = f"  #{num}: {title}"
        if author:
            entry += f" (by {author})"
        lines.append(entry)

    prompt = "\n".join(lines) + "\n\nОдним предложением — суть этих PR:"
    return await _call_claude(_PR_SYSTEM_PROMPT, prompt, cache_key, max_tokens=100, log_label=repo_name)


# ──────────────────────────────────────────────────────────────
# SUMMARIZE WEEKLY INSIGHTS
# ──────────────────────────────────────────────────────────────

_WEEKLY_CACHE_TTL = 21600  # 6 hours — shorter than the default 24h

async def summarize_weekly_insights(
    username: str,
    total_repos: int,
    total_stars: int,
    total_commits_week: int,
    top_active: list[str],
    new_repos: list[str],
    milestone_repos: list[str],
) -> Optional[str]:
    """Generate AI-powered weekly insight paragraph for the digest.

    Produces a 2-3 sentence Russian summary of overall project activity,
    cached for 6 hours to avoid re-generating on every --weekly run.
    """
    if not is_available():
        return None

    week_key = f"{username}:{total_repos}:{total_stars}:{total_commits_week}"
    cache_key = hashlib.sha256(f"{AI_MODEL}:weekly:{week_key}".encode()).hexdigest()[:16]

    lines = [
        f"Проект: {username} на GitHub",
        f"Всего репозиториев: {total_repos}",
        f"Суммарно звёзд: {total_stars}",
        f"Активных репозиториев за неделю (с пушем): {total_commits_week}",
    ]
    if top_active:
        lines.append(f"Самые активные репозитории: {', '.join(top_active[:5])}")
    if new_repos:
        lines.append(f"Новые репозитории: {', '.join(new_repos[:3])}")
    if milestone_repos:
        lines.append(f"Достигли вех по звёздам: {', '.join(milestone_repos[:3])}")

    prompt = "\n".join(lines) + "\n\nКраткий аналитический вывод на русском (2-3 предложения):"
    return await _call_claude(
        _WEEKLY_INSIGHTS_PROMPT, prompt, cache_key, max_tokens=200, ttl=_WEEKLY_CACHE_TTL
    )
