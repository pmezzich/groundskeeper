"""Artifact freshness — catch generated files that went stale against their source.

Repos carry artifacts generated FROM source: TypeScript types built from the
Pydantic schemas, a reference fixture captured from the pinned agent, a symbol
index stubbed from the tool modules. Each ships with a "run this script when the
source changes" note, and a human has to remember. When they don't, the artifact
silently falls behind the code it was generated from — the same failure mode as
the docs drift, one level in.

This watches that gap: on a PR that changes a source-of-truth but does NOT also
refresh the artifact it generates, it flags exactly which artifact is now stale
and the command to refresh it. Deterministic, per-PR, no LLM — the generated copy
can't fall behind silently, because falling behind makes noise.

A repo can ship its own rules (``load_rules`` reads a JSON manifest); the known
repos below are bundled so it works out of the box.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from groundskeeper.models import FileDiff, Finding, Severity, VerdictStatus


class FreshnessRule(BaseModel):
    """One generated artifact and the source it must stay in sync with."""

    id: str
    label: str
    sources: list[str] = Field(default_factory=list)  # globs: the source of truth
    generated: list[str] = Field(default_factory=list)  # globs: the derived artifact
    refresh: str  # the command that regenerates the artifact


def _matches(path: str, pattern: str) -> bool:
    """Glob match with ``**`` meaning 'any characters, including /'."""
    if "**" in pattern:
        rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        return re.fullmatch(rx, path) is not None
    return fnmatch.fnmatch(path, pattern)


def _any_match(paths: list[str], patterns: list[str]) -> list[str]:
    return [p for p in paths if any(_matches(p, pat) for pat in patterns)]


def check_freshness(files: list[FileDiff], rules: list[FreshnessRule]) -> list[Finding]:
    """Flag each rule whose source changed in this PR without the artifact refreshed.

    A rule fires only when a source-of-truth file changed AND no file matching the
    generated artifact changed — i.e. the derived copy was left behind. A PR that
    edits the source and regenerates the artifact together trips nothing.
    """
    paths = [f.path for f in files]
    findings: list[Finding] = []
    for rule in rules:
        touched_sources = _any_match(paths, rule.sources)
        if not touched_sources or _any_match(paths, rule.generated):
            continue
        head = ", ".join(sorted(touched_sources)[:3]) + (" …" if len(touched_sources) > 3 else "")
        findings.append(
            Finding(
                rule_id=f"freshness/{rule.id}",
                rule_title=f"Generated artifact may be stale: {rule.label}",
                status=VerdictStatus.UNCERTAIN,
                severity=Severity.NOTE,
                evidence=touched_sources[0],
                explanation=(
                    f"This PR changes {head} — the source of {rule.label} — but does not refresh "
                    f"{' / '.join(rule.generated)}. Regenerate it so the checked-in copy does not "
                    f"fall behind: `{rule.refresh}`"
                ),
                suggested_fix=rule.refresh,
                deterministic=True,
            )
        )
    return findings


# Bundled manifests for known repos. A repo can also ship its own (load_rules).
SALESAGENT_RULES: list[FreshnessRule] = [
    FreshnessRule(
        id="frontend-types",
        label="the frontend TypeScript types",
        sources=["src/core/schemas/**"],
        generated=["static/js/generated-types.d.ts"],
        refresh="uv run python scripts/generate_frontend_types.py",
    ),
    FreshnessRule(
        id="agent-index",
        label="the AI agent symbol index",
        sources=["src/core/tools/**"],
        generated=[".agent-index/**"],
        refresh="uv run python scripts/gen-agent-index.py",
    ),
    FreshnessRule(
        id="reference-formats",
        label="the creative reference-formats fixture",
        sources=["scripts/creative-agent-stack.sh"],
        generated=["tests/fixtures/creative_formats/reference_formats.json"],
        refresh=(
            "uv run python scripts/refresh-reference-formats.py "
            "--url $(scripts/creative-agent-stack.sh url)"
        ),
    ),
]

_BUNDLED: dict[str, list[FreshnessRule]] = {
    "prebid/salesagent": SALESAGENT_RULES,
}


def rules_for_repo(repo: str) -> list[FreshnessRule]:
    """Bundled freshness rules for a known repo (empty list for repos with none)."""
    return _BUNDLED.get(repo, [])


def load_rules(path: str | Path) -> list[FreshnessRule]:
    """Load freshness rules from a repo-shipped JSON manifest.

    Accepts either ``{"rules": [...]}`` or a bare ``[...]`` of rule objects.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("rules", []) if isinstance(data, dict) else data
    return [FreshnessRule(**r) for r in raw]
