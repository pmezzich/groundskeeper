# Mined Security Patterns

Security patterns mined from the full human review history.

## Every parallel path to the same resource must apply the same guards

When a new or alternate path (fallback header, second outbound sender, by-natural-key resolver, new query mode, new repo method) handles the same class of untrusted input or reaches the same rows as a sibling path, it must apply every guard the sibling applies: authz/access checks, tenant scoping, URL/hostname validation, row limits. The weakest path defines the actual security posture; consolidate into one validated chokepoint where possible.

```python
# WRONG: sibling enforces access, new path does not
def _resolve_by_id(pid, aid): repo.has_access(pid, aid); ...
def _resolve_by_natural_key(key): return repo.find(key)  # no access check

# CORRECT: same guard on every resolution path
def _resolve_by_natural_key(pid, key):
    acct = repo.find(key)
    repo.has_access(pid, acct.id)
```

*Flagged by reviewers in: #1066, #1081, #1097, #1170, #1176*

## Every outbound HTTP request to a caller-supplied URL must pass SSRF validation

Any request field that becomes an outbound fetch or webhook target is an attacker-controlled pivot into the internal network (169.254.169.254) and a token-exfiltration channel, especially when a caller-supplied token is attached as a Bearer header. Run every sender through the repo's URL validator (HTTPS only; reject private/loopback/link-local/metadata IPs post-DNS-resolution) — all N senders, not just one.

```python
# WRONG
resp = httpx.get(f"{ref.agent_url}/lists/{ref.list_id}", headers=auth)

# CORRECT
WebhookURLValidator.validate_webhook_url(ref.agent_url)
resp = httpx.get(f"{ref.agent_url}/lists/{ref.list_id}", headers=auth)
```

*Flagged by reviewers in: #1071, #1081*

## Compare credentials with hmac.compare_digest, never ==

Plain `==` on tokens, API keys, or signatures short-circuits on the first differing byte, enabling timing attacks — and this repo already uses constant-time comparison in its webhook verification code, so == is also a convention violation. Guard against None, then use compare_digest.

```python
# WRONG
if tenant.admin_token == token: ...

# CORRECT
if tenant.admin_token and hmac.compare_digest(tenant.admin_token, token): ...
```

*Flagged by reviewers in: #1066*

## Credential resolution must not silently fall back across an isolation boundary

When an entity explicitly selects a provider but supplies no credential, `config.api_key or platform_default` — or a no-arg library constructor that resolves env vars internally — silently runs one tenant's traffic on another principal's credentials: an isolation, billing, and attribution breach invisible in normal operation. Raise on missing credentials, and always pass credentials explicitly to library constructors so hidden env-var defaults cannot engage.

```python
# WRONG
key = config.api_key or platform_defaults.get("api_key")
provider = GoogleProvider()  # resolves GOOGLE_API_KEY env var

# CORRECT
if not config.api_key: raise ConfigError(f"tenant {tid}: provider set but no api_key")
provider = GoogleProvider(api_key=config.api_key)
```

*Flagged by reviewers in: #1370*
