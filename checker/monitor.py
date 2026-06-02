"""GitHubMonitor: orchestration logic — coordinates API calls, state, and Telegram."""

import concurrent.futures
import fnmatch
import hashlib
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .formatter import escape_html, MessageBuilder
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
    IS_COLD_START,
    load_all_repository_states,
    save_all_repository_states,
    load_last_check_date,
    load_last_message_hash,
    save_last_check_date,
    save_last_message_hash,
    _update_check_state,
)
from .telegram_client import TelegramClient
from .formatter import fmt_date, STAR_MILESTONES


class GitHubMonitor:
    def __init__(self):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        github_token   = os.getenv("G_TOKEN", "").strip()
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.username  = os.getenv("G_USERNAME", "").strip()

        # Comma-separated repo names/glob patterns to skip.
        # Supports fnmatch wildcards, e.g. "Ryazha*,Sys-clk,Atmosphere"
        skip_raw = os.getenv("SKIP_REPOS", "").strip()
        self.skip_patterns: list = [r.strip() for r in skip_raw.split(",") if r.strip()]

        self.dry_run = "--dry-run" in sys.argv or os.getenv("DRY_RUN", "").lower() in ("1", "true")
        self.summary_mode = (
            "--summary" in sys.argv
            or os.getenv("SUMMARY_MODE", "").lower() in ("1", "true")
        )

        missing = []
        if not github_token:   missing.append("G_TOKEN")
        if not self.username:  missing.append("G_USERNAME")
        if not self.dry_run:
            if not telegram_token: missing.append("TELEGRAM_BOT_TOKEN")
            if not self.chat_id:   missing.append("TELEGRAM_CHAT_ID")

        if missing:
            print(f"FATAL missing env: {', '.join(missing)}")
            print("   Configure them: Settings → Secrets and variables → Actions")
            sys.exit(1)

        self.force_send = (
            "--force" in sys.argv
            or os.getenv("FORCE_SEND", "").lower() in ("1", "true")
        )

        if self.dry_run:
            print("DRY-RUN: no telegram delivery.")
        if self.force_send:
            print("FORCE: bypassing early-exit and dedup checks.")

        self.github = GitHubClient(github_token, self.username)
        # TELEGRAM_TOPIC_ID parsed as integer
        self.telegram = TelegramClient(
            telegram_token or "dry_run_placeholder",
            self.chat_id or "0",
            topic_id=int(os.getenv("TELEGRAM_TOPIC_ID") or "0") or None,
        )

    def _collect_repo_data(self, repo: Dict, old_state: Dict) -> Dict[str, Any]:
        """Perform all per-repo API calls and return collected data + new state.

        Designed to be called concurrently via ThreadPoolExecutor.
        """
        import checker.state as _state_mod

        name = repo["name"]

        # Base data from the repo list
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

        # Star / fork deltas
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
            print(f"[{name}] MILESTONE: {m} stars!")

        # Commits: 1 request for the SHA list
        all_commits = self.github.list_commits(name, count=MAX_COMMITS)
        time.sleep(API_DELAY)

        # SHA deduplication uses full SHA
        known_shas = set(old_state.get("known_shas", []))

        # First run: take only the latest commit
        if not known_shas:
            new_commits = all_commits[:1]
        else:
            new_commits = [c for c in all_commits if c.get("sha") not in known_shas]

        # Load changed files ONLY for new commits
        for commit in new_commits:
            commit["files"] = self.github.get_commit_files(name, commit["sha"])
            time.sleep(API_DELAY)

        info["recent_commits"] = new_commits

        # PRs with deduplication
        prs_raw = self.github.get_open_prs(name, count=MAX_PRS)
        known_pr_numbers = set(old_state.get("known_pr_numbers", []))
        if not known_pr_numbers:
            new_prs = prs_raw[:1]
        else:
            new_prs = [p for p in prs_raw if p["number"] not in known_pr_numbers]
        info["open_prs"] = new_prs
        time.sleep(API_DELAY)

        # Issues: use the count from the repo list — saves an API call
        info["open_issues"] = self.github.get_open_issues_count(
            name,
            open_issues_raw=repo.get("open_issues_count", 0),
            pr_count=len(prs_raw),
        )

        # Releases — only if the tag changed
        known_tag = old_state.get("latest_release_tag")
        releases = self.github.get_new_releases(name, known_tag)
        info["releases"] = releases
        time.sleep(API_DELAY)

        # Workflow runs with deduplication
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
            print(f"[{name}] NEW COMMITS: {len(new_commits)}")
        elif not star_delta and not fork_delta:
            print(f"[{name}] no changes")

        # Update repository state
        all_current_shas = [c.get("sha") for c in all_commits if c.get("sha")]
        updated_shas = list(set(known_shas) | set(all_current_shas))

        all_current_pr_numbers = [p["number"] for p in prs_raw]
        updated_pr_numbers = list(set(known_pr_numbers) | set(all_current_pr_numbers))

        all_current_run_ids = [w.get("id") for w in workflows_raw if w.get("id")]
        updated_run_ids = list(set(known_run_ids) | set(all_current_run_ids))

        new_state = {
            "known_shas":       updated_shas[-MAX_KNOWN_SHAS:],
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

    def _run_summary(self) -> None:
        """--summary mode: compact digest of all repositories."""
        print("SUMMARY MODE: building repository digest...")
        repositories = self.github.get_repositories()
        if not repositories:
            print("No repositories found.")
            return

        repositories = [
            r for r in repositories
            if not any(fnmatch.fnmatch(r["name"], p) for p in self.skip_patterns)
        ]

        all_states = load_all_repository_states(self.username)
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
        print(f"Digest length: {len(message)} chars")

        if self.dry_run:
            print(re.sub(r"<[^>]+>", "", message))
            return

        if not self.telegram.validate():
            print("Invalid Telegram token. Exiting.")
            sys.exit(1)

        self.telegram.send(message)
        # Update star states
        for r in repositories:
            rname = r["name"]
            if rname not in all_states:
                all_states[rname] = {}
            all_states[rname]["stars"] = r.get("stargazers_count", 0)
            all_states[rname]["forks"] = r.get("forks_count", 0)
        save_all_repository_states(self.username, all_states)
        print("Digest sent.")

    def run(self) -> None:
        """Main entry point: load state, collect data, format and send."""
        print(f"Starting GitHub monitor for: {self.username}")
        print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        if self.skip_patterns:
            print(f"Skip patterns: {', '.join(self.skip_patterns)}")
        print()

        if self.summary_mode:
            self._run_summary()
            return

        # Load state BEFORE checking for updates so IS_COLD_START is set correctly
        # before any code inspects it.
        _pre_loaded_states = load_all_repository_states(self.username)

        # Re-read the module-level flag (it may have been updated by the call above)
        import checker.state as _state_mod
        is_cold_start = _state_mod.IS_COLD_START

        # Quick check: were there any updates at all?
        print("Checking for new updates...")
        latest_update = self.github.get_latest_repo_update()
        last_check = load_last_check_date()

        if not self.force_send and latest_update and last_check and latest_update <= last_check:
            print(f"No new updates found. Last update: {latest_update}")
            print("No monitoring needed. Exiting.")
            return

        if latest_update:
            print(f"Changes detected! Latest update: {latest_update}")
            print(f"Previous check: {last_check or 'first run'}")

        # Validate Telegram bot (skipped in dry_run — token may be empty)
        if not self.dry_run:
            print()
            print("Validating Telegram bot...")
            if not self.telegram.validate():
                print("Invalid Telegram token. Exiting.")
                sys.exit(1)

        # Fetch repositories
        print()
        print("Fetching repositories...")
        repositories = self.github.get_repositories()

        if not repositories:
            print("No repositories found or API error")
            self.telegram.send(
                f"<b>GitHub Monitor</b>\n"
                f"No repositories found for <code>{escape_html(self.username)}</code>\n"
                f"Check G_TOKEN permissions."
            )
            sys.exit(0)

        # Filter skipped repositories (supports fnmatch glob patterns)
        repositories = [
            r for r in repositories
            if not any(fnmatch.fnmatch(r["name"], p) for p in self.skip_patterns)
        ]
        print(f"Repositories found: {len(repositories)}")

        print()
        print("Collecting repository information...")

        all_states = _pre_loaded_states

        repos_data: List[Dict] = []
        has_real_changes = False

        # Concurrent per-repo API calls via ThreadPoolExecutor
        def _worker(repo: Dict):
            old_state = all_states.get(repo["name"], {})
            result = self._collect_repo_data(repo, old_state)
            return repo["name"], result

        total_repos = len(repositories)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=6, thread_name_prefix="gh-mon"
        ) as executor:
            futures = {executor.submit(_worker, repo): repo["name"] for repo in repositories}
            results: Dict[str, Dict] = {}
            done_count = 0
            # 5-minute hard cap so a hung thread cannot block the whole run.
            try:
                completed = concurrent.futures.as_completed(futures, timeout=300)
            except TypeError:
                completed = concurrent.futures.as_completed(futures)
            for future in completed:
                repo_name = futures[future]
                done_count += 1
                try:
                    rname, result = future.result(timeout=30)
                    results[rname] = result
                    print(f"  [{done_count}/{total_repos}] {rname} — done")
                except concurrent.futures.TimeoutError:
                    print(f"  [{done_count}/{total_repos}] {repo_name} — timeout (>30s)")
                except Exception as exc:
                    print(f"  [{done_count}/{total_repos}] {repo_name} — error: {exc}")

        # Process results sequentially (state saving must be sequential)
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

        # ── Cold start: write baseline without sending notifications ──
        if is_cold_start:
            print()
            print("COLD START: state file was missing or empty.")
            print("Recording current state as baseline; next changes will trigger notifications.")
            save_all_repository_states(self.username, all_states)
            if latest_update:
                save_last_check_date(latest_update)
            cold_msg = (
                "🔄 <b>Monitor started (first run or cache reset).</b>\n\n"
                "Current state recorded; future changes will be notified."
            )
            if self.dry_run:
                print("DRY-RUN cold-start message:", cold_msg)
            else:
                self.telegram.send(cold_msg)
            return

        if not self.force_send and not has_real_changes:
            print()
            print("No real content changes detected.")
            print("No monitoring needed. Exiting.")
            return

        # Sort by recency
        repos_data.sort(key=lambda x: x.get("pushed_at", ""), reverse=True)

        # Build message
        print()
        print("Building message...")
        message, markup = MessageBuilder.build(self.username, repos_data)
        print(f"Message length: {len(message)} chars")

        if self.dry_run:
            print()
            print("─" * 60)
            print("DRY-RUN: message that would be sent:")
            print("─" * 60)
            plain = re.sub(r"<[^>]+>", "", message)
            print(plain)
            print("─" * 60)
            print("DRY-RUN: Telegram not sent.")
            if latest_update:
                save_last_check_date(latest_update)
            return

        # Deduplication: skip if content hasn't changed
        msg_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
        last_hash = load_last_message_hash()
        if not self.force_send and last_hash and last_hash == msg_hash:
            print()
            print(f"Message unchanged (hash={msg_hash}). Skipping send.")
            save_all_repository_states(self.username, all_states)
            if latest_update:
                save_last_check_date(latest_update)
            return

        print()
        print("Sending to Telegram...")
        success = self.telegram.send(message, reply_markup=markup)

        print()
        if success:
            print("Monitor completed successfully")
            # Save states ONLY after a successful send.
            # If we save first and Telegram fails, the next run won't find changes
            # (SHAs already in known_shas) and will skip the notification.
            save_all_repository_states(self.username, all_states)
            # Single atomic write instead of two sequential ones
            updates: Dict[str, Any] = {"last_message_hash": msg_hash}
            if latest_update:
                updates["last_check_date"] = latest_update
            _update_check_state(**updates)
            if latest_update:
                print(f"Last check date updated: {latest_update}")
        else:
            print("Monitor completed with send errors")
            # Save state even on partial failure to avoid re-sending already-sent parts.
            save_all_repository_states(self.username, all_states)
            if latest_update:
                save_last_check_date(latest_update)
            # Non-zero exit so GitHub Actions marks the step as failed.
            sys.exit(2)
