"""Tests for the pure rule-applicability filter (no API).

`rule_applies` decides which rules ever reach the LLM judge, so a wrong glob
match silently drops or over-applies a rule. Pinned here without touching the API.
"""

from __future__ import annotations

from groundskeeper.judge import rule_applies
from groundskeeper.models import Rule


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
