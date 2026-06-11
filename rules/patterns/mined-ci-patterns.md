# Mined CI Patterns

CI patterns mined from the full human review history.

## Added tests must actually execute in CI

Tests added in a directory outside the runner's collection paths (tox/testpaths/matrix), or whose preconditions are never satisfied in baseline CI so every case hits pytest.skip(), provide zero protection while CI stays green. When adding tests in a new location, verify the runner collects them; for conditional-skip tests, seed the required fixtures so the primary cases run.

```yaml
# WRONG: new tests/harness/ files while tox only collects unit+integration
commands = pytest tests/unit tests/integration

# CORRECT
commands = pytest tests/unit tests/integration tests/harness
```

*Flagged by reviewers in: #1175, #1176*
