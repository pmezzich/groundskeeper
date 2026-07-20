"""Docs drift detector — watch the gap between the live site and the migration.

The docs migration converts pages from the live Jekyll site (``master``) onto
the ``docusaurus`` branch one batch at a time, but master keeps moving: the
community edits bidder pages, adds adapters, and fixes typos every day. A
converted copy can silently fall behind the page it was made from — which is
exactly how the last attempt died (five months of unwatched drift, hundreds of
stale pages, nobody knew which).

This watches the gap. Given the SHA the conversion was pinned at, a tree-diff of
the converted source tree (``dev-docs/``) between that SHA and master classifies
every change:

- **drifted** — a page that was already converted has changed on master; its
  converted copy is now stale and needs re-running through the converter.
- **new** — a page added on master that was never converted (a new bidder, say)
  and needs a first conversion.
- **deleted** — a page removed from master whose converted copy is now orphaned.

The same tree-diff serves both jobs: the one-time catch-up measurement AND the
per-PR "did this change touch an already-converted page?" watch. Catch-up and
stay-caught-up are one mechanism.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from groundskeeper.models import FileDiff, Finding, Severity, VerdictStatus

# The live site's source tree that the migration converts. Everything under
# these prefixes on master has a converted counterpart on the docusaurus branch.
DEFAULT_SOURCE_PREFIXES: tuple[str, ...] = ("dev-docs/",)
# The branch that holds the converted pages; its fork point from master is the
# default "converted-at" SHA. Advance it by pinning a later SHA (--since) once a
# catch-up conversion re-syncs a batch.
DEFAULT_CONVERTED_BRANCH = "docusaurus"


class DriftReport(BaseModel):
    """The gap between the converted copies and the live site."""

    since_sha: str = ""
    ref: str = ""
    drifted: list[str] = Field(default_factory=list)  # converted, changed on master
    new: list[str] = Field(default_factory=list)  # never converted
    deleted: list[str] = Field(default_factory=list)  # converted copy now orphaned

    @property
    def total(self) -> int:
        return len(self.drifted) + len(self.new) + len(self.deleted)

    @property
    def has_drift(self) -> bool:
        return self.total > 0

    def summary(self) -> str:
        return (
            f"{len(self.drifted)} drifted, {len(self.new)} new, {len(self.deleted)} deleted"
            + (f" since {self.since_sha[:10]}" if self.since_sha else "")
        )


def _under_source(path: str, source_prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(p) for p in source_prefixes)


def parse_name_status(
    diff_output: str,
    source_prefixes: tuple[str, ...] = DEFAULT_SOURCE_PREFIXES,
    *,
    since_sha: str = "",
    ref: str = "",
) -> DriftReport:
    """Classify ``git diff --name-status`` output into drift buckets.

    Pure — no git, no I/O — so the tree-diff mechanism is testable on a synthetic
    blob. A rename (``R100\\told\\tnew``) counts as drift on the old path: the
    converted copy points at a source path that has moved.
    """
    report = DriftReport(since_sha=since_sha, ref=ref)
    for raw in diff_output.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][:1]
        # For renames/copies the OLD path (parts[1]) is the converted source.
        path = parts[1] if len(parts) > 1 else ""
        new_path = parts[2] if len(parts) > 2 else ""
        if not (_under_source(path, source_prefixes) or _under_source(new_path, source_prefixes)):
            continue
        if status == "A":
            report.new.append(path)
        elif status == "D":
            report.deleted.append(path)
        elif status in ("M", "T"):
            report.drifted.append(path)
        elif status in ("R", "C"):
            report.drifted.append(path)  # moved/copied — the old converted path is stale
    return report


def _git(repo_path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def resolve_since(repo_path: Path, ref: str, converted_branch: str) -> str:
    """The pinned 'converted-at' SHA — the fork point of the converted branch."""
    return _git(repo_path, "merge-base", ref, converted_branch).strip()


def compute_drift(
    repo_path: str | Path,
    since_sha: str | None = None,
    ref: str = "master",
    converted_branch: str = DEFAULT_CONVERTED_BRANCH,
    source_prefixes: tuple[str, ...] = DEFAULT_SOURCE_PREFIXES,
) -> DriftReport:
    """Tree-diff the converted source tree between the pinned SHA and ``ref``."""
    repo = Path(repo_path)
    since = since_sha or resolve_since(repo, ref, converted_branch)
    diff = _git(repo, "diff", "--name-status", "-M", since, ref, "--", *source_prefixes)
    return parse_name_status(diff, source_prefixes, since_sha=since, ref=ref)


def drift_flags(paths: list[str], source_prefixes: tuple[str, ...] = DEFAULT_SOURCE_PREFIXES) -> list[str]:
    """The subset of changed ``paths`` that touch already-converted source — the
    per-PR 'this page was migrated; mirror the change' signal."""
    return [p for p in paths if _under_source(p, source_prefixes)]


def drift_findings(
    files: list[FileDiff],
    source_prefixes: tuple[str, ...] = DEFAULT_SOURCE_PREFIXES,
) -> list[Finding]:
    """Per-PR: flag every changed file under the converted source tree so a PR
    editing an already-migrated page can't merge without mirroring it."""
    findings: list[Finding] = []
    for f in files:
        if not _under_source(f.path, source_prefixes):
            continue
        findings.append(
            Finding(
                rule_id="docs/drift-mirror",
                rule_title="Edits an already-migrated page",
                status=VerdictStatus.UNCERTAIN,
                severity=Severity.NOTE,
                evidence=f.path,
                explanation=(
                    f"`{f.path}` lives under the converted source tree — its copy on the "
                    "docusaurus branch will silently fall behind this change. Re-run the converter "
                    "on this page (or file a mirror task) so the migration stays current."
                ),
                deterministic=True,
            )
        )
    return findings
