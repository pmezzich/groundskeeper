"""Unit tests for the keyless deterministic checks.

This is the layer that runs in CI with no API key, so it's the most important to
pin. Each test drives the real production function with a constructed FileDiff
and asserts the actual Finding — nothing about the code under test is mocked.
"""

from __future__ import annotations

from groundskeeper.checks import (
    check_ci_weakening,
    check_test_skip_added,
    check_type_ignore_added,
    run_deterministic_checks,
)
from groundskeeper.models import DiffLine, FileDiff, VerdictStatus


def _file(path: str, *added: str, status: str = "modified", patch: str = "") -> FileDiff:
    return FileDiff(
        path=path,
        status=status,
        added_lines=[DiffLine(line_number=i + 1, content=c) for i, c in enumerate(added)],
        patch=patch,
    )


class TestTypeIgnoreAdded:
    def test_unscoped_type_ignore_is_a_violation(self):
        findings = check_type_ignore_added([_file("src/foo.py", "value = risky()  # type: ignore")])
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "det/type-ignore-added"
        assert f.status == VerdictStatus.VIOLATION
        assert f.deterministic is True
        assert f.evidence == "src/foo.py:1"

    def test_clean_python_line_no_finding(self):
        assert check_type_ignore_added([_file("src/foo.py", "value = safe()")]) == []

    def test_non_python_file_ignored(self):
        assert check_type_ignore_added([_file("README.md", "# type: ignore")]) == []

    def test_moved_line_not_flagged(self):
        # identical content also appears removed -> moved/reflowed, not a NEW suppression
        f = FileDiff(
            path="src/foo.py",
            status="modified",
            added_lines=[DiffLine(line_number=1, content="x = y()  # type: ignore")],
            removed_lines=["x = y()  # type: ignore"],
        )
        assert check_type_ignore_added([f]) == []


class TestTestSkipAdded:
    def test_unreasoned_skip_is_a_violation(self):
        findings = check_test_skip_added([_file("tests/test_x.py", "@pytest.mark.skip")])
        assert len(findings) == 1
        assert findings[0].rule_id == "det/test-skip-added"
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_skip_with_reason_is_uncertain_not_violation(self):
        findings = check_test_skip_added(
            [_file("tests/test_x.py", '@pytest.mark.skip(reason="unimplemented stub")')]
        )
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.UNCERTAIN

    def test_skip_outside_test_file_ignored(self):
        assert check_test_skip_added([_file("src/foo.py", "@pytest.mark.skip")]) == []


class TestCiWeakening:
    def test_deselect_flagged(self):
        findings = check_ci_weakening(
            [_file(".github/workflows/ci.yml", "        run: pytest --deselect tests/test_flaky.py")]
        )
        assert len(findings) == 1
        assert findings[0].rule_id == "det/ci-weakening"
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_or_true_on_test_command_flagged(self):
        assert len(check_ci_weakening([_file("Makefile", "\tpytest tests/ || true")])) == 1

    def test_or_true_on_cleanup_not_flagged(self):
        # `|| true` on a non-test command (cleanup) is idempotent noise, not weakening
        f = _file(".github/workflows/ci.yml", "        run: docker system prune -f || true")
        assert check_ci_weakening([f]) == []

    def test_non_ci_file_ignored(self):
        assert check_ci_weakening([_file("src/foo.py", "pytest tests/ || true")]) == []


def test_run_deterministic_checks_aggregates_across_checks():
    files = [
        _file("src/foo.py", "z = q()  # type: ignore"),
        _file("tests/test_y.py", "@pytest.mark.xfail"),
    ]
    rule_ids = {f.rule_id for f in run_deterministic_checks(files)}
    assert "det/type-ignore-added" in rule_ids
    assert "det/test-skip-added" in rule_ids


def test_run_deterministic_checks_clean_pr_is_empty():
    files = [_file("src/foo.py", "def add(a, b):", "    return a + b")]
    assert run_deterministic_checks(files) == []
