"""Groundskeeper CLI.

Usage:
    groundskeeper review 1371 --repo prebid/salesagent
    groundskeeper review https://github.com/prebid/salesagent/pull/1371 --no-llm
    groundskeeper review 1371 --post           # post the report as a PR comment
    groundskeeper rules --repo prebid/salesagent
    groundskeeper benchmark 1389 --repo prebid/salesagent
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


def _load_rules(args: argparse.Namespace, token: str, base_ref: str, repo: str) -> list[Rule]:
    """Local --rules-dir(s) win; otherwise fetch rule files from the PR's BASE
    ref. Groundskeeper's bundled supplementary rules are always appended."""
    rules: list[Rule] = []
    if args.rules_dir:
        for rules_dir in args.rules_dir:
            rules.extend(compile_rules_dir(Path(rules_dir)))
        if not rules:
            console.print(f"[red]No rules found under {args.rules_dir}[/red]")
            sys.exit(1)
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

    if BUNDLED_RULES_DIR.is_dir():
        seen = {r.id for r in rules}
        for rule in compile_rules_dir(BUNDLED_RULES_DIR):
            if rule.id not in seen:
                rules.append(rule)
    return rules


def _run_review(args: argparse.Namespace) -> tuple[str, int, list[Finding]]:
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

    rules = _load_rules(args, token, pr.base_ref, repo)
    console.print(f"  {len(rules)} rules compiled")

    findings = run_deterministic_checks(pr.files)
    console.print(f"  deterministic checks: {len(findings)} finding(s)")

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

    return repo, number, findings


def cmd_review(args: argparse.Namespace) -> None:
    repo, number, findings = _run_review(args)
    print_console_report(repo, number, findings)

    if args.output:
        Path(args.output).write_text(render_markdown_report(repo, number, findings), encoding="utf-8")
        console.print(f"[dim]Markdown report saved to {args.output}[/dim]")

    if args.post:
        token = github.resolve_token()
        url = github.post_pr_comment(repo, number, token, render_markdown_report(repo, number, findings))
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
    repo, number, findings = _run_review(args)
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
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
