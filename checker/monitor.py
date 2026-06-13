"""GitHubMonitor: orchestration logic — coordinates API calls, state, and Telegram."""

import argparse
import asyncio
import fnmatch
import hashlib
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

from .diffing import (
    compute_deltas,
    filter_new_commits,
    filter_new_prs,
    filter_new_workflows,
    build_new_state,
)
from .formatter import escape_html, language_icon, MessageBuilder
from .github_client import (
    GitHubClient,
    API_DELAY,
    MAX_COMMITS,
    MAX_PRS,
    MAX_RELEASES,
    MAX_WORKFLOWS,
    MAX_KNOWN_SHAS,
)
from .state import (
    load_all_repository_states,
    save_all_repository_states,
    load_last_check_date,
    load_last_message_hash,
    save_last_check_date,
    save_last_message_hash,
    _update_check_state,
)
from .telegram_client import TelegramClient
from .discord_client import DiscordWebhookClient
from .formatter import fmt_date, STAR_MILESTONES


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the monitor."""
    parser = argparse.ArgumentParser(description="GitHub Repository Monitor")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print message without sending to Telegram",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass early-exit and dedup checks",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Send compact digest of all repositories",
    )
    parser.add_argument(
        "--trending",
        action="store_true",
        help="Send top-10 repos ranked by activity score",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Send comprehensive weekly digest",
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        default=None,
        help="Only report activity after this date (ISO format, e.g. 2024-01-15)",
    )
    # parse_known_args so that unknown flags (e.g. from GitHub Actions) don't fail
    args, _ = parser.parse_known_args()
    return args


class GitHubMonitor:
    def __init__(self):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        args = _parse_args()

        github_token   = os.getenv("G_TOKEN", "").strip()
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.username  = os.getenv("G_USERNAME", "").strip()

        # Comma-separated repo names/glob patterns to skip.
        skip_raw = os.getenv("SKIP_REPOS", "").strip()
        self.skip_patterns: list = []
        for pat in (p.strip() for p in skip_raw.split(",") if p.strip()):
            try:
                fnmatch.translate(pat)  # validates pattern syntax
                self.skip_patterns.append(pat)
            except Exception as exc:
                log.warning("SKIP_REPOS: invalid glob pattern %r — ignored (%s)", pat, exc)

        self.dry_run = args.dry_run or os.getenv("DRY_RUN", "").lower() in ("1", "true")
        self.summary_mode = args.summary or os.getenv("SUMMARY_MODE", "").lower() in ("1", "true")
        self.trending_mode = args.trending or os.getenv("TRENDING_MODE", "").lower() in ("1", "true")
        self.weekly_mode = args.weekly or os.getenv("WEEKLY_MODE", "").lower() in ("1", "true")

        # --since: only report activity after this date
        since_raw = args.since or os.getenv("SINCE_DATE", "").strip()
        self.since_date: Optional[datetime] = None
        if since_raw:
            try:
                self.since_date = datetime.fromisoformat(since_raw).replace(tzinfo=timezone.utc)
                log.info("--since filter active: only activity after %s", since_raw)
            except ValueError:
                log.warning("--since value '%s' is not a valid YYYY-MM-DD date; ignoring.", since_raw)

        missing = []
        if not github_token:   missing.append("G_TOKEN")
        if not self.username:  missing.append("G_USERNAME")
        if not self.dry_run:
            if not telegram_token: missing.append("TELEGRAM_BOT_TOKEN")
            if not self.chat_id:   missing.append("TELEGRAM_CHAT_ID")

        if missing:
            log.critical("Missing required env vars: %s", ", ".join(missing))
            log.critical("Configure them: Settings → Secrets and variables → Actions")
            sys.exit(1)

        self.force_send = (
            args.force
            or os.getenv("FORCE_SEND", "").lower() in ("1", "true")
        )

        if self.dry_run:
            log.info("DRY-RUN: no Telegram delivery.")
        if self.force_send:
            log.info("FORCE: bypassing early-exit and dedup checks.")

        self.github = GitHubClient(github_token, self.username)
        self.telegram = TelegramClient(
            telegram_token or "dry_run_placeholder",
            self.chat_id or "0",
            topic_id=int(os.getenv("TELEGRAM_TOPIC_ID") or "0") or None,
        )

        # Optional Discord webhook — set DISCORD_WEBHOOK_URL to enable
        discord_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        self.discord: Optional[DiscordWebhookClient] = (
            DiscordWebhookClient(discord_url) if discord_url else None
        )
        if self.discord:
            log.info("Discord webhook enabled.")

    def _is_after_since(self, date_str: str) -> bool:
        """Return True if ``date_str`` is after self.since_date (or no filter set)."""
        if not self.since_date or not date_str:
            return True
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt >= self.since_date
        except Exception:
            return True

    async def _fetch_and_filter_repos(self) -> List[Dict]:
        """Fetch all repositories and apply skip-pattern filtering."""
        repositories = await self.github.get_repositories()
        if not repositories:
            return []
        return [
            r for r in repositories
            if not any(fnmatch.fnmatch(r["name"], p) for p in self.skip_patterns)
        ]

    def _prune_deleted_repos(self, all_states: Dict, live_repo_names: set) -> int:
        """Remove state entries for repos that no longer exist. Returns pruned count."""
        stale = [name for name in list(all_states) if name not in live_repo_names]
        for name in stale:
            del all_states[name]
        if stale:
            log.info("Pruned %d stale repo(s) from state: %s", len(stale), ", ".join(stale))
        return len(stale)

    async def _validate_and_send_async(self, message: str, label: str = "message") -> bool:
        """Validate Telegram token, send message, and optionally send to Discord."""
        if not await self.telegram.validate_async():
            log.critical("Invalid Telegram token. Exiting.")
            sys.exit(1)
        ok = await self.telegram.send_async(message)
        if ok:
            log.info("%s sent successfully.", label)
        if self.discord:
            discord_ok = await self.discord.send(message)
            if discord_ok:
                log.info("%s sent to Discord.", label)
            else:
                log.warning("%s Discord delivery failed (Telegram succeeded).", label)
        return ok

    def _update_star_states(
        self, repositories: List[Dict], all_states: Dict
    ) -> None:
        """Persist star/fork baselines so future runs can compute deltas."""
        for r in repositories:
            rname = r["name"]
            if rname not in all_states:
                all_states[rname] = {}
            all_states[rname]["stars"] = r.get("stargazers_count", 0)
            all_states[rname]["forks"] = r.get("forks_count", 0)
        save_all_repository_states(self.username, all_states)

    async def _collect_repo_data(self, repo: Dict, old_state: Dict) -> Dict[str, Any]:
        """Perform all per-repo API calls and return collected data + new state."""
        name = repo["name"]

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

        # Star / fork deltas via diffing module
        star_delta, fork_delta, crossed = compute_deltas(info, old_state)
        info["star_delta"] = star_delta
        info["fork_delta"] = fork_delta
        if star_delta:
            log.info("[%s] stars +%d", name, star_delta)
        if fork_delta:
            log.info("[%s] forks +%d", name, fork_delta)

        info["star_milestones"] = crossed
        for m in crossed:
            log.info("[%s] MILESTONE: %d stars!", name, m)

        # Commits
        all_commits = await self.github.list_commits(name, count=MAX_COMMITS)
        await asyncio.sleep(API_DELAY)

        known_shas = set(old_state.get("known_shas", []))
        new_commits = filter_new_commits(all_commits, known_shas)

        # Apply --since filter on commits
        if self.since_date:
            new_commits = [c for c in new_commits if self._is_after_since(c.get("date", ""))]

        for commit in new_commits:
            commit["files"] = await self.github.get_commit_files(name, commit["sha"])
            await asyncio.sleep(API_DELAY)

        info["recent_commits"] = new_commits

        # AI commit summary
        if new_commits:
            try:
                from .ai_summary import summarize_commits
                ai_text = await summarize_commits(name, new_commits)
                info["ai_summary"] = ai_text
            except Exception as exc:
                log.warning("[%s] AI summary error: %s", name, exc)
                info["ai_summary"] = None
        else:
            info["ai_summary"] = None

        # PRs with deduplication
        prs_raw = await self.github.get_open_prs(name, count=MAX_PRS)
        known_pr_numbers = set(old_state.get("known_pr_numbers", []))
        new_prs = filter_new_prs(prs_raw, known_pr_numbers)

        # Apply --since filter on PRs
        if self.since_date:
            new_prs = [p for p in new_prs if self._is_after_since(p.get("date", ""))]

        info["open_prs"] = new_prs
        await asyncio.sleep(API_DELAY)

        # Issues
        info["open_issues"] = await self.github.get_open_issues_count(
            name,
            open_issues_raw=repo.get("open_issues_count", 0),
            pr_count=len(prs_raw),
        )

        # Releases — only if the tag changed
        known_tag = old_state.get("latest_release_tag")
        releases = await self.github.get_new_releases(name, known_tag)

        # Apply --since filter on releases
        if self.since_date and releases:
            releases = [r for r in releases if self._is_after_since(r.get("published_at", ""))]

        # AI release summary for the latest new release
        if releases:
            try:
                from .ai_summary import summarize_release
                release_summary = await summarize_release(name, releases[0])
                releases[0]["ai_summary"] = release_summary
            except Exception as exc:
                log.warning("[%s] AI release summary error: %s", name, exc)

        info["releases"] = releases
        await asyncio.sleep(API_DELAY)

        # Workflow runs with deduplication
        workflows_raw = await self.github.get_workflow_runs(name, count=MAX_WORKFLOWS)
        known_run_ids = set(old_state.get("known_run_ids", []))
        new_workflows = filter_new_workflows(workflows_raw, known_run_ids)
        info["workflow_runs"] = new_workflows
        await asyncio.sleep(API_DELAY)

        has_real_changes = bool(new_commits or star_delta or fork_delta)
        if new_commits:
            log.info("[%s] new commits: %d", name, len(new_commits))
        elif not star_delta and not fork_delta:
            log.debug("[%s] no changes", name)

        # Build updated state via diffing module
        new_state = build_new_state(
            all_commits=all_commits,
            known_shas=known_shas,
            prs_raw=prs_raw,
            known_pr_numbers=known_pr_numbers,
            workflows_raw=workflows_raw,
            known_run_ids=known_run_ids,
            info=info,
            releases=releases,
            known_tag=known_tag,
        )

        include_in_report = bool(
            new_commits or new_prs or releases
            or star_delta or fork_delta or info.get("star_milestones")
        )

        return {
            "info":              info,
            "new_state":         new_state,
            "has_real_changes":  has_real_changes,
            "include_in_report": include_in_report,
        }

    async def _run_summary(self) -> None:
        """--summary mode: compact digest of all repositories."""
        log.info("SUMMARY MODE: building repository digest...")
        repositories = await self._fetch_and_filter_repos()
        if not repositories:
            log.warning("No repositories found.")
            return

        all_states, _ = load_all_repository_states(self.username)
        total_stars  = sum(r.get("stargazers_count", 0) for r in repositories)
        total_forks  = sum(r.get("forks_count", 0)     for r in repositories)
        total_issues = sum(r.get("open_issues_count", 0) for r in repositories)

        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        lines = [
            f"<b>GITHUB DIGEST</b> [<code>{escape_html(self.username)}</code>] · <i>{now}</i>",
            "",
            (
                f"<b>Repositories:</b> {len(repositories)} · "
                f"<b>Stars:</b> {total_stars} · "
                f"<b>Forks:</b> {total_forks} · "
                f"<b>Issues:</b> {total_issues}"
            ),
            "",
            "<b>Top by stars:</b>",
        ]
        top_by_stars = sorted(
            repositories, key=lambda r: r.get("stargazers_count", 0), reverse=True
        )[:10]
        for i, r in enumerate(top_by_stars, 1):
            rname  = escape_html(r["name"])
            stars  = r.get("stargazers_count", 0)
            lang   = escape_html(r.get("language") or "—")
            pushed = r.get("pushed_at", "")[:10]
            old_st = all_states.get(r["name"], {}).get("stars", stars)
            delta  = stars - old_st
            delta_str = f" <b>(+{delta})</b>" if delta > 0 else ""
            url = f"https://github.com/{self.username}/{r['name']}"
            lines.append(
                f"  {i}. <a href=\"{url}\">{rname}</a> "
                f"— ★{stars}{delta_str} · {lang} · {pushed}"
            )

        lines += ["", "<b>Recent updates:</b>"]
        recent = sorted(repositories, key=lambda r: r.get("pushed_at", ""), reverse=True)[:5]
        for r in recent:
            rname  = escape_html(r["name"])
            pushed = fmt_date(r.get("pushed_at", ""))
            url    = f"https://github.com/{self.username}/{r['name']}"
            lines.append(f"  • <a href=\"{url}\">{rname}</a> — {pushed}")

        message = "\n".join(lines)
        log.info("Digest length: %d chars", len(message))

        if self.dry_run:
            log.info("DRY-RUN digest:\n%s", re.sub(r"<[^>]+>", "", message))
            return

        await self._validate_and_send_async(message, "Digest")
        self._update_star_states(repositories, all_states)

    async def _run_trending(self) -> None:
        """--trending mode: top-10 repos ranked by activity score."""
        log.info("TRENDING MODE: building trending report...")
        repositories = await self._fetch_and_filter_repos()
        if not repositories:
            log.warning("No repositories found.")
            return

        all_states, _ = load_all_repository_states(self.username)

        def _activity_score(r: Dict) -> float:
            stars  = r.get("stargazers_count", 0)
            issues = r.get("open_issues_count", 0)
            pushed = r.get("pushed_at") or ""

            push_bonus = 0.0
            if pushed:
                try:
                    dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - dt).days
                    if age_days <= 7:
                        push_bonus = 200.0
                    elif age_days <= 30:
                        push_bonus = 100.0
                except Exception:
                    pass

            return stars + push_bonus + issues * 0.5

        ranked = sorted(repositories, key=_activity_score, reverse=True)
        message = MessageBuilder.build_trending(self.username, ranked, all_states)
        log.info("Trending message length: %d chars", len(message))

        if self.dry_run:
            log.info("DRY-RUN trending:\n%s", re.sub(r"<[^>]+>", "", message))
            return

        await self._validate_and_send_async(message, "Trending report")
        self._update_star_states(repositories, all_states)

    async def _run_weekly(self) -> None:
        """--weekly mode: comprehensive weekly digest."""
        log.info("WEEKLY MODE: building weekly digest...")
        repositories = await self._fetch_and_filter_repos()
        if not repositories:
            log.warning("No repositories found.")
            return

        all_states, _ = load_all_repository_states(self.username)
        message = MessageBuilder.build_weekly(self.username, repositories, all_states)
        log.info("Weekly digest length: %d chars", len(message))

        if self.dry_run:
            log.info("DRY-RUN weekly:\n%s", re.sub(r"<[^>]+>", "", message))
            return

        await self._validate_and_send_async(message, "Weekly digest")
        self._update_star_states(repositories, all_states)

    async def _run_async(self) -> None:
        """Async main: load state, collect data, format and send."""
        log.info("Starting GitHub monitor for: %s", self.username)
        log.info("Time: %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        if self.skip_patterns:
            log.info("Skip patterns: %s", ", ".join(self.skip_patterns))

        if self.summary_mode:
            await self._run_summary()
            return

        if self.trending_mode:
            await self._run_trending()
            return

        if self.weekly_mode:
            await self._run_weekly()
            return

        _pre_loaded_states, is_cold_start = load_all_repository_states(self.username)

        log.info("Checking for new updates...")
        latest_update = await self.github.get_latest_repo_update()
        last_check = load_last_check_date()

        if not self.force_send and latest_update and last_check and latest_update <= last_check:
            log.info("No new updates found. Last update: %s", latest_update)
            log.info("No monitoring needed. Exiting.")
            return

        if latest_update:
            log.info("Changes detected! Latest update: %s", latest_update)
            log.info("Previous check: %s", last_check or "first run")

        if not self.dry_run:
            log.info("Validating Telegram bot...")
            if not await self.telegram.validate_async():
                log.critical("Invalid Telegram token. Exiting.")
                sys.exit(1)

        log.info("Fetching repositories...")
        repositories = await self.github.get_repositories()

        if not repositories:
            log.error("No repositories found or API error")
            if not self.dry_run:
                err_msg = (
                    f"<b>GitHub Monitor</b>\n"
                    f"No repositories found for <code>{escape_html(self.username)}</code>\n"
                    f"Check G_TOKEN permissions."
                )
                await self.telegram.send_async(err_msg)
                if self.discord:
                    await self.discord.send(err_msg)
            sys.exit(0)

        repositories = [
            r for r in repositories
            if not any(fnmatch.fnmatch(r["name"], p) for p in self.skip_patterns)
        ]
        log.info("Repositories found: %d", len(repositories))
        log.info("Collecting repository information...")

        all_states = _pre_loaded_states
        live_names = {r["name"] for r in repositories}
        self._prune_deleted_repos(all_states, live_names)

        repos_data: List[Dict] = []
        has_real_changes = False

        # Concurrent per-repo async tasks
        total_repos = len(repositories)

        async def _worker(repo: Dict):
            old_state = all_states.get(repo["name"], {})
            # Apply timeout inside the task so cancellation actually reaches _collect_repo_data
            result = await asyncio.wait_for(self._collect_repo_data(repo, old_state), timeout=300)
            return repo["name"], result

        tasks = [asyncio.create_task(_worker(repo)) for repo in repositories]
        results: Dict[str, Dict] = {}
        done_count = 0

        for coro in asyncio.as_completed(tasks):
            done_count += 1
            try:
                rname, result = await coro
                results[rname] = result
                log.debug("[%d/%d] %s — done", done_count, total_repos, rname)
            except asyncio.TimeoutError:
                log.warning("[%d/%d] repo task timeout (>300s)", done_count, total_repos)
            except Exception as exc:
                log.error("[%d/%d] repo task error: %s", done_count, total_repos, exc, exc_info=True)

        for repo in repositories:
            rname = repo["name"]
            if rname not in results:
                continue
            result = results[rname]

            all_states[rname] = result["new_state"]

            if result["has_real_changes"]:
                has_real_changes = True

            if result["include_in_report"]:
                repos_data.append(result["info"])

        if is_cold_start:
            log.info("COLD START: state file was missing or empty.")
            log.info("Recording current state as baseline; next changes will trigger notifications.")
            save_all_repository_states(self.username, all_states)
            if latest_update:
                save_last_check_date(latest_update)
            cold_msg = (
                "🔄 <b>Monitor started (first run or cache reset).</b>\n\n"
                "Current state recorded; future changes will be notified."
            )
            if self.dry_run:
                log.info("DRY-RUN cold-start message: %s", cold_msg)
            else:
                await self.telegram.send_async(cold_msg)
                if self.discord:
                    await self.discord.send(cold_msg)
            return

        if not self.force_send and not has_real_changes:
            log.info("No real content changes detected. No monitoring needed. Exiting.")
            return

        repos_data.sort(key=lambda x: x.get("pushed_at", ""), reverse=True)

        log.info("Building message...")
        message, markup = MessageBuilder.build(self.username, repos_data)
        log.info("Message length: %d chars", len(message))

        if self.dry_run:
            plain = re.sub(r"<[^>]+>", "", message)
            log.info("DRY-RUN message:\n%s\n%s\n%s", "─" * 60, plain, "─" * 60)
            if latest_update:
                save_last_check_date(latest_update)
            return

        msg_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
        last_hash = load_last_message_hash()
        if not self.force_send and last_hash and last_hash == msg_hash:
            log.info("Message unchanged (hash=%s). Skipping send.", msg_hash)
            save_all_repository_states(self.username, all_states)
            if latest_update:
                save_last_check_date(latest_update)
            return

        log.info("Sending to Telegram...")
        success = await self.telegram.send_async(message, reply_markup=markup)

        if self.discord:
            log.info("Sending to Discord...")
            await self.discord.send(message)

        if success:
            log.info("Monitor completed successfully.")
            save_all_repository_states(self.username, all_states)
            updates: Dict[str, Any] = {"last_message_hash": msg_hash}
            if latest_update:
                updates["last_check_date"] = latest_update
            _update_check_state(**updates)
            if latest_update:
                log.info("Last check date updated: %s", latest_update)
        else:
            log.error("Monitor completed with send errors.")
            save_all_repository_states(self.username, all_states)
            if latest_update:
                save_last_check_date(latest_update)
            sys.exit(2)

    def run(self) -> None:
        """Entry point: run the async monitor via asyncio.run()."""
        asyncio.run(self._run_async_with_cleanup())

    async def _run_async_with_cleanup(self) -> None:
        """Wrapper that guarantees the httpx client is closed on exit."""
        try:
            await self._run_async()
        finally:
            await self.github.aclose()
