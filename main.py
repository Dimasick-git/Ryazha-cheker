#!/usr/bin/env python3
"""
GitHub Repository Monitor
Отслеживает репозитории пользователя и отправляет уведомления в Telegram
"""

import os
import sys
import time
import tempfile
import requests
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from urllib.parse import quote


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
MAX_REPOS_WITH_PRS = 3

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
    """Загружает дату последней проверки из JSON файла."""
    try:
        if os.path.exists('last_check.json'):
            with open('last_check.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('last_check_date')
        return None
    except Exception as e:
        print(f'Ошибка загрузки даты последней проверки: {e}')
        return None


def _atomic_json_write(path: str, data: Any) -> None:
    """Пишет JSON атомарно через временный файл (защита от обрыва записи)."""
    dir_name = os.path.dirname(os.path.abspath(path)) or '.'
    with tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', dir=dir_name, delete=False, suffix='.tmp'
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, path)  # atomic on POSIX


def save_last_check_date(date_str: str) -> None:
    """Сохраняет дату последней проверки в JSON файл."""
    try:
        _atomic_json_write('last_check.json', {'last_check_date': date_str})
        print(f'Дата последней проверки обновлена: {date_str}')
    except Exception as e:
        print(f'Ошибка сохранения даты последней проверки: {e}')


def calculate_content_checksum(content: str) -> str:
    """Вычисляет чек-сумму содержимого."""
    return hashlib.md5(content.encode('utf-8')).hexdigest()


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


def build_file_tree(files: List[Dict]) -> str:
    """Строит красивое дерево файлов."""
    if not files:
        return ""
    
    tree_lines = []
    
    # Группируем файлы по папкам
    folders = {}
    root_files = []
    
    for f in files:
        path = f["filename"]
        parts = path.split("/")
        
        if len(parts) == 1:
            # Файл в корне
            root_files.append(f)
        else:
            # Файл в подпапке
            folder = parts[0]
            if folder not in folders:
                folders[folder] = []
            folders[folder].append(f)
    
    if root_files:
        for f in root_files:
            changes = f.get("changes", 0)
            additions = f.get("additions", 0)
            deletions = f.get("deletions", 0)
            filename = f["filename"]
            
            if changes > 0:
                tree_lines.append(f"├── {filename} (+{additions}/-{deletions})")
            else:
                tree_lines.append(f"├── {filename}")
    
    # Добавляем папки
    for folder_name, folder_files in sorted(folders.items()):
        tree_lines.append(f"├── {folder_name}/")
        
        for i, f in enumerate(folder_files):
            changes = f.get("changes", 0)
            additions = f.get("additions", 0)
            deletions = f.get("deletions", 0)
            filename = f["filename"]
            
            rel_path = filename.split("/")[-1]
            is_last = (i == len(folder_files) - 1)
            prefix = "│   └── " if is_last else "│   ├── "
            
            if changes > 0:
                tree_lines.append(f"{prefix}{rel_path} (+{additions}/-{deletions})")
            else:
                tree_lines.append(f"{prefix}{rel_path}")
    
    return "\n".join(tree_lines)


# ──────────────────────────────────────────────────────────────
# GITHUB CLIENT
# ──────────────────────────────────────────────────────────────
class GitHubClient:
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
                resp = self.session.get(url, params=params, timeout=20)

                if resp.status_code == 429 or (
                    resp.status_code == 403
                    and "rate limit" in resp.text.lower()
                ):
                    reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                    wait = max(reset_ts - int(time.time()), delay)
                    print(f"⚠️  Rate limit (попытка {attempt}/{_retry}). Ожидание {wait}с...")
                    time.sleep(wait)
                    delay *= 2
                    continue

                if resp.status_code == 200:
                    return resp.json()

                print(f"❌ Ошибка GitHub API {resp.status_code}: {url}")
                print(f"   Ответ: {resp.text[:200]}")
                return None

            except requests.exceptions.Timeout:
                print(f"⏱️  Таймаут (попытка {attempt}/{_retry}): {url}")
                if attempt < _retry:
                    time.sleep(delay)
                    delay *= 2
                    continue
                return None
            except requests.exceptions.RequestException as e:
                print(f"🌐 Ошибка сети: {e}")
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
        """Список последних N коммитов без загрузки изменённых файлов (1 API-запрос)."""
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
                    "sha":      c["sha"][:7],
                    "full_sha": c["sha"],
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
        """Быстрая проверка: дата обновления самого свежего репо (для раннего выхода)."""
        data = self._get(
            f"{GITHUB_API}/users/{self.username}/repos",
            params={"type": "owner", "sort": "updated", "per_page": 1},
        )
        if data and isinstance(data, list) and data:
            updated_at = data[0].get("updated_at", "")
            return fmt_date(updated_at) if updated_at else None
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
    def __init__(self, token: str, chat_id: str, topic_id: Optional[str] = None):
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
                print(f"✅ Бот: @{bot['username']} (id={bot['id']})")
                return True
            print(f"❌ Ошибка getMe: {data.get('description')}")
            return False
        except Exception as e:
            print(f"❌ Исключение getMe: {e}")
            return False

    def send(self, text: str, parse_mode: str = "HTML", disable_web_page_preview: bool = True, reply_markup: Optional[Dict] = None) -> bool:
        """
        Отправляет сообщение.
        Автоматически разбивает на части если > 4096 символов.
        """
        parts = self._split(text)
        print(f"📤 Отправка {len(parts)} части(ей) в чат {self.chat_id}")
        all_ok = True
        for i, part in enumerate(parts, 1):
            # Кнопки шлём только с последней частью
            current_markup = reply_markup if i == len(parts) else None
            ok = self._send_part(part, parse_mode, disable_web_page_preview, current_markup)
            if ok:
                print(f"  ✅ Часть {i}/{len(parts)} отправлена ({len(part)} символов)")
            else:
                print(f"  ❌ Часть {i}/{len(parts)} не отправлена — пробуем простой текст")
                # Fallback: убираем HTML теги и шлём plain
                plain = self._strip_html(part)
                ok    = self._send_part(plain, parse_mode=None)
                if ok:
                    print(f"  ✅ Часть {i}/{len(parts)} отправлена как простой текст")
                else:
                    print(f"  ❌ Часть {i}/{len(parts)} полностью не отправлена")
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
                print(f"  ⚠️  Ошибка Telegram: {desc}")

                # Подсказки
                if "chat not found" in desc.lower():
                    print("  💡 Проверьте CHAT_ID (отправьте /start боту)")
                elif "blocked" in desc.lower():
                    print("  💡 Пользователь заблокировал бота")
                elif "parse" in desc.lower():
                    print("  💡 Ошибка HTML парсинга — будет попытка как plain text")
                elif "too many requests" in desc.lower():
                    retry_after = data.get("parameters", {}).get("retry_after", delay)
                    print(f"  ⏳ Flood control — ждём {retry_after}s")
                    time.sleep(retry_after)
                    delay = retry_after
                    continue

                return False

            except requests.exceptions.Timeout:
                print(f"  ⏱️  Таймаут Telegram (попытка {attempt + 1}/3) — повтор через {delay}s")
                time.sleep(delay)
                delay *= 2
            except requests.exceptions.ConnectionError as e:
                print(f"  🌐 Ошибка соединения (попытка {attempt + 1}/3): {e} — повтор через {delay}s")
                time.sleep(delay)
                delay *= 2
            except Exception as e:
                print(f"  ❌ Исключение Telegram: {e}")
                return False

        print("  ❌ Все попытки исчерпаны для _send_part")
        return False

    @staticmethod
    def _split(text: str, limit: int = TELEGRAM_MAX_LENGTH) -> List[str]:
        """
        Разбивает текст на части ≤ limit символов.
        Старается резать по строкам а не по середине слова.
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
            f"🔔 <b>GitHub Monitor</b> — <code>{escape_html(username)}</code>",
            f"🕐 {now}",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        changed_repos = [r for r in repos_data if r.get("recent_commits")][:5]

        if not changed_repos:
            return "✅ Активности не обнаружено.", None

        total_commits = sum(len(r.get("recent_commits", [])) for r in changed_repos)
        lines.append(f"📦 Репозиториев с изменениями: <b>{len(changed_repos)}</b>  |  📝 Новых коммитов: <b>{total_commits}</b>\n")

        for repo in changed_repos:
            name = escape_html(repo["name"])
            lang = escape_html(repo.get("language") or "Unknown")
            stars = repo.get("stars", 0)
            forks = repo.get("forks", 0)
            star_delta = repo.get("star_delta", 0)
            fork_delta = repo.get("fork_delta", 0)
            issues = repo.get("open_issues", 0)
            repo_url = f"https://github.com/{username}/{repo['name']}"

            star_str = f"⭐{stars}"
            if star_delta > 0:
                star_str += f" <b>(+{star_delta})</b>"
            fork_str = f"🍴{forks}" if forks else ""
            if fork_delta > 0:
                fork_str += f" <b>(+{fork_delta})</b>"
            issue_str = f"🐛{issues}" if issues else ""

            meta_parts = [s for s in [star_str, fork_str, issue_str] if s]
            lines.append(f"📁 <b>{name}</b>  <i>{lang}</i>  {' · '.join(meta_parts)}")
            buttons.append([{"text": f"🔗 {repo['name']}", "url": repo_url}])

            # Коммиты
            commits = repo.get("recent_commits", [])[:2]
            if commits:
                lines.append("<b>Изменения:</b>")
                for c in commits:
                    sha = escape_html(c["sha"][:7])
                    msg = escape_html(c["message"])
                    auth = escape_html(c["author"])
                    date = escape_html(fmt_date(c["date"]))
                    url = escape_html(c["html_url"])

                    lines.append(f"  • <a href=\"{url}\"><code>{sha}</code></a> {msg}")
                    lines.append(f"    👤 {auth}  🕐 {date}")

                    files = c.get("files", [])
                    if files:
                        tree = build_file_tree(files[:5])
                        if tree.strip():
                            lines.append(html_code_block(tree))

                        file_links = []
                        for f in files[:3]:
                            fname = escape_html(f["filename"])
                            furl = escape_html(build_github_file_url(c["html_url"], f["filename"], f))
                            file_links.append(f"    📄 <a href=\"{furl}\">{fname}</a>")
                        if file_links:
                            lines.extend(file_links)

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
                lines.append(f"🚀 <b>Релиз:</b> <a href=\"{rurl}\">{tag}</a> — {rname}")

            # PRs
            prs = repo.get("open_prs", [])
            if prs:
                pr_parts = [f"#{p['number']} {escape_html(p['title'])}" for p in prs[:2]]
                lines.append(f"🔀 <b>PR:</b> " + " | ".join(pr_parts))

            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

        reply_markup = {"inline_keyboard": buttons} if buttons else None
        return "\n".join(lines), reply_markup


# ──────────────────────────────────────────────────────────────
# MONITOR
# ──────────────────────────────────────────────────────────────
WORKFLOW_ICONS = {
    "success":   "✅",
    "failure":   "❌",
    "cancelled": "⛔",
    "skipped":   "⏭️",
    "in_progress": "🔄",
    "queued":    "⏳",
    "waiting":   "⏳",
    "running":   "🔄",
    None:        "❓",
}


def workflow_icon(conclusion: Optional[str], status: str) -> str:
    if status in ("in_progress", "queued", "waiting"):
        return WORKFLOW_ICONS.get(status, "🔄")
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
        self.username     = os.getenv("G_USERNAME", "Dimasick-git").strip()

        # Comma-separated list of repo names to skip, e.g. "Ryazhahand-Overlay,Sys-clk,Atmosphere"
        skip_raw = os.getenv("SKIP_REPOS", "").strip()
        self.skip_repos: set = {r.strip() for r in skip_raw.split(",") if r.strip()}

        missing = []
        if not github_token:   missing.append("G_TOKEN")
        if not telegram_token: missing.append("TELEGRAM_BOT_TOKEN")
        if not self.chat_id:   missing.append("TELEGRAM_CHAT_ID")

        self.dry_run = "--dry-run" in sys.argv or os.getenv("DRY_RUN", "").lower() in ("1", "true")

        if self.dry_run:
            print("🔍 DRY-RUN режим: Telegram уведомления отправляться не будут.")
            # В dry-run режиме токен Telegram не обязателен
            if not github_token:
                missing.append("G_TOKEN")
        else:
            if missing:
                print(f"❌ Отсутствуют переменные окружения: {', '.join(missing)}")
                print("   Настройте их: Settings → Secrets and variables → Actions")
                sys.exit(1)

        if missing and not self.dry_run:
            print(f"❌ Отсутствуют переменные окружения: {', '.join(missing)}")
            sys.exit(1)

        self.github   = GitHubClient(github_token, self.username)
        self.telegram = TelegramClient(
            telegram_token or "dry_run_placeholder",
            self.chat_id or "0",
            topic_id=os.getenv("TELEGRAM_TOPIC_ID", "").strip() or None
        )

    def run(self):
        print(f"Запуск GitHub монитора для: {self.username}")
        print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        if self.skip_repos:
            print(f"Пропускаем репозитории: {', '.join(sorted(self.skip_repos))}")
        print()

        # Быстрая проверка — были ли вообще обновления (использует авторизованную сессию)
        print("Проверка новых релизов...")
        latest_update = self.github.get_latest_repo_update()
        last_check = load_last_check_date()

        if latest_update and last_check and latest_update == last_check:
            print(f"Новых релизов не найдено. Последнее обновление: {latest_update}")
            print("Мониторинг не требуется. Выход.")
            return

        if latest_update:
            print(f"Обнаружены новые изменения! Последнее обновление: {latest_update}")
            print(f"Предыдущая проверка: {last_check or 'первая'}")

        # Валидируем Telegram бота
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

        # Фильтруем пропускаемые репозитории
        repositories = [r for r in repositories if r["name"] not in self.skip_repos]
        print(f"Найдено репозиториев: {len(repositories)}")

        # Собираем детальную информацию
        print()
        print("Сбор информации о репозиториях...")
        repos_data = []
        has_real_changes = False

        # Загружаем все состояния одним чтением файла
        all_states = load_all_repository_states(self.username)

        for i, repo in enumerate(repositories, 1):
            name = repo["name"]
            print(f"[{i:2d}/{len(repositories)}] {name}", end="", flush=True)

            # Берём состояние из уже загруженного словаря
            old_state = all_states.get(name, {})
            
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
                print(f" [⭐+{star_delta}]", end="")
            if fork_delta:
                print(f" [🍴+{fork_delta}]", end="")

            # Коммиты: 1 запрос для списка SHA
            all_commits = self.github.list_commits(name, count=MAX_COMMITS)
            time.sleep(API_DELAY)

            # Фильтруем только новые коммиты, которых нет в памяти
            known_shas = set(old_state.get("known_shas", []))

            # Если первый запуск — берём только последний коммит
            if not known_shas:
                new_commits = all_commits[:1]
            else:
                new_commits = [c for c in all_commits if c.get("sha") not in known_shas]

            # Загружаем файлы ТОЛЬКО для новых коммитов (раньше делалось для всех)
            for commit in new_commits:
                commit["files"] = self.github.get_commit_files(name, commit["full_sha"])
                time.sleep(API_DELAY)

            info["recent_commits"] = new_commits

            # PRs
            prs = self.github.get_open_prs(name, count=MAX_PRS)
            info["open_prs"] = prs
            time.sleep(API_DELAY)

            # Issues: используем данные из списка репо — без лишнего API-запроса
            info["open_issues"] = self.github.get_open_issues_count(
                name,
                open_issues_raw=repo.get("open_issues_count", 0),
                pr_count=len(prs),
            )

            # Релизы — только если тег изменился
            known_tag = old_state.get("latest_release_tag")
            releases = self.github.get_new_releases(name, known_tag)
            info["releases"] = releases
            time.sleep(API_DELAY)

            # Workflow runs
            workflows = self.github.get_workflow_runs(name, count=MAX_WORKFLOWS)
            info["workflow_runs"] = workflows
            time.sleep(API_DELAY)

            # Проверяем реальные изменения
            if new_commits or star_delta or fork_delta:
                has_real_changes = True
            if new_commits:
                print(f" [НОВЫЕ КОММИТЫ: {len(new_commits)}]")

            # Обновляем состояние репозитория
            all_current_shas = [c.get("sha") for c in all_commits if c.get("sha")]
            updated_shas = list(set(known_shas) | set(all_current_shas))
            new_state = {
                "known_shas": updated_shas[-100:],
                "last_check": datetime.now(timezone.utc).isoformat(),
                "stars": info["stars"],
                "forks": info["forks"],
            }
            if releases:
                new_state["latest_release_tag"] = releases[0].get("tag", known_tag)
            elif known_tag:
                new_state["latest_release_tag"] = known_tag
            all_states[name] = new_state

            if not new_commits and not star_delta and not fork_delta:
                print(" [без изменений]")

            # Добавляем в отчёт если есть что показать
            if new_commits or prs or releases or star_delta or fork_delta:
                repos_data.append(info)

        # Сохраняем все обновлённые состояния одной записью
        save_all_repository_states(self.username, all_states)

        if not has_real_changes:
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
            # Убираем HTML теги для читабельного вывода в консоль
            import re as _re
            plain = _re.sub(r"<[^>]+>", "", message)
            print(plain)
            print("─" * 60)
            print("DRY-RUN: Telegram не отправлялся.")
            if latest_update:
                save_last_check_date(latest_update)
            return

        print()
        print("Отправка в Telegram...")
        success = self.telegram.send(message, reply_markup=markup)

        print()
        if success:
            print("Мониторинг успешно завершен")
            if latest_update:
                save_last_check_date(latest_update)
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
        print("\n⏹️  Interrupted")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print(f"\n💥 Unexpected error: {e}")
        traceback.print_exc()
        sys.exit(1)
