"""Unit tests for the keyless deterministic checks.

This is the layer that runs in CI with no API key, so it's the most important to
pin. Each test drives the real production function with a constructed FileDiff
and asserts the actual Finding — nothing about the code under test is mocked.
"""

from __future__ import annotations

from groundskeeper.checks import (
    check_ci_weakening,
    check_lint_suppression_added,
    check_test_skip_added,
    check_test_skip_multilang,
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


class TestLintSuppressionAdded:
    """Blanket -> violation; scoped -> note; conventional/vendored/prose -> silent."""

    def test_ts_ignore_is_a_violation(self):
        findings = check_lint_suppression_added(
            [_file("src/utils.ts", "// @ts-ignore", "const x = risky();")]
        )
        assert len(findings) == 1
        assert findings[0].rule_id == "det/lint-suppression-added"
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_bare_eslint_disable_is_a_violation(self):
        findings = check_lint_suppression_added([_file("modules/bidder.js", "/* eslint-disable */")])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_scoped_eslint_disable_is_uncertain_not_violation(self):
        # Prebid.js convention: scoped disables without `--` descriptions are
        # routine — they must surface as a note, never a blocking violation.
        findings = check_lint_suppression_added(
            [_file("modules/fooRtdProvider.js", "// eslint-disable-next-line no-console")]
        )
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.UNCERTAIN

    def test_bare_nolint_is_a_violation(self):
        findings = check_lint_suppression_added([_file("adapters/foo/foo.go", "x := y //nolint")])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_scoped_nolint_is_uncertain(self):
        findings = check_lint_suppression_added([_file("adapters/foo/foo.go", "x := y //nolint:gocritic")])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.UNCERTAIN

    def test_unrelated_extension_ignored(self):
        assert check_lint_suppression_added([_file("notes.md", "// @ts-ignore")]) == []

    def test_moved_suppression_not_flagged(self):
        f = FileDiff(
            path="src/a.ts",
            status="modified",
            added_lines=[DiffLine(line_number=1, content="// @ts-ignore")],
            removed_lines=["// @ts-ignore"],
        )
        assert check_lint_suppression_added([f]) == []

    def test_conventional_java_suppress_warnings_is_silent(self):
        # @SuppressWarnings("unchecked") is reviewer-accepted boilerplate in
        # prebid-server-java — flagging it would flood every adapter PR.
        f = _file("src/main/java/FooBidder.java", '    @SuppressWarnings("unchecked")')
        assert check_lint_suppression_added([f]) == []

    def test_unconventional_java_suppress_is_a_note(self):
        f = _file("src/main/java/FooBidder.java", '    @SuppressWarnings("NullAway")')
        findings = check_lint_suppression_added([f])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.UNCERTAIN

    def test_string_literal_mention_not_flagged(self):
        # a directive INSIDE a plain string is not a directive
        f = _file("gulpfile.js", "const DIRECTIVE = 'eslint-disable';")
        assert check_lint_suppression_added([f]) == []

    def test_leftmost_directive_wins_over_prose_mention(self):
        line = "// eslint-disable-next-line no-undef -- window shim, replaces old @ts-ignore"
        findings = check_lint_suppression_added([_file("src/shim.js", line)])
        assert len(findings) == 1
        assert "eslint-disable" in findings[0].rule_title  # not judged as @ts-ignore
        assert findings[0].status == VerdictStatus.UNCERTAIN

    def test_vendored_and_minified_files_skipped(self):
        assert (
            check_lint_suppression_added(
                [
                    _file("libraries/vendored/lib.js", "/* eslint-disable */"),
                    _file("dist/bundle.min.js", "// @ts-ignore"),
                ]
            )
            == []
        )

    def test_kotlin_file_level_suppress_matched(self):
        f = _file("src/main/kotlin/Foo.kt", '@file:Suppress("NOTHING_TO_INLINE")')
        findings = check_lint_suppression_added([f])
        assert len(findings) == 1


class TestCrossLanguageTestSkips:
    def test_js_it_skip_is_a_violation(self):
        findings = check_test_skip_multilang([_file("test/spec/modules/bidder_spec.js", "it.skip('x', fn)")])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_js_spec_file_detected_without_test_in_path(self):
        # .spec. naming alone must qualify as a test file
        findings = check_test_skip_multilang([_file("src/foo.spec.ts", "describe.skip('suite', fn)")])
        assert len(findings) == 1

    def test_js_only_is_a_violation_even_with_quoted_name(self):
        # the quoted arg is the test NAME, not a reason — .only never passes
        findings = check_test_skip_multilang([_file("test/spec/x_spec.js", "it.only('runs', fn)")])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION
        assert ".only" in findings[0].rule_title

    def test_go_skip_with_message_is_uncertain(self):
        findings = check_test_skip_multilang([_file("adapters/foo/foo_test.go", 't.Skip("flaky upstream")')])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.UNCERTAIN

    def test_go_bare_skip_behind_getenv_is_a_violation(self):
        # the quote in os.Getenv("CI") must NOT count as a skip reason
        f = _file("adapters/foo/foo_test.go", 'if os.Getenv("CI") == "" { t.Skip() }')
        findings = check_test_skip_multilang([f])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_junit_disabled_with_reason_is_uncertain(self):
        findings = check_test_skip_multilang([_file("src/test/java/FooTest.java", '@Disabled("see #123")')])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.UNCERTAIN

    def test_bare_junit_ignore_is_a_violation(self):
        findings = check_test_skip_multilang([_file("src/test/java/FooTest.java", "@Ignore")])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_annotation_mention_in_comment_not_flagged(self):
        f = _file("src/test/java/FooTest.java", "// TODO: revisit the @Disabled cases")
        assert check_test_skip_multilang([f]) == []

    def test_xctskipif_conditional_gate_is_uncertain_not_violation(self):
        # capability gating (XCTSkipIf/Unless) is a legitimate iOS idiom
        f = _file("PrebidMobileTests/RenderingTests.swift", "try XCTSkipIf(isSimulator)")
        findings = check_test_skip_multilang([f])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.UNCERTAIN

    def test_fit_function_call_not_flagged(self):
        # `fit(` is a real function name (model.fit, image.fit) — Jasmine focus
        # syntax is deliberately not matched
        files = [
            _file("tests/test_model.py", "    model.fit(X_train, y_train)"),
            _file("test/spec/util_spec.js", "const r = fit(points);"),
        ]
        assert check_test_skip_multilang(files) == []
        assert run_deterministic_checks(files) == []

    def test_docs_under_test_dir_not_scanned(self):
        # markdown/fixtures are not code — samples must not produce findings
        f = _file("test/README.md", "Use `it.only(...)` to focus a test locally.")
        assert check_test_skip_multilang([f]) == []

    def test_python_files_never_touched_by_multilang_check(self):
        f = _file("Tests/test_x.py", "@pytest.mark.skip")
        assert check_test_skip_multilang([f]) == []


class TestPythonBehaviorFrozen:
    """Regression pins: the Python checks are benchmarked on salesagent review
    history — the multi-language layer must not shift their verdicts."""

    def test_positional_skip_message_is_still_a_violation(self):
        # only reason= or a comment above documents a pytest skip
        findings = check_test_skip_added([_file("tests/test_api.py", '@pytest.mark.skip("slow")')])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_star_args_line_is_not_a_justification_comment(self):
        content = "    x = fetch()  # type: ignore[no-any-return]"
        f = FileDiff(
            path="src/app.py",
            status="modified",
            added_lines=[DiffLine(line_number=4, content=content)],
            patch="+def call(\n+    *args,\n+):\n+" + content,
        )
        findings = check_type_ignore_added([f])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION  # scoped but NOT documented

    def test_capitalized_tests_dir_still_out_of_scope(self):
        # the Python gate is case-sensitive substring 'test' — frozen behavior
        assert check_test_skip_added([_file("Tests/Foo.py", "@pytest.mark.skip")]) == []


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

    def test_or_true_on_js_test_script_flagged(self):
        findings = check_ci_weakening([_file("package.json", '    "test": "jest || true",')])
        assert len(findings) == 1
        assert findings[0].status == VerdictStatus.VIOLATION

    def test_cleanup_of_test_artifacts_not_flagged(self):
        # rm/pkill of tool artifacts is teardown, not weakening — even though
        # the line names a test tool
        files = [
            _file(".github/workflows/ci.yml", "        run: rm -rf mochawesome-report || true"),
            _file(".github/workflows/ci.yml", "        run: pkill -f karma || true"),
            _file("Makefile", "\trm -rf .eslintcache || true"),
        ]
        assert check_ci_weakening(files) == []


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
