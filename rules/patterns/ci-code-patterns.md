# CI & Workflow Patterns

Conventions for GitHub Actions workflows, CI scripts, and quality-gate
configuration. Derived from review history on CI-touching PRs.

## CI Job Hygiene

Every job needs a timeout; every workflow needs a concurrency group.

```yaml
# WRONG — wedged step holds the runner for GitHub's 6-hour default
jobs:
  migration-roundtrip:
    runs-on: ubuntu-latest

# CORRECT
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
jobs:
  migration-roundtrip:
    runs-on: ubuntu-latest
    timeout-minutes: 20
```

## CI/Local Parity

CI must run the same gates with the same thresholds as local tooling.
Divergence means "passes locally, fails in CI" (or worse, the reverse).

- Coverage thresholds in CI must match the Makefile/tox values
- mypy invocation in CI must use the same config file as `make typecheck`
- Environment variables that gate behavior must be set identically or
  documented as intentionally different

## No Inverted Dependencies

CI scripts and production code must not import from `tests/`. The test
suite depends on the code, never the other way around.

```python
# WRONG — CI helper importing from the test tree
from tests.harness.transport import build_transport

# CORRECT — shared logic lives in src/ or scripts/, tests import it
from src.core.transport_helpers import build_transport
```

## Gate Must Actually Run

When a CI step's failure is the gate, verify the command can't silently
succeed: shell substitutions that produce empty argv, `$(...)` that fails
inside an `if`, or a grep over zero files all "pass" without testing anything.

```bash
# WRONG — if tr produces nothing, tox runs the FULL default env list silently
tox -e $(echo "$ENVS" | tr ',' ' ')

# CORRECT — fail loudly when the gate input is empty
[ -n "$ENVS" ] || { echo "no envs computed"; exit 1; }
```

## Ratchet Files Only Shrink

Baseline files (`.type-ignore-baseline`, `.duplication-baseline`, etc.) exist
to drain tech debt. Raising a baseline by hand defeats the ratchet.

```text
# WRONG — baseline grew 60 -> 61 to admit a new violation
# CORRECT — fix the new violation; baseline only ever decreases
```
