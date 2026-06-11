# Mined Test Patterns

Test-quality patterns mined from the full human review history.

## Tests must invoke the production code path, and the feature must be wired to it

A test that asserts on local literals, a shadow implementation that production never calls, or plumbing-level tests for a contract the entry point never invokes all produce green suites that survive deletion of the feature. At least one test must execute the stated contract through the real entry point (_impl or transport) and fail if the feature code is removed.

```python
# WRONG: asserts Python semantics on literals, never calls src code
product = {"countries": {"US"}}
assert bool(product["countries"] & {"US", "CA"})  # Covers: UC-001

# CORRECT: drive the production implementation
result = harness.call_impl(_get_products_impl, filters={"countries": ["US"]})
assert result.products[0].product_id == "p1"
```

*Flagged by reviewers in: #1082, #1083, #1217, #1312*

## Mock assertions must verify arguments and resulting state, not just that a call happened

A bare `assert_called_once()` passes with completely wrong arguments, and mock-only tests for write operations pass even when persistence is broken. Use `assert_called_once_with(<full args>)` and keep or add the post-condition assertion on real state (DB row, object status) — especially in mock-migration refactors, which must not downgrade existing state checks.

```python
# WRONG: refactor replaced a state check with a bare call check
mock_repo.update_status.assert_called_once()

# CORRECT
mock_repo.update_status.assert_called_once_with(step_id, "completed")
assert pending_step.status == "completed"
```

*Flagged by reviewers in: #1080, #1097*

## Architecture guards must derive their scope from the source of truth, not a hardcoded slice

A guard that iterates a hardcoded subset of jobs, or scans only tests/unit/ while the pattern can live in tests/helpers/, has blind spots where the named invariant breaks silently — worse than no guard because it reads as coverage. Derive the full set from the source of truth and assert set-equality; allowlist shrink achieved by moving code outside the scan path is a false win.

```python
# WRONG: checks 2 of 13 gated jobs
for job in ("bdd-tests", "e2e-tests"): assert job in summary["needs"]

# CORRECT: derive and compare the whole set
assert set(summary["needs"]) == set(workflow_jobs()) - {"summary"}
```

*Flagged by reviewers in: #1370, #1372*

## New fallback, precedence, and error branches need tests for every cell

When a PR adds a priority/fallback branch (env var overrides DB key) or a new failure/fallback path, tests that all exercise the pre-existing branch give false confidence: the branch the PR exists to add ships unverified. Cover the full matrix — new source alone, both sources (priority winner verified), neither (exact error) — and give failure branches the same per-case tests their happy-path siblings have.

```python
# WRONG: all tests authenticate via the legacy DB-key path only

# CORRECT
def test_env_key_alone_succeeds(): ...
def test_env_key_overrides_db_key(): ...
def test_neither_source_returns_503(): ...
```

*Flagged by reviewers in: #1103, #1125*
