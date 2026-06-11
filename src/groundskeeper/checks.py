"""Deterministic diff checks — exact, free, zero LLM calls.

These catch the enforcement gaps that grep can prove: test weakening,
type-check suppression, FIXME deletion, structural-guard tampering.
They run before any LLM judging so the report always has a baseline
even with no ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import re

from groundskeeper.models import Finding, FileDiff, Severity, VerdictStatus

_CI_FILE_RE = re.compile(r"(\.github/workflows/|Makefile|tox\.ini|run_all_tests|\.pre-commit)")


def _finding(
    rule_id: str,
    title: str,
    file: FileDiff,
    line: int,
    explanation: str,
    fix: str | None = None,
    severity: Severity = Severity.BLOCKING,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_title=title,
        status=VerdictStatus.VIOLATION,
        severity=severity,
        evidence=f"{file.path}:{line}",
        explanation=explanation,
        suggested_fix=fix,
        deterministic=True,
    )


def check_type_ignore_added(files: list[FileDiff]) -> list[Finding]:
    findings = []
    for f in files:
        if not f.path.endswith(".py"):
            continue
        for line in f.added_lines:
            if "# type: ignore" in line.content:
                findings.append(
                    _finding(
                        "det/type-ignore-added",
                        "New `# type: ignore` suppression",
                        f,
                        line.line_number,
                        "Adding `# type: ignore` hides a mypy error instead of fixing it.",
                        "Fix the underlying type error, or document why suppression is unavoidable.",
                    )
                )
    return findings


def check_test_skip_added(files: list[FileDiff]) -> list[Finding]:
    skip_re = re.compile(r"pytest\.mark\.(skip|xfail)|unittest\.skip")
    findings = []
    for f in files:
        if "test" not in f.path:
            continue
        for line in f.added_lines:
            if skip_re.search(line.content):
                findings.append(
                    _finding(
                        "det/test-skip-added",
                        "New test skip/xfail marker",
                        f,
                        line.line_number,
                        "Test-integrity policy: never add skip/xfail to bypass failures "
                        "(stubs for unimplemented work are the only exception).",
                        "Fix the failing test or code; if blocked, report it instead of skipping.",
                    )
                )
    return findings


def check_ci_weakening(files: list[FileDiff]) -> list[Finding]:
    weaken_re = re.compile(r'-k\s+["\']not\s|--deselect|--ignore=|\|\|\s*true')
    findings = []
    for f in files:
        if not _CI_FILE_RE.search(f.path):
            continue
        for line in f.added_lines:
            if weaken_re.search(line.content):
                findings.append(
                    _finding(
                        "det/ci-weakening",
                        "CI invocation weakened",
                        f,
                        line.line_number,
                        "Added test deselection or failure suppression to a CI-facing command.",
                        "Run the full suite; fix failures instead of deselecting them.",
                    )
                )
    return findings


def check_fixme_deleted(files: list[FileDiff]) -> list[Finding]:
    """FIXME tags document revisit conditions — deleting one without addressing
    the condition silently erases the obligation."""
    fixme_re = re.compile(r"FIXME\(([\w-]+)\)")
    findings = []
    for f in files:
        removed_tags = {m.group(1) for line in f.removed_lines for m in [fixme_re.search(line)] if m}
        added_tags = {m.group(1) for line in f.added_lines for m in [fixme_re.search(line.content)] if m}
        for tag in removed_tags - added_tags:
            findings.append(
                Finding(
                    rule_id="det/fixme-deleted",
                    rule_title="Tagged FIXME removed",
                    status=VerdictStatus.UNCERTAIN,
                    severity=Severity.NOTE,
                    evidence=f.path,
                    explanation=f"FIXME({tag}) was removed — verify its revisit condition was "
                    "actually addressed, not just deleted.",
                    deterministic=True,
                )
            )
    return findings


def check_guard_file_touched(files: list[FileDiff]) -> list[Finding]:
    """Structural guard tests enforce shrink-only ratchets. Any edit that grows
    an allowlist or cap deserves explicit human attention."""
    findings = []
    cap_re = re.compile(r"(=\s*\d+|MAX_|_CAP|allowlist|ALLOWLIST)", re.IGNORECASE)
    for f in files:
        if "test_architecture_" not in f.path:
            continue
        cap_lines = [line for line in f.added_lines if cap_re.search(line.content)]
        if cap_lines:
            findings.append(
                Finding(
                    rule_id="det/guard-modified",
                    rule_title="Structural guard test modified",
                    status=VerdictStatus.UNCERTAIN,
                    severity=Severity.BLOCKING,
                    evidence=f"{f.path}:{cap_lines[0].line_number}",
                    explanation="This PR edits caps/allowlists in a structural guard test. "
                    "Guards are shrink-only ratchets — growth needs explicit justification.",
                    deterministic=True,
                )
            )
    return findings


def check_rules_modified(files: list[FileDiff]) -> list[Finding]:
    """A PR that edits the rules it is judged against gets flagged loudly."""
    findings = []
    for f in files:
        if ".claude/rules/" in f.path:
            findings.append(
                Finding(
                    rule_id="det/rules-modified",
                    rule_title="Rule files modified by this PR",
                    status=VerdictStatus.UNCERTAIN,
                    severity=Severity.BLOCKING,
                    evidence=f.path,
                    explanation="This PR modifies the rule files groundskeeper enforces. "
                    "Verdicts here used the BASE-ref rules; review the rule change itself carefully.",
                    deterministic=True,
                )
            )
    return findings


ALL_CHECKS = [
    check_type_ignore_added,
    check_test_skip_added,
    check_ci_weakening,
    check_fixme_deleted,
    check_guard_file_touched,
    check_rules_modified,
]


def run_deterministic_checks(files: list[FileDiff]) -> list[Finding]:
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(files))
    return findings
