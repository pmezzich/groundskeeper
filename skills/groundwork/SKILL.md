---
name: groundwork
description: >
  Pre-work investigation for a prebid/salesagent issue, run BEFORE any code is
  written. Invoke when asked to plan an issue, scope a ticket, figure out what
  an issue actually requires, assess a "good first issue" / "verification-only"
  label, or do groundwork before starting to code. Produces a verified-vs-assumed
  fact table, surfaced decisions, reuse inventory, insertion points, an
  entanglement/merge-order map, and a go/no-go. This is the planning leg only —
  it writes no production or test code.
---

# Groundwork — Pre-Work Investigation for a salesagent Issue

You are handed a prebid/salesagent issue. Your job is to **investigate, not build**. Emit a structured plan (see [Plan Output](#plan-output)). No code is written until every `[BLOCKER]` in the plan is cleared.

This skill is self-contained: a session with zero memory of the issues that motivated it can run it cold. Every command below targets real artifacts in the live repo. Anecdotes labeled `e.g. (illustrative)` are examples from past runs — do not treat their specific line numbers, SHAs, or counts as facts to verify or reuse in the current issue.

## When to use

- "Plan issue #N" / "scope this ticket" / "what does this issue actually require"
- "Is this really a good-first-issue / verification-only?"
- Anytime you're about to start coding on a salesagent issue and haven't traced its claims.
- **Not** for writing the code, the test, or the PR — those come after a GO.

## Core principle

**Verify-and-trace every load-bearing claim before building on it.** A load-bearing claim is one whose falsehood changes the plan: the issue's assertions, a sub-agent's synthesis, *and your own premises* all count. For each, open the cited source and confirm it says what's claimed — mark it `VERIFIED | REFUTED | ADJUSTED`. Re-derive; never inherit.

**Surface decisions instead of glossing.** When the issue is silent on a fork (seeding strategy, validator order, transport set), that silence is not permission to pick the convenient option quietly. Name the fork, make a reasoned call, record where it lands.

**No momentum.** "It looks like a one-liner," "the label says trivial," "the upstream synthesis already established this" — none survive contact. A premise you didn't trace is a blocker, not a head start.

## Environment

```bash
REPO=/c/Users/pmezz/OneDrive/Desktop/prebid/salesagent
PY="$REPO/.venv/Scripts/python.exe"                       # Windows venv interpreter (CPython 3.12)
API=https://api.github.com/repos/prebid/salesagent
TOKEN=$('/c/Program Files/GitHub CLI/gh.exe' auth token)  # GitHub API auth
export PYTHONUTF8=1                                        # CPython on Windows defaults the console to cp1252; dies on diff bytes otherwise
```

Run everything from `$REPO`. Use ASCII only in output (assign `PY` once per command block, not inline per-command; and avoid the `Read` tool on paths containing non-ASCII glyphs -- print file slices with `"$PY" -c` instead). Use ASCII only in output — the console encoder crashes on glyphs like `∩` (U+2229); use `comm -12` on sorted filename lists instead of set-intersection symbols.

---

# Investigation methodology

Run phases **in order** — each gates the next. You can't scope reuse until claims are verified; you can't plan insertion until the entanglement surface is known. Every check is tagged **[AUTO]** (mechanical/scriptable) or **[JUDGMENT]** (requires reading + a decision).

## Phase 0 — Establish ground truth (run first, blocks everything)

Every downstream decision rides on the spec pin and the issue's claims. A stale pin or an unverified claim poisons the whole plan.

### 0.1 Pin the spec/SDK version from the installed package, never the label **[AUTO]**

- **Question:** What adcp/spec version is *actually installed*, and does each field/error-code this change asserts exist on the pinned Pydantic model?
- **How:**
  ```bash
  "$PY" -c "import importlib.metadata as m; print('adcp', m.version('adcp'))"
  cat "$REPO/.venv/Lib/site-packages/adcp/ADCP_VERSION"   # spec string — re-derive; do not inherit a remembered value
  grep -i adcp pyproject.toml uv.lock | head              # cross-check the pin
  grep -i version docs/adcp-spec-version.md
  ```
  > Live values as of authoring (re-run the commands above to confirm — never trust these inline): package `adcp==5.7.0`, `ADCP_VERSION` reads `3.1.0-beta.3`. They are printed here only to show the *shape* of each output, not as facts to reuse. The skill's own rule is never-inherit, re-derive.

  For every asserted field, import the model and check it:
  ```bash
  "$PY" -c "from adcp.types import UpdateMediaBuyRequest as C; print(sorted(C.model_fields)); print('correlation_id' in C.model_fields)"
  ```
  If the class declares **zero** relevant fields, the value rides `extra="allow"` passthrough (e.g. `ContextObject`, `correlation_id` carried as `context.correlation_id`) — that is **NOT** a declared contract, even at a newer spec. Treat a `@v3-1` scenario tag as a *hypothesis*, not proof a field needs an upgrade.
- **Red flag:** PR/issue prose names a version (`adcp==4.3.0`, "no SDK dependency") that disagrees with the live pin; a field assumed "declared at 3.1" is still a passthrough on the installed pin; you're stacking on an upgrade PR for a field that **already exists** on the pin (over-coupling); or you're asserting a field that **doesn't** exist on the pin (would only pass after an unmerged bump).
- **Output the fork:** (a) field exists on pin → change is **independent**; (b) field only on newer spec → change **stacks behind** the upgrade PR — state the merge order.

### 0.2 Trace every load-bearing claim to source; mark verified | refuted | adjusted **[JUDGMENT]**

- **Question:** Does each factual claim in the issue (or a sub-agent's synthesis, or your own premise) actually hold against the code it cites?
- **How:** Enumerate every load-bearing assertion — "X does not validate Y", "template to mirror at `<file>:<lines>`", "verification-only / no production change", "the happy path already works", "no harness exists." For **each**, open the cited file at the cited lines and confirm:
  - **Trace guards transitively.** The impl body may delegate:
    ```bash
    grep -n "get_by_id_or_raise\|_verify_principal" src/core/tools/<impl>.py
    ```
    then open the helper and confirm it raises the structured error. (A "missing" guard sometimes lives inside `_verify_principal`, not the impl body.)
  - **Confirm a cited template emits the shape claimed.** Same code can emit differently — appending an *advisory* `Error` to a `list[]` (multi-buy degradation) is not the same as `raise`-ing a typed exception. Don't blindly copy.
  - **Confirm the typed exception + recovery exist and verify the recovery value** — the repo *intentionally diverges* from adcp 5.7 recovery defaults, so recovery is local and must be read, not assumed:
    ```bash
    grep -nE "class AdCPPackageNotFoundError|class AdCPMediaBuyNotFoundError|_default_recovery" src/core/exceptions.py
    # confirm _default_recovery: ClassVar[RecoveryHint] = "correctable" on the relevant class
    ```
  - **Apply the same standard to your OWN premises and any sub-agent output** — re-derive, don't inherit. ("No harness exists" and "field declared at 3.1" both fail when trusted from an upstream synthesis.)
- **Red flag:** A premise is cited but the referenced lines don't contain what's described; any unverified load-bearing claim. **This is a hard blocker to starting code.**

### 0.3 Ground every load-bearing noun/verb in the DoD against the pinned models **[AUTO]**

- **Question:** Does each key noun/verb in the DoD ("observation", "graded failure", "reported as an X") map to a concrete model field, exception class, or enum on the pinned SDK -- or does it exist only in the compliance/grading runner?
- **How:** for each load-bearing term:
  ```bash
  grep -rin "<term>" src/core/schemas/ src/core/exceptions.py
  "$PY" -c "from adcp.types import <Response> as R; print(sorted(R.model_fields))"
  ```
- **Red flag:** the term has no field/class/enum anywhere in `src/` or the pinned response model -- it is a *test-grading semantic* (a compliance-runner concept like `on_out_of_scope=warn`), not a production contract. (Real example: #1411's "observation" maps to nothing in `ListCreativeFormatsResponse`.)
- **Output the fork:** term maps to a model field -> production assertion; term maps to nothing -> resolve it as a BDD-assertion convention (e.g. "empty `formats[]` + no fabricated entry"), NOT a schema change. Writing production code for a term with no model representation is the failure this catches.

## Phase 1 — Scope: code vs. test, and is the label honest

### 1.1 Distrust the difficulty/scope label; independently re-scope **[JUDGMENT]**

- **Question:** Does "good first issue" / "verification-only" / "happy path already works" survive independent scoping?
- **How:**
  ```bash
  curl -s -H "Authorization: Bearer $TOKEN" "$API/issues/<N>" | "$PY" -c "import sys,json; d=json.load(sys.stdin); print([l['name'] for l in d['labels']]); print(d['body'])"
  ```
  Then independently list every concrete artifact you'd add/change by reading the named impl file **and** the harness/feature file the scenario needs. If that list contains a design decision or a missing test capability, the label is wrong. Cross-check a merged sibling:
  ```bash
  git -C "$REPO" show <sha> --stat   # e.g. (illustrative) a "verification-only" sibling still needed ~85 lines of new step wiring
  ```
- **Red flag:** Label says trivial, but scoping turns up a missing harness capability, a seed-vs-drive fork, or net-new step/fixture code. Treat scope as unknown until traced.

### 1.2 Split each sub-requirement into needs-code vs. needs-test-pin **[JUDGMENT]**

- **Question:** Which sub-requirements need a production change vs. only a test that pins an already-correct contract?
- **How:** Enumerate every distinct error code / behavior demanded (e.g. `MEDIA_BUY_NOT_FOUND`, `PACKAGE_NOT_FOUND`). Run the 0.2 verify per item, then classify:
  - already-wired guard → **test-only pin**
  - no existing guard (`grep -n "get_package_or_raise" src/core/tools/<impl>.py` returns nothing) → **new production guard**

  Read the issues' own "Sequencing" notes — collisions on the same function (both touch `update_media_buy`) must be planned as **one** change.
- **Red flag:** Treating every DoD checkbox as a code task (over-scoping the test-only half); or missing that two issues collide on one function (under-scoping by shipping the guard without the BDD pin). e.g. (illustrative) one real PR closed a test-only issue + a 22-line-guard issue together at `media_buy_update.py:267-272`.

## Phase 2 — Harness & infra capability (can the test even be written?)

### 2.1 Confirm a harness supports the full scenario flow **[JUDGMENT]**

- **Question:** Does one test env support the full chained flow (e.g. create-then-query on the same account), or is every harness single-purpose?
- **How:** Identify the operations the scenario chains. For each candidate in `tests/harness/`:
  ```bash
  grep -nE "def (call_impl|call_a2a|call_mcp)|EXTERNAL_PATCHES|_[a-z_]+_impl" tests/harness/<env>.py
  ```
  Confirm whether **one** env exposes **both** operations. (A list env that calls only `_get_media_buys_impl` with `EXTERNAL_PATCHES={}` is read-only; a create env drives only `_create_media_buy_impl`. Neither does create→query.)
- **Red flag:** The scenario's natural flow spans two operations but each harness implements exactly one. You're about to silently build a dual harness or fake one side — both are real decisions the issue didn't name. Surface the gap; it feeds the design fork (4.1).

### 2.2 xfail-registry / strict-xpass status check **[AUTO]**

- **Question:** Is the target tag in any xfail/skip registry, and does "flips xfail→pass" match reality, or am I about to trip a `strict=True` xpass failure?
- **How:**
  ```bash
  grep -nE "_XFAIL_TAGS|_SELECTIVE_XFAIL|_MCP_SELECTIVE_XFAIL|_REST_XFAIL_TAGS" tests/bdd/conftest.py
  grep -n "<your-tag>" tests/bdd/conftest.py
  ```
  Check the transport-specific `pytest_collection_modifyitems` blocks too. Note the auto-xfail mechanism: a missing step def is converted to xfail at runtime (`pytest_runtest_makereport`), so a not-yet-wired scenario is *already* xfail. Confirm the tag is absent from all `strict=True` lists.
- **Red flag:** Tag sits in a `strict=True` list (wiring it → XPASS → build fails), or a transport carve-out xfails only some parametrized variants. Conversely, if the issue says "flips xfail→pass" but the tag is in **no** registry, it was only auto-xfailed for a missing step — you must ensure **all four transports** (a2a/mcp/rest/impl) genuinely pass.

### 2.3 Per-transport request-shape parity -- can each transport even carry the scenario's params? **[AUTO]**

- **Question:** For every filter/param the scenario's When-step sends, does *each* transport in the DoD actually forward it? A "flips xfail -> pass across all 4 transports" DoD is false the moment one transport drops the param at its boundary.
- **How:** for each param the scenario sends, confirm REST forwards it (the usual offender):
  ```bash
  grep -n "build_rest_body" tests/harness/*.py          # does it return {} or omit the param?
  grep -n "class .*Body" src/routes/api_v1.py            # does the route Body model declare the field at all?
  grep -n "<scenario-tag>" tests/bdd/conftest.py         # is the tag already in _REST_XFAIL_TAGS (strict=True)?
  ```
- **Red flag:** DoD says "all 4 transports" but the param is in `_REST_XFAIL_TAGS`, OR `build_rest_body` returns `{}` / omits it, OR the route Body model has no field for it. That transport's DoD line is a SEPARATE production fix (forward the param), not a test-pin. (Real example: #1411 -- REST `build_rest_body` returns `{}`, so every `format_ids`-filter scenario is hard-xfailed on REST; "4 transports" is unachievable without first fixing the endpoint.)
- **Output the fork:** transport carries the param -> in scope; transport drops it -> the "N transports" claim is false; re-scope (prerequisite endpoint PR, or keep that transport legitimately xfailed with a FIXME).

## Phase 3 — Reuse / DRY inventory (extend, don't reimplement)

DRY is a correctness invariant here, enforced by `check_code_duplication.py` (at `.pre-commit-hooks/check_code_duplication.py`). Inventory before you write.

Step definitions live under subdirectories, not flat: domain-specific steps in `tests/bdd/steps/domain/` (e.g. `tests/bdd/steps/domain/uc003_update_media_buy.py`, `uc004_delivery.py`) and shared steps in `tests/bdd/steps/generic/`. The top-level `tests/bdd/steps/` holds only `__init__.py`, `_harness_db.py`, `_outcome_helpers.py`, and the `domain/` + `generic/` dirs. Target the right subdir in every command below — a flat `steps/<file>.py` or non-recursive `steps/*.py` glob finds nothing.

### 3.1 Inventory reusable seed/dispatch/assert helpers in the target step module **[AUTO]**

- **Question:** For every seed/dispatch/assert action the scenario needs, does a reusable helper already exist so I extend rather than reimplement?
- **How:** Read the full target step file (under `domain/` or `generic/`), then:
  ```bash
  grep -nE "^def (_register_principal|_register_media_buy|_generate_unique_id|_dispatch_query|_get_media_buys)" tests/bdd/steps/domain/<file>.py
  grep -nE "Factory|import" tests/bdd/steps/domain/<file>.py | head   # MediaBuyFactory, MediaPackageFactory imports
  ```
  Map each Given/When/Then you must add to an existing helper. Only thin `@given/@when/@then` wrappers should be net-new.
- **Red flag:** You find yourself writing a fresh DB-seed routine, request dispatcher, or id generator when `_register_principal` / `MediaBuyFactory` / `_dispatch_query` / `_generate_unique_id` already exist in the same file (often defined hundreds of lines earlier).

### 3.2 Match per-file fixture/factory + assertion conventions **[JUDGMENT]**

- **Question:** Does each test file have a prevailing convention (factory vs raw single-commit; wire-envelope vs reconstructed-exception assertion) that my new rows/assertions must match?
- **How — seeding:** Open each test file you'll touch; check how sibling rows are built. Some files use `MediaPackageFactory` (factory-session fixture); others build every model raw inside one `get_db_session()` block and commit once. **Match per-file.** Verify a new fixture mirrors production writes:
  ```bash
  grep -rn "DBMediaPackage(" src/   # confirm create_media_buy dual-writes a row per package
  ```
- **How — error assertions:** Read `tests/CLAUDE.md` § "Error Verification Policy". New error tests must assert on the **wire envelope**:
  ```python
  assert_envelope_shape(result.wire_error_envelope, code, recovery=...)   # tests/helpers/envelope_assertions.py
  ```
  NOT on a reconstructed `error.recovery`. Confirm the dispatcher stashes `result.wire_error_envelope` (real bytes on A2A/MCP/REST) and `synthesized_error_envelope` (IMPL) into ctx. Plan the assertion wire-authoritative across all 4 transports from the start.
- **Red flag:** Using a factory in a raw-single-commit file (factory commits mid-block, needs the buy flushed first for the FK — different transaction shape); seeding a fixture that doesn't match production writes; or Then-steps reading the reconstructed exception (lossy; degrades to a weak `isinstance` on IMPL).

## Phase 4 — Hidden-decision detection (surface forks the issue glossed)

### 4.1 Detect & document every implementation fork **[JUDGMENT]**

- **Question:** What forks does this task contain (seeding strategy, schema authority, transport set, harness depth), and for each, has the plan named it, made a reasoned call, and recorded it where a reviewer sees it?
- **How:** Enumerate open decisions before coding. Typical forks:
  - **Seed via factory vs drive the real create call** — pick by what the contract under test *is*. For by-id resolution, the impl reads the same committed row regardless of how it was created → factory-seeding is contract-faithful.
  - **Schema authority** when no standalone `*.json` ships → repo convention is the pinned Pydantic model, round-tripped via `model_validate`.
  - **Transport set** — BDD wire set is a2a/mcp/rest; impl retained for unit/integration.
  - **Harness depth** — read-only seed vs full create→get dual harness.

  For each: pick, give the reason, plan to document in (a) a code comment at the site **and** (b) the PR body's "decisions surfaced" notes. Be ready to push back on a reviewer with the reason, with an explicit "happy to build the dual harness if you'd prefer — flag it" offer.
- **Red flag:** A fork silently resolved with no comment and no PR note — the call reads as an accident, not a decision.

### 4.2 Find the hidden precedence/ordering decision for a new validator **[JUDGMENT]**

- **Question:** When a new validator is inserted among existing ones, what ORDER is intended relative to them — did the issue specify it, or gloss it?
- **How:** List existing raise sites in execution order in the impl (e.g. terminal-state, disallowed-action, [new guard], budget / min-package / state validators later). Decide where the new guard goes and **why**. Check whether the issue says anything about ordering (often it doesn't). Mirror an existing sibling's placement for consistency — e.g. `property_targeting` validation runs *before* the `dry_run` early return so dry-run requests are also rejected. The decision lands as (a) an intent comment **and** (b) a precedence test.
- **Red flag:** Inserting the guard wherever is convenient, leaving the "both-invalid" case (package absent AND under-budget) unpinned. A silent ordering change flips which error code the buyer sees (`PACKAGE_NOT_FOUND` vs `BUDGET_TOO_LOW`). Resolution typically needs an intent comment plus a `test_..._absent_package_preempts_budget_wire_envelope` precedence test.

### 4.3 Mine the repo's recurring reviewer objections and pre-empt each surfaced decision **[JUDGMENT]**

- **Question:** For each fork I just resolved (4.1/4.2) and each guard I plan to touch (5.2), what is the *specific human-reviewer objection* this repo's reviewers actually raise — and does my plan pre-empt it in the place they'll look? There is **no** `.github/pull_request_template.md` in this repo (verified absent), so nothing prompts the reviewer's standard asks for you — you must surface them yourself.
- **How:** Build the repo's review-hot-button list from three real sources, then pair each surfaced decision to the objection it pre-empts:
  ```bash
  # 1. Mine actual recurring objections from recently merged PRs' review threads.
  gh pr list --state merged --limit 30 --json number,title,url | "$PY" -c "import sys,json; [print(d['number'], d['title']) for d in json.load(sys.stdin)]"
  # For the handful whose titles touch the same surface as your issue, pull review comments and scan for repeated asks:
  curl -s -H "Authorization: Bearer $TOKEN" "$API/pulls/<PR>/comments?per_page=100" | "$PY" -c "import sys,json; [print('-', c['user']['login'], ':', c['body'][:200].replace(chr(10),' ')) for c in json.load(sys.stdin)]"
  curl -s -H "Authorization: Bearer $TOKEN" "$API/pulls/<PR>/reviews" | "$PY" -c "import sys,json; [print('--', r['user']['login'], r['state'], ':', (r.get('body') or '')[:200].replace(chr(10),' ')) for r in json.load(sys.stdin)]"
  # 2. The codified hot-buttons that reviewers enforce by hand when a guard does not yet catch them:
  sed -n '/Structural Guards/,/^## /p' CLAUDE.md     # the "Structural Guards" table — each row is a recurring reviewer ask
  ls .claude/rules/patterns/                          # code-patterns.md, mcp-patterns.md, testing-patterns.md
  grep -rnE "MUST|NEVER|do not|always|prefer" .claude/rules/patterns/*.md | head -40
  ```
  Cluster the results into a short list of recurring asks (typical clusters seen in this repo: "assert the wire envelope, not a reconstructed exception"; "no new allowlist entries — fix, don't suppress"; "does this test fail if you revert the fix?"; "why this validator order?"; "is this stacked on the right PR / does it collapse on rebase?"; "extend the existing helper, don't reimplement"). Then, **for every entry in DECISIONS TO SURFACE, write the one reviewer objection it pre-empts and where you've answered it** (code comment site + PR-body line). A surfaced decision with no matching objection is fine; a known recurring objection with no surfaced answer is a gap to close before GO.
- **Red flag:** You documented a fork (4.1) or a precedence call (4.2) but never checked whether reviewers *historically* push on that exact axis — so the PR body answers questions nobody asked while the reviewer's real recurring ask (e.g. "this mock bypasses the predicate — does it fail on revert?") goes unaddressed. Mechanically pre-empting guards (5.2) is not the same as pre-empting the human; both are required.

## Phase 5 — Test-faithfulness & guard-compliance (will the test be real, will it pass pre-commit)

### 5.1 Plan mutation-detecting coverage through the real entrypoint **[JUDGMENT]**

- **Question:** Will the test drive the real entrypoint and FAIL when the production line under test is deleted/inverted — and (for error paths) does the asserted `error_code` actually exist in the AdCP source of truth?
- **How — revert-check:** Name the exact production line the fix adds/changes; confirm the planned assertion fires when that line is deleted or inverted ("mentally delete or invert the line, then ask whether the assertion would fire"). Avoid false-coverage modes:
  - asserting Python semantics on local literals the test built;
  - a mock that bypasses the predicate (e.g. `scalars().all()` stubbed to a static list, so a tenant-scoping fix reverts with no failure);
  - `patch()` of the function under test;
  - bare `assert_called_once()` instead of `assert_called_once_with(<full args>)` + a state assertion.

  Prefer an integration test (`@pytest.mark.requires_db`) or BDD/e2e using `tests/harness/` + `tests/factories/` driving `_impl` or a transport — wire-correctness, JSONB `flag_modified`, transaction ordering all slip through mock-only tests.
- **How — error-code source of truth (in priority order):**
  1. the AdCP error_code enum / pinned exception subclasses in `src/core/exceptions.py`;
  2. the generated `BR-*.feature` scenario (traces to the upstream AdCP requirements repo — authoritative);
  3. `docs/test-obligations/` is **bootstrap only, NOT authoritative**.

  Plan to assert via `result.wire_error_envelope` as primary, exception `.context` as impl-transport fallback, and echo `context.correlation_id`.
- **Red flag:** The planned test still passes with the fix reverted; assertions read a value the test constructed; the only coverage is a manual `MagicMock` stack for DB/wire behavior; or a "production gap" xfail is slated for *implementation* without confirming the spec requires it (the xfail-as-bug trap — don't build non-spec behavior behind a wiring PR).

### 5.2 Pre-flight the structural guards — fix, never allowlist **[AUTO]**

- **Question:** Will the change trip any structural-guard ratchet, and is the plan to FIX rather than allowlist?
- **How:** Read `CLAUDE.md` "Structural Guards" table and `.claude/rules/patterns/*.md`. Enumerate guards the change could hit:
  - `model_dump` in `_impl`; weak-mock (`assert_called_once` vs `assert_called_once_with`); raw `select()`/`session.query()` outside repositories; raw `MediaPackage` select; `type: ignore`; code-duplication baseline; SQLAlchemy-2.0; single migration head; transport-boundary (`_impl` imports / `ResolvedIdentity` / no `ToolError`); BDD no-op/trivial/dict-registry/duplicate steps.

  For each, decide compliance up front.
- **Red flag:** Plan reaches for `# type: ignore`, a new `model_dump` allowlist line, a raw `session.query(...)`, or copies an allowlisted pattern "to match convention." **Hard rule: allowlists only SHRINK** — a new FIXME-deferred entry is a **blocker**, not a Medium; "match the existing allowlisted convention" is never valid (existing entries are debt, not templates). Pre-fill the PR's `[ ] No new allowlist entries` checkbox.

## Phase 6 — Entanglement with open PRs (do this last; it can invalidate everything above)

Run only after the plan's shape is known — the entanglement surface is defined by *which files your plan touches*. **But if 6.0 finds the base is stale, STOP and rebase before any other check here is meaningful.**

### 6.0 Merge-order & base-collapse: is my stacking premise still true? **[AUTO]**

- **Question:** Which PR must merge first, is my stacking premise still true after the dependency landed, and does my diff collapse cleanly on rebase?
- **How:** Re-read the PR body's stacking/merge-order claim, then check live state:
  ```bash
  curl -s -H "Authorization: Bearer $TOKEN" "$API/pulls/<MINE>" | "$PY" -c "import sys,json; d=json.load(sys.stdin); print('mergeable',d['mergeable'],'state',d['mergeable_state'])"
  git -C "$REPO" fetch upstream main
  git -C "$REPO" rev-list --left-right --count upstream/main...<myhead>   # behind<TAB>ahead
  curl -s -H "Authorization: Bearer $TOKEN" "$API/pulls/<DEP>" | "$PY" -c "import sys,json; print('merged_at', json.load(sys.stdin)['merged_at'])"
  ```
- **Red flag:** PR body says "stacked on #DEP, 0 behind main" but #DEP has merged and the PR is now `mergeable:false` / `mergeable_state:dirty`, behind>0 — the premise is stale, the big diff has **not** collapsed, and a conflicted rebase is required **before any other entanglement check is meaningful.** `[BLOCKER]`

### 6.1 File-overlap intersection vs. all open + just-merged PRs **[AUTO]**

- **Question:** Which of MY changed files are also touched by other open PRs and the just-merged spec PR?
- **How:** List my files, every open PR's files, and the merged spec PR's files; intersect with `comm -12` on sorted lists.
  ```bash
  files() { curl -s -H "Authorization: Bearer $TOKEN" "$API/pulls/$1/files?per_page=100" | "$PY" -c "import sys,json; [print(f['filename']) for f in json.load(sys.stdin)]"; }
  # paginate (?page=2,3…) until a page returns <100 entries
  files <MINE>  | sort -u > /tmp/mine.txt
  files <OTHER> | sort -u > /tmp/other.txt
  comm -12 /tmp/mine.txt /tmp/other.txt
  ```
  (`export PYTHONUTF8=1` first — the Windows console encoder dies on diff bytes otherwise. ASCII only.)
- **Red flag:** Any production file or shared BDD step/util file in both lists. **Production-file overlap is highest-severity.**

### 6.2 Insertion-point survival **[JUDGMENT]**

- **Question:** Does my insertion's anchor still exist after the other PR restructures the same function?
- **How:** For each shared production file, pull both PRs' patches; read `@@` hunk headers + context. Identify the textual anchor your insertion depends on (a status-validation block, a return statement, an import) and grep the other PR's patch for edits to those exact lines. Compare `@@` ranges for overlap/shift.
  ```bash
  curl -s -H "Authorization: Bearer $TOKEN" "$API/pulls/<OTHER>/files" | "$PY" -c "import sys,json; [print(f['filename'],'\n',f.get('patch','')) for f in json.load(sys.stdin) if f['filename']=='<shared file>']"
  ```
- **Red flag:** My anchor lines are deleted/reworded/relocated; **or** the other PR changes the enclosing function's signature/return type so my code no longer type-checks. (A return type changing from `X | Y` to a unified `Result` wrapper means a positionally-surviving guard still composes into a changed return contract that must be re-validated.)

### 6.3 Duplicate step-registration / parser shadowing **[AUTO]**

- **Question:** Did the other PR register the same pytest-bdd step decorator (or a parser that shadows my literal step) in a shared module?
- **How:** Diff both PRs' versions of every shared step file under `tests/bdd/steps/` (recurse into `domain/` and `generic/` — use `grep -r` or a `steps/**/*.py` glob, **not** a flat `steps/*.py`); extract added/removed `@given/@when/@then` strings. Flag (a) identical literal step strings added by both, (b) a `parsers.parse(...)` in one PR matching a literal step the other relies on, (c) a literal step deleted and replaced by a parser. pytest-bdd resolves by registration order and can silently bind the wrong impl.
- **Red flag:** Both PRs add the same step text; or one converts a literal step the other depends on (e.g. `@then('the error should include "suggestion" field')`) into a `{field}`-parameterized parser — any scenario bound to the old literal now resolves through the new parser: a behavior-changing shadow, not a clean error.

### 6.4 Scenario-text survival in shared .feature files **[AUTO]**

- **Question:** Do the Gherkin lines my steps bind to still exist byte-identical after the other PR edits the same `.feature` files?
- **How:** Filter the files API for `.feature`. For each Given/When/Then my decorators target, grep the other PR's feature patch for deleted/reworded versions of that exact line. Cross-check the `@tag` — a renamed tag orphans a `scenarios()`-bound test.
- **Red flag:** A line my step matches is deleted/reworded, or an Examples row I depend on is dropped → my step orphans (auto-xfail/error) or matches the wrong scenario. If the other PR reworded lines in a feature my steps bind into, they must match the **new** text post-rebase.

---

# Plan output

Emit this structured plan. Every fact and entanglement line carries a head-of-line status marker so a reader can triage at a glance:

- `[OK]` — verified from source, no action needed.
- `[HIGH]` — verified but high-severity / needs a deliberate decision before GO.
- `[BLOCKER]` — load-bearing and unresolved; **no code is written until every `[BLOCKER]` is cleared.**

(Use `VERIFIED` / `ASSUMED` inline as the audit verb where it reads more naturally — the marker is the triage signal, the verb is the provenance.)

```
ISSUE: #<N> — <title>   |   Closes: #<N>[, #<M> if collision]
PINNED SPEC: adcp==<installed version> / spec <ADCP_VERSION>   [OK] re-derived from package, not prose

-- VERIFIED FACTS (re-derived from source) ----------------------------
  [OK]      <claim> -> VERIFIED at <file>:<lines>  (e.g. _verify_principal:219 -> get_by_id_or_raise:66 raises MEDIA_BUY_NOT_FOUND)
  [OK]      <field> exists on pinned <Model>.model_fields   OR rides extra="allow" passthrough via context.<field>
  [HIGH]    Template <file>:<lines> emits <RAISE typed exc | advisory list[]>  <- shape divergence affects the plan
  [OK]      Typed exc <AdCP...Error> -> recovery='<value>'  (verified; repo diverges from adcp 5.7 defaults)

-- ASSUMED / UNVERIFIED (must close before coding) --------------------
  [BLOCKER] <any load-bearing claim not yet traced>
  [HIGH]    <label "verification-only" — independently re-scoped? Y/N>

-- SCOPE SPLIT --------------------------------------------------------
  [OK]   PRODUCTION CHANGE: <list>  (e.g. PACKAGE_NOT_FOUND guard, ~22 lines @ media_buy_update.py:267)
  [OK]   TEST-PIN ONLY:     <list>  (e.g. MEDIA_BUY_NOT_FOUND — already wired, BDD pin only)
  [OK]   Transports to assert: a2a / mcp / rest [+ impl for unit/integration]

-- HARNESS CAPABILITY ------------------------------------------------
  [HIGH/OK]  Flow needs: <op1 -> op2>.  Single env supports both? Y/N
  [HIGH]     If N -> see DECISION FORK (seeding strategy)
  [HIGH/OK]  xfail status: tag in <registry|NONE>; strict-xpass risk? Y/N; all 4 transports pass on wire? Y/N

-- DECISIONS TO SURFACE (each -> code comment + PR "decisions surfaced") --
  1. [HIGH] <fork>: chose <A> over <B> because <contract-faithfulness reason>; alt offered: <B>
            PRE-EMPTS REVIEWER OBJECTION: "<recurring ask mined in 4.3>"  -> answered at <comment site> + PR-body line
  2. [HIGH] Schema authority: <pinned Model via model_validate | standalone *.json>
            PRE-EMPTS: "<objection>"  -> answered at <where>
  3. [HIGH] Validator precedence: <new guard> fires BEFORE <X> / AFTER <Y> -> pinned by <test name>; intent comment @ <lines>
            PRE-EMPTS: "why this order?"  -> answered by precedence test + intent comment
  4. [HIGH] Seeding/fixture per-file: <factory in fileA / raw single-commit in fileB> because <FK/transaction reason>
            PRE-EMPTS: "<objection>"  -> answered at <where>
  (Cross-check against 4.3's mined hot-button list: every recurring objection on an axis this PR touches must map to a line above, or it is a gap.)

-- INSERTION POINTS --------------------------------------------------
  [OK]   <file>: anchor = <textual anchor>, after <block>, before <block>  (mirror sibling <name>)
  [OK]   Assertion routed through assert_envelope_shape(result.wire_error_envelope, <code>, recovery=...)
  [OK]   Revert-check: deleting <prod line> makes <test> FAIL  [confirmed]

-- RISKS / GUARDS ----------------------------------------------------
  [HIGH] Structural guards possibly hit: <list>.  Plan: FIX, no new allowlist entry.  [ ] checkbox pre-filled
  [OK]   Mutation-detection: no mock bypasses the predicate; drives real _impl/transport
  [OK]   Error-code traced to: exceptions.py enum + BR-*.feature (upstream) — NOT docs/test-obligations

-- ENTANGLEMENT / MERGE-ORDER (run last) -----------------------------
  [BLOCKER] Base state: mergeable=<...>, <behind>/<ahead> vs upstream/main.  Stacking premise still true? Y/N
            -> if base is dirty: REBASE FIRST, re-run all entanglement checks
  [HIGH]    File overlap (comm -12): <shared files>.  Production-file overlap? <files>  (highest severity)
  [HIGH/OK] Insertion-point survival: anchor survives <other PR>? Y/N; return-type/signature changed? <detail>
  [HIGH/OK] Step-registration collisions: <literal->parser shadow | dup step text>  in <file>
  [HIGH/OK] Scenario-text survival: my steps still match <other PR>'s NEW feature text? Y/N
  [OK]      MERGE ORDER: <#dep> must merge first; then rebase; diff collapses to <N files / delta>

-- GO / NO-GO --------------------------------------------------------
  GO   only if: every load-bearing claim VERIFIED ([OK], no [BLOCKER] left); spec-fork decided; harness exists or
                seeding-fork decided+documented; no strict-xpass landmine; every recurring reviewer objection on a
                touched axis (4.3) has a surfaced answer; base is clean (or rebased) and entanglement surface mapped;
                guard-compliance plan = FIX.
  NO-GO if: any ASSUMED fact load-bearing & untraced ([BLOCKER]); base dirty/premise stale (rebase first);
            a silently-resolved fork; an unanswered recurring reviewer objection; a test that survives reverting the
            fix; a new allowlist entry.
```

## Phase → check index

| Phase | Checks | Gate it provides |
|-------|--------|------------------|
| 0 Ground truth | 0.1 spec pin [AUTO], 0.2 claim-trace [JUDGMENT] | nothing downstream is meaningful until these pass |
| 1 Scope | 1.1 label distrust, 1.2 code-vs-test split [JUDGMENT] | what you're actually building |
| 2 Harness | 2.1 capability [JUDGMENT], 2.2 xfail registry [AUTO] | whether the test is even writable |
| 3 Reuse | 3.1 helper inventory [AUTO], 3.2 conventions [JUDGMENT] | extend, don't reimplement |
| 4 Hidden decisions | 4.1 forks, 4.2 precedence [JUDGMENT], 4.3 reviewer-objection mining [JUDGMENT] | surfaced decisions for the PR, each paired to the human reviewer's recurring ask |
| 5 Faithfulness | 5.1 mutation-detection [JUDGMENT], 5.2 guards [AUTO] | the test is real and passes pre-commit |
| 6 Entanglement | 6.0 merge-order, 6.1 file-overlap, 6.3/6.4 step/feature [AUTO]; 6.2 insertion-survival [JUDGMENT] | run last; can invalidate everything above |
