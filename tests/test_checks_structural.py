"""Tests for the structural-guard family of deterministic checks.

rules-modified / guard-tampering / ratchet-raised / workflow-hygiene /
fixme-deleted. Each drives the real check function with a constructed diff;
the code under test is never mocked.
"""

from __future__ import annotations

from collections.abc import Sequence

from groundskeeper.checks import (
    check_fixme_deleted,
    check_guard_file_touched,
    check_ratchet_increased,
    check_rules_modified,
    check_workflow_job_hygiene,
)
from groundskeeper.models import DiffLine, FileDiff, VerdictStatus


def _diff(
    path: str,
    added: Sequence[str] = (),
    removed: Sequence[str] = (),
    status: str = "modified",
) -> FileDiff:
    return FileDiff(
        path=path,
        status=status,
        added_lines=[DiffLine(line_number=i + 1, content=c) for i, c in enumerate(added)],
        removed_lines=list(removed),
    )


class TestRulesModified:
    def test_editing_the_rules_dir_is_flagged(self):
        findings = check_rules_modified([_diff(".claude/rules/patterns/code.md", added=["## New rule"])])
        assert len(findings) == 1
        assert findings[0].rule_id == "det/rules-modified"

    def test_non_rules_file_ignored(self):
        assert check_rules_modified([_diff("src/foo.py", added=["x = 1"])]) == []


class TestGuardFileTouched:
    def test_growing_an_allowlist_in_a_guard_is_flagged(self):
        f = _diff("tests/unit/test_architecture_no_bare_basemodel.py", added=['    ALLOWLIST = {"foo"}'])
        findings = check_guard_file_touched([f])
        assert len(findings) == 1
        assert findings[0].rule_id == "det/guard-modified"

    def test_introducing_a_new_guard_is_not_tampering(self):
        f = _diff("tests/unit/test_architecture_new.py", added=["    MAX_X = 5"], status="added")
        assert check_guard_file_touched([f]) == []

    def test_non_guard_test_ignored(self):
        assert check_guard_file_touched([_diff("tests/test_foo.py", added=["    MAX_X = 5"])]) == []


class TestRatchetIncreased:
    def test_raising_a_baseline_value_is_a_violation(self):
        findings = check_ratchet_increased([_diff(".duplication-baseline", added=["42"], removed=["36"])])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION
        assert findings[0].rule_id == "det/ratchet-increased"

    def test_lowering_a_baseline_is_allowed(self):
        assert check_ratchet_increased([_diff(".duplication-baseline", added=["30"], removed=["36"])]) == []

    def test_net_new_entries_flagged(self):
        f = _diff(".type-ignore-baseline", added=["src/a.py", "src/b.py"], removed=["src/a.py"])
        assert len(check_ratchet_increased([f])) == 1

    def test_creating_a_baseline_not_flagged(self):
        assert check_ratchet_increased([_diff(".duplication-baseline", added=["50"], status="added")]) == []


class TestWorkflowJobHygiene:
    def test_new_job_without_timeout_or_concurrency_flagged(self):
        f = _diff(
            ".github/workflows/x.yml",
            added=["jobs:", "  build:", "    runs-on: ubuntu-latest"],
            status="added",
        )
        ids = {x.rule_id for x in check_workflow_job_hygiene([f])}
        assert "det/job-timeout-missing" in ids
        assert "det/workflow-no-concurrency" in ids

    def test_job_with_timeout_and_concurrency_is_clean(self):
        f = _diff(
            ".github/workflows/x.yml",
            added=[
                "concurrency: { group: x, cancel-in-progress: true }",
                "  build:",
                "    runs-on: ubuntu-latest",
                "    timeout-minutes: 5",
            ],
            status="added",
        )
        assert check_workflow_job_hygiene([f]) == []


class TestFixmeDeleted:
    def test_deleting_a_tagged_fixme_is_flagged(self):
        f = _diff("src/foo.py", added=["    pass"], removed=["    # FIXME(gk-42): revisit after X"])
        findings = check_fixme_deleted([f])
        assert len(findings) == 1
        assert findings[0].rule_id == "det/fixme-deleted"
        assert "gk-42" in findings[0].explanation

    def test_retagged_fixme_not_flagged(self):
        # one tag removed, another added on the same code -> retag, not a deletion
        f = _diff(
            "src/foo.py",
            added=["    # FIXME(gk-99): new condition"],
            removed=["    # FIXME(gk-42): old condition"],
        )
        assert check_fixme_deleted([f]) == []
