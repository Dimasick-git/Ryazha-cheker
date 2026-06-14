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
    issues_raw: List[Dict],
    known_issue_numbers: set,
) -> List[Dict]:
    """Return issues whose number is not in ``known_issue_numbers``."""
    if not known_issue_numbers:
        return issues_raw[:1]
    return [i for i in issues_raw if i.get("number") not in known_issue_numbers]


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
    issues_raw: Optional[List[Dict]] = None,
    known_issue_numbers: Optional[set] = None,
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

    if issues_raw is not None:
        current_issue_nums = [i["number"] for i in issues_raw if i.get("number")]
        known_in = known_issue_numbers or set()
        new_state["known_issue_numbers"] = _merge_ids(current_issue_nums, known_in, 200)

    return new_state
