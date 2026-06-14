"""GitHub API client with pagination and exponential backoff (async httpx)."""

import asyncio
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional

import httpx

from .formatter import truncate

GITHUB_API = "https://api.github.com"

log = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


API_DELAY = float(os.environ.get("API_DELAY", "0.5"))
MAX_COMMITS = _int_env("MAX_COMMITS", 5)
MAX_PRS = _int_env("MAX_PRS", 3)
MAX_RELEASES = _int_env("MAX_RELEASES", 1)
MAX_WORKFLOWS = _int_env("MAX_WORKFLOWS", 3)
MAX_KNOWN_SHAS = _int_env("MAX_KNOWN_SHAS", 500)

# Circuit breaker: open after this many consecutive failures, reset after cooldown.
_CB_FAILURE_THRESHOLD = _int_env("CB_FAILURE_THRESHOLD", 5)
_CB_COOLDOWN_SECONDS = float(os.environ.get("CB_COOLDOWN_SECONDS", "60"))


class GitHubClient:
    # Warn when remaining API calls drop below this threshold.
    _RATE_WARN_THRESHOLD = 20
    # Proactively pause when remaining drops this low (avoids hitting the wall mid-run).
    _RATE_PAUSE_THRESHOLD = 5

    def __init__(self, token: str, username: str):
        self.username = username
        self._token = token
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept":        "application/vnd.github.v3+json",
                "User-Agent":    f"GitHubMonitor/{username}",
            },
            timeout=20.0,
        )
        # Created here (not at class level) to avoid asyncio.Semaphore()
        # being instantiated before any event loop exists (DeprecationWarning
        # in Python 3.10+, RuntimeError in 3.12+).
        self._api_semaphore = asyncio.Semaphore(3)
        # Per-repo circuit breaker: context -> (consecutive_failures, opened_at)
        # Empty string "" is the global context (used for non-repo calls).
        self._cb: dict = {}
        # Proactive rate-limit tracking: updated from response headers after every call.
        self._rate_remaining: int = 5000
        self._rate_reset_ts: float = 0.0
        self._rate_warned: bool = False
        # Dynamic inter-request delay: adjusted based on remaining quota and reset window.
        self._dynamic_delay: float = API_DELAY

    def _cb_is_open(self, context: str = "") -> bool:
        """Return True when the circuit breaker for `context` is open."""
        failures, opened_at = self._cb.get(context, (0, 0.0))
        if failures < _CB_FAILURE_THRESHOLD:
            return False
        if time.monotonic() - opened_at >= _CB_COOLDOWN_SECONDS:
            log.info("Circuit breaker [%s]: cooldown elapsed, resetting.", context or "global")
            self._cb.pop(context, None)
            return False
        return True

    def _cb_record_success(self, context: str = "") -> None:
        self._cb.pop(context, None)

    def _cb_record_failure(self, context: str = "") -> None:
        failures, opened_at = self._cb.get(context, (0, 0.0))
        failures += 1
        if failures >= _CB_FAILURE_THRESHOLD:
            opened_at = time.monotonic()
            log.warning(
                "Circuit breaker OPENED [%s] after %d consecutive failures. "
                "Pausing calls for %.0fs.",
                context or "global",
                _CB_FAILURE_THRESHOLD,
                _CB_COOLDOWN_SECONDS,
            )
        self._cb[context] = (failures, opened_at)

    async def aclose(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()

    def _update_rate_state(self, headers) -> None:
        """Parse X-RateLimit-* headers and warn/pause proactively if running low."""
        remaining_str = headers.get("X-RateLimit-Remaining")
        reset_str = headers.get("X-RateLimit-Reset")
        if remaining_str is None:
            return
        try:
            self._rate_remaining = int(remaining_str)
        except (ValueError, TypeError):
            return
        if reset_str:
            try:
                self._rate_reset_ts = float(reset_str)
            except (ValueError, TypeError):
                pass

        if self._rate_remaining <= self._RATE_WARN_THRESHOLD and not self._rate_warned:
            self._rate_warned = True
            reset_in = max(0, int(self._rate_reset_ts - time.time()))
            log.warning(
                "GitHub rate limit low: %d calls remaining (resets in %ds). "
                "Reduce polling frequency or add G_TOKEN with higher quota.",
                self._rate_remaining, reset_in,
            )
        elif self._rate_remaining > self._RATE_WARN_THRESHOLD:
            self._rate_warned = False

        # Dynamically adjust inter-request pacing to spread remaining quota across reset window.
        if self._rate_remaining > 0 and self._rate_reset_ts > 0:
            reset_window = max(1.0, self._rate_reset_ts - time.time())
            pace = reset_window / self._rate_remaining
            # Clamp between the configured minimum and 5 seconds.
            self._dynamic_delay = max(API_DELAY, min(pace, 5.0))
        else:
            self._dynamic_delay = API_DELAY

    async def _proactive_rate_pause(self) -> None:
        """If remaining calls are critically low, sleep until the reset window."""
        if self._rate_remaining > self._RATE_PAUSE_THRESHOLD:
            return
        reset_in = max(0, self._rate_reset_ts - time.time())
        if reset_in <= 0:
            return
        wait = reset_in + random.uniform(1.0, 3.0)
        log.warning(
            "GitHub rate limit critically low (%d remaining) — pausing %.0fs until reset.",
            self._rate_remaining, wait,
        )
        await asyncio.sleep(wait)
        self._rate_remaining = 5000
        self._rate_warned = False

    @property
    def rate_limit_remaining(self) -> int:
        """Most-recently observed X-RateLimit-Remaining value."""
        return self._rate_remaining

    async def _get(self, url: str, params: dict = None, _retry: int = 3, context: str = "") -> Optional[Any]:
        """Async GET request with rate-limit handling (exponential backoff) and error recovery.

        `context` is an optional repo name used to scope the circuit breaker so that
        persistent errors from one repo don't block all other repos.
        """
        if self._cb_is_open(context):
            log.debug("Circuit breaker open [%s] — skipping %s", context or "global", url)
            return None

        await self._proactive_rate_pause()

        delay = 2
        for attempt in range(1, _retry + 1):
            try:
                async with self._api_semaphore:
                    resp = await self._client.get(url, params=params)

                self._update_rate_state(resp.headers)

                if resp.status_code == 429 or (
                    resp.status_code == 403
                    and "rate limit" in resp.text.lower()
                ):
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        wait = float(retry_after) + random.uniform(0.5, 2.0)
                    else:
                        reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                        wait = max(reset_ts - int(time.time()), delay)
                        wait += random.uniform(0.5, delay)
                    log.warning("Rate-limit hit attempt=%d/%d wait=%.1fs", attempt, _retry, wait)
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue

                if resp.status_code == 200:
                    self._cb_record_success(context)
                    return resp.json()

                if resp.status_code >= 500 and attempt < _retry:
                    log.warning("Server error status=%d attempt=%d/%d, retrying in %ds",
                                resp.status_code, attempt, _retry, delay)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue

                log.error("GitHub API error status=%d url=%s body=%s",
                          resp.status_code, url, resp.text[:200])
                self._cb_record_failure(context)
                return None

            except httpx.TimeoutException:
                log.warning("Timeout attempt=%d/%d url=%s", attempt, _retry, url)
                if attempt < _retry:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                self._cb_record_failure(context)
                return None
            except httpx.RequestError as e:
                log.error("Network error: %s", e)
                self._cb_record_failure(context)
                return None
        self._cb_record_failure(context)
        return None

    async def get_repositories(self) -> List[Dict]:
        """All user repositories with pagination."""
        all_repos = []
        page = 1

        while True:
            data = await self._get(
                f"{GITHUB_API}/users/{self.username}/repos",
                params={"page": page, "per_page": 100, "type": "all"},
            )
            if not data:
                break
            all_repos.extend(data)
            if len(data) < 100:
                break
            page += 1
            await asyncio.sleep(self._dynamic_delay)

        return all_repos

    async def list_commits(self, repo: str, count: int = 5) -> List[Dict]:
        """Last N commits without loading changed files (1 API request)."""
        data = await self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/commits",
            params={"per_page": count},
            context=repo,
        )
        if not data or not isinstance(data, list):
            return []

        result = []
        for c in data[:count]:
            try:
                result.append({
                    "sha":      c["sha"],
                    "message":  truncate(c["commit"]["message"].split("\n")[0], 60),
                    "author":   truncate(c["commit"]["author"]["name"], 25),
                    "date":     c["commit"]["author"]["date"],
                    "html_url": c["html_url"],
                    "files":    [],
                })
            except (KeyError, TypeError):
                continue
        return result

    async def get_commit_files(self, repo: str, sha: str) -> List[Dict]:
        """Load the list of changed files for a single commit."""
        data = await self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/commits/{sha}",
            context=repo,
        )
        if not data or "files" not in data:
            return []
        result = []
        for f in data["files"][:8]:
            result.append({
                "filename":  f["filename"],
                "changes":   f.get("changes", 0),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "status":    f.get("status", "modified"),
                "blob_url":  f.get("blob_url"),
                "raw_url":   f.get("raw_url"),
                "patch":     f.get("patch", "")[:200],
            })
        return result

    async def get_open_prs(self, repo: str, count: int = 3) -> List[Dict]:
        """Open pull requests for a repository."""
        data = await self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/pulls",
            params={"state": "open", "per_page": count},
            context=repo,
        )
        if not data or not isinstance(data, list):
            return []

        result = []
        for pr in data[:count]:
            try:
                result.append({
                    "number": pr["number"],
                    "title":  truncate(pr["title"], 55),
                    "author": pr["user"]["login"],
                    "date":   pr["created_at"],
                })
            except (KeyError, TypeError):
                continue
        return result

    async def get_releases(self, repo: str, count: int = MAX_RELEASES) -> List[Dict]:
        """Recent releases for a repository (up to `count`)."""
        data = await self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/releases",
            params={"per_page": min(count, 10)},
            context=repo,
        )
        if not data or not isinstance(data, list):
            return []

        result = []
        for item in data[:count]:
            try:
                result.append({
                    "tag":          item["tag_name"],
                    "name":         truncate(item.get("name") or item["tag_name"], 50),
                    "author":       item["author"]["login"] if item.get("author") else "Unknown",
                    "published_at": item["published_at"],
                    "html_url":     item["html_url"],
                    "body":         (item.get("body") or "")[:600],
                    "assets":       [
                        {"name": a["name"]}
                        for a in (item.get("assets") or [])[:6]
                        if a.get("name")
                    ],
                })
            except (KeyError, TypeError):
                continue
        return result

    async def get_workflow_runs(self, repo: str, count: int = 5) -> List[Dict]:
        """Recent workflow runs for a repository."""
        data = await self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/actions/runs",
            params={"per_page": count},
            context=repo,
        )
        if not data or not isinstance(data, dict) or "workflow_runs" not in data:
            return []

        result = []
        for run in data["workflow_runs"][:count]:
            try:
                result.append({
                    "id":         run["id"],
                    "name":       truncate(run["name"], 40),
                    "status":     run["status"],
                    "conclusion": run.get("conclusion", "running"),
                    "created_at": run["created_at"],
                    "html_url":   run["html_url"],
                })
            except (KeyError, TypeError):
                continue
        return result

    async def get_latest_repo_update(self) -> Optional[str]:
        """Quick check: raw ISO string of the most recently updated repo."""
        data = await self._get(
            f"{GITHUB_API}/users/{self.username}/repos",
            params={"type": "owner", "sort": "updated", "per_page": 1},
        )
        if data and isinstance(data, list) and data:
            updated_at = data[0].get("updated_at", "")
            return updated_at if updated_at else None
        return None

    async def get_open_issues_count(self, repo: str, open_issues_raw: int = -1, pr_count: int = 0) -> int:
        """Open issue count (excluding PRs).

        GitHub's open_issues_count includes PRs, so subtracting pr_count gives
        an accurate enough result without touching the Search API (rate-limited
        to 30 req/min authenticated vs 5000/min for other endpoints).
        """
        if open_issues_raw >= 0:
            return max(0, open_issues_raw - pr_count)
        # Raw count unavailable — fall back to Search API
        data = await self._get(
            f"{GITHUB_API}/search/issues",
            params={"q": f"repo:{self.username}/{repo} type:issue state:open", "per_page": 1},
            context=repo,
        )
        if data and isinstance(data, dict) and "total_count" in data:
            return data["total_count"]
        repo_data = await self._get(f"{GITHUB_API}/repos/{self.username}/{repo}", context=repo)
        if repo_data and isinstance(repo_data, dict):
            return repo_data.get("open_issues_count", 0)
        return 0

    async def get_recent_issues(self, repo: str, count: int = 5) -> List[Dict]:
        """Fetch recently opened issues (excluding pull requests)."""
        data = await self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/issues",
            params={"state": "open", "sort": "created", "direction": "desc", "per_page": count},
            context=repo,
        )
        if not data or not isinstance(data, list):
            return []

        result = []
        for issue in data[:count]:
            try:
                if issue.get("pull_request"):
                    continue
                result.append({
                    "number": issue["number"],
                    "title":  truncate(issue["title"], 60),
                    "author": issue["user"]["login"],
                    "date":   issue["created_at"],
                    "url":    issue["html_url"],
                    "labels": [la["name"] for la in (issue.get("labels") or [])[:3]],
                })
            except (KeyError, TypeError):
                continue
        return result

    async def get_new_releases(self, repo: str, known_tag: Optional[str]) -> List[Dict]:
        """Return all releases published after `known_tag` (or the latest on first run)."""
        releases = await self.get_releases(repo, count=5)
        if not releases:
            return []
        if not known_tag:
            return releases[:1]
        new = []
        for r in releases:
            if r.get("tag") == known_tag:
                break
            new.append(r)
        return new
