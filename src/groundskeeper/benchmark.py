"""Calibration harness — replay groundskeeper against a human-reviewed PR and
measure recall vs the human reviewer's findings as ground truth.

This is the demo's closing slide: "on PR #1389 groundskeeper covered N of M
of Konstantin's review comments."
"""

from __future__ import annotations

import logging
import os

import anthropic

from groundskeeper.models import BenchmarkReport, Finding, VerdictStatus

logger = logging.getLogger(__name__)

MODEL = os.environ.get("GROUNDSKEEPER_MODEL", "claude-opus-4-8")

_MATCH_INSTRUCTIONS = """You compare a bot's PR-review findings against a human reviewer's
inline comments, to measure the bot's recall.

For EACH human comment, decide whether any bot finding covers the same underlying
issue. "Covers" means the bot flagged the same problem class at the same location
or in clearly the same code — not merely a vaguely related topic.

Ignore human comments that are questions, praise, or process chatter with no
actionable code issue — mark those covered=true with matching_rule_id=null and
reasoning "not an actionable finding"."""


def measure_recall(
    findings: list[Finding],
    human_comments: list[dict[str, str]],
) -> tuple[BenchmarkReport | None, float]:
    """Returns (report, recall) where recall counts actionable human comments
    covered by at least one bot finding."""
    if not human_comments:
        return None, 0.0

    actionable_findings = [
        f for f in findings if f.status in (VerdictStatus.VIOLATION, VerdictStatus.UNCERTAIN)
    ]
    findings_block = "\n".join(
        f"- [{f.rule_id}] {f.evidence or ''}: {f.explanation}" for f in actionable_findings
    ) or "(the bot produced no findings)"
    comments_block = "\n\n".join(
        f"COMMENT {i + 1} ({c['author']} on {c['path']}):\n{c['body'][:800]}"
        for i, c in enumerate(human_comments)
    )

    client = anthropic.Anthropic()
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": _MATCH_INSTRUCTIONS}],
            messages=[
                {
                    "role": "user",
                    "content": f"BOT FINDINGS:\n{findings_block}\n\nHUMAN COMMENTS:\n{comments_block}",
                }
            ],
            output_format=BenchmarkReport,
        )
        report = response.parsed_output
    except anthropic.APIError as exc:
        logger.warning("Benchmark matching call failed: %s", exc)
        return None, 0.0

    if report is None or not report.matches:
        return report, 0.0
    covered = sum(1 for m in report.matches if m.covered)
    return report, covered / len(report.matches)
