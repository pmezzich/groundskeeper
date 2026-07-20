"""Groundskeeper CLI.

Usage:
    groundskeeper review 1371 --repo prebid/salesagent
    groundskeeper review https://github.com/prebid/salesagent/pull/1371 --no-llm
    groundskeeper review 1371 --post           # post the report as a PR comment
    groundskeeper rules --repo prebid/salesagent
    groundskeeper benchmark 1389 --repo prebid/salesagent
    groundskeeper watch --judge claude-cli        # review all open PRs (dry-run)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from rich.console import Console

from groundskeeper import github
from groundskeeper.checks import run_deterministic_checks
from groundskeeper.models import Finding, Rule
from groundskeeper.report import print_console_report, render_markdown_report
from groundskeeper.rules import compile_rules_dir, compile_rules_file

console = Console()

DEFAULT_REPO = "prebid/salesagent"
DEFAULT_RULES_PATH = ".claude/rules/patterns"


BUNDLED_RULES_DIR = Path(__file__).resolve().parent.parent.parent / "rules"


def _load_rules(args: argparse.Namespace, token: str, base_ref: str, repo: str) -> tuple[list[Rule], str]:
    """Local --rules-dir(s) win; otherwise fetch rule files from the PR's BASE
    ref. Groundskeeper's bundled supplementary rules are always appended.

    Returns (rules, provenance_note) — reports must say where rules came from."""
    rules: list[Rule] = []
    parts: list[str] = []
    if args.rules_dir:
        for rules_dir in args.rules_dir:
            rules.extend(compile_rules_dir(Path(rules_dir)))
        if not rules:
            console.print(f"[red]No rules found under {args.rules_dir}[/red]")
            sys.exit(1)
        parts.append(f"{len(rules)} from local rules dir(s)")
    else:
        contents = github.fetch_rules_from_base(repo, base_ref, token, DEFAULT_RULES_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in contents.items():
                p = Path(tmp) / name
                p.write_text(text, encoding="utf-8")
                rules.extend(compile_rules_file(p, source_label=name))
        if not rules:
            console.print(f"[red]No rule files found at {repo}@{base_ref}:{DEFAULT_RULES_PATH}[/red]")
            sys.exit(1)
        parts.append(f"{len(rules)} from this repo's `.claude/rules` (base ref)")

    if BUNDLED_RULES_DIR.is_dir():
        seen = {r.id for r in rules}
        bundled_n = 0
        for rule in compile_rules_dir(BUNDLED_RULES_DIR):
            if rule.id not in seen:
                rules.append(rule)
                bundled_n += 1
        if bundled_n:
            parts.append(f"{bundled_n} bundled")
    return rules, f"Rules: {', '.join(parts)}."


def _run_review(args: argparse.Namespace) -> tuple[str, int, list[Finding], str]:
    token = github.resolve_token()
    repo, number = github.parse_pr_ref(args.pr, args.repo)

    console.print(f"[bold]Fetching {repo}#{number}…[/bold]")
    at_commit = getattr(args, "at_commit", None)
    if getattr(args, "first_review", False):
        at_commit = github.resolve_first_review_commit(repo, number, token)
        if at_commit:
            console.print(f"  first human review was at commit {at_commit[:10]}")
        else:
            console.print("[yellow]  no human review found — using current head[/yellow]")
    if at_commit:
        pr = github.fetch_pull_request_at_commit(repo, number, at_commit, token)
    else:
        pr = github.fetch_pull_request(repo, number, token)
    console.print(f"  {len(pr.files)} changed files, base={pr.base_ref}")

    rules, rules_note = _load_rules(args, token, pr.base_ref, repo)
    console.print(f"  {len(rules)} rules compiled")

    findings = run_deterministic_checks(pr.files)
    from groundskeeper.freshness import check_freshness, rules_for_repo

    freshness = check_freshness(pr.files, rules_for_repo(repo))
    findings.extend(freshness)
    note = f"  deterministic checks: {len(findings)} finding(s)"
    if freshness:
        note += f" (incl. {len(freshness)} stale-artifact)"
    console.print(note)

    if args.no_llm:
        console.print("[yellow]--no-llm: skipping semantic judge[/yellow]")
    else:
        from groundskeeper.judge import judge_all, resolve_backend

        backend = resolve_backend(getattr(args, "judge", None))
        if backend is None:
            console.print(
                "[yellow]No judge backend — set ANTHROPIC_API_KEY or install the `claude` CLI. "
                "Deterministic checks only.[/yellow]"
            )
        else:
            console.print(f"  judging semantic rules… (backend: {backend})")
            findings.extend(judge_all(rules, pr.files, backend=backend))

    return repo, number, findings, rules_note


def cmd_review(args: argparse.Namespace) -> None:
    repo, number, findings, rules_note = _run_review(args)
    print_console_report(repo, number, findings)

    if args.output:
        Path(args.output).write_text(
            render_markdown_report(repo, number, findings, rules_note), encoding="utf-8"
        )
        console.print(f"[dim]Markdown report saved to {args.output}[/dim]")

    if args.post:
        token = github.resolve_token()
        url = github.post_pr_comment(
            repo, number, token, render_markdown_report(repo, number, findings, rules_note)
        )
        console.print(f"[green]Posted:[/green] {url}")


def cmd_rules(args: argparse.Namespace) -> None:
    if args.rules_dir:
        rules = compile_rules_dir(Path(args.rules_dir))
    else:
        token = github.resolve_token()
        contents = github.fetch_rules_from_base(args.repo, args.ref, token, DEFAULT_RULES_PATH)
        rules = []
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in contents.items():
                p = Path(tmp) / name
                p.write_text(text, encoding="utf-8")
                rules.extend(compile_rules_file(p, source_label=name))

    from rich.table import Table

    table = Table(title=f"Compiled rules ({len(rules)})")
    table.add_column("ID", style="cyan")
    table.add_column("Severity")
    table.add_column("Applies to")
    for r in rules:
        table.add_row(r.id, r.severity.value, ", ".join(r.file_globs))
    console.print(table)


def cmd_benchmark(args: argparse.Namespace) -> None:
    repo, number, findings, _ = _run_review(args)
    print_console_report(repo, number, findings)

    token = github.resolve_token()
    comments = github.fetch_human_review_comments(repo, number, token)
    console.print(f"\n[bold]Benchmarking against {len(comments)} human review comment(s)…[/bold]")
    if not comments:
        console.print("[yellow]No human review comments on this PR — nothing to benchmark.[/yellow]")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]Benchmark matching needs ANTHROPIC_API_KEY[/red]")
        sys.exit(1)

    from groundskeeper.benchmark import measure_recall

    report, recall = measure_recall(findings, comments)
    if report is None:
        console.print("[red]Benchmark matching failed[/red]")
        sys.exit(1)

    from rich.table import Table

    table = Table(title="Coverage vs human review")
    table.add_column("Human comment", max_width=60)
    table.add_column("Covered")
    table.add_column("By rule")
    for m in report.matches:
        table.add_row(
            m.reviewer_comment_summary,
            "[green]yes[/green]" if m.covered else "[red]no[/red]",
            m.matching_rule_id or "—",
        )
    console.print(table)
    console.print(f"\n[bold]Recall: {recall:.0%}[/bold] of human review comments covered")


def cmd_watch(args: argparse.Namespace) -> None:
    from groundskeeper.judge import resolve_backend
    from groundskeeper.watch import run_watch

    token = github.resolve_token()
    backend = resolve_backend(getattr(args, "judge", None))
    if backend is None:
        console.print("[red]No judge backend — set ANTHROPIC_API_KEY or install the `claude` CLI.[/red]")
        sys.exit(1)

    mode = "POST (live comments)" if args.post else "dry-run (local reports)"
    console.print(f"[bold]Watching {args.repo}[/bold] — backend: {backend}, mode: {mode}")
    result = run_watch(
        args.repo,
        token,
        backend=backend,
        post=args.post,
        out_dir=Path(args.out_dir),
        state_file=Path(args.state_file),
        include_drafts=args.include_drafts,
        skip_authors=frozenset(args.skip_author or ()),
        limit=args.limit,
        rules_dirs=args.rules_dir,
    )
    tail = f", posted {result.posted}" if args.post else ""
    console.print(f"  reviewed {len(result.reviewed)} PR(s), skipped {result.skipped}{tail}")
    console.print(f"  reports written to [bold]{result.out_dir}[/bold] (see SUMMARY.md)")
    if not args.post:
        console.print("[dim]dry-run: nothing posted. Re-run with --post to go live.[/dim]")


def cmd_watch_all(args: argparse.Namespace) -> None:
    from groundskeeper.judge import resolve_backend
    from groundskeeper.watch import load_repos_config, run_watch_all

    token = github.resolve_token()
    backend = resolve_backend(getattr(args, "judge", None))
    if backend is None:
        console.print("[red]No judge backend — set ANTHROPIC_API_KEY or install the `claude` CLI.[/red]")
        sys.exit(1)

    specs = load_repos_config(Path(args.config))
    if not specs:
        console.print(f"[red]No repos found in config {args.config}[/red]")
        sys.exit(1)

    mode = "POST (live comments)" if args.post else "dry-run (local reports)"
    console.print(f"[bold]Watching {len(specs)} repos[/bold] — backend: {backend}, mode: {mode}")
    results = run_watch_all(
        specs,
        token,
        backend=backend,
        post=args.post,
        out_root=Path(args.out_dir),
        include_drafts=args.include_drafts,
        skip_authors=frozenset(args.skip_author or ()),
        limit=args.limit,
    )
    for repo, result in results:
        if result is None:
            console.print(f"  [red]{repo}: failed (see log)[/red]")
        else:
            console.print(f"  {repo}: reviewed {len(result.reviewed)}, skipped {result.skipped}")
    console.print(f"  index written to [bold]{Path(args.out_dir) / 'INDEX.md'}[/bold]")
    if not args.post:
        console.print("[dim]dry-run: nothing posted. Re-run with --post to go live.[/dim]")


def cmd_freshness(args: argparse.Namespace) -> None:
    from rich.table import Table

    from groundskeeper.freshness import load_rules, regenerate_and_check, rules_for_repo

    rules = load_rules(args.manifest) if args.manifest else rules_for_repo(args.repo)
    if not rules:
        console.print(f"[yellow]No freshness rules for {args.manifest or args.repo}.[/yellow]")
        return
    console.print(f"[bold]Freshness[/bold] — regenerating {len(rules)} artifact(s) in {args.repo_path}")
    stale = regenerate_and_check(args.repo_path, rules)
    by_id = {s.rule_id: s for s in stale}

    table = Table(title="Generated-artifact freshness")
    table.add_column("Artifact", style="cyan")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    for rule in rules:
        s = by_id.get(rule.id)
        if s is None:
            table.add_row(rule.label, "[green]fresh[/green]", "")
        elif s.error:
            table.add_row(rule.label, "[yellow]could not run[/yellow]", s.error)
        else:
            first = s.changes.splitlines()[0] if s.changes else ""
            table.add_row(rule.label, "[red]STALE[/red]", f"{first}  |  fix: {rule.refresh}")
    console.print(table)

    genuine = [s for s in stale if not s.error]
    if genuine:
        console.print(f"\n[red]{len(genuine)} artifact(s) out of date[/red] - run the refresh command(s).")
        sys.exit(1)
    console.print("\n[green]all generated artifacts are up to date[/green]")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="groundskeeper",
        description="Skills-enforcement PR review — compiles .claude/rules into verdicts",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    review = sub.add_parser("review", help="Review a PR against the repo's rules")
    review.add_argument("pr", help="PR number or full URL")
    review.add_argument("--repo", default=DEFAULT_REPO)
    review.add_argument(
        "--rules-dir",
        action="append",
        help="Local rules dir (repeatable; overrides base-ref fetch)",
    )
    review.add_argument("--no-llm", action="store_true", help="Deterministic checks only")
    review.add_argument(
        "--judge",
        choices=["auto", "api", "claude-cli"],
        default="auto",
        help="Judge backend: 'api' or 'claude-cli' (a Claude subscription). Default: auto.",
    )
    review.add_argument("--output", help="Save markdown report to this path")
    review.add_argument("--post", action="store_true", help="Post report as a PR comment")
    review.add_argument("--at-commit", help="Judge the diff as of this head SHA (compare vs base)")
    review.add_argument(
        "--first-review",
        action="store_true",
        help="Judge the diff the first human review saw (benchmark mode)",
    )

    rules_p = sub.add_parser("rules", help="List compiled rules")
    rules_p.add_argument("--repo", default=DEFAULT_REPO)
    rules_p.add_argument("--ref", default="main")
    rules_p.add_argument("--rules-dir")

    bench = sub.add_parser("benchmark", help="Measure recall vs human review on a PR")
    bench.add_argument("pr", help="PR number or full URL")
    bench.add_argument("--repo", default=DEFAULT_REPO)
    bench.add_argument("--rules-dir", action="append")
    bench.add_argument("--no-llm", action="store_true")
    bench.add_argument("--at-commit")
    bench.add_argument("--first-review", action="store_true", default=True)

    watch_p = sub.add_parser("watch", help="Review a repo's open PRs; dry-run local reports or --post live")
    watch_p.add_argument("--repo", default=DEFAULT_REPO)
    watch_p.add_argument(
        "--judge",
        choices=["auto", "api", "claude-cli"],
        default="auto",
        help="Judge backend: 'api' or 'claude-cli' (a Claude subscription). Default: auto.",
    )
    watch_p.add_argument(
        "--post",
        action="store_true",
        help="Post comments on the PRs (default: dry-run, local reports only)",
    )
    watch_p.add_argument(
        "--out-dir",
        default="groundskeeper-reports",
        help="Directory for per-PR reports + SUMMARY.md",
    )
    watch_p.add_argument(
        "--state-file",
        default=".groundskeeper-state.json",
        help="Tracks reviewed head SHAs so unchanged PRs are skipped between runs",
    )
    watch_p.add_argument("--limit", type=int, default=10, help="Max PRs to review per run")
    watch_p.add_argument("--include-drafts", action="store_true", help="Also review draft PRs")
    watch_p.add_argument(
        "--skip-author",
        action="append",
        help="Skip PRs by this author, e.g. a bot (repeatable)",
    )
    watch_p.add_argument(
        "--rules-dir",
        action="append",
        help="Local rules dir for this repo (repeatable) — e.g. the mined mobile corpus",
    )

    watchall = sub.add_parser("watch-all", help="Watch every repo in a config file (one INDEX.md across all)")
    watchall.add_argument(
        "--config",
        required=True,
        help='JSON: {"repos": ["org/repo", {"repo": "org/r", "rules_dir": "mobile/rules"}]}',
    )
    watchall.add_argument(
        "--judge",
        choices=["auto", "api", "claude-cli"],
        default="auto",
        help="Judge backend: 'api' or 'claude-cli' (a Claude subscription). Default: auto.",
    )
    watchall.add_argument(
        "--post", action="store_true", help="Post comments (default: dry-run, local reports only)"
    )
    watchall.add_argument(
        "--out-dir",
        default="groundskeeper-reports",
        help="Root dir; one subfolder per repo + an aggregate INDEX.md",
    )
    watchall.add_argument("--limit", type=int, default=10, help="Max PRs per repo per run")
    watchall.add_argument("--include-drafts", action="store_true", help="Also review draft PRs")
    watchall.add_argument("--skip-author", action="append", help="Skip PRs by this author (repeatable)")

    fresh_p = sub.add_parser(
        "freshness",
        help="Regenerate each artifact and report which are actually stale vs committed (exit 1 on stale)",
    )
    fresh_p.add_argument("--repo-path", required=True, help="Path to a repo checkout (clean working tree)")
    fresh_p.add_argument("--repo", default=DEFAULT_REPO, help="Repo name for bundled freshness rules")
    fresh_p.add_argument("--manifest", help="Path to a freshness JSON manifest (overrides --repo bundle)")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(name)s %(levelname)s: %(message)s",
    )

    if args.command == "review":
        cmd_review(args)
    elif args.command == "rules":
        cmd_rules(args)
    elif args.command == "benchmark":
        cmd_benchmark(args)
    elif args.command == "watch":
        cmd_watch(args)
    elif args.command == "watch-all":
        cmd_watch_all(args)
    elif args.command == "freshness":
        cmd_freshness(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
