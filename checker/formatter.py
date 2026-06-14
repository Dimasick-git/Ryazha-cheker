"""Message formatting utilities: HTML helpers, emoji icons, and MessageBuilder."""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote


# ──────────────────────────────────────────────────────────────
# WORKFLOW ICONS — emoji only, no text tags
# ──────────────────────────────────────────────────────────────
WORKFLOW_ICONS: Dict[Optional[str], str] = {
    "success":     "✅",
    "failure":     "❌",
    "cancelled":   "🚫",
    "skipped":     "⏭",
    "in_progress": "⏳",
    "queued":      "⏳",
    "waiting":     "⏳",
    "running":     "⏳",
    None:          "❓",
}

# Star counts that trigger a milestone notification
STAR_MILESTONES = {5, 10, 25, 50, 100, 250, 500, 1000}

# ──────────────────────────────────────────────────────────────
# LANGUAGE ICONS
# ──────────────────────────────────────────────────────────────
LANGUAGE_ICONS: Dict[str, str] = {
    "Python":      "🐍",
    "JavaScript":  "🟨",
    "TypeScript":  "🔷",
    "C":           "🔵",
    "C++":         "⚙️",
    "C#":          "🟣",
    "Java":        "☕",
    "Go":          "🐹",
    "Rust":        "🦀",
    "Ruby":        "💎",
    "PHP":         "🐘",
    "Swift":       "🍎",
    "Kotlin":      "🎯",
    "HTML":        "🌐",
    "CSS":         "🎨",
    "Shell":       "🐚",
    "Dockerfile":  "🐋",
    "Lua":         "🌙",
    "R":           "📊",
    "Scala":       "♾️",
    "Haskell":     "λ",
    "Elixir":      "💧",
    "Dart":        "🎯",
    "MATLAB":      "🔢",
    "Assembly":    "⚡",
    "Makefile":    "🔧",
    "CMake":       "🔧",
    "Nix":         "❄️",
    "Vim script":  "📝",
    "Unknown":     "❓",
}


def language_icon(lang: Optional[str]) -> str:
    """Return the emoji icon for a programming language."""
    return LANGUAGE_ICONS.get(lang or "Unknown", "📁")


def workflow_icon(conclusion: Optional[str], status: str) -> str:
    """Return the appropriate emoji for a workflow run."""
    if status in ("in_progress", "queued", "waiting"):
        return WORKFLOW_ICONS.get(status, "⏳")
    return WORKFLOW_ICONS.get(conclusion, WORKFLOW_ICONS[None])


# ──────────────────────────────────────────────────────────────
# HTML HELPERS
# ──────────────────────────────────────────────────────────────

def classify_commit(message: str) -> str:
    """Detect conventional-commit type and return an emoji prefix."""
    lower = message.lower().lstrip()
    _PREFIXES = [
        (("feat(", "feat:"),         "✨"),
        (("fix(", "fix:"),           "🐛"),
        (("perf(", "perf:"),         "⚡"),
        (("refactor(", "refactor:"), "♻️"),
        (("docs(", "docs:"),         "📝"),
        (("chore(", "chore:"),       "🔧"),
        (("build(", "build:"),       "🏗️"),
        (("ci(", "ci:"),             "⚙️"),
        (("test(", "test:"),         "🧪"),
        (("release(", "release:"),   "🚀"),
        (("style(", "style:"),       "💄"),
    ]
    for prefixes, emoji in _PREFIXES:
        if any(lower.startswith(p) for p in prefixes):
            return emoji
    # Heuristic fallback for non-conventional commits
    for kw, emoji in (
        ("add", "✨"), ("new", "✨"), ("добав", "✨"), ("введ", "✨"),
        ("fix", "🐛"), ("bug", "🐛"), ("исправ", "🐛"), ("патч", "🐛"),
        ("update", "📦"), ("bump", "📦"), ("обновл", "📦"), ("upgrade", "📦"),
        ("remove", "🗑️"), ("delete", "🗑️"), ("удал", "🗑️"),
        ("refactor", "♻️"), ("clean", "♻️"), ("рефакт", "♻️"),
        ("optim", "⚡"), ("perf", "⚡"), ("speed", "⚡"), ("оптим", "⚡"),
        ("release", "🚀"), ("version", "🚀"), ("релиз", "🚀"),
        ("docs", "📝"), ("readme", "📝"), ("документ", "📝"),
        ("ci", "⚙️"), ("workflow", "⚙️"), ("actions", "⚙️"),
    ):
        if kw in lower:
            return emoji
    return "🔹"


def escape_html(text: str) -> str:
    """Escape special characters for Telegram HTML parse_mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def html_code_block(text: str) -> str:
    """Return a proper <pre> code block for Telegram HTML parse_mode.

    Telegram HTML mode does not process Markdown ``` syntax — it renders as
    plain text. The supported multi-line code format is the <pre> tag.
    """
    return f"<pre>{escape_html(text)}</pre>"


def fmt_date(iso: str) -> str:
    """ISO 8601 → human-readable UTC date string."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def truncate(text: str, max_len: int = 60) -> str:
    """Truncate a string with an ellipsis."""
    text = str(text)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def split_message(text: str, limit: int) -> List[str]:
    """Split text into chunks of at most `limit` characters, cutting on newlines."""
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
        if not text:
            break
    return parts


def build_github_file_url(commit_url: str, filename: str, file_info: Dict[str, Any]) -> str:
    """Build a working link to a file changed by a specific commit."""
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


# ──────────────────────────────────────────────────────────────
# MESSAGE BUILDER
# ──────────────────────────────────────────────────────────────

class MessageBuilder:
    """Builds an HTML message for Telegram."""

    @staticmethod
    def build(username: str, repos_data: List[Dict]) -> "tuple[str, Optional[Dict]]":
        lines: List[str] = []
        buttons: List[List[Dict]] = []

        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        lines += [
            f"<b>GITHUB MONITOR</b> [<code>{escape_html(username)}</code>] · <i>{now}</i>",
            "",
        ]

        # Include all repos with any change (commits, releases, stars, milestones, PRs),
        # not only those with commits — repos with a new release or star milestone
        # but zero commits would otherwise be silently dropped.
        _limit = max(1, int(os.environ.get("MAX_DISPLAY_REPOS", "5")))
        active_repos = repos_data[:_limit]

        if not active_repos:
            return "<i>status: idle. no changes detected.</i>", None

        total_commits  = sum(len(r.get("recent_commits", [])) for r in active_repos)
        total_releases = sum(len(r.get("releases", []))       for r in active_repos)
        total_prs      = sum(len(r.get("open_prs", []))       for r in active_repos)
        total_issues   = sum(r.get("open_issues", 0)          for r in active_repos)

        summary_parts = [f"repos=<b>{len(active_repos)}</b>", f"commits=<b>{total_commits}</b>"]
        if total_releases:
            summary_parts.append(f"releases=<b>{total_releases}</b>")
        if total_prs:
            summary_parts.append(f"prs=<b>{total_prs}</b>")
        if total_issues:
            summary_parts.append(f"issues=<b>{total_issues}</b>")
        lines.append(f"<i>{' · '.join(summary_parts)}</i>\n")

        for repo in active_repos:
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
            lang_icon = language_icon(repo.get("language"))
            lines.append(f"▸ <b>[{name}]</b> {lang_icon} <i>{lang}</i>{meta_str}")
            buttons.append([{"text": f"open: {repo['name']}", "url": repo_url}])

            # Milestone notification
            milestones = repo.get("star_milestones", [])
            for m in milestones:
                lines.append(f"  🌟 <b>MILESTONE: {m} звёзд!</b>")

            # AI summary (if available)
            ai_summary = repo.get("ai_summary")
            if ai_summary:
                lines.append(f"  💡 <i>{escape_html(ai_summary)}</i>")

            # Commits — no sub-heading
            commits = repo.get("recent_commits", [])[:2]
            for c in commits:
                sha = escape_html(c["sha"][:7])
                raw_msg = c["message"]
                msg = escape_html(raw_msg)
                auth = escape_html(c["author"])
                date = escape_html(fmt_date(c["date"]))
                url = escape_html(c["html_url"])
                commit_icon = classify_commit(raw_msg)

                lines.append(f"  {commit_icon} <a href=\"{url}\"><code>{sha}</code></a> {msg}")
                lines.append(f"  <i>by {auth} @ {date}</i>")

                files = c.get("files", [])
                if files:
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

            # Releases
            releases = repo.get("releases", [])
            if releases:
                r = releases[0]
                tag    = escape_html(r["tag"])
                rname  = escape_html(r["name"])
                rurl   = escape_html(r["html_url"])
                author = escape_html(r.get("author", ""))
                date   = fmt_date(r.get("published_at", ""))
                author_str = f" by {author}" if author and author != "Unknown" else ""
                extra = f" <i>(+{len(releases) - 1} more)</i>" if len(releases) > 1 else ""
                lines.append(
                    f"🚀 <b>RELEASE:</b> <a href=\"{rurl}\">{tag}</a> — {rname}"
                    f"<i>{author_str} · {date}</i>{extra}"
                )
                release_ai = r.get("ai_summary")
                if release_ai:
                    lines.append(f"  📋 <i>{escape_html(release_ai)}</i>")

            # PRs
            prs = repo.get("open_prs", [])
            if prs:
                pr_parts = [f"#{p['number']} {escape_html(p['title'])}" for p in prs[:2]]
                lines.append("<b>PR:</b> " + " | ".join(pr_parts))
                pr_ai = repo.get("pr_ai_summary")
                if pr_ai:
                    lines.append(f"  🔀 <i>{escape_html(pr_ai)}</i>")

            # New issues
            new_issues = repo.get("new_issues", [])
            if new_issues:
                issue_parts = [
                    f"<a href='{escape_html(i['url'])}'>#{i['number']}</a> {escape_html(i['title'])}"
                    for i in new_issues[:2]
                ]
                lines.append("🐛 <b>Issues:</b> " + " | ".join(issue_parts))

            lines.append("────────────────────")

        reply_markup = {"inline_keyboard": buttons} if buttons else None
        return "\n".join(lines), reply_markup

    @staticmethod
    def build_trending(username: str, repos: List[Dict], all_states: Dict) -> str:
        """Build a Trending Report message (top-10 repos by activity score).

        ``repos`` must already be sorted by activity score (descending) and
        contain the raw GitHub repo dicts.  ``all_states`` is the persisted
        state dict used to compute star deltas.
        """
        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        lines = [
            f"<b>TRENDING REPORT</b> [<code>{escape_html(username)}</code>] · <i>{now}</i>",
            "",
            "<i>Top 10 repositories by activity (stars + pushes + issues)</i>",
            "",
        ]

        for i, r in enumerate(repos[:10], 1):
            name      = r["name"]
            rname     = escape_html(name)
            stars     = r.get("stargazers_count", 0)
            issues    = r.get("open_issues_count", 0)
            lang      = r.get("language") or "Unknown"
            lang_icon = language_icon(lang)
            pushed    = (r.get("pushed_at") or "")[:10]
            url       = f"https://github.com/{username}/{name}"

            old_stars = all_states.get(name, {}).get("stars", stars)
            delta     = stars - old_stars
            delta_str = f" <b>(+{delta})</b>" if delta > 0 else ""

            lines.append(
                f"<b>{i}.</b> <a href=\"{url}\">{rname}</a>\n"
                f"   ★ {stars}{delta_str} · {lang_icon} {escape_html(lang)}"
                f" · 📅 {pushed} · 🐛 {issues}"
            )

        return "\n".join(lines)

    @staticmethod
    def build_weekly(username: str, repos: List[Dict], all_states: Dict, ai_insights: Optional[str] = None) -> str:
        """Build a comprehensive Weekly Digest message.

        ``repos`` is the full raw list of GitHub repo dicts.
        ``all_states`` is the persisted state dict.
        ``ai_insights`` is an optional AI-generated paragraph added after the stats header.
        """
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        now_str = now.strftime("%d.%m.%Y %H:%M UTC")
        week_ago = now - timedelta(days=7)

        total_repos  = len(repos)
        total_stars  = sum(r.get("stargazers_count", 0) for r in repos)
        total_forks  = sum(r.get("forks_count", 0)      for r in repos)
        total_issues = sum(r.get("open_issues_count", 0) for r in repos)

        lines = [
            f"<b>WEEKLY DIGEST</b> [<code>{escape_html(username)}</code>] · <i>{now_str}</i>",
            "",
            "<b>Overall stats</b>",
            (
                f"  Repos: <b>{total_repos}</b> · "
                f"Stars: <b>{total_stars}</b> · "
                f"Forks: <b>{total_forks}</b> · "
                f"Issues: <b>{total_issues}</b>"
            ),
            "",
        ]

        # AI weekly insights (if available)
        if ai_insights:
            lines += [
                "<b>AI-аналитика недели:</b>",
                f"  💡 <i>{escape_html(ai_insights)}</i>",
                "",
            ]

        # ── Top 5 most active (by pushed_at) ──────────────────────
        lines.append("<b>Top 5 most active this week</b>")
        most_active = sorted(repos, key=lambda r: r.get("pushed_at") or "", reverse=True)[:5]
        for i, r in enumerate(most_active, 1):
            name      = r["name"]
            rname     = escape_html(name)
            pushed    = fmt_date(r.get("pushed_at") or "")
            lang      = r.get("language") or "Unknown"
            lang_icon = language_icon(lang)
            url       = f"https://github.com/{username}/{name}"
            lines.append(f"  {i}. <a href=\"{url}\">{rname}</a> — {lang_icon} · 📅 {pushed}")

        lines.append("")

        # ── Top 5 by stars ────────────────────────────────────────
        lines.append("<b>Top 5 by stars</b>")
        top_stars = sorted(repos, key=lambda r: r.get("stargazers_count", 0), reverse=True)[:5]
        for i, r in enumerate(top_stars, 1):
            name      = r["name"]
            rname     = escape_html(name)
            stars     = r.get("stargazers_count", 0)
            lang      = r.get("language") or "Unknown"
            lang_icon = language_icon(lang)
            url       = f"https://github.com/{username}/{name}"

            old_stars = all_states.get(name, {}).get("stars", stars)
            delta     = stars - old_stars
            delta_str = f" <b>(+{delta})</b>" if delta > 0 else ""

            lines.append(
                f"  {i}. <a href=\"{url}\">{rname}</a> — ★ {stars}{delta_str} · {lang_icon}"
            )

        lines.append("")

        # ── Repos that crossed a star milestone ───────────────────
        milestone_repos = []
        for r in repos:
            name      = r["name"]
            stars     = r.get("stargazers_count", 0)
            old_stars = all_states.get(name, {}).get("stars", stars)
            crossed   = sorted(m for m in STAR_MILESTONES if old_stars < m <= stars)
            if crossed:
                milestone_repos.append((r, crossed))

        if milestone_repos:
            lines.append("<b>Star milestones reached</b>")
            for r, milestones in milestone_repos:
                name  = r["name"]
                rname = escape_html(name)
                url   = f"https://github.com/{username}/{name}"
                ms_str = ", ".join(str(m) for m in milestones)
                lines.append(f"  🌟 <a href=\"{url}\">{rname}</a> — <b>{ms_str}</b> stars!")
            lines.append("")

        # ── New repos this week (created_at within last 7 days) ───
        new_repos = []
        for r in repos:
            created_raw = r.get("created_at") or ""
            if not created_raw:
                continue
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                if created_dt >= week_ago.replace(tzinfo=timezone.utc):
                    new_repos.append(r)
            except Exception:
                continue

        if new_repos:
            lines.append("<b>New this week</b>")
            for r in new_repos:
                name      = r["name"]
                rname     = escape_html(name)
                lang      = r.get("language") or "Unknown"
                lang_icon = language_icon(lang)
                created   = (r.get("created_at") or "")[:10]
                url       = f"https://github.com/{username}/{name}"
                lines.append(
                    f"  + <a href=\"{url}\">{rname}</a> — {lang_icon} · created {created}"
                )
            lines.append("")

        lines.append(f"<i>Generated {now_str}</i>")
        return "\n".join(lines)
