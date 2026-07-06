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
        if len(selected) >= limit:
            break
        if pr.draft and not include_drafts:
            continue
        if pr.author in skip_authors:
            continue
        if state.get(str(pr.number)) == pr.head_sha:
            continue
        selected.append(pr)
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


def _compile_rules(repo: str, base_ref: str, token: str, rules_dirs: list[str] | None = None) -> list[Rule]:
    """Compile the rules to judge this repo against, from three sources
    (deduped by id, first-seen wins):
      1. explicit local rules dirs (e.g. the mined mobile corpus for a repo with
         no `.claude/rules` of its own),
      2. the repo's own `.claude/rules` at the PR's BASE ref (a PR can't edit
         rules to pass itself) — absent for most non-salesagent repos, which is
         fine: that fetch returns nothing rather than erroring,
      3. groundskeeper's bundled supplementary rules.
    """
    rules: list[Rule] = []
    seen: set[str] = set()

    def _add(candidates: list[Rule]) -> None:
        for rule in candidates:
            if rule.id not in seen:
                seen.add(rule.id)
                rules.append(rule)

    for rules_dir in rules_dirs or []:
        _add(compile_rules_dir(Path(rules_dir)))

    contents = github.fetch_rules_from_base(repo, base_ref, token, DEFAULT_RULES_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in contents.items():
            p = Path(tmp) / name
            p.write_text(text, encoding="utf-8")
            _add(compile_rules_file(p, source_label=name))

    if BUNDLED_RULES_DIR.is_dir():
        _add(compile_rules_dir(BUNDLED_RULES_DIR))
    return rules


def review_one(
    repo: str, number: int, token: str, backend: str, rules_dirs: list[str] | None = None
) -> list[Finding]:
    """Full pipeline for one PR: deterministic checks + semantic judge."""
    pr = github.fetch_pull_request(repo, number, token)
    rules = _compile_rules(repo, pr.base_ref, token, rules_dirs)
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
    rules_dirs: list[str] | None = None,
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
        findings = review_one(repo, pr_ref.number, token, backend, rules_dirs)
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


@dataclass
class RepoSpec:
    """One repo to watch, with an optional local rules dir (e.g. a mined corpus
    for a repo that has no `.claude/rules` of its own)."""

    repo: str
    rules_dir: str | None = None


def load_repos_config(path: Path) -> list[RepoSpec]:
    """Parse a repos config file. Entries may be a bare "org/repo" string or an
    object {"repo": "org/repo", "rules_dir": "path"}. Malformed entries (no repo)
    are skipped rather than aborting the run."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    specs: list[RepoSpec] = []
    for entry in data.get("repos", []):
        if isinstance(entry, str):
            specs.append(RepoSpec(repo=entry))
        elif isinstance(entry, dict) and entry.get("repo"):
            specs.append(RepoSpec(repo=str(entry["repo"]), rules_dir=entry.get("rules_dir")))
    return specs


def _repo_slug(repo: str) -> str:
    return repo.replace("/", "__")


def _write_index(out_root: Path, results: list[tuple[str, WatchResult | None]], post: bool) -> None:
    mode = "POSTED as PR comments" if post else "DRY-RUN — local only, nothing posted"
    lines = [
        "# Groundskeeper watch — all repos",
        "",
        f"Mode: **{mode}**.",
        "",
        "| Repo | Reviewed | Violations | Uncertain | Report |",
        "|---|---|---|---|---|",
    ]
    for repo, result in results:
        if result is None:
            lines.append(f"| {repo} | — | — | — | _(failed — see log)_ |")
            continue
        v = sum(_counts(f)[0] for _, f in result.reviewed)
        u = sum(_counts(f)[1] for _, f in result.reviewed)
        slug = _repo_slug(repo)
        lines.append(
            f"| {repo} | {len(result.reviewed)} | {v} | {u} | [`{slug}/SUMMARY.md`]({slug}/SUMMARY.md) |"
        )
    (out_root / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_watch_all(
    specs: list[RepoSpec],
    token: str,
    *,
    backend: str,
    post: bool,
    out_root: Path,
    include_drafts: bool = False,
    skip_authors: frozenset[str] = frozenset(),
    limit: int = 10,
) -> list[tuple[str, WatchResult | None]]:
    """Watch each repo in turn (own subfolder + state), then write an aggregate
    INDEX.md. One repo failing (transient API error, etc.) must not abort the
    rest, so per-repo exceptions are caught and recorded as a null result."""
    out_root.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, WatchResult | None]] = []
    for spec in specs:
        sub = out_root / _repo_slug(spec.repo)
        rules_dirs = [spec.rules_dir] if spec.rules_dir else None
        try:
            result: WatchResult | None = run_watch(
                spec.repo,
                token,
                backend=backend,
                post=post,
                out_dir=sub,
                state_file=sub / "state.json",
                include_drafts=include_drafts,
                skip_authors=skip_authors,
                limit=limit,
                rules_dirs=rules_dirs,
            )
        except Exception as exc:  # noqa: BLE001 — batch resilience: skip a bad repo, keep going
            logger.warning("watch failed for %s: %s", spec.repo, exc)
            result = None
        results.append((spec.repo, result))
    _write_index(out_root, results, post)
    return results
