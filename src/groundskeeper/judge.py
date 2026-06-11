"""LLM judge — verdicts for the semantic rules deterministic greps can't decide.

Design constraints (carried over from pr-agents where they earned their keep):
- Component isolation: the judge sees rule text + diff hunks ONLY. Never the
  PR title or description, so author claims can't bias verdicts.
- Pre-digested input: relevant hunks per rule group, never the raw full patch.
- Per-group exception isolation: a failed call degrades to `uncertain`
  verdicts, never kills the run.
- Prompt caching: instructions + rule text are the stable prefix
  (cache_control), the per-PR hunks come after it.
"""

from __future__ import annotations

import fnmatch
import logging
import os

import anthropic

from groundskeeper.models import (
    Finding,
    FileDiff,
    JudgeResponse,
    Rule,
    VerdictStatus,
)

logger = logging.getLogger(__name__)

MODEL = os.environ.get("GROUNDSKEEPER_MODEL", "claude-opus-4-8")

_JUDGE_INSTRUCTIONS = """You are a code-review compliance judge for the prebid/salesagent repository.

You receive: (1) a set of numbered rules, each with WRONG/CORRECT examples, and
(2) diff hunks from a pull request (added lines only, with line numbers).

For EVERY rule, return a verdict:
- "violation": an added line clearly matches the rule's WRONG pattern. Cite file:line.
- "pass": the diff touches code the rule governs and complies with it.
- "not_applicable": the diff doesn't exercise this rule at all.
- "uncertain": you suspect a violation but the hunks don't contain enough
  context to be sure. Explain what you'd need to see.

Verdict discipline:
- Judge ONLY what is in the hunks. Never assume code outside them.
- A rule's WRONG example is illustrative, not exhaustive — judge the principle.
- Do not report style nits the rules don't cover.
- Report every genuine violation, even low-confidence ones, as "uncertain"
  rather than silently dropping them — a downstream filter handles ranking.
- evidence must be the exact "path:line" of an added line you were shown.

Justification-comment policy (calibrated against review history):
- A comment EXCUSES a deviation only when it cites a tracked external
  obligation: a GitHub issue (#NNNN), a spec section, or an SDK/version
  constraint with a stated revisit condition.
- A comment that merely EXPLAINS the deviation — "matches existing
  patterns", "consistent with the file", "will clean up later" with no
  issue link — does NOT excuse it. Existing debt is not a template;
  report these as violations.
- When unsure which kind a comment is, return "uncertain", never "pass"."""


def rule_applies(rule: Rule, path: str) -> bool:
    return any(
        fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, f"*/{glob}") or glob == "**"
        for glob in rule.file_globs
    )


def _hunks_block(files: list[FileDiff], rules: list[Rule], max_chars: int = 60_000) -> str:
    """Render added-line hunks for files any rule in this group applies to."""
    parts: list[str] = []
    for f in files:
        if not any(rule_applies(r, f.path) for r in rules):
            continue
        if not f.added_lines:
            continue
        lines = "\n".join(f"{line.line_number}: {line.content}" for line in f.added_lines)
        parts.append(f"=== {f.path} ({f.status}) ===\n{lines}")
    block = "\n\n".join(parts)
    if len(block) > max_chars:
        block = block[:max_chars] + "\n\n[... truncated for length ...]"
    return block


def _rules_block(rules: list[Rule]) -> str:
    return "\n\n".join(
        f"### RULE {r.id}\nTitle: {r.title}\nSource: {r.source_file} § {r.source_section}\n\n{r.body}"
        for r in rules
    )


def judge_rule_group(
    client: anthropic.Anthropic,
    rules: list[Rule],
    files: list[FileDiff],
) -> list[Finding]:
    """One API call judging one group of rules against the relevant hunks."""
    hunks = _hunks_block(files, rules)
    if not hunks:
        return [
            Finding(
                rule_id=r.id,
                rule_title=r.title,
                status=VerdictStatus.NOT_APPLICABLE,
                severity=r.severity,
                explanation="No changed files match this rule's scope.",
            )
            for r in rules
        ]

    rules_by_id = {r.id: r for r in rules}
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": _JUDGE_INSTRUCTIONS,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": _rules_block(rules),
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": f"Diff hunks to judge:\n\n{hunks}"}],
            output_format=JudgeResponse,
        )
        parsed = response.parsed_output
    except anthropic.APIError as exc:
        logger.warning("Judge call failed for group %s: %s", [r.id for r in rules], exc)
        parsed = None

    if parsed is None:
        return [
            Finding(
                rule_id=r.id,
                rule_title=r.title,
                status=VerdictStatus.UNCERTAIN,
                severity=r.severity,
                explanation="Judge call failed — rule not evaluated this run.",
            )
            for r in rules
        ]

    findings: list[Finding] = []
    seen: set[str] = set()
    for verdict in parsed.verdicts:
        rule = rules_by_id.get(verdict.rule_id)
        if rule is None:
            continue
        seen.add(rule.id)
        findings.append(
            Finding(
                rule_id=rule.id,
                rule_title=rule.title,
                status=verdict.status,
                severity=rule.severity,
                evidence=verdict.evidence,
                explanation=verdict.explanation,
                suggested_fix=verdict.suggested_fix,
            )
        )
    # Any rule the model dropped from its response degrades to uncertain.
    for rule in rules:
        if rule.id not in seen:
            findings.append(
                Finding(
                    rule_id=rule.id,
                    rule_title=rule.title,
                    status=VerdictStatus.UNCERTAIN,
                    severity=rule.severity,
                    explanation="Judge omitted this rule from its response.",
                )
            )
    return findings


def judge_all(rules: list[Rule], files: list[FileDiff]) -> list[Finding]:
    """Group rules by source file (one call per rule file) and judge each group."""
    client = anthropic.Anthropic()
    groups: dict[str, list[Rule]] = {}
    for rule in rules:
        groups.setdefault(rule.source_file, []).append(rule)

    findings: list[Finding] = []
    for source, group in groups.items():
        logger.info("Judging %d rules from %s", len(group), source)
        findings.extend(judge_rule_group(client, group, files))
    return findings
