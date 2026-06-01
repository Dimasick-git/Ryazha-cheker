#!/usr/bin/env python3
"""
GitHub Repository Monitor
Отслеживает репозитории пользователя и отправляет уведомления в Telegram
"""

import concurrent.futures
import fnmatch
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import hashlib

import requests


# ──────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────
TELEGRAM_MAX_LENGTH = 4096
GITHUB_API         = "https://api.github.com"
TELEGRAM_API       = "https://api.telegram.org"

# Сколько репо/коммитов показывать
MAX_COMMITS        = 5
MAX_PRS            = 3
MAX_RELEASES       = 1  # Только latest релиз
MAX_WORKFLOWS      = 3
# Задержка между запросами к GitHub API (rate limit protection)
API_DELAY          = 0.5   # секунд


# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

def escape_html(text: str) -> str:
    """Экранирует спецсимволы для HTML parse_mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def html_code_block(text: str) -> str:
    """Возвращает настоящий блок кода для Telegram HTML parse_mode.

    В HTML-режиме Telegram не обрабатывает Markdown-синтаксис с ```.
    Поэтому тройные обратные кавычки отображались как обычный текст.
    Поддерживаемый Telegram вариант для многострочного кода — тег <pre>.
    """
    return f"<pre>{escape_html(text)}</pre>"


def build_github_file_url(commit_url: str, filename: str, file_info: Dict[str, Any]) -> str:
    """Строит рабочую ссылку на файл, изменённый конкретным коммитом."""
    direct_url = file_info.get("blob_url") or file_info.get("raw_url")
    if direct_url:
        return direct_url

    parts = commit_url.split("/")
    if len(parts) >= 7:
        owner = quote(parts[3], safe="")
        repo = quote(parts[4], safe="")
        sha = quote(parts[-1], safe="")
        path = quote(filename, safe="/")
        return f"https://github.com/{owner}/{repo}/blob/{sha}/{path}"

    return commit_url


def fmt_date(iso: str) -> str:
    """ISO 8601 → читаемая дата UTC."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def truncate(text: str, max_len: int = 60) -> str:
    """Обрезает строку с многоточием."""
    text = str(text)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def load_last_check_date() -> Optional[str]:
    """Загружает дату последней проверки из JSON файла (raw ISO string)."""
    try:
        if os.path.exists('last_check.json'):
            with open('last_check.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('last_check_date')
        return None
    except Exception as e:
        print(f'Ошибка загрузки даты последней проверки: {e}')
        return None


def load_last_message_hash() -> Optional[str]:
    """Загружает хэш последнего отправленного сообщения (дедупликация)."""
    try:
        if os.path.exists('last_check.json'):
            with open('last_check.json', 'r', encoding='utf-8') as f:
                return json.load(f).get('last_message_hash')
    except Exception:
        pass
    return None


def save_last_message_hash(h: str) -> None:
    """Обновляет хэш последнего отправленного сообщения."""
    try:
        data: Dict[str, Any] = {}
        if os.path.exists('last_check.json'):
            with open('last_check.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
        data['last_message_hash'] = h
        _atomic_json_write('last_check.json', data)
    except Exception as e:
        print(f'Ошибка сохранения хэша сообщения: {e}')


def _atomic_json_write(path: str, data: Any) -> None:
    """Пишет JSON атомарно через временный файл (защита от обрыва записи)."""
    dir_name = os.path.dirname(os.path.abspath(path)) or '.'
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=dir_name, delete=False, suffix='.tmp'
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


def save_last_check_date(date_str: str) -> None:
    """Сохраняет дату последней проверки в JSON файл (raw ISO string)."""
    try:
        data: Dict[str, Any] = {}
        if os.path.exists('last_check.json'):
            with open('last_check.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
        data['last_check_date'] = date_str
        _atomic_json_write('last_check.json', data)
        print(f'Дата последней проверки обновлена: {date_str}')
    except Exception as e:
        print(f'Ошибка сохранения даты последней проверки: {e}')


def load_all_repository_states(username: str) -> Dict[str, Any]:
    """Загружает все состояния репозиториев из файла одним чтением."""
    try:
        state_file = f"repo_states_{username}.json"
        if os.path.exists(state_file):
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        print(f'Ошибка загрузки состояний репозиториев: {e}')
        return {}


def save_all_repository_states(username: str, states: Dict[str, Any]) -> None:
    """Сохраняет все состояния репозиториев одной записью."""
    try:
        state_file = f"repo_states_{username}.json"
        _atomic_json_write(state_file, states)
    except Exception as e:
        print(f'Ошибка сохранения состояний репозиториев: {e}')


class GitHubClient:
    # Limits concurrent API calls across all threads to avoid bursting the rate limit.
    _api_semaphore = threading.Semaphore(3)

    def __init__(self, token: str, username: str):
        self.username = username
        self.session  = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept":        "application/vnd.github.v3+json",
            "User-Agent":    f"GitHubMonitor/{username}",
        })

    def _get(self, url: str, params: dict = None, _retry: int = 3) -> Optional[Any]:
        """GET запрос с обработкой rate limit (exponential backoff) и ошибок."""
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
                    # Jitter prevents all 6 threads from retrying simultaneously.
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
        """Все репозитории пользователя (пагинация)."""
        all_repos = []
        page      = 1

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
        """Список последних N коммитов без загрузки изменённых файлов (1 API-запрос).

        Fix #1: stores full SHA in "sha" field; display truncation happens at render time.
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
        """Загружает список изменённых файлов для одного конкретного коммита."""
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
        """Открытые PR репозитория."""
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

    def get_releases(self, repo: str, count: int = 1) -> List[Dict]:
        """Последний релиз репозитория (latest)."""
        data = self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/releases/latest",
        )
        if not data or not isinstance(data, dict):
            return []

        try:
            return [{
                "tag":         data["tag_name"],
                "name":        truncate(data.get("name") or data["tag_name"], 50),
                "author":      data["author"]["login"] if data.get("author") else "Unknown",
                "published_at": data["published_at"],
                "html_url":    data["html_url"],
            }]
        except (KeyError, TypeError):
            return []

    def get_workflow_runs(self, repo: str, count: int = 5) -> List[Dict]:
        """Последние workflow runs репозитория."""
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
                    "id":           run["id"],
                    "name":         truncate(run["name"], 40),
                    "status":       run["status"],
                    "conclusion":   run.get("conclusion", "running"),
                    "created_at":   run["created_at"],
                    "html_url":     run["html_url"],
                })
            except (KeyError, TypeError):
                continue
        return result

    def get_latest_repo_update(self) -> Optional[str]:
        """Быстрая проверка: raw ISO string обновления самого свежего репо (для раннего выхода).

        Fix #2: returns raw ISO string instead of fmt_date() result so comparison is reliable.
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
        """Количество открытых issues (без PR).

        Если open_issues_raw передан (из уже загруженного списка репо), API-запрос
        не делается — экономим лимит. Иначе запрашивает репо отдельно.
        """
        if open_issues_raw >= 0:
            return max(0, open_issues_raw - pr_count)
        data = self._get(f"{GITHUB_API}/repos/{self.username}/{repo}")
        if data and isinstance(data, dict):
            total = data.get("open_issues_count", 0)
            return max(0, total - pr_count)
        return 0

    def get_new_releases(self, repo: str, known_tag: Optional[str]) -> List[Dict]:
        """Возвращает релиз если тег изменился с момента последней проверки."""
        releases = self.get_releases(repo)
        if not releases:
            return []
        current_tag = releases[0].get("tag", "")
        if known_tag and current_tag == known_tag:
            return []
        return releases


# ──────────────────────────────────────────────────────────────
# TELEGRAM CLIENT
# ──────────────────────────────────────────────────────────────
class TelegramClient:
    def __init__(self, token: str, chat_id: str, topic_id: Optional[int] = None):
        self.token   = token
        self.chat_id = chat_id
        self.topic_id = topic_id  # Поддержка тем/топиков в Telegram
        self.base    = f"{TELEGRAM_API}/bot{token}"
        self.session = requests.Session()

    def validate(self) -> bool:
        """Проверяет токен через getMe."""
        try:
            resp = self.session.get(
                f"{self.base}/getMe", timeout=15
            )
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

    def send(self, text: str, parse_mode: str = "HTML", disable_web_page_preview: bool = True, reply_markup: Optional[Dict] = None) -> bool:
        """
        Отправляет сообщение.
        Автоматически разбивает на части если > 4096 символов.
        """
        parts = self._split(text)
        print(f"SEND parts={len(parts)} chat={self.chat_id}")
        all_ok = True
        for i, part in enumerate(parts, 1):
            # Кнопки шлём только с последней частью
            current_markup = reply_markup if i == len(parts) else None
            ok = self._send_part(part, parse_mode, disable_web_page_preview, current_markup)
            if ok:
                print(f"  OK part={i}/{len(parts)} bytes={len(part)}")
            else:
                print(f"  RETRY part={i}/{len(parts)} fallback=plain")
                # Fallback: убираем HTML теги и шлём plain
                plain = self._strip_html(part)
                ok    = self._send_part(plain, parse_mode=None)
                if ok:
                    print(f"  OK part={i}/{len(parts)} fallback=plain")
                else:
                    print(f"  FAIL part={i}/{len(parts)} all-attempts-exhausted")
                    all_ok = False

            if i < len(parts):
                time.sleep(0.5)   # не флудим

        return all_ok

    def _send_part(self, text: str, parse_mode: Optional[str], disable_web_page_preview: bool = True, reply_markup: Optional[Dict] = None) -> bool:
        """Отправляет одну часть сообщения."""
        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text":    text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if self.topic_id:
            payload["message_thread_id"] = self.topic_id
        delay = 2
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

                desc = data.get("description", "неизвестная ошибка")
                print(f"  WARN telegram: {desc}")

                # Подсказки
                if "chat not found" in desc.lower():
                    print("  hint: verify CHAT_ID (send /start to bot)")
                elif "blocked" in desc.lower():
                    print("  hint: user blocked the bot")
                elif "parse" in desc.lower():
                    print("  hint: HTML parse error; retry as plain")
                elif "too many requests" in desc.lower():
                    retry_after = data.get("parameters", {}).get("retry_after", delay)
                    print(f"   Flood control — ждём {retry_after}s")
                    time.sleep(retry_after)
                    delay = retry_after
                    continue

                return False

            except requests.exceptions.Timeout:
                print(f"  ⏱  Таймаут Telegram (попытка {attempt + 1}/3) — повтор через {delay}s")
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
        """
        Разбивает текст на части ≤ limit символов.
        Старается резать по строкам а не по середине слова.

        Fix #4: adds guard against empty string after lstrip to avoid appending "".
        """
        if len(text) <= limit:
            return [text]

        parts = []
        while text:
            if len(text) <= limit:
                parts.append(text)
                break
            # Ищем последний перенос строки в допустимом диапазоне
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
        """Убирает HTML теги для plain text fallback."""
        clean = re.sub(r"<[^>]+>", "", text)
        return (
            clean
            .replace("&amp;",  "&")
            .replace("&lt;",   "<")
            .replace("&gt;",   ">")
            .replace("&quot;", '"')
        )


# ──────────────────────────────────────────────────────────────
# MESSAGE BUILDER
# ──────────────────────────────────────────────────────────────
class MessageBuilder:
    """Строит HTML сообщение для Telegram."""

    @staticmethod
    def build(username: str, repos_data: List[Dict]) -> tuple[str, Optional[Dict]]:
        lines: List[str] = []
        buttons: List[List[Dict]] = []

        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        lines += [
            f"<b>GITHUB MONITOR</b> [<code>{escape_html(username)}</code>] · <i>{now}</i>",
            "",
        ]

        changed_repos = [r for r in repos_data if r.get("recent_commits")][:5]

        if not changed_repos:
            return "<i>status: idle. no changes detected.</i>", None

        total_commits = sum(len(r.get("recent_commits", [])) for r in changed_repos)
        total_prs = sum(len(r.get("open_prs", [])) for r in repos_data)
        total_issues = sum(r.get("open_issues", 0) for r in repos_data)
        lines.append(
            f"<i>repos=<b>{len(changed_repos)}</b> "
            f"commits=<b>{total_commits}</b> "
            f"prs=<b>{total_prs}</b> "
            f"issues=<b>{total_issues}</b></i>\n"
        )

        for repo in changed_repos:
            name = escape_html(repo["name"])
            lang = escape_html(repo.get("language") or "Unknown")
            stars = repo.get("stars", 0)
            forks = repo.get("forks", 0)
            star_delta = repo.get("star_delta", 0)
            fork_delta = repo.get("fork_delta", 0)
            issues = repo.get("open_issues", 0)
            repo_url = f"https://github.com/{username}/{repo['name']}"

            star_str = f"stars={stars}"
            if star_delta > 0:
                star_str += f" <b>(+{star_delta})</b>"
            fork_str = f"forks={forks}" if forks else ""
            if fork_delta > 0:
                fork_str += f" <b>(+{fork_delta})</b>"
            issue_str = f"issues={issues}" if issues else ""

            meta_parts = [s for s in [star_str, fork_str, issue_str] if s]
            meta_str = " · " + " · ".join(meta_parts) if meta_parts else ""
            lines.append(f"▸ <b>[{name}]</b> <i>{lang}</i>{meta_str}")
            buttons.append([{"text": f"open: {repo['name']}", "url": repo_url}])

            # Milestone уведомление
            milestones = repo.get("star_milestones", [])
            for m in milestones:
                lines.append(f"  🌟 <b>MILESTONE: {m} звёзд!</b>")

            # Коммиты -- без подзаголовка
            commits = repo.get("recent_commits", [])[:2]
            for c in commits:
                sha = escape_html(c["sha"][:7])
                msg = escape_html(c["message"])
                auth = escape_html(c["author"])
                date = escape_html(fmt_date(c["date"]))
                url = escape_html(c["html_url"])

                lines.append(f"  <a href=\"{url}\"><code>{sha}</code></a> {msg}")
                lines.append(f"  <i>by {auth} @ {date}</i>")

                files = c.get("files", [])
                if files:
                    # Tree-style ├─ / └─ + полный путь. Каждая строка -- clickable.
                    shown = files[:8]
                    overflow = max(0, len(files) - 8)
                    for i, f in enumerate(shown):
                        is_last = (i == len(shown) - 1) and overflow == 0
                        marker = "└─" if is_last else "├─"
                        fname = escape_html(f["filename"])
                        furl = escape_html(build_github_file_url(c["html_url"], f["filename"], f))
                        adds = f.get("additions", 0)
                        dels = f.get("deletions", 0)
                        stats = f" <i>(+{adds}/-{dels})</i>" if (adds or dels) else ""
                        lines.append(f"  {marker} <a href=\"{furl}\">{fname}</a>{stats}")
                    if overflow:
                        lines.append(f"  └─ <i>... +{overflow} more</i>")
                lines.append("")

            # CI / Workflow runs
            workflows = repo.get("workflow_runs", [])
            if workflows:
                wf_parts = []
                for w in workflows[:3]:
                    icon = workflow_icon(w.get("conclusion"), w.get("status", ""))
                    wf_name = escape_html(truncate(w["name"], 30))
                    wf_url = escape_html(w["html_url"])
                    wf_parts.append(f"{icon} <a href=\"{wf_url}\">{wf_name}</a>")
                lines.append("<b>CI:</b> " + "  |  ".join(wf_parts))

            # Релизы
            releases = repo.get("releases", [])
            if releases:
                r = releases[0]
                tag = escape_html(r["tag"])
                rname = escape_html(r["name"])
                rurl = escape_html(r["html_url"])
                lines.append(f"<b>RELEASE:</b> <a href=\"{rurl}\">{tag}</a> — {rname}")

            # PRs
            prs = repo.get("open_prs", [])
            if prs:
                pr_parts = [f"#{p['number']} {escape_html(p['title'])}" for p in prs[:2]]
                lines.append(f"<b>PR:</b> " + " | ".join(pr_parts))

            lines.append("────────────────────")

        reply_markup = {"inline_keyboard": buttons} if buttons else None
        return "\n".join(lines), reply_markup


# ──────────────────────────────────────────────────────────────
# MONITOR
# ──────────────────────────────────────────────────────────────
WORKFLOW_ICONS = {
    "success":     "✅",
    "failure":     "❌",
    "cancelled":   "🚫",
    "skipped":     "⏭",
    "in_progress": "🔄",
    "queued":      "⏳",
    "waiting":     "⏳",
    "running":     "🔄",
    None:          "❓",
}

# Star counts that trigger a milestone notification
STAR_MILESTONES = {5, 10, 25, 50, 100, 250, 500, 1000}


def workflow_icon(conclusion: Optional[str], status: str) -> str:
    if status in ("in_progress", "queued", "waiting"):
        return WORKFLOW_ICONS.get(status, "[RUN]")
    return WORKFLOW_ICONS.get(conclusion, WORKFLOW_ICONS[None])


class GitHubMonitor:
    def __init__(self):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        github_token      = os.getenv("G_TOKEN", "").strip()
        telegram_token    = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id      = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.username     = os.getenv("G_USERNAME", "").strip()

        # Comma-separated list of repo names/glob patterns to skip.
        # Supports fnmatch wildcards, e.g. "Ryazha*,Sys-clk,Atmosphere"
        skip_raw = os.getenv("SKIP_REPOS", "").strip()
        self.skip_patterns: list = [r.strip() for r in skip_raw.split(",") if r.strip()]

        self.dry_run = "--dry-run" in sys.argv or os.getenv("DRY_RUN", "").lower() in ("1", "true")
        self.summary_mode = "--summary" in sys.argv or os.getenv("SUMMARY_MODE", "").lower() in ("1", "true")

        missing = []
        if not github_token:    missing.append("G_TOKEN")
        if not self.username:   missing.append("G_USERNAME")
        if not self.dry_run:
            if not telegram_token: missing.append("TELEGRAM_BOT_TOKEN")
            if not self.chat_id:   missing.append("TELEGRAM_CHAT_ID")

        if missing:
            print(f"FATAL missing env: {', '.join(missing)}")
            print("   Настройте их: Settings → Secrets and variables → Actions")
            sys.exit(1)

        self.force_send = "--force" in sys.argv or os.getenv("FORCE_SEND", "").lower() in ("1", "true")

        if self.dry_run:
            print("DRY-RUN: no telegram delivery.")
        if self.force_send:
            print("FORCE: bypassing early-exit and dedup checks.")

        self.github   = GitHubClient(github_token, self.username)
        # Fix #5: TELEGRAM_TOPIC_ID properly parsed as integer
        self.telegram = TelegramClient(
            telegram_token or "dry_run_placeholder",
            self.chat_id or "0",
            topic_id=int(os.getenv("TELEGRAM_TOPIC_ID") or "0") or None
        )

    def _collect_repo_data(self, repo: Dict, old_state: Dict) -> Dict[str, Any]:
        """Performs ALL per-repo API calls and returns collected data + new state.

        Fix #10: designed to be called concurrently via ThreadPoolExecutor.
        """
        name = repo["name"]

        # Базовые данные из списка
        info: Dict[str, Any] = {
            "name":        name,
            "description": repo.get("description") or "",
            "updated_at":  repo.get("updated_at", ""),
            "pushed_at":   repo.get("pushed_at", ""),
            "stars":       repo.get("stargazers_count", 0),
            "forks":       repo.get("forks_count", 0),
            "language":    repo.get("language") or "Unknown",
            "private":     repo.get("private", False),
            "recent_commits": [],
            "open_prs":       [],
            "releases":       [],
            "workflow_runs":  [],
            "open_issues":    0,
            "star_delta":     0,
            "fork_delta":     0,
        }

        # Дельта по звёздам и форкам
        old_stars = old_state.get("stars", info["stars"])
        old_forks = old_state.get("forks", info["forks"])
        star_delta = max(0, info["stars"] - old_stars)
        fork_delta = max(0, info["forks"] - old_forks)
        info["star_delta"] = star_delta
        info["fork_delta"] = fork_delta
        if star_delta:
            print(f"[{name}] stars +{star_delta}")
        if fork_delta:
            print(f"[{name}] forks +{fork_delta}")

        # Milestone: check if we crossed any star threshold this run
        crossed = [m for m in STAR_MILESTONES if old_stars < m <= info["stars"]]
        info["star_milestones"] = crossed
        for m in crossed:
            print(f"[{name}] 🌟 MILESTONE: {m} stars!")

        # Коммиты: 1 запрос для списка SHA
        all_commits = self.github.list_commits(name, count=MAX_COMMITS)
        time.sleep(API_DELAY)

        # Fix #1: SHA deduplication uses full SHA
        known_shas = set(old_state.get("known_shas", []))

        # Если первый запуск — берём только последний коммит
        if not known_shas:
            new_commits = all_commits[:1]
        else:
            new_commits = [c for c in all_commits if c.get("sha") not in known_shas]

        # Загружаем файлы ТОЛЬКО для новых коммитов
        for commit in new_commits:
            commit["files"] = self.github.get_commit_files(name, commit["sha"])
            time.sleep(API_DELAY)

        info["recent_commits"] = new_commits

        # PRs with deduplication — Fix #3
        prs_raw = self.github.get_open_prs(name, count=MAX_PRS)
        known_pr_numbers = set(old_state.get("known_pr_numbers", []))
        if not known_pr_numbers:
            new_prs = prs_raw[:1]
        else:
            new_prs = [p for p in prs_raw if p["number"] not in known_pr_numbers]
        info["open_prs"] = new_prs
        time.sleep(API_DELAY)

        # Issues: используем данные из списка репо — без лишнего API-запроса
        info["open_issues"] = self.github.get_open_issues_count(
            name,
            open_issues_raw=repo.get("open_issues_count", 0),
            pr_count=len(prs_raw),
        )

        # Релизы — только если тег изменился
        known_tag = old_state.get("latest_release_tag")
        releases = self.github.get_new_releases(name, known_tag)
        info["releases"] = releases
        time.sleep(API_DELAY)

        # Workflow runs with deduplication — Fix #3
        workflows_raw = self.github.get_workflow_runs(name, count=MAX_WORKFLOWS)
        known_run_ids = set(old_state.get("known_run_ids", []))
        if not known_run_ids:
            new_workflows = workflows_raw[:1]
        else:
            new_workflows = [w for w in workflows_raw if w.get("id") not in known_run_ids]
        info["workflow_runs"] = new_workflows
        time.sleep(API_DELAY)

        has_real_changes = bool(new_commits or star_delta or fork_delta)
        if new_commits:
            print(f"[{name}] НОВЫЕ КОММИТЫ: {len(new_commits)}")
        elif not star_delta and not fork_delta:
            print(f"[{name}] без изменений")

        # Обновляем состояние репозитория
        # Fix #1: all_current_shas uses full SHA (c.get("sha") is now full)
        all_current_shas = [c.get("sha") for c in all_commits if c.get("sha")]
        updated_shas = list(set(known_shas) | set(all_current_shas))

        # Fix #3: update known PR numbers and run IDs
        all_current_pr_numbers = [p["number"] for p in prs_raw]
        updated_pr_numbers = list(set(known_pr_numbers) | set(all_current_pr_numbers))

        all_current_run_ids = [w.get("id") for w in workflows_raw if w.get("id")]
        updated_run_ids = list(set(known_run_ids) | set(all_current_run_ids))

        new_state = {
            "known_shas":       updated_shas[-100:],
            "known_pr_numbers": updated_pr_numbers[-200:],
            "known_run_ids":    updated_run_ids[-200:],
            "last_check":       datetime.now(timezone.utc).isoformat(),
            "stars":            info["stars"],
            "forks":            info["forks"],
        }
        if releases:
            new_state["latest_release_tag"] = releases[0].get("tag", known_tag)
        elif known_tag:
            new_state["latest_release_tag"] = known_tag

        # Добавляем в отчёт если есть что показать
        include_in_report = bool(new_commits or new_prs or releases or star_delta or fork_delta or info.get("star_milestones"))

        return {
            "info":             info,
            "new_state":        new_state,
            "has_real_changes": has_real_changes,
            "include_in_report": include_in_report,
        }

    def _run_summary(self) -> None:
        """Режим --summary: компактный дайджест всех репозиториев."""
        print("SUMMARY MODE: формирую дайджест всех репозиториев...")
        repositories = self.github.get_repositories()
        if not repositories:
            print("Репозитории не найдены.")
            return

        repositories = [
            r for r in repositories
            if not any(fnmatch.fnmatch(r["name"], p) for p in self.skip_patterns)
        ]

        all_states = load_all_repository_states(self.username)
        total_stars = sum(r.get("stargazers_count", 0) for r in repositories)
        total_forks = sum(r.get("forks_count", 0) for r in repositories)
        total_issues = sum(r.get("open_issues_count", 0) for r in repositories)
        repos_with_ci = sum(1 for r in repositories if r.get("has_pages") or r.get("has_actions"))

        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        lines = [
            f"<b>GITHUB DIGEST</b> [<code>{escape_html(self.username)}</code>] · <i>{now}</i>",
            "",
            f"<b>Репозиториев:</b> {len(repositories)} · <b>Звёзды:</b> {total_stars} · <b>Форки:</b> {total_forks} · <b>Issues:</b> {total_issues}",
            "",
            "<b>Топ по звёздам:</b>",
        ]
        top_by_stars = sorted(repositories, key=lambda r: r.get("stargazers_count", 0), reverse=True)[:10]
        for i, r in enumerate(top_by_stars, 1):
            name = escape_html(r["name"])
            stars = r.get("stargazers_count", 0)
            lang = escape_html(r.get("language") or "—")
            pushed = r.get("pushed_at", "")[:10]
            old_stars = all_states.get(r["name"], {}).get("stars", stars)
            delta = stars - old_stars
            delta_str = f" <b>(+{delta})</b>" if delta > 0 else ""
            url = f"https://github.com/{self.username}/{r['name']}"
            lines.append(f"  {i}. <a href=\"{url}\">{name}</a> — ★{stars}{delta_str} · {lang} · {pushed}")

        lines += ["", "<b>Последние обновления:</b>"]
        recent = sorted(repositories, key=lambda r: r.get("pushed_at", ""), reverse=True)[:5]
        for r in recent:
            name = escape_html(r["name"])
            pushed = fmt_date(r.get("pushed_at", ""))
            url = f"https://github.com/{self.username}/{r['name']}"
            lines.append(f"  • <a href=\"{url}\">{name}</a> — {pushed}")

        message = "\n".join(lines)
        print(f"Длина дайджеста: {len(message)} символов")

        if self.dry_run:
            print(re.sub(r"<[^>]+>", "", message))
            return

        if not self.telegram.validate():
            print("Неверный токен Telegram. Выход.")
            sys.exit(1)

        self.telegram.send(message)
        # Обновляем состояния звёзд
        for r in repositories:
            name = r["name"]
            if name not in all_states:
                all_states[name] = {}
            all_states[name]["stars"] = r.get("stargazers_count", 0)
            all_states[name]["forks"] = r.get("forks_count", 0)
        save_all_repository_states(self.username, all_states)
        print("Дайджест отправлен.")

    def run(self):
        print(f"Запуск GitHub монитора для: {self.username}")
        print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        if self.skip_patterns:
            print(f"Паттерны пропуска репозиториев: {', '.join(self.skip_patterns)}")
        print()

        if self.summary_mode:
            self._run_summary()
            return

        # Быстрая проверка — были ли вообще обновления
        print("Проверка новых релизов...")
        # Fix #2: get_latest_repo_update returns raw ISO string; compare ISO strings directly
        latest_update = self.github.get_latest_repo_update()
        last_check = load_last_check_date()

        if not self.force_send and latest_update and last_check and latest_update <= last_check:
            print(f"Новых релизов не найдено. Последнее обновление: {latest_update}")
            print("Мониторинг не требуется. Выход.")
            return

        if latest_update:
            print(f"Обнаружены новые изменения! Последнее обновление: {latest_update}")
            print(f"Предыдущая проверка: {last_check or 'первая'}")

        # Валидируем Telegram бота (пропускаем в dry_run — токен может быть пустым)
        if not self.dry_run:
            print()
            print("Проверка Telegram бота...")
            if not self.telegram.validate():
                print("Неверный токен Telegram. Выход.")
                sys.exit(1)

        # Получаем репозитории
        print()
        print("Получение репозиториев...")
        repositories = self.github.get_repositories()

        if not repositories:
            print("Репозитории не найдены или ошибка API")
            self.telegram.send(
                f"<b>GitHub Monitor</b>\n"
                f"Репозитории не найдены для <code>{escape_html(self.username)}</code>\n"
                f"Проверьте права G_TOKEN."
            )
            sys.exit(0)

        # Фильтруем пропускаемые репозитории (поддержка fnmatch glob-паттернов)
        repositories = [
            r for r in repositories
            if not any(fnmatch.fnmatch(r["name"], p) for p in self.skip_patterns)
        ]
        print(f"Найдено репозиториев: {len(repositories)}")

        # Собираем детальную информацию
        print()
        print("Сбор информации о репозиториях...")

        # Загружаем все состояния одним чтением файла
        all_states = load_all_repository_states(self.username)

        repos_data = []
        has_real_changes = False

        # Fix #10: concurrent per-repo API calls using ThreadPoolExecutor
        def _worker(repo: Dict) -> tuple[str, Dict]:
            old_state = all_states.get(repo["name"], {})
            result = self._collect_repo_data(repo, old_state)
            return repo["name"], result

        total_repos = len(repositories)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="gh-mon") as executor:
            futures = {executor.submit(_worker, repo): repo["name"] for repo in repositories}
            results: Dict[str, Dict] = {}
            done_count = 0
            # 5-minute hard cap so a hung thread can't block the whole run.
            try:
                completed = concurrent.futures.as_completed(futures, timeout=300)
            except TypeError:
                completed = concurrent.futures.as_completed(futures)
            for future in completed:
                repo_name = futures[future]
                done_count += 1
                try:
                    name, result = future.result(timeout=30)
                    results[name] = result
                    print(f"  [{done_count}/{total_repos}] {name} — готово")
                except concurrent.futures.TimeoutError:
                    print(f"  [{done_count}/{total_repos}] {repo_name} — таймаут (>30с)")
                except Exception as exc:
                    print(f"  [{done_count}/{total_repos}] {repo_name} — ошибка: {exc}")

        # Process results sequentially (state saving must be sequential)
        for repo in repositories:
            name = repo["name"]
            if name not in results:
                continue
            result = results[name]

            all_states[name] = result["new_state"]

            if result["has_real_changes"]:
                has_real_changes = True

            if result["include_in_report"]:
                repos_data.append(result["info"])

        if not self.force_send and not has_real_changes:
            print()
            print("Реальных изменений в содержимом не найдено.")
            print("Мониторинг не требуется. Выход.")
            return

        # Сортируем по свежести
        repos_data.sort(
            key=lambda x: x.get("pushed_at", ""),
            reverse=True,
        )

        # Формируем сообщение
        print()
        print("Формирование сообщения...")
        message, markup = MessageBuilder.build(self.username, repos_data)
        print(f"Длина сообщения: {len(message)} символов")

        if self.dry_run:
            print()
            print("─" * 60)
            print("DRY-RUN: сообщение которое было бы отправлено:")
            print("─" * 60)
            # Fix #7: use module-level re instead of importing re as _re inside function
            plain = re.sub(r"<[^>]+>", "", message)
            print(plain)
            print("─" * 60)
            print("DRY-RUN: Telegram не отправлялся.")
            if latest_update:
                save_last_check_date(latest_update)
            return

        # Дедупликация: пропускаем если контент не изменился
        msg_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
        last_hash = load_last_message_hash()
        if not self.force_send and last_hash and last_hash == msg_hash:
            print()
            print(f"Сообщение не изменилось (hash={msg_hash}). Пропускаем отправку.")
            save_all_repository_states(self.username, all_states)
            if latest_update:
                save_last_check_date(latest_update)
            return

        print()
        print("Отправка в Telegram...")
        success = self.telegram.send(message, reply_markup=markup)

        print()
        if success:
            print("Мониторинг успешно завершен")
            # Сохраняем состояния ТОЛЬКО после успешной отправки.
            # Если сохранить раньше и Telegram упадёт — следующий запуск
            # не найдёт изменений (SHA уже в known_shas) и пропустит уведомление.
            save_all_repository_states(self.username, all_states)
            if latest_update:
                save_last_check_date(latest_update)
            save_last_message_hash(msg_hash)
        else:
            print("Мониторинг завершен с ошибками отправки")
            self.telegram.send(
                "<b>GitHub Monitor</b>\n"
                "Отчет сформирован, но некоторые части не отправлены.\n"
                "Проверьте логи Actions для деталей."
            )
            sys.exit(1)


# ──────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        monitor = GitHubMonitor()
        monitor.run()
    except KeyboardInterrupt:
        print("\n⏹  Interrupted")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print(f"\n Unexpected error: {e}")
        traceback.print_exc()
        sys.exit(1)
