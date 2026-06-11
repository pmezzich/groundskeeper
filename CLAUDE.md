# Groundskeeper

Skills-enforcement PR review bot. Compiles a repo's own `.claude/rules/patterns/*.md`
into an executable ruleset and judges PR diffs against it — "CI built against the
skills themselves."

## Why this exists

salesagent has 5 enforcement layers (CI, pre-commit, structural guard tests,
Makefile gates, `.claude/rules`). The rules layer is documentation only — nothing
mechanically enforces it. Groundskeeper closes that gap: the docs ARE the ruleset,
so docs and enforcement cannot drift.

## Architecture

```
src/groundskeeper/
  cli.py        # review / rules / benchmark subcommands
  github.py     # PR fetch, base-ref rule fetch, comment posting (httpx, REST)
  rules.py      # markdown rule files -> Rule registry (## section = rule)
  checks.py     # deterministic diff checks (no LLM): skips, type-ignores, CI weakening
  judge.py      # Claude judge: rule text + hunks -> strict JSON verdicts
  report.py     # rich console table + markdown PR comment
  benchmark.py  # recall vs human reviewer comments as ground truth
  models.py     # pydantic models, incl. structured-output schemas
```

## Key design rules

- **Rules load from the BASE ref, never the PR head** — a PR can't edit the rules
  to pass itself. PRs touching `.claude/rules/` get flagged for human attention.
- **Component isolation** — the judge sees rule text + diff hunks only. Never the
  PR title/description, so author claims can't bias verdicts.
- **Deterministic checks first** — grep-able rules (test skips, `# type: ignore`,
  CI weakening, guard tampering) cost zero LLM calls and never false-positive.
- **Strict JSON verdicts** — `client.messages.parse()` with pydantic schemas;
  per-group failures degrade to `uncertain`, never kill the run.
- **Prompt caching** — judge instructions + rule text are the stable cached prefix;
  per-PR hunks come after.

## Quick start

```bash
uv sync
# GITHUB_TOKEN from env or `gh auth token`; ANTHROPIC_API_KEY for semantic judging
uv run groundskeeper review 1371 --repo prebid/salesagent --no-llm   # free, deterministic only
uv run groundskeeper review 1371                                      # full review
uv run groundskeeper benchmark 1389                                   # recall vs human review
```

## Model

`claude-opus-4-8` (override with `GROUNDSKEEPER_MODEL`). Adaptive thinking,
structured outputs via `messages.parse()`.
