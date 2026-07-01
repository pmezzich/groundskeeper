"""Tests for the watcher's pure PR-selection logic (no network).

`select_prs_to_review` decides what gets a (subscription-costing) review each
pass, so its skip/dedup/limit rules are pinned here without touching the API.
"""

from __future__ import annotations

from groundskeeper.models import PRRef
from groundskeeper.watch import select_prs_to_review


def _pr(number: int, sha: str = "abc", *, draft: bool = False, author: str = "alice") -> PRRef:
    return PRRef(number=number, head_sha=sha, draft=draft, author=author, title=f"PR {number}")


def test_fresh_state_selects_all_nondraft():
    prs = [_pr(1), _pr(2), _pr(3)]
    assert [p.number for p in select_prs_to_review(prs, {})] == [1, 2, 3]


def test_already_reviewed_head_is_skipped():
    prs = [_pr(1, "sha1"), _pr(2, "sha2")]
    state = {"1": "sha1"}  # #1 already reviewed at this exact head
    assert [p.number for p in select_prs_to_review(prs, state)] == [2]


def test_moved_head_is_rereviewed():
    prs = [_pr(1, "sha-new")]
    state = {"1": "sha-old"}  # head moved since last review
    assert [p.number for p in select_prs_to_review(prs, state)] == [1]


def test_drafts_excluded_by_default_included_on_flag():
    prs = [_pr(1, draft=True), _pr(2)]
    assert [p.number for p in select_prs_to_review(prs, {})] == [2]
    assert [p.number for p in select_prs_to_review(prs, {}, include_drafts=True)] == [1, 2]


def test_skip_authors_filtered():
    prs = [_pr(1, author="dependabot"), _pr(2, author="alice")]
    out = select_prs_to_review(prs, {}, skip_authors=frozenset({"dependabot"}))
    assert [p.number for p in out] == [2]


def test_limit_caps_selection():
    prs = [_pr(n) for n in range(1, 6)]
    assert [p.number for p in select_prs_to_review(prs, {}, limit=2)] == [1, 2]


def test_limit_counts_only_eligible():
    # first PR is a draft (skipped); limit=2 should still yield two REAL PRs
    prs = [_pr(1, draft=True), _pr(2), _pr(3), _pr(4)]
    assert [p.number for p in select_prs_to_review(prs, {}, limit=2)] == [2, 3]


def test_limit_zero_selects_nothing():
    prs = [_pr(1), _pr(2)]
    assert select_prs_to_review(prs, {}, limit=0) == []
