# Groundskeeper as CI

Groundskeeper runs from **its own repo** and reviews pull requests in target
repos (e.g. `prebid/salesagent`). Nothing needs to live in the target repo
except a tiny one-time trigger — and even that is optional (you can run reviews
by hand from the Actions tab).

## The two workflows here

| Workflow | What it does | Secrets |
|---|---|---|
| [`review-pr.yml`](workflows/review-pr.yml) | Reviews a target PR against its `.claude/rules` and posts the report as a PR comment | `GROUNDSKEEPER_GH_TOKEN`, `ANTHROPIC_API_KEY` |
| [`ci.yml`](workflows/ci.yml) | Build + keyless smoke check for groundskeeper itself | none |

## How a review gets triggered

**By hand (works today, zero setup):** Actions tab -> *Groundskeeper review* ->
*Run workflow* -> enter the target repo + PR number. Good for the demo.

**Automatically (event-driven):** drop
[`examples/groundskeeper-trigger.yml`](../examples/groundskeeper-trigger.yml)
into the consumer repo. On every PR it fires a `repository_dispatch` to this
repo, which runs the review. This is the "other prebid repos can reuse it"
path — each consumer repo adds ~15 lines and one dispatch token.

## Secrets

- **`GROUNDSKEEPER_GH_TOKEN`** (this repo) — a PAT with **read** on the target
  repo (to fetch the diff + rules) and **pull-requests: write** (to post the
  comment). Falls back to the built-in workflow token, which only suffices for
  reviewing *this* repo's own PRs.
- **`ANTHROPIC_API_KEY`** (this repo) — enables the LLM judge. **If it is
  absent, the run still succeeds** and posts the keyless deterministic findings
  only. That is the "deterministic layer ships independently" path — it needs no
  key at all.
- **`GROUNDSKEEPER_DISPATCH_TOKEN`** (consumer repo, only for the auto-trigger)
  — a PAT with `contents: write` on this repo, just enough to POST a dispatch.

## Two depths

`review-pr.yml` takes a `mode` input:

- **`full`** — deterministic checks + LLM judge (needs `ANTHROPIC_API_KEY`).
- **`deterministic-only`** — keyless ratchet/skip/CI-weakening/guard-tamper
  checks (`--no-llm`). Free, near-zero false positives, no API spend.

## Gate policy

Both workflows are **advisory** as written — they post a report / run a smoke
check and do not block merges. Graduating individual rules to blocking (once
their precision is proven) is a deliberate next step, not the default.
