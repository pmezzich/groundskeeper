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

from groundskeeper.models import FileDiff, Finding, Severity, VerdictStatus

_CI_FILE_RE = re.compile(
    r"(\.github/workflows/|Makefile|tox\.ini|run_all_tests|\.pre-commit"
    r"|(^|/)package\.json$|Jenkinsfile|\.gitlab-ci|\.circleci/|azure-pipelines|\.travis)"
)
# Word-bound tool names: "mochawesome-report" must not read as mocha, nor
# ".eslintcache" as eslint.
_TEST_INVOCATION_RE = re.compile(
    r"pytest|tox\b|make\s+\S*test|run_all_tests|coverage|mypy|ruff"
    r"|\bjest\b|\bmocha\b|\bvitest\b|\bkarma\b|gulp\s+test|npm\s+(?:run\s+)?test|yarn\s+test"
    r"|go\s+test|gradle\w*\s+\S*(?:test|check)|xcodebuild\s+\S*test|\beslint\b|\btsc\b"
)
# Cleanup/teardown verbs: `rm -rf mochawesome-report || true` is idempotent
# noise, not CI weakening, even though it names a test tool's artifacts.
_CLEANUP_RE = re.compile(r"\b(rm|rimraf|pkill|killall|del|prune|clean)\b")
# Python-comment shape ONLY — the Python checks' justification semantics are
# benchmarked and frozen; //-style languages use _C_COMMENT_RE further down.
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


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _moved_or_reflowed(file: FileDiff, content: str) -> bool:
    """True when the added line was moved, re-indented, or reflowed (line
    wrapping changed) rather than newly introduced — its whitespace-collapsed
    content matches or is contained in a removed line (or vice versa)."""
    norm = _normalize(content)
    if not norm:
        return False
    for removed in file.removed_lines:
        rnorm = _normalize(removed)
        if not rnorm:
            continue
        if norm == rnorm or (len(norm) > 20 and (norm in rnorm or rnorm in norm)):
            return True
    return False


def _has_justification_above(file: FileDiff, content: str) -> bool:
    """True when the suppression is documented — a trailing comment beyond
    the directive itself, or a comment line within 6 patch lines above."""
    # Trailing explanation after the directive, e.g. `x = y  # type: ignore[x]  # SDK stub gap`
    if re.search(r"# type: ignore\[[\w,-]+\]\s*#\s*\S", content):
        return True
    patch_lines = file.patch.splitlines()
    target = "+" + content
    for i, line in enumerate(patch_lines):
        if line == target:
            for prev in patch_lines[max(0, i - 6) : i]:
                code = prev[1:] if prev[:1] in "+- " else prev
                if _COMMENT_RE.match(code):
                    return True
    return False


_MOCK_ASSIGN_RE = re.compile(r"# type: ignore\[(method-assign|assignment)\]")


def check_type_ignore_added(files: list[FileDiff]) -> list[Finding]:
    findings = []
    for f in files:
        if not f.path.endswith(".py"):
            continue
        is_test = "test" in f.path
        for line in f.added_lines:
            if "# type: ignore" not in line.content:
                continue
            if _moved_or_reflowed(f, line.content):
                continue  # moved/re-indented/reflowed, not a new suppression
            if is_test and _MOCK_ASSIGN_RE.search(line.content):
                continue  # idiomatic mock method-assign in tests — repo convention
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
        for line in f.added_lines:
            if not skip_re.search(line.content):
                continue
            if _moved_or_reflowed(f, line.content):
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


# ---------------------------------------------------------------------------
# Multi-language layer (JS/TS, Go, Java, Kotlin, Swift).
#
# ISOLATION INVARIANT: nothing below may alter the behavior of the Python
# checks above — those are benchmarked against salesagent review history and
# must stay byte-identical. Every pattern here is extension-gated and uses its
# own comment/justification semantics. (An earlier draft mixed the two and an
# adversarial review demonstrated three Python verdict flips — keep them apart.)
#
# Noise policy (calibrated for posting on strangers' PRs):
# - Blanket suppressions (@ts-ignore, bare eslint-disable, bare //nolint,
#   bare swiftlint:disable) -> VIOLATION.
# - Scoped suppressions (named rules/codes) -> UNCERTAIN note, never blocking:
#   they are idiomatic in these ecosystems.
# - Conventional Java/Kotlin boilerplate (unchecked/deprecation/...) -> silent.
# - Vendored/generated/minified files -> skipped entirely.

_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue")
_GENERATED_PATH_RE = re.compile(
    r"\.min\.(js|css)$|(^|/)(dist|build|vendor|vendored|node_modules|generated)/"
    r"|package-lock\.json$|creative-renderer"
)

_C_COMMENT_RE = re.compile(r"^\s*(//|/\*)\s*\S")


def _has_c_comment_above(file: FileDiff, content: str) -> bool:
    """A //-style comment line within 6 patch lines above the added line."""
    patch_lines = file.patch.splitlines()
    target = "+" + content
    for i, line in enumerate(patch_lines):
        if line == target:
            for prev in patch_lines[max(0, i - 6) : i]:
                code = prev[1:] if prev[:1] in "+- " else prev
                if _C_COMMENT_RE.match(code):
                    return True
    return False


# Each spec: (extensions, directive, scoped-variant-or-None, label,
# needs_comment_context). "Scoped" = the directive names what it suppresses.
# needs_comment_context: the directive text must appear after a comment marker
# on its line, so prose/string MENTIONS of a directive don't fire.
_SUPPRESSION_SPECS: list[tuple[tuple[str, ...], re.Pattern[str], re.Pattern[str] | None, str, bool]] = [
    (_JS_EXTS, re.compile(r"@ts-ignore\b"), None, "`@ts-ignore`", True),
    # @ts-expect-error self-verifies (errors when unnecessary) -> inherently scoped
    (
        _JS_EXTS,
        re.compile(r"@ts-expect-error\b"),
        re.compile(r"@ts-expect-error\b"),
        "`@ts-expect-error`",
        True,
    ),
    (
        _JS_EXTS,
        re.compile(r"eslint-disable(?:-next-line|-line)?"),
        re.compile(r"eslint-disable(?:-next-line|-line)?\s+[\w@/-]"),  # names specific rules
        "`eslint-disable`",
        True,
    ),
    ((".go",), re.compile(r"//\s*nolint\b"), re.compile(r"//\s*nolint:\w"), "`//nolint`", False),
    # Annotations take mandatory args naming the warning -> inherently scoped.
    (
        (".java",),
        re.compile(r"@SuppressWarnings\s*\("),
        re.compile(r"@SuppressWarnings\s*\("),
        "`@SuppressWarnings`",
        False,
    ),
    (
        (".kt", ".kts"),
        re.compile(r"@(?:file:)?Suppress\s*\("),
        re.compile(r"@(?:file:)?Suppress\s*\("),
        "`@Suppress`",
        False,
    ),
    (
        (".swift",),
        re.compile(r"//\s*swiftlint:disable\b"),
        re.compile(r"swiftlint:disable(?::\w+)?\s+\w"),  # names specific rules
        "`swiftlint:disable`",
        False,
    ),
]

# Reviewer-accepted Java/Kotlin boilerplate — flagging these floods adapter PRs.
_CONVENTIONAL_SUPPRESS_RE = re.compile(
    r'@(?:file:)?Suppress(?:Warnings)?\s*\(\s*[{\[]?\s*"'
    r"(unchecked|deprecation|rawtypes|serial|DEPRECATION|UNCHECKED_CAST)\""
)


def _in_js_comment_context(content: str, idx: int) -> bool:
    head = content[:idx]
    return "//" in head or "/*" in head or "<!--" in head


def check_lint_suppression_added(files: list[FileDiff]) -> list[Finding]:
    """Blanket lint/type suppressions in the non-Python prebid languages."""
    findings = []
    for f in files:
        if _GENERATED_PATH_RE.search(f.path):
            continue
        specs = [s for s in _SUPPRESSION_SPECS if f.path.endswith(s[0])]
        if not specs:
            continue
        for line in f.added_lines:
            # When several directives share a line ("// eslint-disable-next-line
            # no-undef -- replaces old @ts-ignore"), judge the LEFTMOST one —
            # it's the actual directive; later mentions are prose.
            matches = [(m.start(), spec, m) for spec in specs for m in [spec[1].search(line.content)] if m]
            if not matches:
                continue
            start, spec, m = min(matches, key=lambda t: t[0])
            _, _, scoped_re, label, needs_ctx = spec
            if needs_ctx and not _in_js_comment_context(line.content, start):
                continue  # prose/string mention, not a directive
            if label in ("`@SuppressWarnings`", "`@Suppress`") and not line.content.lstrip().startswith("@"):
                continue  # mentioned mid-line (comment/string), not an annotation
            if _CONVENTIONAL_SUPPRESS_RE.search(line.content):
                continue  # unchecked/deprecation boilerplate — reviewer-accepted
            if _moved_or_reflowed(f, line.content):
                continue
            scoped = scoped_re is not None and scoped_re.search(line.content) is not None
            if not scoped:
                findings.append(
                    _finding(
                        "det/lint-suppression-added",
                        f"Blanket {label} suppression",
                        f,
                        line.line_number,
                        f"A blanket {label} (no rule named) hides every future error at this "
                        "site, not just the current one.",
                        "Scope the suppression to the specific rule, or fix the underlying issue.",
                    )
                )
            else:
                tail = line.content[m.end() :]
                # eslint's official description syntax is `... rule -- why`;
                # anchored after the directive so `i--` etc. can't fake it.
                documented = _has_c_comment_above(f, line.content) or bool(re.search(r"\s--\s+\S", tail))
                explanation = (
                    f"New scoped, documented {label} — verify the justification holds."
                    if documented
                    else f"New scoped {label} with no adjacent justification — idiomatic, but worth a glance."
                )
                findings.append(
                    _finding(
                        "det/lint-suppression-added",
                        f"New scoped {label} suppression",
                        f,
                        line.line_number,
                        explanation,
                        severity=Severity.NOTE,
                        status=VerdictStatus.UNCERTAIN,
                    )
                )
    return findings


def _is_multilang_test_file(path: str) -> bool:
    if path.endswith(".go"):
        return path.endswith("_test.go")
    p = path.lower()
    if path.endswith(_JS_EXTS):
        return "test" in p or ".spec." in p or "__tests__" in p
    return "test" in p  # .java/.kt/.kts/.swift conventions


# Each spec: (extensions, pattern, reason_arg, conditional_gate).
# reason_arg: a quoted arg IMMEDIATELY after the match is a skip reason
# (t.Skip("flaky")) — for JS it.skip("name", fn) the arg is just the test name.
# conditional_gate: XCTSkipIf/XCTSkipUnless-style capability gates are a
# legitimate idiom -> always UNCERTAIN note, never a violation.
# NOTE: Jasmine's `fit(`/`fdescribe(` are deliberately NOT matched — `fit(` is
# a common real function name (model.fit, image.fit) and none of the prebid
# JS repos use Jasmine.
_SKIP_SPECS: list[tuple[tuple[str, ...], re.Pattern[str], bool, bool]] = [
    (_JS_EXTS, re.compile(r"\b(it|test|describe|context)\.skip\b"), False, False),
    (_JS_EXTS, re.compile(r"\bx(it|describe|test)\s*\("), False, False),
    ((".go",), re.compile(r"\bt\.Skipf?\b"), True, False),
    ((".java", ".kt", ".kts"), re.compile(r"@(Ignore|Disabled)\b"), True, False),
    ((".swift",), re.compile(r"\bXCTSkip\b"), True, False),
    ((".swift",), re.compile(r"\bXCTSkip(If|Unless)\b"), False, True),
]
_JS_ONLY_RE = re.compile(r"\b(it|test|describe|context)\.only\s*\(")


def check_test_skip_multilang(files: list[FileDiff]) -> list[Finding]:
    """Test skips and exclusive focus in the non-Python prebid languages."""
    findings = []
    for f in files:
        if _GENERATED_PATH_RE.search(f.path) or not _is_multilang_test_file(f.path):
            continue
        specs = [s for s in _SKIP_SPECS if f.path.endswith(s[0])]
        is_js = f.path.endswith(_JS_EXTS)
        if not specs and not is_js:
            continue
        for line in f.added_lines:
            # Exclusive focus silently deselects the REST of the suite — worse
            # than a skip, and never legitimate in a merged PR.
            if is_js and _JS_ONLY_RE.search(line.content):
                if _moved_or_reflowed(f, line.content):
                    continue
                findings.append(
                    _finding(
                        "det/test-skip-added",
                        "Exclusive test focus (.only) added",
                        f,
                        line.line_number,
                        "`.only` runs just this test and silently deselects the rest of the "
                        "suite — a debugging artifact that must not merge.",
                        "Remove the exclusive-focus marker.",
                    )
                )
                continue
            hit = next(
                ((spec, m) for spec in specs for m in [spec[1].search(line.content)] if m),
                None,
            )
            if hit is None:
                continue
            (_, _, reason_arg, conditional), m = hit
            # Annotations must BE annotations, not prose mentions ("// TODO:
            # revisit the @Disabled cases").
            if m.group(0).startswith("@") and not line.content.lstrip().startswith("@"):
                continue
            if _moved_or_reflowed(f, line.content):
                continue
            tail = line.content[m.end() :]
            documented = (
                conditional
                or (reason_arg and re.match(r"\s*\(\s*[\"']", tail) is not None)
                or _has_c_comment_above(f, line.content)
            )
            if documented:
                explanation = (
                    "Conditional capability gate — verify the condition genuinely can't be met "
                    "in CI, not that a failing case is being dodged."
                    if conditional
                    else "New skip with a stated reason — verify it's a sanctioned stub, not a "
                    "bypassed failure."
                )
                findings.append(
                    _finding(
                        "det/test-skip-added",
                        "New test skip (has reason)",
                        f,
                        line.line_number,
                        explanation,
                        severity=Severity.NOTE,
                        status=VerdictStatus.UNCERTAIN,
                    )
                )
            else:
                findings.append(
                    _finding(
                        "det/test-skip-added",
                        "New test skip marker",
                        f,
                        line.line_number,
                        "Test-integrity policy: never add a skip to bypass failures "
                        "(stubs for unimplemented work are the only exception).",
                        "Fix the failing test or code; if blocked, report it instead of skipping.",
                    )
                )
    return findings


def check_ci_weakening(files: list[FileDiff]) -> list[Finding]:
    deselect_re = re.compile(r'-k\s+["\']not\s|--deselect|--testPathIgnorePatterns')
    suppress_re = re.compile(r"\|\|\s*true")
    findings = []
    for f in files:
        if not _CI_FILE_RE.search(f.path):
            continue
        for line in f.added_lines:
            if _moved_or_reflowed(f, line.content):
                continue
            is_deselect = deselect_re.search(line.content)
            # `|| true` is only weakening when it suppresses a test/quality
            # command — cleanup/teardown (docker prune, rm -rf artifacts) is
            # idempotent noise even when it names a test tool's output dirs.
            is_suppress = (
                suppress_re.search(line.content)
                and _TEST_INVOCATION_RE.search(line.content)
                and not _CLEANUP_RE.search(line.content)
            )
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
    seen_tags: set[str] = set()
    for f in files:
        if f.status == "removed":
            continue  # whole file deleted — the FIXME's subject is gone with it
        removed_count = sum(1 for line in f.removed_lines if fixme_re.search(line))
        added_count = sum(1 for line in f.added_lines if fixme_re.search(line.content))
        # A diff that substantially rewrites the code around a removed FIXME
        # most likely completed the tracked work — only flag near-pure deletions.
        substantive_edit = len(f.added_lines) > 3 * max(removed_count, 1)
        if removed_count > added_count and not substantive_edit:
            removed_tags = {m.group(1) for line in f.removed_lines for m in [fixme_re.search(line)] if m}
            added_tags = {m.group(1) for line in f.added_lines for m in [fixme_re.search(line.content)] if m}
            new_gone = (removed_tags - added_tags) - seen_tags
            if not new_gone:
                continue  # already reported these tags from another file
            seen_tags.update(new_gone)
            gone = ", ".join(sorted(new_gone))
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
        if f.status == "added":
            continue  # a PR INTRODUCING a guard isn't tampering with one
        cap_lines = [
            line
            for line in f.added_lines
            if cap_re.search(line.content) and not _moved_or_reflowed(f, line.content)
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


_NUM_RE = re.compile(r"-?\d+")


def _baseline_values(lines: list[str]) -> dict[str, int]:
    """Map each line's non-numeric part (the 'key') to its numeric value, so
    both bare-number ratchets (`60`) and JSON-ish ones (`"tests": 100,`) work."""
    values: dict[str, int] = {}
    for line in lines:
        m = _NUM_RE.search(line)
        if not m:
            continue
        key = _NUM_RE.sub("", line).strip().strip('",:{}')
        values[key] = int(m.group())
    return values


def check_ratchet_increased(files: list[FileDiff]) -> list[Finding]:
    """Baseline/ratchet files (.type-ignore-baseline, .duplication-baseline, …)
    may only shrink. A hand-raised ratchet — numeric value bumped up, or new
    entries added — is a top reviewer finding when it happens."""
    findings = []
    for f in files:
        name = f.path.rsplit("/", 1)[-1].lower()
        if "baseline" not in name and "ratchet" not in name:
            continue
        if f.status == "added":
            continue  # creating a new baseline IS the gate being introduced
        before = _baseline_values(f.removed_lines)
        after = _baseline_values([line.content for line in f.added_lines])
        grew = {key: (before[key], val) for key, val in after.items() if key in before and val > before[key]}
        added_count = sum(1 for line in f.added_lines if line.content.strip())
        removed_count = sum(1 for line in f.removed_lines if line.strip())
        if grew:
            detail = "; ".join(f"{key or 'value'}: {old} -> {new}" for key, (old, new) in grew.items())
            findings.append(
                Finding(
                    rule_id="det/ratchet-increased",
                    rule_title="Ratchet/baseline raised",
                    status=VerdictStatus.VIOLATION,
                    severity=Severity.BLOCKING,
                    evidence=f.path,
                    explanation=f"Baseline raised ({detail}). Ratchet files may only shrink — "
                    "fix the new violations instead of raising the baseline.",
                    suggested_fix="Revert the baseline bump and fix the underlying issues.",
                    deterministic=True,
                )
            )
        elif added_count > removed_count:
            findings.append(
                Finding(
                    rule_id="det/ratchet-increased",
                    rule_title="Ratchet/baseline file grew",
                    status=VerdictStatus.VIOLATION,
                    severity=Severity.BLOCKING,
                    evidence=f.path,
                    explanation=f"Baseline gained {added_count - removed_count} net entr"
                    f"{'y' if added_count - removed_count == 1 else 'ies'}. Ratchet files may "
                    "only shrink — fix the new violations instead of expanding the allowlist.",
                    suggested_fix="Remove the new baseline entries and fix the underlying issues.",
                    deterministic=True,
                )
            )
    return findings


def check_workflow_job_hygiene(files: list[FileDiff]) -> list[Finding]:
    """New GitHub Actions jobs without timeout-minutes hang runners on wedge;
    new workflow files without a concurrency group stack duplicate runs."""
    findings = []
    for f in files:
        if "/.github/workflows/" not in f"/{f.path}" or not f.path.endswith((".yml", ".yaml")):
            continue
        added_text = "\n".join(line.content for line in f.added_lines)
        new_jobs = [line for line in f.added_lines if re.match(r"\s+runs-on:", line.content)]
        if new_jobs and "timeout-minutes" not in added_text:
            findings.append(
                Finding(
                    rule_id="det/job-timeout-missing",
                    rule_title="New CI job(s) without timeout-minutes",
                    status=VerdictStatus.UNCERTAIN,
                    severity=Severity.NOTE,
                    evidence=f"{f.path}:{new_jobs[0].line_number}",
                    explanation=f"{len(new_jobs)} new job(s) added with no timeout-minutes in the "
                    "diff — a wedged step holds the runner for GitHub's 6-hour default.",
                    suggested_fix="Add `timeout-minutes:` to each new job.",
                    deterministic=True,
                )
            )
        if f.status == "added" and "concurrency" not in added_text:
            findings.append(
                Finding(
                    rule_id="det/workflow-no-concurrency",
                    rule_title="New workflow without concurrency group",
                    status=VerdictStatus.UNCERTAIN,
                    severity=Severity.NOTE,
                    evidence=f.path,
                    explanation="New workflow file has no `concurrency:` group — pushes in quick "
                    "succession will stack duplicate runs.",
                    suggested_fix="Add a concurrency group keyed on the ref, with cancel-in-progress.",
                    deterministic=True,
                )
            )
    return findings


ALL_CHECKS = [
    check_type_ignore_added,
    check_lint_suppression_added,
    check_test_skip_added,
    check_test_skip_multilang,
    check_ci_weakening,
    check_fixme_deleted,
    check_guard_file_touched,
    check_rules_modified,
    check_ratchet_increased,
    check_workflow_job_hygiene,
]


def run_deterministic_checks(files: list[FileDiff]) -> list[Finding]:
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(files))
    return findings
