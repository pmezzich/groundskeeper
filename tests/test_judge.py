"""Tests for the pure rule-applicability filter (no API).

`rule_applies` decides which rules ever reach the LLM judge, so a wrong glob
match silently drops or over-applies a rule. Pinned here without touching the API.
"""

from __future__ import annotations

import shutil

from groundskeeper.judge import _parse_judge_json, resolve_backend, rule_applies
from groundskeeper.models import Rule, VerdictStatus


def _rule(*globs: str) -> Rule:
    return Rule(id="r", title="R", source_file="f", source_section="R", body="b", file_globs=list(globs))


def test_glob_matches_path():
    assert rule_applies(_rule("src/**"), "src/foo.py") is True


def test_glob_does_not_match_unrelated_path():
    assert rule_applies(_rule("src/**"), "README.md") is False


def test_double_star_matches_everything():
    assert rule_applies(_rule("**"), "anything/at/all.txt") is True


def test_any_matching_glob_qualifies():
    assert rule_applies(_rule("tests/**", "test_*"), "tests/test_x.py") is True


def test_no_globs_never_applies():
    assert rule_applies(_rule(), "src/foo.py") is False


class TestResolveBackend:
    """Backend selection: explicit choice > env var > auto-detect."""

    def test_explicit_api(self, monkeypatch):
        monkeypatch.delenv("GROUNDSKEEPER_JUDGE_BACKEND", raising=False)
        assert resolve_backend("api") == "api"

    def test_explicit_claude_cli(self, monkeypatch):
        monkeypatch.delenv("GROUNDSKEEPER_JUDGE_BACKEND", raising=False)
        assert resolve_backend("claude-cli") == "claude-cli"

    def test_env_var_selects_backend(self, monkeypatch):
        monkeypatch.setenv("GROUNDSKEEPER_JUDGE_BACKEND", "claude-cli")
        assert resolve_backend() == "claude-cli"

    def test_auto_prefers_api_key(self, monkeypatch):
        monkeypatch.delenv("GROUNDSKEEPER_JUDGE_BACKEND", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert resolve_backend("auto") == "api"

    def test_auto_falls_back_to_claude_cli(self, monkeypatch):
        monkeypatch.delenv("GROUNDSKEEPER_JUDGE_BACKEND", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
        assert resolve_backend("auto") == "claude-cli"

    def test_auto_none_when_nothing_available(self, monkeypatch):
        monkeypatch.delenv("GROUNDSKEEPER_JUDGE_BACKEND", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert resolve_backend("auto") is None


class TestParseJudgeJson:
    """The CLI backend gets plain text back and must recover the verdict JSON."""

    def test_valid_json(self):
        text = (
            '{"verdicts": [{"rule_id": "r1", "status": "violation", '
            '"evidence": "f.py:1", "explanation": "bad"}]}'
        )
        resp = _parse_judge_json(text)
        assert resp is not None
        assert len(resp.verdicts) == 1
        assert resp.verdicts[0].rule_id == "r1"
        assert resp.verdicts[0].status == VerdictStatus.VIOLATION

    def test_json_embedded_in_prose_and_fences(self):
        text = 'Here is my verdict:\n```json\n{"verdicts": []}\n```\nDone.'
        resp = _parse_judge_json(text)
        assert resp is not None
        assert resp.verdicts == []

    def test_no_json_returns_none(self):
        assert _parse_judge_json("no json here at all") is None
