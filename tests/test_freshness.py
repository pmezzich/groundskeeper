"""Freshness watcher tests — catch generated artifacts left stale against their source."""

from __future__ import annotations

import subprocess
from pathlib import Path

from groundskeeper.freshness import (
    FreshnessRule,
    auto_refresh,
    check_freshness,
    load_rules,
    regenerate_and_check,
    rules_for_repo,
)
from groundskeeper.models import FileDiff, Severity, VerdictStatus


def _file(path: str) -> FileDiff:
    return FileDiff(path=path, status="modified")


_RULE = FreshnessRule(
    id="frontend-types",
    label="the frontend types",
    sources=["src/core/schemas/**"],
    generated=["static/js/generated-types.d.ts"],
    refresh="uv run python scripts/generate_frontend_types.py",
)


class TestCheckFreshness:
    def test_source_changed_without_regen_is_flagged(self) -> None:
        findings = check_freshness([_file("src/core/schemas/product.py")], [_RULE])
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "freshness/frontend-types"
        assert f.severity == Severity.NOTE
        assert f.status == VerdictStatus.UNCERTAIN
        assert f.evidence == "src/core/schemas/product.py"
        assert "generate_frontend_types.py" in (f.suggested_fix or "")

    def test_source_and_artifact_changed_together_is_clean(self) -> None:
        files = [_file("src/core/schemas/product.py"), _file("static/js/generated-types.d.ts")]
        assert check_freshness(files, [_RULE]) == []

    def test_unrelated_change_is_clean(self) -> None:
        assert check_freshness([_file("README.md"), _file("src/routes/api_v1.py")], [_RULE]) == []

    def test_only_artifact_changed_is_clean(self) -> None:
        # Regenerating without a source change (a manual refresh) is fine.
        assert check_freshness([_file("static/js/generated-types.d.ts")], [_RULE]) == []

    def test_rules_are_independent(self) -> None:
        # Only the rule whose source changed fires (a schema change -> frontend-types).
        files = [_file("src/core/schemas/product.py")]
        findings = check_freshness(files, rules_for_repo("prebid/salesagent"))
        assert [f.rule_id for f in findings] == ["freshness/frontend-types"]


class TestGlobMatching:
    def test_double_star_matches_any_depth(self) -> None:
        rule = FreshnessRule(id="x", label="x", sources=["src/core/tools/**"], generated=["out"], refresh="r")
        assert check_freshness([_file("src/core/tools/creatives/_sync.py")], [rule])
        assert check_freshness([_file("src/core/other.py")], [rule]) == []

    def test_exact_generated_path_match(self) -> None:
        rule = FreshnessRule(id="x", label="x", sources=["a/**"], generated=["b/exact.json"], refresh="r")
        # source touched AND the exact generated file touched -> clean
        assert check_freshness([_file("a/x.py"), _file("b/exact.json")], [rule]) == []


class TestManifests:
    def test_salesagent_bundle_has_rules(self) -> None:
        rules = rules_for_repo("prebid/salesagent")
        assert {r.id for r in rules} == {"frontend-types", "reference-formats"}

    def test_unknown_repo_has_no_rules(self) -> None:
        assert rules_for_repo("prebid/prebid.js") == []

    def test_load_rules_from_json(self, tmp_path) -> None:
        manifest = tmp_path / "freshness.json"
        manifest.write_text(
            '{"rules": [{"id": "r1", "label": "L", "sources": ["s/**"], '
            '"generated": ["g"], "refresh": "cmd"}]}',
            encoding="utf-8",
        )
        rules = load_rules(manifest)
        assert len(rules) == 1 and rules[0].id == "r1"
        assert len(check_freshness([_file("s/x.py")], rules)) == 1


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


class TestRegenerateAndCheck:
    """The ground-truth engine: run the generator, diff vs committed, restore."""

    def _repo(self, tmp_path: Path, committed: str, generator_writes: str) -> Path:
        repo = tmp_path
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t.t")
        _git(repo, "config", "user.name", "t")
        (repo / "src").mkdir()
        (repo / "src" / "model.py").write_text("x = 1", encoding="utf-8")
        (repo / "out.txt").write_text(committed, encoding="utf-8")
        (repo / "gen.py").write_text(f"open('out.txt', 'w').write({generator_writes!r})", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        return repo

    def _rule(self) -> FreshnessRule:
        return FreshnessRule(
            id="out", label="out.txt", sources=["src/**"], generated=["out.txt"], refresh="python gen.py"
        )

    def test_detects_a_stale_artifact(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path, "v1", "v2")  # generator output differs from committed
        stale = regenerate_and_check(repo, [self._rule()])
        assert len(stale) == 1
        assert stale[0].rule_id == "out" and stale[0].error is None
        assert "out.txt" in stale[0].changes
        # working tree restored to the committed state
        assert (repo / "out.txt").read_text(encoding="utf-8") == "v1"
        assert _git(repo, "status", "--porcelain").strip() == ""

    def test_fresh_artifact_is_clean(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path, "v1", "v1")  # generator reproduces the committed copy
        assert regenerate_and_check(repo, [self._rule()]) == []
        assert _git(repo, "status", "--porcelain").strip() == ""

    def test_generator_failure_is_reported_not_raised(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path, "v1", "v1")
        bad = FreshnessRule(
            id="bad",
            label="bad",
            sources=["src/**"],
            generated=["out.txt"],
            refresh='python -c "import sys; sys.exit(3)"',
        )
        stale = regenerate_and_check(repo, [bad])
        assert len(stale) == 1 and stale[0].error is not None and "exit 3" in stale[0].error


class TestAutoRefresh:
    """The auto-update loop: report (preview) / commit (local refresh branch)."""

    def _repo(self, tmp_path: Path, committed: str, generator_writes: str) -> Path:
        repo = tmp_path
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t.t")
        _git(repo, "config", "user.name", "t")
        (repo / "src").mkdir()
        (repo / "src" / "model.py").write_text("x = 1", encoding="utf-8")
        (repo / "out.txt").write_text(committed, encoding="utf-8")
        (repo / "gen.py").write_text(f"open('out.txt', 'w').write({generator_writes!r})", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        return repo

    def _rule(self) -> FreshnessRule:
        return FreshnessRule(
            id="out", label="out.txt", sources=["src/**"], generated=["out.txt"], refresh="python gen.py"
        )

    def test_report_mode_previews_without_writing(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path, "v1", "v2")
        (r,) = auto_refresh(repo, [self._rule()], mode="report")
        assert r.stale and not r.committed and not r.branch
        assert (repo / "out.txt").read_text(encoding="utf-8") == "v1"  # untouched
        assert _git(repo, "status", "--porcelain").strip() == ""

    def test_commit_mode_opens_a_refresh_branch(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path, "v1", "v2")
        base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        (r,) = auto_refresh(repo, [self._rule()], mode="commit")
        assert r.stale and r.committed and r.branch == "groundskeeper/refresh-out"
        # returned to a clean base with the original content intact
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == base
        assert (repo / "out.txt").read_text(encoding="utf-8") == "v1"
        assert _git(repo, "status", "--porcelain").strip() == ""
        # the refresh branch carries the regenerated artifact
        assert _git(repo, "show", "groundskeeper/refresh-out:out.txt").strip() == "v2"

    def test_fresh_artifact_needs_no_branch(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path, "v1", "v1")
        (r,) = auto_refresh(repo, [self._rule()], mode="commit")
        assert not r.stale and not r.committed and not r.branch
