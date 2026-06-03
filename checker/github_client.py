"""GitHub API client with pagination and exponential backoff."""

import random
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from .formatter import truncate

GITHUB_API = "https://api.github.com"

# Loaded from env vars at import time via the module that owns _int_env;
# we import from the top-level constants module instead.
import os


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


class GitHubClient:
    # Limits concurrent API calls across all threads to avoid bursting the rate limit.
    _api_semaphore = threading.Semaphore(3)

    def __init__(self, token: str, username: str):
        self.username = username
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept":        "application/vnd.github.v3+json",
            "User-Agent":    f"GitHubMonitor/{username}",
        })

    def _get(self, url: str, params: dict = None, _retry: int = 3) -> Optional[Any]:
        """GET request with rate-limit handling (exponential backoff) and error recovery."""
        delay = 2
        for attempt in range(1, _retry + 1):
            try:
                with self._api_semaphore:
                    resp = self.session.get(url, params=params, timeout=20)

                if resp.status_code == 429 or (
                    resp.status_code == 403
                    and "rate limit" in resp.text.lower()
                ):
                    reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                    wait = max(reset_ts - int(time.time()), delay)
                    # Jitter prevents all threads from retrying simultaneously.
                    wait += random.uniform(0.5, delay)
                    print(f"WARN rate-limit attempt={attempt}/{_retry} wait={wait:.1f}s")
                    time.sleep(wait)
                    delay *= 2
                    continue

                if resp.status_code == 200:
                    return resp.json()

                print(f"ERR github-api status={resp.status_code} url={url}")
                print(f"   Ответ: {resp.text[:200]}")
                return None

            except requests.exceptions.Timeout:
                print(f"⏱  Таймаут (попытка {attempt}/{_retry}): {url}")
                if attempt < _retry:
                    time.sleep(delay)
                    delay *= 2
                    continue
                return None
            except requests.exceptions.RequestException as e:
                print(f"ERR network: {e}")
                return None
        return None

    def get_repositories(self) -> List[Dict]:
        """All user repositories with pagination."""
        all_repos = []
        page = 1

        while True:
            data = self._get(
                f"{GITHUB_API}/users/{self.username}/repos",
                params={"page": page, "per_page": 100, "type": "all"},
            )
            if not data:
                break
            all_repos.extend(data)
            if len(data) < 100:
                break
            page += 1
            time.sleep(API_DELAY)

        return all_repos

    def list_commits(self, repo: str, count: int = 5) -> List[Dict]:
        """Last N commits without loading changed files (1 API request).

        Stores full SHA in "sha" field; display truncation happens at render time.
        """
        data = self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/commits",
            params={"per_page": count},
        )
        if not data or not isinstance(data, list):
            return []

        result = []
        for c in data[:count]:
            try:
                result.append({
                    "sha":      c["sha"],          # full SHA — dedup uses this
                    "message":  truncate(c["commit"]["message"].split("\n")[0], 60),
                    "author":   truncate(c["commit"]["author"]["name"], 25),
                    "date":     c["commit"]["author"]["date"],
                    "html_url": c["html_url"],
                    "files":    [],
                })
            except (KeyError, TypeError):
                continue
        return result

    def get_commit_files(self, repo: str, sha: str) -> List[Dict]:
        """Load the list of changed files for a single commit."""
        data = self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/commits/{sha}"
        )
        if not data or "files" not in data:
            return []
        result = []
        for f in data["files"][:5]:
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

    def get_open_prs(self, repo: str, count: int = 3) -> List[Dict]:
        """Open pull requests for a repository."""
        data = self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/pulls",
            params={"state": "open", "per_page": count},
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

    def get_releases(self, repo: str) -> List[Dict]:
        """Latest release for a repository."""
        data = self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/releases/latest",
        )
        if not data or not isinstance(data, dict):
            return []

        try:
            return [{
                "tag":          data["tag_name"],
                "name":         truncate(data.get("name") or data["tag_name"], 50),
                "author":       data["author"]["login"] if data.get("author") else "Unknown",
                "published_at": data["published_at"],
                "html_url":     data["html_url"],
            }]
        except (KeyError, TypeError):
            return []

    def get_workflow_runs(self, repo: str, count: int = 5) -> List[Dict]:
        """Recent workflow runs for a repository."""
        data = self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/actions/runs",
            params={"per_page": count},
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

    def get_latest_repo_update(self) -> Optional[str]:
        """Quick check: raw ISO string of the most recently updated repo.

        Returns raw ISO string so ISO string comparisons are reliable.
        """
        data = self._get(
            f"{GITHUB_API}/users/{self.username}/repos",
            params={"type": "owner", "sort": "updated", "per_page": 1},
        )
        if data and isinstance(data, list) and data:
            updated_at = data[0].get("updated_at", "")
            return updated_at if updated_at else None
        return None

    def get_open_issues_count(self, repo: str, open_issues_raw: int = -1, pr_count: int = 0) -> int:
        """Open issue count (excluding PRs).

        If open_issues_raw is provided (from an already-fetched repo list), skip
        an extra API call to save rate limit budget.
        """
        if open_issues_raw >= 0:
            return max(0, open_issues_raw - pr_count)
        data = self._get(f"{GITHUB_API}/repos/{self.username}/{repo}")
        if data and isinstance(data, dict):
            total = data.get("open_issues_count", 0)
            return max(0, total - pr_count)
        return 0

    def get_new_releases(self, repo: str, known_tag: Optional[str]) -> List[Dict]:
        """Return release if the tag has changed since the last check."""
        releases = self.get_releases(repo)
        if not releases:
            return []
        current_tag = releases[0].get("tag", "")
        if known_tag and current_tag == known_tag:
            return []
        return releases
