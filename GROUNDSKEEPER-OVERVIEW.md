# Groundskeeper - skills enforcement for salesagent

Quick summary: I built a review agent that compiles the repo's `.claude/rules` into an
actual ruleset and checks PR diffs against it. Basically CI built against the skills.
I benchmarked it on 16 merged PRs using the human reviews as ground truth and got
roughly 39% recall at ~85% precision, validated on held-out PRs the rules were never
derived from. For comparison, the two bots recently added to the repo (code-quality /
advanced-security) haven't yet covered a single human-flagged issue on the PRs where
they were active, though they're new enough that the sample is small.

This is a prototype meant to give the "what should this look like" discussion real data,
not a finished thing.

## Why I built it this way

The rules files are instructions, nothing checks that they actually get followed.
salesagent already has CI, pre-commit, the structural guard tests, the Makefile gates,
and the rules files. The rules layer is the only one that's words-only. Since this
compiles the repo's own rule files, the docs and the enforcement can't drift apart,
the docs are the ruleset. No per-repo config to maintain.

## Pipeline

1. Fetch the PR diff. There's a `--first-review` flag that judges the diff as it stood
   at the first human review, which matters for benchmarking (later commits already
   contain the fixes reviewers asked for).
2. Compile rules from `.claude/rules` at the BASE ref, not the PR head, so a PR can't
   edit the rules to pass itself. PRs that touch the rules dir get flagged separately.
3. Deterministic checks first, no LLM involved: baseline/ratchet bumps, new test
   skips/xfails, new type-ignores, CI weakening (`-k not`, `--deselect`, `|| true` on
   test commands), guard-file tampering, missing job timeouts. These are free and
   they're the part that can run today with no API key.
4. LLM judge for the semantic rules. Each rule group gets the rule text with its
   WRONG/CORRECT examples plus only the relevant hunks. Output is a strict JSON verdict
   per rule (pass / violation / not_applicable / uncertain, with file:line evidence).
   The judge never sees the PR title or description on purpose, so the author's framing
   can't bias verdicts. I took that idea from pr-agents, along with pre-digesting input
   instead of dumping raw patches.
5. Output is a compliance table (console, markdown, or posted as a PR comment).

## Mining the review history

This is the "trained off the reviewer feedback" part. I pulled all 429 human review
comments across 34 merged PRs (everything except a held-out test set), generalized them
into recurring patterns, and added 33 new rules to the corpus. Kept everything that
showed up in 2+ different PRs plus a few mechanical one-offs.

The recurrence counts were the interesting part:

| mined rule | distinct PRs where a human flagged it |
|---|---|
| same fact/mapping encoded in multiple places | 7 |
| parallel paths to a resource missing the guards their siblings have | 5 |
| sibling structures asymmetric with no comment explaining why | 5 |
| new canonical helper not adopted at every call site in the same PR | 4 |
| tests that never invoke the production code path | 4 |
| silent no-op degradation instead of rejecting | 4 |

These are the things you and Constantine keep re-explaining by hand. Corpus is ~70 rules
now, and every future review comment the bot misses is a candidate rule, so it compounds
with normal review activity.

## Numbers

Methodology: judge the diff as of the first human review commit, score against the
actionable human comments whose issue is actually visible in that diff, strict matching
(same issue, same location, vague topical overlap doesn't count). Precision = would a
maintainer act on or at least acknowledge the finding. Held-out = PRs never used to
derive or calibrate anything.

| eval | recall vs human reviewers | precision |
|---|---|---|
| in-sample (5 PRs) | 40% | 85% |
| held-out set A (6 PRs) | 39% | 87% |
| held-out set B, after mining (5 PRs) | 39% | 81% |

On the bot comparison, I have to scope it honestly: code-quality and advanced-security
are recent additions (first comments June 10 and May 22 respectively), so they simply
weren't active on most of the benchmark PRs. On the PRs where they were active (#1389,
#1312), they covered 0 of the human-flagged issues, and 12 of code-quality's 14
comments on #1312 were "unused global variable" FPs on alembic revision boilerplate.
I only counted PR comments, not security-tab alerts. Small sample, but so far their
inline signal hasn't overlapped with anything a human reviewer flagged.

The 39% held across three separate test sets, so I'm fairly confident it's the real
ceiling for diff-scoped judging rather than a lucky run. For context on the denominator:
that's recall against everything senior reviewers caught across multi-round reviews. And
most PRs currently get no human review at all, so on those it's coverage vs nothing.

## Held-out examples worth looking at

- #1200: the "canonical helper adopted at every call site" rule (mined from other PRs
  entirely) flagged tests/conftest_db.py:401. Constantine's final CHANGES_REQUESTED
  review flagged the same line.
- #1379: the "gate must actually run" rule caught the bdd-shard mapfile silent-failure
  path (empty argv -> pytest quietly collects the whole tree) at first review. It got
  mis-verified as safe in review and you flagged it as your #1 hardening item 7 days
  later.
- #1372: the deterministic concurrency-group check matched the #1 pre-merge fix, and
  the inverted-deps rule caught the seed of PAT-01 a review round before the human did.
- #1306: flagged the xfail strict=False that silences the only E2E lifecycle test. The
  merged head actually regressed back to strict=False after review asked for
  strict=True, and it was still like that on main when I measured.
- It also finds valid stuff humans didn't flag (an internal-tool-ID FIXME on #1176,
  raw buyer-controlled IDs going into logger.debug on #1389).

## Caveats, being upfront

- The deterministic layer runs today, keyless. The LLM judge layer needs an
  ANTHROPIC_API_KEY to run for real. For the benchmarks I ran the judging through
  claude locally with the same rules and prompts the API version uses, so the 39% is
  what the judge architecture produces, but it's not yet what the unattended CLI
  produces. Cost with prompt caching should land around $0.50-1 per PR.
- The misses cluster in cross-file dataflow and repo/dependency knowledge. Full-file
  context for the judge is the next step and should help, but a diff-scoped bot is
  never going to catch a bug that lives outside the diff.
- Scoring this kind of thing involves judgment calls on what counts as actionable and
  covered. I held the methodology constant across rounds and erred strict. Happy to
  walk through any individual call.

## Open questions for when we talk

1. Simple audit agent vs CI check: the layers map onto both. The deterministic layer
   basically is the simple audit agent (free, no key, near-zero FPs). The judge layer
   is where the recall comes from. They could ship independently.
2. Gate policy: start neutral/advisory like ruff and CodeQL did here, then graduate
   individual rules to blocking as their precision gets proven?
3. Where it lives: in-repo (tools/ + workflow, simplest for secrets) vs a standalone
   repo other prebid repos could use. Probably tied to the harness open-sourcing
   question.
4. Rule corpus governance: the mined rules currently live with the tool. Which ones
   belong upstream in .claude/rules? (#1371 was the start of that.)
