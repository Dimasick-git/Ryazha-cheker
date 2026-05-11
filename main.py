#!/usr/bin/env python3
"""
GitHub Repository Monitor
Отслеживает репозитории пользователя и отправляет уведомления в Telegram
"""

import os
import sys
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional


# ──────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────
TELEGRAM_MAX_LENGTH = 4096
GITHUB_API         = "https://api.github.com"
TELEGRAM_API       = "https://api.telegram.org"

# Сколько репо/коммитов показывать
MAX_ACTIVE_REPOS   = 5
MAX_COMMITS        = 2
MAX_PRS            = 3
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
                print(f"⚠️  Rate limit hit. Waiting {wait}s...")
                time.sleep(wait)
                resp = self.session.get(url, params=params, timeout=20)

            if resp.status_code == 200:
                return resp.json()

            print(f"❌ GitHub API {resp.status_code}: {url}")
            print(f"   Response: {resp.text[:200]}")
            return None

        except requests.exceptions.Timeout:
            print(f"⏱️  Timeout: {url}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"🌐 Network error: {e}")
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
                result.append({
                    "sha":     c["sha"][:7],
                    "message": truncate(c["commit"]["message"].split("\n")[0], 60),
                    "author":  truncate(c["commit"]["author"]["name"], 25),
                    "date":    c["commit"]["author"]["date"],
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


# ──────────────────────────────────────────────────────────────
# TELEGRAM CLIENT
# ──────────────────────────────────────────────────────────────
class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
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
                print(f"✅ Bot: @{bot['username']} (id={bot['id']})")
                return True
            print(f"❌ getMe failed: {data.get('description')}")
            return False
        except Exception as e:
            print(f"❌ getMe exception: {e}")
            return False

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Отправляет сообщение.
        Автоматически разбивает на части если > 4096 символов.
        """
        parts = self._split(text)
        print(f"📤 Sending {len(parts)} part(s) to chat {self.chat_id}")

        all_ok = True
        for i, part in enumerate(parts, 1):
            ok = self._send_part(part, parse_mode)
            if ok:
                print(f"  ✅ Part {i}/{len(parts)} sent ({len(part)} chars)")
            else:
                print(f"  ❌ Part {i}/{len(parts)} failed — trying plain text")
                # Fallback: убираем HTML теги и шлём plain
                plain = self._strip_html(part)
                ok    = self._send_part(plain, parse_mode=None)
                if ok:
                    print(f"  ✅ Part {i}/{len(parts)} sent as plain text")
                else:
                    print(f"  ❌ Part {i}/{len(parts)} failed completely")
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

            desc = data.get("description", "unknown error")
            print(f"  ⚠️  Telegram error: {desc}")

            # Подсказки
            if "chat not found" in desc.lower():
                print("  💡 Fix: send /start to the bot first, or check CHAT_ID")
            elif "blocked" in desc.lower():
                print("  💡 Fix: user blocked the bot — unblock it in Telegram")
            elif "parse" in desc.lower():
                print("  💡 Fix: HTML parse error — will retry as plain text")

            return False

        except requests.exceptions.Timeout:
            print("  ⏱️  Telegram timeout")
            return False
        except Exception as e:
            print(f"  ❌ Telegram exception: {e}")
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
            "🔍 <b>GitHub Repository Monitor</b>",
            f"👤 User: <code>{escape_html(username)}</code>",
            f"📅 {now}",
            "",
        ]

        # ── Сводка ─────────────────────────────────────────────
        total_repos = len(repos_data)
        total_stars = sum(r.get("stars", 0) for r in repos_data)
        total_forks = sum(r.get("forks", 0) for r in repos_data)

        lines += [
            "📊 <b>Summary</b>",
            f"• Repositories: <b>{total_repos}</b>",
            f"• ⭐ Stars: <b>{total_stars}</b>",
            f"• 🍴 Forks: <b>{total_forks}</b>",
            "",
        ]

        # ── Активные репо ───────────────────────────────────────
        active = [r for r in repos_data if r.get("recent_commits")][:MAX_ACTIVE_REPOS]

        if active:
            lines.append("🚀 <b>Recently Active Repositories</b>")
            lines.append("")

            for repo in active:
                name  = escape_html(repo["name"])
                lang  = escape_html(repo.get("language") or "—")
                desc  = repo.get("description") or ""
                stars = repo.get("stars", 0)
                forks = repo.get("forks", 0)

                lines.append(f"📁 <b>{name}</b>")

                if desc and desc != "No description":
                    lines.append(f"📝 {escape_html(truncate(desc, 80))}")

                lines.append(
                    f"⭐ {stars}  🍴 {forks}  💻 {lang}"
                    + ("  🔒 Private" if repo.get("private") else "")
                )

                # Коммиты
                commits = repo.get("recent_commits", [])[:MAX_COMMITS]
                if commits:
                    lines.append("📝 Recent commits:")
                    for c in commits:
                        sha  = escape_html(c["sha"])
                        msg  = escape_html(c["message"])
                        auth = escape_html(c["author"])
                        date = fmt_date(c["date"])
                        lines.append(f"  • <code>{sha}</code> {msg}")
                        lines.append(f"    by {auth} · {date}")

                # Open PRs
                prs = repo.get("open_prs", [])
                if prs:
                    lines.append(f"🔄 Open PRs: {len(prs)}")

                lines.append("")

        # ── Репо с открытыми PRs ────────────────────────────────
        repos_with_prs = [r for r in repos_data if r.get("open_prs")][:MAX_REPOS_WITH_PRS]

        if repos_with_prs:
            lines.append("🔄 <b>Open Pull Requests</b>")
            lines.append("")

            for repo in repos_with_prs:
                name = escape_html(repo["name"])
                prs  = repo["open_prs"][:MAX_PRS]
                lines.append(f"📁 <b>{name}</b> — {len(prs)} open PR(s):")

                for pr in prs:
                    num    = pr["number"]
                    title  = escape_html(pr["title"])
                    author = escape_html(pr["author"])
                    date   = fmt_date(pr["date"])
                    lines.append(f"  • #{num} {title}")
                    lines.append(f"    by {author} · {date}")

                lines.append("")

        # ── Нет активности ──────────────────────────────────────
        if not active and not repos_with_prs:
            lines.append("😴 No recent activity found.")
            lines.append("")

        lines.append("—")
        lines.append(
            f"<i>GitHub Monitor · Run #{os.getenv('GITHUB_RUN_NUMBER', '?')}</i>"
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
            print(f"❌ Missing environment variables: {', '.join(missing)}")
            print("   Set them in: Settings → Secrets and variables → Actions")
            sys.exit(1)

        self.github   = GitHubClient(github_token, self.username)
        self.telegram = TelegramClient(telegram_token, self.chat_id)

    def run(self):
        print(f"🚀 Starting GitHub Monitor for: {self.username}")
        print(f"   Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print()

        # ── Валидируем Telegram бота ───────────────────────────
        print("1️⃣  Validating Telegram bot...")
        if not self.telegram.validate():
            print("❌ Invalid Telegram token. Exiting.")
            sys.exit(1)

        # ── Получаем репозитории ───────────────────────────────
        print()
        print("2️⃣  Fetching repositories...")
        repositories = self.github.get_repositories()

        if not repositories:
            print("⚠️  No repositories found or API error")
            self.telegram.send(
                f"⚠️ <b>GitHub Monitor</b>\n"
                f"No repositories found for <code>{escape_html(self.username)}</code>\n"
                f"Check G_TOKEN permissions."
            )
            sys.exit(0)

        print(f"   Found {len(repositories)} repositories")

        # ── Собираем детальную информацию ─────────────────────
        print()
        print("3️⃣  Collecting repository details...")
        repos_data = []

        for i, repo in enumerate(repositories, 1):
            name = repo["name"]
            print(f"   [{i:2d}/{len(repositories)}] {name}", end="", flush=True)

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
            }

            # Коммиты
            commits = self.github.get_recent_commits(name, count=3)
            info["recent_commits"] = commits
            time.sleep(API_DELAY)

            # PRs
            prs = self.github.get_open_prs(name, count=3)
            info["open_prs"] = prs
            time.sleep(API_DELAY)

            flag = ""
            if commits: flag += " 📝"
            if prs:     flag += f" 🔄{len(prs)}"
            print(flag)

            repos_data.append(info)

        # Сортируем по свежести
        repos_data.sort(
            key=lambda x: x.get("pushed_at", ""),
            reverse=True,
        )

        # ── Формируем и отправляем сообщение ──────────────────
        print()
        print("4️⃣  Building message...")
        message = MessageBuilder.build(self.username, repos_data)
        print(f"   Message length: {len(message)} chars")

        print()
        print("5️⃣  Sending to Telegram...")
        success = self.telegram.send(message)

        print()
        if success:
            print("✅ Monitor completed successfully")
        else:
            print("❌ Monitor completed with send errors")
            # Пробуем отправить аварийное сообщение
            self.telegram.send(
                "⚠️ <b>GitHub Monitor</b>\n"
                "Report was generated but some parts failed to send.\n"
                "Check Actions logs for details."
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
