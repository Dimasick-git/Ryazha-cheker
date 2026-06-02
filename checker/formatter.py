"""Message formatting utilities: HTML helpers, emoji icons, and MessageBuilder."""

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


def workflow_icon(conclusion: Optional[str], status: str) -> str:
    """Return the appropriate emoji for a workflow run."""
    if status in ("in_progress", "queued", "waiting"):
        return WORKFLOW_ICONS.get(status, "⏳")
    return WORKFLOW_ICONS.get(conclusion, WORKFLOW_ICONS[None])


# ──────────────────────────────────────────────────────────────
# HTML HELPERS
# ──────────────────────────────────────────────────────────────

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

            # Milestone notification
            milestones = repo.get("star_milestones", [])
            for m in milestones:
                lines.append(f"  🌟 <b>MILESTONE: {m} звёзд!</b>")

            # Commits — no sub-heading
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
                tag = escape_html(r["tag"])
                rname = escape_html(r["name"])
                rurl = escape_html(r["html_url"])
                lines.append(f"<b>RELEASE:</b> <a href=\"{rurl}\">{tag}</a> — {rname}")

            # PRs
            prs = repo.get("open_prs", [])
            if prs:
                pr_parts = [f"#{p['number']} {escape_html(p['title'])}" for p in prs[:2]]
                lines.append("<b>PR:</b> " + " | ".join(pr_parts))

            lines.append("────────────────────")

        reply_markup = {"inline_keyboard": buttons} if buttons else None
        return "\n".join(lines), reply_markup
