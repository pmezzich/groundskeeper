"""Open-PR watcher — review a repo's open PRs on a schedule.

Dry-run (default) writes each PR's report to a local folder; --post publishes
the SAME body as a PR comment. The two modes share all logic and differ by one
flag, so the run that produces a preview for sign-off becomes the live bot
unchanged — nothing diverges between "show it" and "ship it".

State: a JSON map of {pr_number: last_reviewed_head_sha} so a PR is re-reviewed
only when its head moves (keeps subscription usage proportional to real churn).
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from groundskeeper import github
from groundskeeper.checks import run_deterministic_checks
from groundskeeper.judge import judge_all
from groundskeeper.models import Finding, PRRef, Rule, VerdictStatus
from groundskeeper.report import render_markdown_report
from groundskeeper.rules import compile_rules_dir, compile_rules_file

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = ".claude/rules/patterns"
BUNDLED_RULES_DIR = Path(__file__).resolve().parent.parent.parent / "rules"


def select_prs_to_review(
    open_prs: list[PRRef],
    state: dict[str, str],
    *,
    include_drafts: bool = False,
    skip_authors: frozenset[str] = frozenset(),
    limit: int = 10,
) -> list[PRRef]:
    """Pure: the open PRs that still need review this pass.

    Skips drafts (unless included), skip_authors, and any PR whose current head
    SHA we've already reviewed. Returns at most `limit`, in input order (the
    caller lists most-recently-updated first).
    """
    selected: list[PRRef] = []
    for pr in open_prs:
        if pr.draft and not include_drafts:
            continue
        if pr.author in skip_authors:
            continue
        if state.get(str(pr.number)) == pr.head_sha:
            continue
        selected.append(pr)
        if len(selected) >= limit:
            break
    return selected


def _load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read state file %s; starting fresh", path)
        return {}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    return {}


def _save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _compile_rules(repo: str, base_ref: str, token: str) -> list[Rule]:
    """Rules from the PR's BASE ref (a PR can't edit rules to pass itself),
    plus groundskeeper's bundled supplementary rules."""
    rules: list[Rule] = []
    contents = github.fetch_rules_from_base(repo, base_ref, token, DEFAULT_RULES_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in contents.items():
            p = Path(tmp) / name
            p.write_text(text, encoding="utf-8")
            rules.extend(compile_rules_file(p, source_label=name))
    if BUNDLED_RULES_DIR.is_dir():
        seen = {r.id for r in rules}
        for rule in compile_rules_dir(BUNDLED_RULES_DIR):
            if rule.id not in seen:
                rules.append(rule)
    return rules


def review_one(repo: str, number: int, token: str, backend: str) -> list[Finding]:
    """Full pipeline for one PR: deterministic checks + semantic judge."""
    pr = github.fetch_pull_request(repo, number, token)
    rules = _compile_rules(repo, pr.base_ref, token)
    findings = run_deterministic_checks(pr.files)
    findings.extend(judge_all(rules, pr.files, backend=backend))
    return findings


@dataclass
class WatchResult:
    reviewed: list[tuple[PRRef, list[Finding]]] = field(default_factory=list)
    skipped: int = 0
    posted: int = 0
    out_dir: Path | None = None


def _counts(findings: list[Finding]) -> tuple[int, int]:
    v = sum(1 for f in findings if f.status == VerdictStatus.VIOLATION)
    u = sum(1 for f in findings if f.status == VerdictStatus.UNCERTAIN)
    return v, u


def _write_summary(out_dir: Path, repo: str, result: WatchResult, post: bool) -> None:
    mode = "POSTED as PR comments" if post else "DRY-RUN — local only, nothing posted"
    lines = [
        f"# Groundskeeper watch — {repo}",
        "",
        f"Mode: **{mode}**.",
        "",
        f"Reviewed {len(result.reviewed)} PR(s); skipped {result.skipped} "
        "(drafts / already-reviewed / filtered).",
        "",
        "| PR | Title | Violations | Uncertain | Report |",
        "|---|---|---|---|---|",
    ]
    for pr_ref, findings in result.reviewed:
        v, u = _counts(findings)
        title = pr_ref.title.replace("|", "\\|")
        lines.append(
            f"| [#{pr_ref.number}]({pr_ref.url}) | {title} | {v} | {u} "
            f"| [`pr-{pr_ref.number}.md`](pr-{pr_ref.number}.md) |"
        )
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_watch(
    repo: str,
    token: str,
    *,
    backend: str,
    post: bool,
    out_dir: Path,
    state_file: Path,
    include_drafts: bool = False,
    skip_authors: frozenset[str] = frozenset(),
    limit: int = 10,
) -> WatchResult:
    """Review the repo's open PRs. Dry-run writes reports; post also comments."""
    state = _load_state(state_file)
    open_prs = github.list_open_pull_requests(repo, token)
    to_review = select_prs_to_review(
        open_prs,
        state,
        include_drafts=include_drafts,
        skip_authors=skip_authors,
        limit=limit,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    result = WatchResult(out_dir=out_dir, skipped=len(open_prs) - len(to_review))

    for pr_ref in to_review:
        logger.info("reviewing %s#%d @ %s", repo, pr_ref.number, pr_ref.head_sha[:10])
        findings = review_one(repo, pr_ref.number, token, backend)
        body = render_markdown_report(repo, pr_ref.number, findings)
        (out_dir / f"pr-{pr_ref.number}.md").write_text(body, encoding="utf-8")
        if post:
            url = github.post_pr_comment(repo, pr_ref.number, token, body)
            logger.info("  posted: %s", url)
            result.posted += 1
        state[str(pr_ref.number)] = pr_ref.head_sha
        result.reviewed.append((pr_ref, findings))

    _save_state(state_file, state)
    _write_summary(out_dir, repo, result, post)
    return result
