"""Deterministic diff checks — exact, free, zero LLM calls.

These catch the enforcement gaps that grep can prove: test weakening,
type-check suppression, FIXME deletion, structural-guard tampering.
They run before any LLM judging so the report always has a baseline
even with no ANTHROPIC_API_KEY.

Calibration notes (from benchmarking against 5 reviewed PRs):
- Moved/re-indented lines are not new suppressions — skip any added line
  whose stripped content also appears in the file's removed lines.
- `|| true` only counts as CI weakening on test/quality invocations, not
  on cleanup/teardown commands (docker prune etc.).
- A suppression with a justification comment nearby is surfaced as
  UNCERTAIN (verify the justification), not VIOLATION.
- A FIXME whose tag changed but count didn't is a retag, not a deletion.
"""

from __future__ import annotations

import re

from groundskeeper.models import Finding, FileDiff, Severity, VerdictStatus

_CI_FILE_RE = re.compile(r"(\.github/workflows/|Makefile|tox\.ini|run_all_tests|\.pre-commit)")
_TEST_INVOCATION_RE = re.compile(r"pytest|tox\b|make\s+\S*test|run_all_tests|coverage|mypy|ruff")
_COMMENT_RE = re.compile(r"^\s*#\s*\S+")


def _finding(
    rule_id: str,
    title: str,
    file: FileDiff,
    line: int,
    explanation: str,
    fix: str | None = None,
    severity: Severity = Severity.BLOCKING,
    status: VerdictStatus = VerdictStatus.VIOLATION,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_title=title,
        status=status,
        severity=severity,
        evidence=f"{file.path}:{line}",
        explanation=explanation,
        suggested_fix=fix,
        deterministic=True,
    )


def _moved_lines(file: FileDiff) -> set[str]:
    """Stripped content of removed lines — an added line matching one of
    these was moved or re-indented, not newly introduced."""
    return {line.strip() for line in file.removed_lines if line.strip()}


def _has_justification_above(file: FileDiff, content: str) -> bool:
    """True when a comment line sits within 3 patch lines above the added
    line — i.e. the suppression is documented, not silent."""
    patch_lines = file.patch.splitlines()
    target = "+" + content
    for i, line in enumerate(patch_lines):
        if line == target:
            for prev in patch_lines[max(0, i - 3) : i]:
                code = prev[1:] if prev[:1] in "+- " else prev
                if _COMMENT_RE.match(code):
                    return True
    return False


def check_type_ignore_added(files: list[FileDiff]) -> list[Finding]:
    findings = []
    for f in files:
        if not f.path.endswith(".py"):
            continue
        moved = _moved_lines(f)
        for line in f.added_lines:
            if "# type: ignore" not in line.content:
                continue
            if line.content.strip() in moved:
                continue  # moved/re-indented, not a new suppression
            scoped = re.search(r"# type: ignore\[[\w,-]+\]", line.content) is not None
            justified = _has_justification_above(f, line.content)
            if scoped and justified:
                findings.append(
                    _finding(
                        "det/type-ignore-added",
                        "New scoped `# type: ignore` (documented)",
                        f,
                        line.line_number,
                        "New scoped, documented type suppression — verify the justification holds.",
                        severity=Severity.NOTE,
                        status=VerdictStatus.UNCERTAIN,
                    )
                )
            else:
                findings.append(
                    _finding(
                        "det/type-ignore-added",
                        "New `# type: ignore` suppression",
                        f,
                        line.line_number,
                        "Adding an unscoped/undocumented `# type: ignore` hides a mypy error "
                        "instead of fixing it.",
                        "Fix the underlying type error, or scope the code and document why "
                        "suppression is unavoidable.",
                    )
                )
    return findings


def check_test_skip_added(files: list[FileDiff]) -> list[Finding]:
    skip_re = re.compile(r"pytest\.mark\.(skip|xfail)|unittest\.skip")
    findings = []
    for f in files:
        if "test" not in f.path:
            continue
        moved = _moved_lines(f)
        for line in f.added_lines:
            if not skip_re.search(line.content):
                continue
            if line.content.strip() in moved:
                continue
            documented = "reason=" in line.content or _has_justification_above(f, line.content)
            if documented:
                findings.append(
                    _finding(
                        "det/test-skip-added",
                        "New test skip/xfail (has reason)",
                        f,
                        line.line_number,
                        "New skip/xfail with a stated reason — verify it's a sanctioned stub, "
                        "not a bypassed failure. Watch for `strict=False` on xfail, which "
                        "silences the test entirely.",
                        severity=Severity.NOTE,
                        status=VerdictStatus.UNCERTAIN,
                    )
                )
            else:
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
    deselect_re = re.compile(r'-k\s+["\']not\s|--deselect')
    suppress_re = re.compile(r"\|\|\s*true")
    findings = []
    for f in files:
        if not _CI_FILE_RE.search(f.path):
            continue
        moved = _moved_lines(f)
        for line in f.added_lines:
            if line.content.strip() in moved:
                continue
            is_deselect = deselect_re.search(line.content)
            # `|| true` is only weakening when it suppresses a test/quality
            # command — cleanup/teardown (docker prune etc.) is idempotent noise.
            is_suppress = suppress_re.search(line.content) and _TEST_INVOCATION_RE.search(line.content)
            if is_deselect or is_suppress:
                findings.append(
                    _finding(
                        "det/ci-weakening",
                        "CI invocation weakened",
                        f,
                        line.line_number,
                        "Added test deselection or failure suppression to a CI test command.",
                        "Run the full suite; fix failures instead of deselecting them.",
                    )
                )
    return findings


def check_fixme_deleted(files: list[FileDiff]) -> list[Finding]:
    """FIXME tags document revisit conditions — deleting one without addressing
    the condition silently erases the obligation. A retag (old tag removed,
    new tag added on the same code) is not a deletion."""
    fixme_re = re.compile(r"FIXME\(([\w#-]+)\)")
    findings = []
    for f in files:
        removed_count = sum(1 for line in f.removed_lines if fixme_re.search(line))
        added_count = sum(1 for line in f.added_lines if fixme_re.search(line.content))
        if removed_count > added_count:
            removed_tags = {
                m.group(1) for line in f.removed_lines for m in [fixme_re.search(line)] if m
            }
            added_tags = {
                m.group(1) for line in f.added_lines for m in [fixme_re.search(line.content)] if m
            }
            gone = ", ".join(sorted(removed_tags - added_tags)) or "unknown"
            findings.append(
                Finding(
                    rule_id="det/fixme-deleted",
                    rule_title="Tagged FIXME removed",
                    status=VerdictStatus.UNCERTAIN,
                    severity=Severity.NOTE,
                    evidence=f.path,
                    explanation=f"FIXME({gone}) removed without a replacement tag — verify its "
                    "revisit condition was actually addressed, not just deleted.",
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
        moved = _moved_lines(f)
        cap_lines = [
            line
            for line in f.added_lines
            if cap_re.search(line.content) and line.content.strip() not in moved
        ]
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
