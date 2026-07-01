"""Tests for the pure github helpers (PR-ref parsing + unified-diff parsing).

No network — these are the deterministic parsers that shape every downstream
check, so they're worth pinning independently of the API calls around them.
"""

from __future__ import annotations

from groundskeeper.github import _parse_unified_diff, parse_pr_ref


class TestParsePrRef:
    def test_bare_number(self):
        assert parse_pr_ref("1371", "prebid/salesagent") == ("prebid/salesagent", 1371)

    def test_hash_number(self):
        assert parse_pr_ref("#42", "o/r") == ("o/r", 42)

    def test_full_url_overrides_default_repo(self):
        assert parse_pr_ref("https://github.com/prebid/salesagent/pull/1371", "other/repo") == (
            "prebid/salesagent",
            1371,
        )


class TestParseUnifiedDiff:
    def test_added_and_removed_with_line_numbers(self):
        patch = "\n".join(
            [
                "@@ -1,3 +1,4 @@",
                " context line",
                "-old line",
                "+new line",
                " another context",
            ]
        )
        added, removed = _parse_unified_diff(patch)
        assert removed == ["old line"]
        assert len(added) == 1
        assert added[0].content == "new line"
        assert added[0].line_number == 2  # one context line after the hunk's +1 start

    def test_file_headers_are_not_treated_as_added_removed(self):
        patch = "\n".join(
            [
                "--- a/foo.py",
                "+++ b/foo.py",
                "@@ -0,0 +1 @@",
                "+only line",
            ]
        )
        added, removed = _parse_unified_diff(patch)
        assert removed == []
        assert [d.content for d in added] == ["only line"]
        assert added[0].line_number == 1
