#!/usr/bin/env python3
"""
GitHub Repository Monitor
Отслеживает репозитории пользователя и отправляет уведомления в Telegram
"""

import os
import sys
import time
import requests
import hashlib
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional


# ──────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────
TELEGRAM_MAX_LENGTH = 4096
GITHUB_API         = "https://api.github.com"
TELEGRAM_API       = "https://api.telegram.org"

# Сколько репо/коммитов показывать
MAX_ACTIVE_REPOS   = 10  # Показываем все репозитории
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
import json

def escape_html(text: str) -> str:
    """Экранирует спецсимволы для HTML parse_mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


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


def save_last_check_date(date_str: str) -> None:
    """Сохраняет дату последней проверки в JSON файл."""
    try:
        with open('last_check.json', 'w', encoding='utf-8') as f:
            json.dump({'last_check_date': date_str}, f, ensure_ascii=False, indent=2)
        print(f'Дата последней проверки обновлена: {date_str}')
    except Exception as e:
        print(f'Ошибка сохранения даты последней проверки: {e}')


def check_for_new_releases(username: str) -> Optional[str]:
    """Проверяет наличие новых релизов на GitHub."""
    try:
        # Получаем информацию о последнем релизе пользователя
        response = requests.get(
            f"{GITHUB_API}/users/{username}/repos",
            params={"type": "owner", "sort": "updated", "per_page": 10},
            timeout=20
        )
        
        if response.status_code == 200:
            repos = response.json()
            if repos:
                # Берем дату обновления самого свежего репозитория
                latest_update = repos[0].get('updated_at', '')
                if latest_update:
                    return fmt_date(latest_update)
        return None
    except Exception as e:
        print(f'Ошибка проверки новых релизов: {e}')
        return None


def calculate_content_checksum(content: str) -> str:
    """Вычисляет чек-сумму содержимого."""
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def load_repository_state(username: str, repo_name: str) -> Dict[str, Any]:
    """Загружает состояние репозитория из файла."""
    try:
        state_file = f"repo_states_{username}.json"
        if os.path.exists(state_file):
            with open(state_file, 'r', encoding='utf-8') as f:
                states = json.load(f)
                return states.get(repo_name, {})
        return {}
    except Exception as e:
        print(f'Ошибка загрузки состояния репозитория: {e}')
        return {}


def save_repository_state(username: str, repo_name: str, state: Dict[str, Any]) -> None:
    """Сохраняет состояние репозитория в файл."""
    try:
        state_file = f"repo_states_{username}.json"
        states = {}
        
        # Загружаем существующие состояния
        if os.path.exists(state_file):
            with open(state_file, 'r', encoding='utf-8') as f:
                states = json.load(f)
        
        # Обновляем состояние конкретного репозитория
        states[repo_name] = state
        
        # Сохраняем все состояния
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(states, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        print(f'Ошибка сохранения состояния репозитория: {e}')


def detect_content_changes(old_commits: List[Dict], new_commits: List[Dict]) -> bool:
    """Определяет есть ли реальные изменения в содержимом коммитов."""
    if not old_commits or not new_commits:
        return True
    
    # Сравниваем чек-суммы первых 3 коммитов
    for i in range(min(3, len(old_commits), len(new_commits))):
        old_commit = old_commits[i]
        new_commit = new_commits[i]
        
        # Создаем строку для сравнения
        old_content = f"{old_commit.get('sha', '')}{old_commit.get('message', '')}{old_commit.get('date', '')}"
        new_content = f"{new_commit.get('sha', '')}{new_commit.get('message', '')}{new_commit.get('date', '')}"
        
        if calculate_content_checksum(old_content) != calculate_content_checksum(new_content):
            return True
    
    return False


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
    
    # Добавляем корневые файлы
    if root_files:
        for f in root_files:
            changes = f.get("changes", 0)
            additions = f.get("additions", 0)
            deletions = f.get("deletions", 0)
            filename = escape_html(f["filename"])
            
            if changes > 0:
                tree_lines.append(f"├─ {filename} (+{additions}/-{deletions})")
            else:
                tree_lines.append(f"├─ {filename}")
    
    # Добавляем папки
    for folder_name, folder_files in sorted(folders.items()):
        tree_lines.append(f"├─ {folder_name}/")
        
        for i, f in enumerate(folder_files):
            changes = f.get("changes", 0)
            additions = f.get("additions", 0)
            deletions = f.get("deletions", 0)
            filename = escape_html(f["filename"])
            
            # Вычисляем относительный путь
            rel_path = filename.replace(f"{folder_name}/", "", 1)
            
            # Определяем префикс (├─ или └─)
            if i == len(folder_files) - 1 and not root_files:
                prefix = "└─"
            else:
                prefix = "│  ├─"
            
            if changes > 0:
                tree_lines.append(f"{prefix} {rel_path} (+{additions}/-{deletions})")
            else:
                tree_lines.append(f"{prefix} {rel_path}")
    
    return "\n".join(tree_lines)


# ──────────────────────────────────────────────────────────────
# GITHUB CLIENT
# ──────────────────────────────────────────────────────────────
class GitHubClient:
    def __init__(self, token: str, username: str):
        self.username = username
        self.session  = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {token}",
            "Accept":        "application/vnd.github.v3+json",
            "User-Agent":    f"GitHubMonitor/{username}",
        })

    def _get(self, url: str, params: dict = None) -> Optional[Any]:
        """GET запрос с обработкой rate limit и ошибок."""
        try:
            resp = self.session.get(url, params=params, timeout=20)

            # Rate limit
            if resp.status_code == 429 or (
                resp.status_code == 403
                and "rate limit" in resp.text.lower()
            ):
                reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait     = max(reset_ts - int(time.time()), 5)
                print(f"⚠️  Лимит запросов превышен. Ожидание {wait}с...")
                time.sleep(wait)
                resp = self.session.get(url, params=params, timeout=20)

            if resp.status_code == 200:
                return resp.json()

            print(f"❌ Ошибка GitHub API {resp.status_code}: {url}")
            print(f"   Ответ: {resp.text[:200]}")
            return None

        except requests.exceptions.Timeout:
            print(f"⏱️  Таймаут: {url}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"🌐 Ошибка сети: {e}")
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

    def get_recent_commits(self, repo: str, count: int = 3) -> List[Dict]:
        """Последние N коммитов репозитория."""
        data = self._get(
            f"{GITHUB_API}/repos/{self.username}/{repo}/commits",
            params={"per_page": count},
        )
        if not data or not isinstance(data, list):
            return []

        result = []
        for c in data[:count]:
            try:
                # Получаем информацию об измененных файлах
                files_data = self._get(
                    f"{GITHUB_API}/repos/{self.username}/{repo}/commits/{c['sha']}"
                )
                
                files = []
                if files_data and "files" in files_data:
                    for f in files_data["files"][:5]:  # Показываем до 5 файлов
                        files.append({
                            "filename": f["filename"],
                            "changes": f.get("changes", 0),
                            "additions": f.get("additions", 0),
                            "deletions": f.get("deletions", 0),
                            "patch": f.get("patch", "")[:200]  # Обрезаем патч
                        })
                
                result.append({
                    "sha":     c["sha"][:7],
                    "message": truncate(c["commit"]["message"].split("\n")[0], 60),
                    "author":  truncate(c["commit"]["author"]["name"], 25),
                    "date":    c["commit"]["author"]["date"],
                    "html_url": c["html_url"],
                    "files":   files
                })
            except (KeyError, TypeError):
                continue
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

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Отправляет сообщение.
        Автоматически разбивает на части если > 4096 символов.
        """
        parts = self._split(text)
        print(f"📤 Отправка {len(parts)} части(ей) в чат {self.chat_id}")

        all_ok = True
        for i, part in enumerate(parts, 1):
            ok = self._send_part(part, parse_mode)
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

    def _send_part(self, text: str, parse_mode: Optional[str]) -> bool:
        """Отправляет одну часть сообщения."""
        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text":    text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

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
                print("  💡 Исправление: отправьте /start боту сначала, или проверьте CHAT_ID")
            elif "blocked" in desc.lower():
                print("  💡 Исправление: пользователь заблокировал бота — разблокируйте в Telegram")
            elif "parse" in desc.lower():
                print("  💡 Исправление: ошибка HTML парсинга — будет повторная попытка как простой текст")

            return False

        except requests.exceptions.Timeout:
            print("  ⏱️  Таймаут Telegram")
            return False
        except Exception as e:
            print(f"  ❌ Исключение Telegram: {e}")
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
        import re
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
    def build(username: str, repos_data: List[Dict]) -> str:
        lines = []

        # ── Заголовок ──────────────────────────────────────────
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines += [
            "<b>Мониторинг репозиториев GitHub</b>",
            f"Пользователь: <code>{escape_html(username)}</code>",
            f"Время: {now}",
            "",
        ]

        # ── Сводка ─────────────────────────────────────────────
        total_repos = len(repos_data)
        total_stars = sum(r.get("stars", 0) for r in repos_data)
        total_forks = sum(r.get("forks", 0) for r in repos_data)

        lines += [
            "<b>Общая статистика</b>",
            f"Всего репозиториев: <b>{total_repos}</b>",
            f"Звезд: <b>{total_stars}</b>",
            f"Форков: <b>{total_forks}</b>",
            "",
        ]

        # ── Активные репо ───────────────────────────────────────
        active = [r for r in repos_data if r.get("recent_commits")][:MAX_ACTIVE_REPOS]

        if active:
            lines.append("<b>Активные репозитории</b>")
            lines.append("")

            for repo in active:
                name  = escape_html(repo["name"])
                lang  = escape_html(repo.get("language") or "—")
                desc  = repo.get("description") or ""
                stars = repo.get("stars", 0)
                forks = repo.get("forks", 0)

                lines.append(f"<b>{name}</b>")

                if desc and desc != "No description":
                    lines.append(f"{escape_html(truncate(desc, 80))}")

                lines.append(
                    f"Звезд: {stars} | Форков: {forks} | Язык: {lang}"
                    + (" | Приватный" if repo.get("private") else "")
                )
                lines.append("<hr>")

                # Коммиты
                commits = repo.get("recent_commits", [])[:MAX_COMMITS]
                if commits:
                    lines.append("Последние коммиты:")
                    for c in commits:
                        sha  = escape_html(c["sha"])
                        msg  = escape_html(c["message"])
                        auth = escape_html(c["author"])
                        date = fmt_date(c["date"])
                        url  = c["html_url"]
                        
                        lines.append(f"<a href=\"{url}\"><code>{sha}</code></a>")
                        lines.append(f"<pre><code>{msg}</code></pre>")
                        lines.append(f"Автор: {auth} | {date}")
                        
                        # Показываем измененные файлы
                        files = c.get("files", [])
                        if files:
                            lines.append("Измененные файлы:")
                            
                            # Добавляем дату модификации
                            commit_date = fmt_date(c["date"])
                            lines.append(f"Дата последнего изменения: {commit_date}")
                            lines.append("")
                            
                            # Строим дерево файлов
                            tree = build_file_tree(files)
                            lines.append(f"<pre><code>{tree}</code></pre>")
                            
                            # Добавляем кликабельные ссылки
                            lines.append("Ссылки на файлы:")
                            for f in files[:5]:  # Показываем до 5 файлов
                                filename = escape_html(f["filename"])
                                changes = f.get("changes", 0)
                                additions = f.get("additions", 0)
                                deletions = f.get("deletions", 0)
                                
                                # Ссылка на файл в GitHub
                                file_url = f"{url.replace('/commit/', '/blob/')}/{f['filename']}"
                                
                                if changes > 0:
                                    lines.append(f"• <a href=\"{file_url}\">{filename}</a> (+{additions}/-{deletions})")
                                else:
                                    lines.append(f"• <a href=\"{file_url}\">{filename}</a>")
                        
                        lines.append("")

                # Релизы
                releases = repo.get("releases", [])[:MAX_RELEASES]
                if releases:
                    lines.append("Последний релиз:")
                    for rel in releases:
                        tag = escape_html(rel["tag"])
                        name = escape_html(rel["name"])
                        author = escape_html(rel["author"])
                        date = fmt_date(rel["published_at"])
                        url = rel["html_url"]
                        lines.append(f"<a href=\"{url}\">{tag}</a> - {name}")
                        lines.append(f"Автор: {author} | {date}")
                    lines.append("")

                # Open PRs
                prs = repo.get("open_prs", [])
                if prs:
                    lines.append(f"Открытые PR: {len(prs)}")

                # Workflow runs
                workflows = repo.get("workflow_runs", [])[:MAX_WORKFLOWS]
                if workflows:
                    lines.append(f"Последние workflow: {len(workflows)}")
                    for wf in workflows:
                        name = escape_html(wf["name"])
                        status = wf["status"]
                        conclusion = wf.get("conclusion", "running")
                        date = fmt_date(wf["created_at"])
                        url = wf["html_url"]
                        status_icon = "✅" if conclusion == "success" else "❌" if conclusion == "failure" else "⏳"
                        lines.append(f"{status_icon} <a href=\"{url}\">{name}</a> - {conclusion}")
                        lines.append(f"Статус: {status} | {date}")
                    lines.append("")

                lines.append("")

        # ── Репо с открытыми PRs ────────────────────────────────
        repos_with_prs = [r for r in repos_data if r.get("open_prs")][:MAX_REPOS_WITH_PRS]

        if repos_with_prs:
            lines.append("<b>Открытые Pull Requests</b>")
            lines.append("")

            for repo in repos_with_prs:
                name = escape_html(repo["name"])
                prs  = repo["open_prs"][:MAX_PRS]
                lines.append(f"<b>{name}</b> — {len(prs)} открытых PR:")

                for pr in prs:
                    num    = pr["number"]
                    title  = escape_html(pr["title"])
                    author = escape_html(pr["author"])
                    date   = fmt_date(pr["date"])
                    lines.append(f"#{num} {title}")
                    lines.append(f"Автор: {author} | {date}")

                lines.append("")

        # ── Нет активности ──────────────────────────────────────
        if not active and not repos_with_prs:
            lines.append("Недавней активности не найдено.")
            lines.append("")

        lines.append("—")
        lines.append(
            f"<i>GitHub Monitor · Запуск #{os.getenv('GITHUB_RUN_NUMBER', '?')}</i>"
        )

        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# MONITOR
# ──────────────────────────────────────────────────────────────
class GitHubMonitor:
    def __init__(self):
        # Читаем env
        github_token      = os.getenv("G_TOKEN", "").strip()
        telegram_token    = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id      = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.username     = os.getenv("G_USERNAME", "Dimasick-git").strip()

        # Валидация
        missing = []
        if not github_token:   missing.append("G_TOKEN")
        if not telegram_token: missing.append("TELEGRAM_BOT_TOKEN")
        if not self.chat_id:   missing.append("TELEGRAM_CHAT_ID")

        if missing:
            print(f"❌ Отсутствуют переменные окружения: {', '.join(missing)}")
            print("   Настройте их: Settings → Secrets and variables → Actions")
            sys.exit(1)

        self.github   = GitHubClient(github_token, self.username)
        self.telegram = TelegramClient(
            telegram_token, 
            self.chat_id, 
            topic_id=os.getenv("TELEGRAM_TOPIC_ID", "").strip() or None
        )

    def run(self):
        print(f"🚀 Запуск GitHub монитора для: {self.username}")
        print(f"   Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print()

        # ── Проверка новых релизов ───────────────────────────────
        print("0️⃣  Проверка новых релизов...")
        latest_update = check_for_new_releases(self.username)
        last_check = load_last_check_date()
        
        if latest_update and last_check and latest_update == last_check:
            print(f"⚠️  Новых релизов не найдено. Последнее обновление: {latest_update}")
            print("   Мониторинг не требуется. Выход.")
            return
        
        if latest_update:
            print(f"✅ Обнаружены новые изменения! Последнее обновление: {latest_update}")
            if last_check:
                print(f"   Предыдущая проверка: {last_check}")
            else:
                print("   Первая проверка")

        # ── Валидируем Telegram бота ───────────────────────────
        print()
        print("1️⃣  Проверка Telegram бота...")
        if not self.telegram.validate():
            print("❌ Неверный токен Telegram. Выход.")
            sys.exit(1)

        # ── Получаем репозитории ───────────────────────────────
        print()
        print("2️⃣  Получение репозиториев...")
        repositories = self.github.get_repositories()

        if not repositories:
            print("⚠️  Репозитории не найдены или ошибка API")
            self.telegram.send(
                f"⚠️ <b>GitHub Monitor</b>\n"
                f"Репозитории не найдены для <code>{escape_html(self.username)}</code>\n"
                f"Проверьте права G_TOKEN."
            )
            sys.exit(0)

        print(f"   Найдено репозиториев: {len(repositories)}")

        # ── Собираем детальную информацию ─────────────────────
        print()
        print("3️⃣  Сбор информации о репозиториях...")
        repos_data = []
        has_real_changes = False

        for i, repo in enumerate(repositories, 1):
            name = repo["name"]
            print(f"   [{i:2d}/{len(repositories)}] {name}", end="", flush=True)

            # Загружаем предыдущее состояние
            old_state = load_repository_state(self.username, name)
            
            # Базовые данные из списка (без доп. запроса)
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
            }

            # Коммиты
            commits = self.github.get_recent_commits(name, count=MAX_COMMITS)
            info["recent_commits"] = commits
            time.sleep(API_DELAY)

            # PRs
            prs = self.github.get_open_prs(name, count=MAX_PRS)
            info["open_prs"] = prs
            time.sleep(API_DELAY)

            # Релизы
            releases = self.github.get_releases(name, count=MAX_RELEASES)
            info["releases"] = releases
            time.sleep(API_DELAY)

            # Workflow runs
            workflows = self.github.get_workflow_runs(name, count=MAX_WORKFLOWS)
            info["workflow_runs"] = workflows
            time.sleep(API_DELAY)

            # Проверяем реальные изменения
            old_commits = old_state.get("recent_commits", [])
            has_changes = detect_content_changes(old_commits, commits)
            
            if has_changes:
                has_real_changes = True
                print(" 🔄")
                # Сохраняем новое состояние
                save_repository_state(self.username, name, {
                    "recent_commits": commits,
                    "last_check": datetime.now(timezone.utc).isoformat()
                })
            else:
                print(" ⏸️")  # Нет изменений

            flag = ""
            if commits: flag += " 📝"
            if prs:     flag += f" 🔄{len(prs)}"
            if releases: flag += f" 🚀{len(releases)}"
            if workflows: flag += f" ⚙️{len(workflows)}"
            if has_changes: flag += " ✨"  # Новые изменения
            print(flag)

            repos_data.append(info)

        if not has_real_changes:
            print()
            print("⚠️  Реальных изменений в содержимом не найдено.")
            print("   Мониторинг не требуется. Выход.")
            return

        # Сортируем по свежести
        repos_data.sort(
            key=lambda x: x.get("pushed_at", ""),
            reverse=True,
        )

        # ── Формируем и отправляем сообщение ──────────────────
        print()
        print("4️⃣  Формирование сообщения...")
        message = MessageBuilder.build(self.username, repos_data)
        print(f"   Длина сообщения: {len(message)} символов")

        print()
        print("5️⃣  Отправка в Telegram...")
        success = self.telegram.send(message)

        print()
        if success:
            print("✅ Мониторинг успешно завершен")
            # Сохраняем дату последней успешной проверки
            if latest_update:
                save_last_check_date(latest_update)
        else:
            print("❌ Мониторинг завершен с ошибками отправки")
            # Пробуем отправить аварийное сообщение
            self.telegram.send(
                "⚠️ <b>GitHub Monitor</b>\n"
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
