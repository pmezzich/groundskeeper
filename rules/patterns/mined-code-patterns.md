# Mined Code Patterns

Patterns mined from the full human review history (429 comments, 34 PRs).
Each rule cites the PRs where reviewers flagged it.

## Encode each fact, mapping, or transformation in exactly one place

When one logical concept (an alias set, domain normalization, URL prefix, error-advisory payload, or a legacy+canonical field pair) is encoded at multiple sites, the copies drift and the system behaves inconsistently — especially when the copies sit on opposite sides of a join or on parallel emit paths. Collapse to one named constant/helper that every site imports; never apply a transformation a second layer already owns, and never add per-call-site fallback ladders between duplicate fields.

```python
# WRONG: two normalizers with different semantics feed the same intersection
def _normalize_domain(d): return d.removeprefix("www.")  # provider side
def _normalize_domain(d): return strip(d, ["www.", "m.", "mobile."])  # buyer side

# CORRECT: one shared helper imported by both sides
from src.core.domains import normalize_domain
```

*Flagged by reviewers in: #667, #1160, #1276, #1306, #1370, #1372, #1389*

## Keep parallel sibling structures symmetric or document the outlier

Among N parallel siblings (CI jobs, except-branches, error paths, transport handlers, validation paths), one that silently omits a step the others perform — a scoping parameter, caching call, audit-log write, timeout, permission block — reads as copy-paste drift and becomes the path where the invariant silently does not hold. Make siblings behavior-for-behavior symmetric, or attach a one-line comment at the divergent site naming why it differs.

```python
# WRONG: one error branch skips the audit log its siblings write
except AdCPError as e: audit_log(e); return envelope(e)
except ValueError as e: return envelope(e)  # no audit_log, no comment

# CORRECT: symmetric, or commented divergence
except ValueError as e: audit_log(e); return envelope(e)
```

*Flagged by reviewers in: #1276, #1306, #1312, #1372, #1389*

## A new canonical helper must be adopted at every call site in the same PR

Introducing a helper (sanitizer, error-envelope builder, parser) and leaving sites on the old inline pattern — sometimes in files the PR itself touches — is worse than no abstraction: readers assume the helper is authoritative while bypass sites drift, and for security sanitizers each unmigrated site is a live vulnerability. Grep the repo for the replaced pattern and convert every site, or do not introduce the helper.

```python
# WRONG: helper added, sibling branch still hand-builds the envelope
except AdCPError as e: return build_error_envelope(e)
except Exception as e: return {"error": str(e)}  # bypasses envelope

# CORRECT: every exit routes through the canonical builder
except Exception as e: return build_error_envelope(normalize_to_adcp_error(e))
```

*Flagged by reviewers in: #1107, #1306, #1372, #1389*

## Never ship comments that admit the code is wrong, open, or deferred without a tracked issue

Committed question comments, MISPLACED/FIXME apologies, and 'separate task' deferrals with no issue link evaporate: future readers cannot tell whether the doubt was resolved, and the known limitation resurfaces as a production bug. Resolve the question before merge, land only the correct placement, or convert the deferral into a filed issue referenced from the comment.

```python
# WRONG
# MISPLACED: response-level field, remove once webhook service confirmed
# Why is status missing from the protocol here?

# CORRECT
# Per-day webhook fields intentionally omitted; response-level only.
# Known spec deviation tracked in #1234.
```

*Flagged by reviewers in: #667, #945, #1071, #1081*

## Reject or surface what you cannot honor — never silently no-op

When code cannot honor a request — an accepted-but-unimplemented field, a wildcard grammar treated as a literal, a state transition from a non-handled source state, a capability stubbed to an empty function whose callers stay live, or computed drop-reasons that are discarded — the caller gets a clean success with degraded semantics. Reject explicitly (UNSUPPORTED_FEATURE / error), or surface an advisory on the success envelope; remove or disable the affordances of removed capabilities.

```python
# WRONG: parsed then ignored; silent no-op transition
req.collection_list  # never used
if buy.status == "pending_approval": buy.status = "rejected"  # else: nothing

# CORRECT
advisories.append(unsupported("collection_list"))
else: raise InvalidTransition(buy.status, "rejected")
```

*Flagged by reviewers in: #667, #1176, #1276, #1389*

## Thread new parameters through every transport entry point, with per-transport wire tests

When a tool is exposed via MCP, A2A, REST, and raw _impl, a parameter forwarded by some wrappers but not others creates silent per-transport feature gaps invisible to single-transport tests. Update every entry point in the same change, treat the _impl signature as the contract all wrappers fully forward, and replicate wire-level assertions per transport.

```python
# WRONG: A2A handler builds the request without the new field
req = CreateMediaBuyRequest(**params)  # reporting_webhook dropped

# CORRECT: every wrapper forwards the full contract
req = CreateMediaBuyRequest(**params, reporting_webhook=params.get("reporting_webhook"))
```

*Flagged by reviewers in: #942, #1071, #1389*

## Error recovery hints and status codes must match the failure's actual semantics

Classify each raise by who can fix it: caller-fixable is correctable, retry-helps is transient, truly unfixable is terminal — and derive HTTP status from the typed error itself, never a hand-maintained parallel code-to-status map or a hardcoded 500. A broad catch that labels everything transient, or a new subclass silently inheriting a wrong base default, sends clients into futile retry storms or stops retries that would succeed.

```python
# WRONG
except Exception as e: raise AdCPValidationError(str(e), recovery="transient")
return JSONResponse(status_code=500, content=err.body)

# CORRECT
except OperationalError as e: raise AdCPInternalError(..., recovery="terminal")
return JSONResponse(status_code=err.status_code, content=err.body)
```

*Flagged by reviewers in: #1083, #1306, #1389*

## Never classify exceptions by message substring

Control flow keyed on `"some text" in str(exc)` (or broad isinstance plus a substring) is overbroad — it swallows genuine bugs whose message happens to match — and fragile, breaking when upstream wording changes. Classify by precise exception type or structured attributes; in test infrastructure use explicit per-case xfail markers, never runtime heuristics.

```python
# WRONG
if excinfo.errisinstance(KeyError) and "env" in str(excinfo.value): xfail()
if "validation error" in str(exc) and "type=" in str(exc): retry()

# CORRECT
if isinstance(exc, ToolArgValidationError) and exc.title.startswith("call["): retry()
```

*Flagged by reviewers in: #1170, #1175, #1185*

## Comparisons and isinstance filters must use one canonical representation

When the same logical data reaches a check in two representations — Pydantic model vs dict, dict-shaped sizes vs strings, or an isinstance test against the raw input type after validation already coerced it — the check is degenerate: always true (phantom change detection), always false (filter drops 100% of real data), or the branch is dead and the feature silently disabled. Normalize both sides before comparing, and check the field's declared post-validation type.

```python
# WRONG: model vs dict is always unequal; Pydantic coerced brand already
if db_row.agents != incoming.model_dump(): mark_changed()
brand = req.brand if isinstance(req.brand, dict) else {}

# CORRECT
if db_row.agents != incoming: mark_changed()  # model == model
domain = req.brand.domain
```

*Flagged by reviewers in: #1071, #1170, #1176*

## Stated guarantees must not outrun what the code enforces

When a PR description or docstring claims 'bypass fixed', 'paths are symmetric', or 'CI deduplicated' while a surviving branch still falls through, one path skips the audit log, or the dedup covers 1 of 7 sites, future work builds on the stronger stated contract and the gap resurfaces as an unreviewed hole. Either enforce the full claim or weaken the wording to match the code; review by diffing each claimed guarantee against the actual paths.

```python
# WRONG
"""REST and A2A handle errors identically."""  # REST skips audit_log

# CORRECT
"""REST matches A2A except audit logging (tracked in #999)."""
```

*Flagged by reviewers in: #1306, #1370, #1372*

## Declared optionality must match the real contract

Do not declare `X | None = None` for a parameter the function immediately rejects when None or that callers always supply, and do not loosen a spec-required field to Optional (silenced with type: ignore) — the failure just moves from a static/boundary error to a late IntegrityError or a spec-violating null on the wire, plus dead defensive narrowing code. Push optionality to the transport boundary that actually faces missing data.

```python
# WRONG
def resolve(identity: ResolvedIdentity | None = None):
    if identity is None: raise ValueError(...)
name: str | None = None  # type: ignore[assignment]  # DB column is NOT NULL

# CORRECT
def resolve(identity: ResolvedIdentity): ...
name: str
```

*Flagged by reviewers in: #1066, #1071, #1081*

## Validate missing required values; never substitute guessed, sentinel, or fail-open defaults

Coercing a missing business value to a guess (`duration or 30`, click-URL falling back to the image URL), a None scope to a sentinel (`tenant_id or ""`), or a nullable safety flag to the permissive side (`manual_approval_required or False`) converts invalid input into silent wrong behavior in the exact situation where you want a loud failure. Required fields must be required in the schema; safety gates default restrictive or raise when unset.

```python
# WRONG
cfg["manual_approval_required"] = row.approval_required or False
uow = AdminCreativeUoW(tenant_id or "")

# CORRECT
if row.approval_required is None: raise ConfigError("approval flag unset")
if not tenant_id: raise ValueError("tenant_id required")
```

*Flagged by reviewers in: #630, #662, #1097*

## A retry or fallback must change the input in the dimension that caused the failure

Retrying a deterministic failure with identical input (e.g. `json.loads(json.dumps(x))` on data that is already plain JSON types) can never succeed: it doubles work, logs misleading 'recovered' noise, and masks the real design gap. Verify the retried input genuinely differs (e.g. schema-driven stripping of unknown fields); if the failure is deterministic and the input unchanged, fail fast.

```python
# WRONG: round-trip is a no-op for JSON-native args
except ValidationError: result = call(json.loads(json.dumps(args)))

# CORRECT: transformation that actually changes the input
except ValidationError: result = call(strip_unknown_props(args, tool_schema))
```

*Flagged by reviewers in: #1175, #1185*

## Dispatch over a closed variant set must handle every member explicitly

When code branches over a declared union, enum, or tag vocabulary, an omitted member degrades silently — no exception, just dropped functionality (a click-tracker url_type with no elif; a union member absorbed by a generic else). Enumerate every member; if an else intentionally covers one, name it in a comment or assert on it, and when widening the set, extend every dispatch over it.

```python
# WRONG: 'tracker_redirect' silently unhandled
if url_type == "tracker_impression": wire_impression(url)

# CORRECT
elif url_type == "tracker_redirect": wire_click(url)
else: raise ValueError(f"unhandled url_type {url_type}")
```

*Flagged by reviewers in: #839, #944*

## Never commit machine-specific paths or local-dev scaffolding to shared config

Absolute paths containing usernames or worktree names, hardcoded localhost URLs with no override, and docker-compose volume mounts of a sibling repo checkout over installed packages work only on the author's machine and silently shadow pinned dependencies for everyone else. Use repo-relative paths, environment variables with documented defaults, and untracked override files (docker-compose.override.yml) for personal setups.

```yaml
# WRONG
volumes:
  - ../adcp-client-python/src/adcp:/app/.venv/lib/python3.12/site-packages/adcp:ro

# CORRECT: keep personal mounts in untracked docker-compose.override.yml
```

*Flagged by reviewers in: #15, #839*

## One bad item must not fail a whole collection response

In loops aggregating results over N independent items (list endpoints rehydrating stored rows), a per-item exception that propagates — including the no-op `except Exception as e: raise e` — turns one corrupt record into a total outage for the tenant. Catch per item, log with the item's identifier, and degrade that item (None overlay or per-item errors[] entry) while returning the healthy rest; delete bare re-raise wrappers outright.

```python
# WRONG
for row in rows:
    overlay = Targeting(**row.targeting_raw)  # one bad row kills all

# CORRECT
for row in rows:
    try: overlay = Targeting(**row.targeting_raw)
    except ValidationError: logger.error("bad targeting %s", row.id); overlay = None
```

*Flagged by reviewers in: #1071, #1276*

## Catch only the exceptions the wrapped code can actually raise

`except Exception` (or a tuple containing it, which makes the narrower entries dead) around an operation with known, narrow failure modes converts programming errors and infrastructure failures into silent wrong behavior — a dead DB connection becomes 'start at index 0' or a client-facing 'transient'. Enumerate the real failure modes, catch only those, and let unexpected errors propagate.

```python
# WRONG
except (ValueError, Exception): start_index = 0

# CORRECT
except ValueError: start_index = 0  # covers binascii.Error
```

*Flagged by reviewers in: #1080, #1389*

## A trailing comma on a plain assignment creates a 1-tuple

Kwarg-style lines pasted into statement position keep their trailing commas, silently binding `(value,)` instead of `value` with no error at the assignment site. Remove the comma; an Assign whose RHS is an unparenthesized single-element tuple is essentially always a bug. Also prefer `d.get('k')` over `d['k'] if 'k' in d else None`.

```python
# WRONG: task_type is a 1-tuple
task_type = metadata['task_type'] if 'task_type' in metadata else None,

# CORRECT
task_type = metadata.get('task_type')
```

*Flagged by reviewers in: #839*

## Add lint/type suppressions only where the violation actually fires

A `# noqa` or `# type: ignore` on a line where the rule would never trigger is dead weight that masks future real violations on the same line. Verify the lint fires before suppressing; remove stale directives (enforceable via ruff RUF100).

```python
# WRONG: import is used at line 499, F401 never fires
from src.admin.auth_utils import extract_user_info  # noqa: F401

# CORRECT
from src.admin.auth_utils import extract_user_info
```

*Flagged by reviewers in: #1125*

## Grep for every importer before removing or moving a module-level symbol

Entry points outside test collection (scripts/, cron jobs) crash with ImportError at runtime while CI stays green, because nothing imports them during tests. When deleting or renaming a public symbol, search the whole repo (including scripts/ and docs snippets) and update or re-export; an import-smoke test over script entry points makes this mechanical.

```python
# WRONG: function deleted from sync_api.py while scripts/init_key.py still imports it

# CORRECT: update the importer in the same PR, plus
def test_scripts_importable():
    importlib.import_module("scripts.initialize_tenant_mgmt_api_key")
```

*Flagged by reviewers in: #1103*

## Error-translation code must be infallible or defensively guarded

An operation inside an exception handler that can itself raise (json.dumps on arbitrary detail values containing datetimes) replaces the real error with a secondary crash at exactly the moment the original signal is most needed. Guard fallible work in error paths so the original error always propagates.

```python
# WRONG
except AdCPError as e: details = json.dumps(e.details)

# CORRECT
except AdCPError as e:
    try: details = json.dumps(e.details)
    except (TypeError, ValueError): details = None
```

*Flagged by reviewers in: #1175*

## Run typed models through their own encoder before json.dumps

`json.dumps(model)` on a Pydantic model raises TypeError on the first real request — the signature failure of dict-to-model migrations, where the parameter type widened but a serialization site did not. Use `model.model_dump()` (or model_dump_json), and when a parameter's type changes from dict to models, audit every json.dumps/loads of it in the same change.

```python
# WRONG
size = len(json.dumps(payload).encode())  # payload: Task | McpWebhookPayload

# CORRECT
size = len(json.dumps(payload.model_dump(mode="json")).encode())
```

*Flagged by reviewers in: #839*
