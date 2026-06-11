"""Rule compiler — parses markdown rule files into a rule registry.

The repo's own ``.claude/rules/patterns/*.md`` files ARE the config. Each
``##`` section becomes one Rule. No onboarding JSON, no duplication: when
the docs change, the enforced ruleset changes with them.

Rules must always be loaded from the BASE ref of the repo under review,
never the PR head — otherwise a PR could edit the rules to pass itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from groundskeeper.models import Rule, Severity

# Maps rule-file name patterns to the file globs their rules apply to.
# A rule only reaches the judge when the PR touches a matching file.
# Order matters: first keyword match wins.
_DEFAULT_APPLICABILITY: dict[str, list[str]] = {
    "ci-": [".github/workflows/**", "Makefile", "tox.ini", "scripts/**", "*.sh", "*baseline*"],
    "src-": ["src/**", "scripts/**"],
    "test": ["tests/**", "test_*", "*_test.py", "conftest.py"],
    "code": ["src/**", "scripts/**", "*.py"],
    "mcp": ["src/**"],
}

_SECTION_RE = re.compile(r"^## (?!Also See|See Also)(.+)$", re.MULTILINE)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60]


def _globs_for(file_name: str) -> list[str]:
    for keyword, globs in _DEFAULT_APPLICABILITY.items():
        if keyword in file_name:
            return globs
    return ["**"]


def compile_rules_file(path: Path, source_label: str | None = None) -> list[Rule]:
    """Parse one markdown rule file into Rules — one per ``##`` section."""
    text = path.read_text(encoding="utf-8")
    label = source_label or path.name
    globs = _globs_for(path.name.lower())

    sections = _SECTION_RE.split(text)
    # sections = [preamble, title1, body1, title2, body2, ...]
    rules: list[Rule] = []
    for i in range(1, len(sections) - 1, 2):
        title = sections[i].strip()
        body = sections[i + 1].strip()
        if not body:
            continue
        severity = Severity.NOTE if "non-blocking" in title.lower() else Severity.BLOCKING
        rules.append(
            Rule(
                id=f"{path.stem}/{_slugify(title)}",
                title=title,
                source_file=label,
                source_section=title,
                body=body,
                file_globs=globs,
                severity=severity,
            )
        )
    return rules


def compile_rules_dir(rules_dir: Path) -> list[Rule]:
    """Compile every pattern file under a .claude/rules directory."""
    patterns_dir = rules_dir / "patterns"
    search_dir = patterns_dir if patterns_dir.is_dir() else rules_dir
    rules: list[Rule] = []
    for md in sorted(search_dir.glob("*.md")):
        rules.extend(compile_rules_file(md))
    return rules
