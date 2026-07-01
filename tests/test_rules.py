"""Unit tests for the markdown rule compiler (the docs-are-the-config core)."""

from __future__ import annotations

from pathlib import Path

from groundskeeper.models import Severity
from groundskeeper.rules import compile_rules_dir, compile_rules_file

_SAMPLE = """# Test Patterns

Preamble text — not a rule.

## First Rule Title

Body of the first rule.

```python
# WRONG
x = 1
```

## Second Rule (non-blocking)

Body of the second rule.

## Empty Section
"""


def _sample(tmp_path: Path) -> Path:
    p = tmp_path / "test-patterns.md"
    p.write_text(_SAMPLE, encoding="utf-8")
    return p


def test_one_rule_per_section_empty_skipped(tmp_path: Path):
    rules = compile_rules_file(_sample(tmp_path))
    # Empty-body "## Empty Section" is skipped -> 2 rules, in order.
    assert [r.title for r in rules] == ["First Rule Title", "Second Rule (non-blocking)"]


def test_rule_id_is_stem_plus_slug(tmp_path: Path):
    rules = compile_rules_file(_sample(tmp_path))
    assert rules[0].id == "test-patterns/first-rule-title"


def test_non_blocking_title_becomes_note_severity(tmp_path: Path):
    rules = compile_rules_file(_sample(tmp_path))
    assert rules[0].severity == Severity.BLOCKING
    assert rules[1].severity == Severity.NOTE  # "non-blocking" in the title


def test_globs_derived_from_filename_keyword(tmp_path: Path):
    # filename contains "test" -> the test-file applicability globs
    rules = compile_rules_file(_sample(tmp_path))
    assert "tests/**" in rules[0].file_globs


def test_body_retains_wrong_correct_examples(tmp_path: Path):
    rules = compile_rules_file(_sample(tmp_path))
    assert "# WRONG" in rules[0].body  # the judge needs the examples, so they must survive


def test_compile_dir_reads_every_md(tmp_path: Path):
    (tmp_path / "a-patterns.md").write_text("## Rule A\n\nbody a\n", encoding="utf-8")
    (tmp_path / "b-patterns.md").write_text("## Rule B\n\nbody b\n", encoding="utf-8")
    rules = compile_rules_dir(tmp_path)
    assert {r.title for r in rules} == {"Rule A", "Rule B"}
