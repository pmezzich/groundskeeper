# Groundskeeper — skills enforcement for salesagent

**TL;DR:** a review agent that compiles the repo's own `.claude/rules` into an executable
ruleset and judges PR diffs against it — "CI built against the skills." Benchmarked against
16 merged PRs using the human reviews as ground truth: **~39% recall at ~85% precision**,
validated on held-out PRs. The two bots already commenting on the repo scored **0%** on the
same PRs. Built as a prototype to give the "what should this look like" discussion real data.

---

## Why

Rules/skills in `.claude/` are instructions, not enforcement — nothing verifies an agent
(or human) actually followed them. salesagent already has 5 enforcement layers (CI,
pre-commit, structural guard tests, Makefile gates, rules files); the rules layer is the
only one that's words-only. Groundskeeper makes it executable, and because it compiles the
repo's own rule files, docs and enforcement can't drift — the docs *are* the ruleset.

## How it works

```
PR opened/updated
  └─ fetch diff (supports --first-review: judge the diff a reviewer actually saw)
  └─ compile rules from .claude/rules at the BASE ref   ← a PR can't edit rules to pass itself
  └─ layer 1: deterministic checks (no LLM, free)        ← ratchet/baseline bumps, test skips,
                                                            type-ignore policy, CI weakening,
                                                            guard tampering, job timeouts
  └─ layer 2: LLM judge per rule group                   ← rule text + WRONG/CORRECT examples
                                                            + only the relevant hunks; strict
                                                            JSON verdicts (pass/violation/
                                                            not_applicable/uncertain + file:line)
  └─ report: compliance table (console / markdown / PR comment)
```

Design rules carried over from pr-agents: the judge never sees the PR title/description
(author claims can't bias verdicts), input is pre-digested hunks (never raw full patches),
per-group failures degrade to `uncertain` rather than killing the run.

## Trained off the reviewer feedback

Mined **429 human review comments across 34 merged PRs** (all of repo history except a
held-out test set) → 93 raw finding shapes → distilled to 40 → **33 added to the corpus**
(every shape recurring in 2+ PRs, plus mechanical single-PR ones). Top recurring shapes:

| Mined rule | Distinct PRs where humans flagged it |
|---|---|
| single fact/mapping encoded in one place | 7 |
| every parallel path applies the same guards (authz/scoping) | 5 |
| sibling structures stay symmetric (or document the outlier) | 5 |
| new canonical helper adopted at every call site, same PR | 4 |
| tests must exercise the production path | 4 |
| no silent no-op degradation | 4 |

Corpus is now ~70 rules. Every future human review comment the bot misses is a new rule
candidate — the corpus compounds with normal review activity.

## The numbers

**Methodology:** judge the diff as of the *first human review commit* (later heads already
contain the fixes), score against actionable human review comments visible in that diff,
strict same-issue-same-location matching, precision judged as "would a maintainer act on or
acknowledge this." Held-out = PRs never used to derive or calibrate any rule.

| Eval | Recall vs human reviewers | Precision |
|---|---|---|
| In-sample (5 PRs) | 40% | 85% |
| Held-out set A (6 PRs) | 39% | 87% |
| **Held-out set B, after mining (5 PRs)** | **39%** | **81%** |
| github-code-quality[bot] (same 16 PRs) | 0% | ~13% (12 of 15 comments were alembic-boilerplate FPs) |
| github-advanced-security[bot] (same 16 PRs) | 0% | 2 comments total |

The ~39% is stable across three independent test sets — it's the measured ceiling of
diff-scoped judging, not a lucky run. Denominator context: that's recall against
*everything senior reviewers caught across multi-round reviews*. Most PRs currently get
zero human review; on those, this is coverage vs nothing.

## Held-out case studies

- **#1200** — mined rule "canonical helper adopted at every call site" flagged
  `tests/conftest_db.py:401`; the maintainer's final CHANGES_REQUESTED review flagged the
  identical line. Rule was derived from other PRs entirely.
- **#1379** — "gate must actually run" caught the bdd-shard `mapfile` silent-failure path
  (empty argv → pytest silently collects everything) at first review. It was explicitly
  mis-verified as safe in review and flagged by a human 7 days later.
- **#1372** — deterministic concurrency-group check reproduced the #1 pre-merge fix;
  inverted-deps rule caught the seed of PAT-01 a full review round before the human.
- **#1306** — flagged the `xfail strict=False` silencing the only E2E lifecycle test; the
  merged head regressed back to `strict=False` *after* review demanded strict=True — still
  live in main when measured.
- It also surfaces valid issues humans missed (e.g. an internal-tool-ID FIXME violation on
  #1176, log-injection sites on #1389 that no human or bot flagged at first review).

## Honest caveats

- **The semantic judge layer is currently simulated** (Claude agents running the same rules
  and prompts the API judge would use). The deterministic layer runs today, keyless. Making
  the 39% real in CI needs an `ANTHROPIC_API_KEY` repo secret (~$0.50–1/PR with caching).
- Remaining misses cluster in cross-file dataflow and repo/dependency knowledge — fixable
  with full-file context for the judge (the next architecture step, also key-gated), but a
  diff-scoped bot will never catch what's outside the diff.
- Scoring has judgment in it (what counts as "actionable", "covered"). The methodology was
  held constant across rounds and erred strict; happy to walk through any individual call.

## Open questions (the "what should this look like" discussion)

1. **Simple audit agent vs CI check** — the layers map to both: the deterministic layer IS
   the simple audit agent (free, zero-key, near-zero FP); the judge layer is where recall
   comes from. They can ship independently — e.g. det layer as a check run now, judge
   advisory-only behind a key.
2. **Gate policy** — start `neutral`/advisory (like ruff/CodeQL's ramp-up here), graduate
   individual rules to blocking as their precision is proven per-rule?
3. **Where it lives** — in-repo (`tools/` + workflow, secrets-simple) vs standalone repo
   (reusable across prebid, relevant to the harness open-sourcing decision).
4. **Rule corpus governance** — mined rules currently live with the tool; which belong
   upstream in `.claude/rules` (PR #1371 is the start of that)?
