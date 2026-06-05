"""AI-powered commit summarization using Anthropic Claude API."""
import hashlib
import logging
import os
import time
from typing import Optional

try:
    import anthropic as _anthropic_mod
    _anthropic_available = True
except ImportError:
    _anthropic_mod = None
    _anthropic_available = False

log = logging.getLogger(__name__)

_CACHE_TTL = 3600  # 1 hour
_cache: dict[str, tuple[str, float]] = {}
_client = None

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "claude-haiku-4-5-20251001")

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 2.0  # seconds

_SYSTEM_PROMPT = (
    "Ты — краткий суммаризатор GitHub-активности. "
    "По списку коммитов и изменённых файлов напиши 1-2 предложения на русском языке: "
    "что изменилось и почему это важно для пользователей. "
    "Будь конкретным. Не перечисляй SHA-хэши. Не используй markdown."
)


def _get_client():
    global _client
    if _client is None and _anthropic_available and ANTHROPIC_API_KEY:
        _client = _anthropic_mod.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def is_available() -> bool:
    return bool(ANTHROPIC_API_KEY and _anthropic_available)


def _is_retryable(exc: Exception) -> bool:
    if _anthropic_available:
        if isinstance(exc, _anthropic_mod.RateLimitError):
            return True
        if isinstance(exc, _anthropic_mod.APIStatusError) and exc.status_code >= 500:
            return True
        if isinstance(exc, _anthropic_mod.APIConnectionError):
            return True
    return False


def summarize_commits(repo_name: str, commits: list) -> Optional[str]:
    """Summarize commits for a repo. Returns Russian summary or None if unavailable.
    Retries up to _RETRY_ATTEMPTS times on transient errors (rate limit, 5xx, network).
    """
    if not commits or not is_available():
        return None

    commit_shas = "".join(c.get("sha", "")[:8] for c in commits[:5])
    cache_key = hashlib.sha256(f"{repo_name}:{commit_shas}".encode()).hexdigest()[:16]

    entry = _cache.get(cache_key)
    if entry and time.time() - entry[1] < _CACHE_TTL:
        return entry[0]

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

    client = _get_client()
    if client is None:
        return None

    delay = _RETRY_BASE_DELAY
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = client.messages.create(
                model=AI_MODEL,
                max_tokens=160,
                system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
            )
            result = resp.content[0].text.strip() if resp.content else None
            if result:
                _cache[cache_key] = (result, time.time())
                log.debug("AI summary generated for %s", repo_name)
            return result
        except Exception as e:
            if _is_retryable(e) and attempt < _RETRY_ATTEMPTS:
                log.warning("AI summary attempt %d/%d failed (%s) for %s, retrying in %.1fs",
                            attempt, _RETRY_ATTEMPTS, type(e).__name__, repo_name, delay)
                time.sleep(delay)
                delay *= 2
                continue
            log.warning("AI summarization failed for %s: %s", repo_name, e)
            return None
    return None
