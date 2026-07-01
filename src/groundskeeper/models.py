"""Core data models for groundskeeper."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    BLOCKING = "blocking"
    NOTE = "note"


class VerdictStatus(StrEnum):
    PASS = "pass"
    VIOLATION = "violation"
    NOT_APPLICABLE = "not_applicable"
    UNCERTAIN = "uncertain"


class Rule(BaseModel):
    """One enforceable rule compiled from a markdown rule file."""

    id: str
    title: str
    source_file: str
    source_section: str
    body: str
    file_globs: list[str] = Field(default_factory=list)
    severity: Severity = Severity.BLOCKING


class DiffLine(BaseModel):
    """One added line in a diff hunk."""

    line_number: int
    content: str


class FileDiff(BaseModel):
    """Parsed diff for a single changed file."""

    path: str
    status: str  # added | modified | removed | renamed
    added_lines: list[DiffLine] = Field(default_factory=list)
    removed_lines: list[str] = Field(default_factory=list)
    patch: str = ""


class PullRequest(BaseModel):
    """The PR data we judge against. Deliberately excludes title/description —
    code verdicts must never see author claims (component isolation)."""

    number: int
    repo: str
    base_ref: str
    head_sha: str
    files: list[FileDiff] = Field(default_factory=list)


class PRRef(BaseModel):
    """Lightweight open-PR reference for the watcher. `title` is used only for
    the human-facing index — it is never fed to the judge (component isolation)."""

    number: int
    head_sha: str
    draft: bool = False
    author: str = ""
    title: str = ""
    url: str = ""


class Finding(BaseModel):
    """A single check result, deterministic or LLM-judged."""

    rule_id: str
    rule_title: str
    status: VerdictStatus
    severity: Severity
    evidence: str | None = None  # "path/to/file.py:42"
    explanation: str = ""
    suggested_fix: str | None = None
    deterministic: bool = False


# --- LLM structured output schemas (used with client.messages.parse) ---


class RuleVerdict(BaseModel):
    """One rule's verdict from the judge. Strict schema enforced by the API."""

    rule_id: str
    status: VerdictStatus
    evidence: str | None = Field(
        default=None, description="file:line of the violating hunk, when status is violation"
    )
    explanation: str = Field(description="One to three sentences citing the specific code")
    suggested_fix: str | None = Field(
        default=None, description="Concrete fix matching the rule's CORRECT example, when applicable"
    )


class JudgeResponse(BaseModel):
    verdicts: list[RuleVerdict]


class CommentMatch(BaseModel):
    """Benchmark: does one of our findings cover a human reviewer comment?"""

    reviewer_comment_summary: str
    covered: bool
    matching_rule_id: str | None = None
    reasoning: str


class BenchmarkReport(BaseModel):
    matches: list[CommentMatch]
