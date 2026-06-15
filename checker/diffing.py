"""Pure diff logic: compares old vs new repo state to find new items."""

from typing import Any, Dict, List, Optional, Tuple

from .formatter import STAR_MILESTONES
from .github_client import MAX_KNOWN_SHAS


def compute_deltas(
    info: Dict[str, Any],
    old_state: Dict[str, Any],
) -> Tuple[int, int, List[int]]:
    """Compute star delta, fork delta, and crossed star milestones.

    Returns ``(star_delta, fork_delta, crossed_milestones)``.
    """
    old_stars = old_state.get("stars", info["stars"])
    old_forks = old_state.get("forks", info["forks"])
    star_delta = max(0, info["stars"] - old_stars)
    fork_delta = max(0, info["forks"] - old_forks)
    crossed = sorted(m for m in STAR_MILESTONES if old_stars < m <= info["stars"])
    return star_delta, fork_delta, crossed


def filter_new_commits(
    all_commits: List[Dict],
    known_shas: set,
) -> List[Dict]:
    """Return commits whose SHA is not in ``known_shas``.

    On first run (empty known_shas) returns only the latest commit so that
    the cold-start run doesn't report hundreds of old commits as "new".
    """
    if not known_shas:
        return all_commits[:1]
    return [c for c in all_commits if c.get("sha") not in known_shas]


def filter_new_prs(
    prs_raw: List[Dict],
    known_pr_numbers: set,
) -> List[Dict]:
    """Return PRs whose number is not in ``known_pr_numbers``."""
    if not known_pr_numbers:
        return prs_raw[:1]
    return [p for p in prs_raw if p["number"] not in known_pr_numbers]


def filter_new_workflows(
    workflows_raw: List[Dict],
    known_run_ids: set,
) -> List[Dict]:
    """Return workflow runs whose id is not in ``known_run_ids``."""
    if not known_run_ids:
        return workflows_raw[:1]
    return [w for w in workflows_raw if w.get("id") not in known_run_ids]


def filter_new_issues(
    current_issues: List[Dict],
    known_numbers: set,
    cold_start: bool,
) -> List[Dict]:
    """Return issues not in known_numbers. On cold start, report at most 1."""
    new = [i for i in current_issues if i["number"] not in known_numbers]
    if cold_start and len(new) > 1:
        new = new[:1]
    return new


def _merge_ids(current: List, known: set, max_count: int) -> List:
    """Merge current IDs with known ones, deduplicating and capping the result."""
    current_set = set(current)
    return (current + [x for x in known if x not in current_set])[:max_count]


def build_new_state(
    all_commits: List[Dict],
    known_shas: set,
    prs_raw: List[Dict],
    known_pr_numbers: set,
    workflows_raw: List[Dict],
    known_run_ids: set,
    info: Dict[str, Any],
    releases: List[Dict],
    known_tag: Optional[str],
    current_issues: Optional[List[Dict]] = None,
    old_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the updated per-repo state dict to persist for the next run."""
    from datetime import datetime, timezone

    current_shas    = [c.get("sha") for c in all_commits if c.get("sha")]
    current_pr_nums = [p["number"] for p in prs_raw]
    current_run_ids = [w.get("id") for w in workflows_raw if w.get("id")]

    new_state: Dict[str, Any] = {
        "known_shas":       _merge_ids(current_shas,    known_shas,         MAX_KNOWN_SHAS),
        "known_pr_numbers": _merge_ids(current_pr_nums, known_pr_numbers,   200),
        "known_run_ids":    _merge_ids(current_run_ids, known_run_ids,      200),
        "last_check":       datetime.now(timezone.utc).isoformat(),
        "stars":            info["stars"],
        "forks":            info["forks"],
    }
    if releases:
        new_state["latest_release_tag"] = releases[0].get("tag", known_tag)
    elif known_tag:
        new_state["latest_release_tag"] = known_tag

    # Persist known issue numbers
    old_known_issue_numbers = set((old_state or {}).get("known_issue_numbers", []))
    current_issue_nums = {i["number"] for i in (current_issues or [])}
    new_state["known_issue_numbers"] = list(old_known_issue_numbers | current_issue_nums)

    return new_state
