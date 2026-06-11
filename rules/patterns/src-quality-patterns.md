# Source Quality Patterns

Production-code patterns reviewers consistently flag that aren't covered by
the test-focused rules. Derived from review history.

## Code DRY — Canonical Forms Encoded Once

When the same literal set, mapping, or logic block appears in 2+ production
files (or 3+ sites in one file), extract it. Duplicated encodings of a
canonical form WILL drift.

```python
# WRONG — google model aliases encoded separately in config.py AND factory.py
# config.py
GOOGLE_ALIASES = {"gemini", "google-gla", "google-vertex"}
# factory.py
if provider in ("gemini", "google_gla", "google_vertex"):  # already drifted

# CORRECT — one canonical definition, imported everywhere
from src.core.providers import GOOGLE_PROVIDER_ALIASES
```

Watch especially for: repeated cache-and-return blocks, repeated
validation+error construction, repeated normalization functions. Three
similar blocks in one diff = extract a helper in the same PR.

## Log Hygiene — Never Interpolate Raw External Input

User/buyer-controlled values in log statements enable log injection
(CWE-117) and leak unsanitized data. Quote, sanitize, or use a dedicated
loggable-form helper.

```python
# WRONG — raw buyer-controlled list_id straight into the log
logger.debug(f"resolving property list {list_id}")

# CORRECT — sanitized form (and if the PR adds a helper for this, USE it)
logger.debug("resolving property list %s", loggable_list_id(list_id))
```

If the diff introduces a sanitization helper, every log site touching that
value class must use it — an unused helper next to raw interpolation is a bug.

## Behavior Changes Need a Test Exercising the Contract

A PR that adds or changes externally observable behavior (a new feature
flag, caching semantics, retry/replay behavior, an error contract) must
include at least one test that exercises that contract end to end — not
just unit tests of internal helpers.

```python
# WRONG — PR adds idempotent replay caching; tests only cover the cache helper
# CORRECT — a test creates, replays the same request, and asserts the cached
# response is returned (and a deleted-lookup replay still succeeds)
```

## New Helpers Need Production Callers

A helper added "for the architecture" with zero production call sites is
dead weight and hides incomplete migrations. Either wire it in within the
PR or don't add it yet.
