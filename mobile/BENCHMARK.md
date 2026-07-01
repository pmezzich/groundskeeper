# Mobile corpus benchmark

Same methodology as the salesagent benchmark: judge the mined rules against a
PR's diff, score **recall** against the actual human review comments (strict
matching — same issue + location), **precision** = would a maintainer act on the
finding. Samples are small; read the caveats before quoting a number.

## Results

| Set | PRs | Recall | Precision | Diff judged |
|---|---|---|---|---|
| iOS in-sample | 1261, 1235, 1203 | 90% (9/10) | 82% (9/11) | first-commit |
| Android in-sample | 896, 823, 887 | 100% (7/7) | 100% (7/7) | merged (proxy) |
| **iOS held-out** | 1198, 1201, 1164 | **~22% (2/9)** | 100% (2/2) | merged (coverage proxy) |
| **Android held-out** | 854, 814, 886 | **100% (3/3)** | 100% (3/3) | first-commit |

- **In-sample** = PRs the corpus was mined from → train-on-test. A consistency
  check that the rules encode these reviewers' recurring asks, **not**
  generalization.
- **Held-out** = PRs the corpus was never built from → the generalization test.

## What the numbers actually say

Two honest signals:

1. **When a reviewer's ask falls within one of the 17 mined patterns, the corpus
   catches it reliably.** Android held-out hit 3/3 at first-commit — including a
   genuine *silent-behavior regression* (an unconditional setter clobbering a
   conditional `FULLSCREEN` one) that is diff-evident on its own, not just an
   iOS-parity echo. iOS in-sample hit 9/10.
2. **The corpus is small and convention-heavy, so it misses asks outside its
   patterns.** On the iOS held-out slice only **2 of 9** reviewer asks mapped to
   any rule — the rest were a numeric correctness bug (negative-precision), a
   defensive-optionality ask, and a naming convention. The single
   highest-value human catch (the precision logic bug) is **not** one of the 17
   patterns.

So "recall" swings with how many of a PR's asks happen to be in-corpus: ~22% on
a logic-heavy iOS slice, ~100% on an in-pattern Android slice. **The limiting
factor is corpus breadth, not the judge** — the same shape the salesagent corpus
had early on (it's now ~70 rules at ~39% held-out; this is **17 rules** mined
from far thinner, maintainer-gated mobile review history).

## Caveats (do not quote a single number without these)

- **Tiny samples** — 3 PRs per set, 1–6 actionable comments each. Percentages are
  high-variance; one match swings pooled recall 10–30 points.
- **Method is not uniform.** The **first-commit** runs (iOS in-sample, Android
  held-out) judge the diff a bot sees at PR-open, *before* the fixes land — the
  correct method. The **merged-diff** runs (iOS held-out, Android in-sample)
  judge the final state, where the reviewers' fixes are already applied; those
  are rule-*coverage* proxies (does the corpus even encode the ask?), not true
  recall. **Treat the first-commit runs as primary.**
- **Judge ran on the session model, not the deployed CLI** (no API key on hand)
  — the same "simulated judge" caveat as the salesagent benchmark rounds.
- **Only ~4 of the 17 rules were exercised** across the held-out PRs. The
  release-wiring, view-leak, null-annotation, and magic-literal rules are
  untested here.
- **No incumbent comparison** — neither mobile repo runs a review bot or a
  linter to compare against.

## Honest read

This is a **prototype**, and the benchmark says so. The mined rules reliably
catch the maintainers' recurring style / public-API / test / async asks (which
dominate these repos' reviews), but the 17-rule starter corpus under-covers
logic bugs and generalizes unevenly on a tiny held-out sample. The path to a
salesagent-grade number is the one the overview already names: a bigger corpus
(every missed review comment is a candidate rule) + full-file context for the
judge.

## Reproduce

- Corpus: `mobile/rules/{ios,android}-review-patterns.md`
- In-sample PRs: iOS 1261/1235/1203, Android 896/823/887
- Held-out PRs: iOS 1198/1201/1164, Android 854/814/886
- Method: reconstruct each PR's **first-commit** diff (via the commits API) vs
  its base; judge the corpus rules against it; match findings to the actionable
  reviewer comments (strict, same issue + location); precision = maintainer would
  act on it. (Two of the four runs fell back to the merged diff — labelled above.)
