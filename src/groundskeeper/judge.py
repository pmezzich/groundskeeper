"""LLM judge — verdicts for the semantic rules deterministic greps can't decide.

Design constraints (carried over from pr-agents where they earned their keep):
- Component isolation: the judge sees rule text + diff hunks ONLY. Never the
  PR title or description, so author claims can't bias verdicts.
- Pre-digested input: relevant hunks per rule group, never the raw full patch.
- Per-group exception isolation: a failed call degrades to `uncertain`
  verdicts, never kills the run.
- Prompt caching: instructions + rule text are the stable prefix
  (cache_control), the per-PR hunks come after it (api backend).

Two interchangeable backends — the caller picks, or it auto-detects:
- "api": the Anthropic API (`messages.parse` structured output). Needs
  ANTHROPIC_API_KEY; metered per-token billing.
- "claude-cli": shells out to `claude -p` (Claude Code). Runs on a Claude
  subscription's usage window — no API key, no per-token cost.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess

import anthropic

from groundskeeper.models import (
    FileDiff,
    Finding,
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

# Appended only for the claude-cli backend, which has no enforced output schema.
_JSON_TAIL = """

Respond with ONLY a JSON object of this exact shape — no prose, no code fences:
{"verdicts": [{"rule_id": "<id>", "status": "violation|pass|not_applicable|uncertain", \
"evidence": "path:line or null", "explanation": "<one to three sentences>", \
"suggested_fix": "<concrete fix or null>"}]}"""


def resolve_backend(explicit: str | None = None) -> str | None:
    """Pick the judge backend: explicit choice > env var > auto-detect.

    Returns "api", "claude-cli", or None when neither an API key nor the
    `claude` CLI is available (caller then runs deterministic checks only).
    """
    choice = (explicit or os.environ.get("GROUNDSKEEPER_JUDGE_BACKEND") or "auto").strip().lower()
    if choice in ("api", "claude-cli"):
        return choice
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if shutil.which("claude"):
        return "claude-cli"
    return None


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


def _parse_judge_json(text: str) -> JudgeResponse | None:
    """Extract and validate the verdict JSON returned by the CLI backend."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return JudgeResponse.model_validate_json(match.group(0))
    except ValueError:
        return None


def _judge_via_cli(system_text: str, user_text: str) -> JudgeResponse | None:
    """One judge call through `claude -p` — subscription auth, no API key."""
    claude = shutil.which("claude") or "claude"
    prompt = f"{system_text}{_JSON_TAIL}\n\n{user_text}"
    try:
        # Prompt goes on STDIN, not argv: the rules+hunks prompt routinely
        # exceeds the OS command-line length limit (~32 KB on Windows), which
        # would truncate or fail an argv-passed prompt. Force UTF-8 so unicode
        # in rules/diffs (arrows, em-dashes) can't hit the Windows cp1252 codec.
        proc = subprocess.run(
            [claude, "-p", "--output-format", "json", "--model", MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            # No console window on Windows: when run from a scheduled task, a
            # flashing cmd window looks like malware — and a user closing it
            # kills the whole run tree (observed in production).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("claude CLI judge call failed: %s", exc)
        return None
    if proc.returncode != 0:
        logger.warning("claude CLI exited %d: %s", proc.returncode, proc.stderr[:300])
        return None
    # `--output-format json` wraps the reply in an envelope; unwrap to the text.
    text = proc.stdout
    try:
        envelope = json.loads(proc.stdout)
        if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
            text = envelope["result"]
    except json.JSONDecodeError:
        pass
    return _parse_judge_json(text)


def _judge_via_api(client: anthropic.Anthropic, rules: list[Rule], hunks: str) -> JudgeResponse | None:
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[
                {"type": "text", "text": _JUDGE_INSTRUCTIONS, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": _rules_block(rules), "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": f"Diff hunks to judge:\n\n{hunks}"}],
            output_format=JudgeResponse,
        )
        return response.parsed_output
    except anthropic.APIError as exc:
        logger.warning("Judge API call failed for group %s: %s", [r.id for r in rules], exc)
        return None


def judge_rule_group(
    rules: list[Rule],
    files: list[FileDiff],
    backend: str,
    client: anthropic.Anthropic | None = None,
) -> list[Finding]:
    """Judge one group of rules against the relevant hunks, via `backend`."""
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

    if backend == "claude-cli":
        parsed = _judge_via_cli(
            f"{_JUDGE_INSTRUCTIONS}\n\n{_rules_block(rules)}", f"Diff hunks to judge:\n\n{hunks}"
        )
    else:
        parsed = _judge_via_api(client or anthropic.Anthropic(), rules, hunks)

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

    rules_by_id = {r.id: r for r in rules}
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


def judge_all(rules: list[Rule], files: list[FileDiff], backend: str | None = None) -> list[Finding]:
    """Group rules by source file (one call per rule file) and judge each group."""
    resolved = resolve_backend(backend)
    if resolved is None:
        logger.warning("No judge backend available (no ANTHROPIC_API_KEY, no `claude` CLI).")
        return []

    client = anthropic.Anthropic() if resolved == "api" else None
    groups: dict[str, list[Rule]] = {}
    for rule in rules:
        groups.setdefault(rule.source_file, []).append(rule)

    findings: list[Finding] = []
    for source, group in groups.items():
        logger.info("Judging %d rules from %s (backend=%s)", len(group), source, resolved)
        findings.extend(judge_rule_group(group, files, resolved, client))
    return findings
