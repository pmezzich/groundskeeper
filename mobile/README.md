# Mobile review — a groundskeeper prototype

A starter for pointing groundskeeper at the Prebid mobile SDKs
(`prebid-mobile-ios`, `prebid-mobile-android`). The architecture is unchanged —
compile a rule corpus, run deterministic checks + an LLM judge over the PR diff.
Only the *corpus* and a few deterministic idioms are language-specific.

## The problem, in numbers

I sampled the 40 most-recent merged PRs in each repo and classified each by
whether it got a *substantive* review (≥1 inline code comment, or a review body
beyond "LGTM"):

| Repo | Substantive review | Rubber-stamped |
|---|---|---|
| prebid-mobile-ios | **14 / 40 (35%)** | 65% |
| prebid-mobile-android | **9 / 40 (23%)** | 78% |

Branch protection requires *an* approval, not a *meaningful* one, and real
review is concentrated in one or two maintainers per repo. When they don't
engage deeply, PRs merge unreviewed. That is the gap.

Telling detail: **both repos already have merged PRs whose review is an
AI-generated review a maintainer pasted in and endorsed** (ios #1228,
android #925). The team is already reaching for automated review — this makes
it repeatable and grounded in each repo's own conventions.

## Why the architecture ports cleanly

- **The judge is language-agnostic.** It reads a rule + WRONG/CORRECT examples
  + the diff hunks and returns a verdict. Swift and Kotlin work out of the box;
  only the *rules* are new. Those live in [`rules/`](rules/), mined from each
  repo's own review history exactly as the salesagent corpus was.
- **The deterministic layer needs a few mobile idioms.** The keyless checks map
  across languages with small edits:

  | groundskeeper (Python) | iOS equivalent | Android equivalent |
  |---|---|---|
  | new `@pytest.mark.skip` / `xfail` | `XCTSkip` / `XCTSkipIf` | `@Ignore` / `@Disabled` |
  | new `# type: ignore` | `// swiftlint:disable` | `@Suppress(...)` |
  | CI weakening (`\|\| true`, `--deselect`) | same (workflow YAML) | same (workflow YAML) |
  | guard-file / baseline tampering | n/a (no swiftlint baseline) | n/a (no detekt/lint config) |

  Note: **neither repo runs a linter/static-analysis step** (ios CI = build +
  tests; android CI = build + JUnit). So the deterministic layer's value on
  mobile is smaller than on salesagent — the recall comes from the judge + the
  mined rules.

## The starter corpus

Mined from real reviewer comments (≥2 distinct PRs each, unless labelled
`[MINED-1]` / `[INFERRED]`):

- [`rules/ios-review-patterns.md`](rules/ios-review-patterns.md) — Swift / iOS SDK
- [`rules/android-review-patterns.md`](rules/android-review-patterns.md) — Kotlin / Java SDK

The single strongest signal in **both** repos is **public-API stability +
tests for new public behavior** — the maintainers' dominant concern for an SDK
that publishers depend on. A cross-repo rule also emerged: Android reviewers
link the matching iOS PR as the spec and expect API/name/test parity.

## Run it

Keyless (deterministic checks only, no API key):

```bash
groundskeeper review <pr> --repo prebid/prebid-mobile-ios \
  --rules-dir mobile/rules --no-llm
```

Full (judge on, needs `ANTHROPIC_API_KEY`):

```bash
groundskeeper review <pr> --repo prebid/prebid-mobile-android \
  --rules-dir mobile/rules
```

`--rules-dir mobile/rules` is required because the mobile repos don't ship a
`.claude/rules` dir — the corpus lives here until (if) it moves in-repo.

## Does it work? — benchmark

Benchmarked the mined rules against real reviewed PRs (recall vs the actual
human review comments), same method as salesagent. Headline: the corpus
reliably catches asks that fall **within** its 17 patterns (Android held-out
3/3 first-commit; iOS in-sample 9/10), but **under-covers logic bugs outside
them** (iOS held-out: only 2/9 reviewer asks mapped to a rule). The limiter is
corpus breadth, not the judge — the early-stage shape salesagent had. Full
numbers + caveats: [`BENCHMARK.md`](BENCHMARK.md).

## Honest caveats

- **Review history is thin and maintainer-gated.** Usable signal came from
  ~12 iOS PRs and ~9 android PRs; several strong patterns rest on a single PR
  and are labelled `[MINED-1]`. The corpus compounds as review activity grows.
- **Kotlin-idiom rules are under-evidenced** — the android SDK core is Java
  (androidx `@Nullable`/`@NonNull`); Kotlin lives in event-handler/demo modules,
  and the coroutine-idiom asks trace to a single PR. Flagged, not promoted.
- **This is a prototype to make the "review agents for mobile" conversation
  concrete**, not a finished tool.
