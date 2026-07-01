"""Tests for report rendering — pure markdown output, no I/O."""

from __future__ import annotations

from groundskeeper.models import Finding, Severity, VerdictStatus
from groundskeeper.report import render_markdown_report, sort_findings


def _finding(
    status: VerdictStatus,
    title: str = "Rule",
    evidence: str | None = "src/f.py:1",
    explanation: str = "because",
    fix: str | None = None,
) -> Finding:
    return Finding(
        rule_id="r",
        rule_title=title,
        status=status,
        severity=Severity.BLOCKING,
        evidence=evidence,
        explanation=explanation,
        suggested_fix=fix,
    )


def test_markdown_has_header_and_violation_count():
    md = render_markdown_report("owner/repo", 42, [_finding(VerdictStatus.VIOLATION)])
    assert "## Groundskeeper compliance report" in md
    assert "**1 violation(s)**" in md


def test_violation_row_rendered_with_emoji_and_title():
    md = render_markdown_report("o/r", 1, [_finding(VerdictStatus.VIOLATION, title="No print")])
    assert "❌ violation" in md
    assert "No print" in md


def test_not_applicable_findings_excluded_from_table():
    md = render_markdown_report("o/r", 1, [_finding(VerdictStatus.NOT_APPLICABLE, title="SkipMe")])
    assert "SkipMe" not in md


def test_pipe_in_explanation_is_escaped():
    md = render_markdown_report("o/r", 1, [_finding(VerdictStatus.VIOLATION, explanation="a | b")])
    assert "a \\| b" in md  # escaped so it can't break the markdown table


def test_suggested_fix_section_rendered():
    md = render_markdown_report(
        "o/r", 1, [_finding(VerdictStatus.VIOLATION, evidence="x.py:9", fix="do the thing")]
    )
    assert "### Suggested fixes" in md
    assert "do the thing" in md


def test_sort_orders_violation_uncertain_pass():
    findings = [
        _finding(VerdictStatus.PASS),
        _finding(VerdictStatus.VIOLATION),
        _finding(VerdictStatus.UNCERTAIN),
    ]
    assert [f.status for f in sort_findings(findings)] == [
        VerdictStatus.VIOLATION,
        VerdictStatus.UNCERTAIN,
        VerdictStatus.PASS,
    ]
