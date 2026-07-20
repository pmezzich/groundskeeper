"""Freshness watcher tests — catch generated artifacts left stale against their source."""

from __future__ import annotations

from groundskeeper.freshness import (
    FreshnessRule,
    check_freshness,
    load_rules,
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
        # Only the rule whose source changed fires (a tools change -> agent-index).
        findings = check_freshness([_file("src/core/tools/products.py")], rules_for_repo("prebid/salesagent"))
        assert [f.rule_id for f in findings] == ["freshness/agent-index"]


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
        assert {r.id for r in rules} == {"frontend-types", "agent-index", "reference-formats"}

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
