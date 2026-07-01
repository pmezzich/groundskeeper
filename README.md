# Groundskeeper

A PR review agent that compiles a repo's own `.claude/rules` into an enforceable
ruleset and checks pull-request diffs against it — **CI built against the skills
themselves.**

Instead of generic best-practices, it enforces *your* repo's documented
conventions and is trained on *your* reviewers' actual comments. Benchmarked on
`prebid/salesagent` at **~39% recall / ~85% precision** vs. human reviewers on
held-out PRs; the two general-purpose bots on that repo covered **0** of the
human-flagged issues where they were active.

## How it works

1. **Fetch** the PR diff (`--first-review` judges it as it stood at the first
   human review — used for benchmarking).
2. **Compile** rules from `.claude/rules` at the PR's **base ref** — so a PR
   can't edit the rules to pass itself.
3. **Deterministic checks** first (keyless, no LLM): snuck-in test skips,
   `# type: ignore`, CI weakening (`-k not`, `--deselect`, `|| true`),
   guard-file / ratchet tampering, missing job timeouts.
4. **LLM judge** for the semantic rules — each rule ships its WRONG/CORRECT
   examples plus only the relevant hunks, and returns a strict per-rule verdict.
   The judge never sees the PR title or description, so author framing can't
   bias it.
5. **Output** a compliance table (console, markdown, or posted as a PR comment).

## Usage

```bash
groundskeeper review 1371 --repo prebid/salesagent      # console report
groundskeeper review 1371 --no-llm                      # keyless (no API key)
groundskeeper review 1371 --judge claude-cli            # judge on a Claude subscription (no key)
groundskeeper review 1371 --post                        # post as a PR comment
groundskeeper rules  --repo prebid/salesagent           # list compiled rules
groundskeeper benchmark 1389 --repo prebid/salesagent   # recall vs human review
```

The LLM judge runs via either the Anthropic API (`ANTHROPIC_API_KEY`, metered)
or `claude -p` (a Claude subscription — no key, no per-token cost); pick with
`--judge {auto,api,claude-cli}`. With neither available, `review` runs the
deterministic checks only.

## Run it as CI

Groundskeeper runs from its own repo and reviews other repos' PRs — see
[`.github/WORKFLOWS.md`](.github/WORKFLOWS.md). The deterministic half runs with
no secrets; the judge half needs an API key as a repo secret.

## Docs

- [`GROUNDSKEEPER-OVERVIEW.md`](GROUNDSKEEPER-OVERVIEW.md) — full design,
  benchmark methodology, and numbers.
- [`mobile/`](mobile/) — a prototype aiming groundskeeper at the Prebid mobile
  SDKs (mined rule corpus + a held-out benchmark).

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy src/groundskeeper --ignore-missing-imports
uv run pytest
```

CI runs all of the above on every PR and push to `main`.
