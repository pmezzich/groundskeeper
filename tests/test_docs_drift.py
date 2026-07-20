"""Docs drift-detector tests — the tree-diff that watches the migration gap."""

from __future__ import annotations

import subprocess
from pathlib import Path

from groundskeeper.docs_drift import (
    compute_drift,
    drift_findings,
    drift_flags,
    parse_name_status,
)
from groundskeeper.models import FileDiff, Severity, VerdictStatus


class TestParseNameStatus:
    def test_classifies_added_modified_deleted(self) -> None:
        blob = "\n".join(
            [
                "M\tdev-docs/bidders/rubicon.md",  # drifted
                "A\tdev-docs/bidders/newbidder.md",  # new
                "D\tdev-docs/bidders/gone.md",  # deleted
                "M\tguides/getting-started.md",  # outside source -> ignored
            ]
        )
        r = parse_name_status(blob)
        assert r.drifted == ["dev-docs/bidders/rubicon.md"]
        assert r.new == ["dev-docs/bidders/newbidder.md"]
        assert r.deleted == ["dev-docs/bidders/gone.md"]
        assert r.total == 3
        assert r.has_drift is True

    def test_rename_counts_as_drift_on_the_old_path(self) -> None:
        r = parse_name_status("R096\tdev-docs/bidders/old.md\tdev-docs/bidders/new.md")
        assert r.drifted == ["dev-docs/bidders/old.md"]

    def test_ignores_paths_outside_the_source_tree(self) -> None:
        r = parse_name_status("M\tsrc/pages/index.js\nA\tREADME.md")
        assert r.total == 0
        assert r.has_drift is False

    def test_custom_source_prefixes(self) -> None:
        r = parse_name_status("M\tdocs/foo.md\nM\tdev-docs/bar.md", source_prefixes=("docs/",))
        assert r.drifted == ["docs/foo.md"]


class TestDriftFlags:
    def test_flags_only_converted_source_paths(self) -> None:
        paths = ["dev-docs/bidders/x.md", "src/components/Foo.tsx", "dev-docs/modules/y.md"]
        assert drift_flags(paths) == ["dev-docs/bidders/x.md", "dev-docs/modules/y.md"]

    def test_empty_when_nothing_touches_source(self) -> None:
        assert drift_flags(["README.md", "package.json"]) == []


class TestDriftFindings:
    def test_flags_a_migrated_page_edit(self) -> None:
        findings = drift_findings([FileDiff(path="dev-docs/bidders/rubicon.md", status="modified")])
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "docs/drift-mirror"
        assert f.severity == Severity.NOTE
        assert f.status == VerdictStatus.UNCERTAIN
        assert f.evidence == "dev-docs/bidders/rubicon.md"
        assert f.deterministic is True

    def test_no_finding_for_non_source_files(self) -> None:
        assert drift_findings([FileDiff(path="src/pages/index.js", status="modified")]) == []


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


class TestComputeDriftOnARealRepo:
    def test_tree_diff_classifies_changes_since_the_pinned_sha(self, tmp_path: Path) -> None:
        repo = tmp_path
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t.t")
        _git(repo, "config", "user.name", "t")
        # The "converted-at" state: two dev-docs pages plus one out-of-tree file.
        (repo / "dev-docs").mkdir()
        (repo / "dev-docs" / "rubicon.md").write_text("v1", encoding="utf-8")
        (repo / "dev-docs" / "gone.md").write_text("bye", encoding="utf-8")
        (repo / "guides.md").write_text("g1", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "converted-at")
        base = _git(repo, "rev-parse", "HEAD").strip()
        # master moves: one page drifts, one new page appears, one is deleted,
        # and an out-of-tree file changes (which must be ignored).
        (repo / "dev-docs" / "rubicon.md").write_text("v2 changed", encoding="utf-8")
        (repo / "dev-docs" / "newbidder.md").write_text("new", encoding="utf-8")
        (repo / "dev-docs" / "gone.md").unlink()
        (repo / "guides.md").write_text("g2", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "master moved")

        report = compute_drift(repo, since_sha=base, ref="HEAD")
        assert report.drifted == ["dev-docs/rubicon.md"]
        assert report.new == ["dev-docs/newbidder.md"]
        assert report.deleted == ["dev-docs/gone.md"]
        assert report.has_drift is True
