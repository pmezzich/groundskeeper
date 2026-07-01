# Android Review Patterns (prebid-mobile-android)

Recurring reviewer asks mined from merged PR reviews in prebid-mobile-android
(Java SDK core; Kotlin in event-handler / demo modules). Recurrence counts cite
distinct PRs. `[MINED-1]` = one strong PR, generalized; `[INFERRED]` = repo
conventions (androidx annotations, injected dispatchers) — the repo runs **no**
detekt / ktlint / checkstyle.

## Unit Tests for New Public API — recurred in 4 PRs

Every new public class / method / property ships with JUnit tests in the same
PR; new creative/VAST parsing needs a fixture-driven test.

```java
// WRONG — new public SharedId + TargetingParams methods, no test class
public final class SharedId { public static String getSharedId() { ... } }

// CORRECT — add SharedIdTest; cover empty `type` for banner AND video;
// add a VAST test with a single-quote char.
```

## No Silent Public-API Break — recurred in 3 PRs

Don't rename/remove/change the signature of a public (publisher-facing) class,
method, or callback. If it must change, `@Deprecated` the old one and add the
replacement alongside. Keep base-class methods `public`, not `protected`.

```java
// WRONG — rename breaks publisher imports
public class PbCreativeScanResult { ... }   // was public PbFindSizeError

// CORRECT — keep the old name, or:
@Deprecated public class PbFindSizeError extends PbCreativeScanResult { ... }
```

## No Silent Default-Behavior Change — recurred in 3 PRs

A refactor must not silently change a runtime default (timeout, flag semantics,
tracking on/off). Preserve prior observable behavior; gate any change behind an
explicit "user set it" signal.

```java
// WRONG — swaps SOCKET_TIMEOUT (3000ms) for timeoutMillis (default 2000ms),
// silently shortening every publisher's timeout
int timeout = config.timeoutMillis;   // defaults to 2000

// CORRECT — keep effective default 3000ms; only apply a custom value when the
// user actually set one
int timeout = config.timeoutOverridden ? config.timeoutMillis : SOCKET_TIMEOUT;
```

## Async Callbacks + Main-Thread Access — recurred in 3 PRs

Client/publisher delegates must be invoked asynchronously (don't block SDK
methods); WebView/View access must be on the main thread; and `Handler.post` is
async — don't use it where the caller needs the value synchronously.

```java
// WRONG — read WebView state via handler.post, then use it on the next line
handler.post(() -> state = webView.getState());
return state;   // still stale — post ran async

// CORRECT — funnel WebView/JS-bridge access through a main-thread-safe wrapper
```

## View / Context Leak on Lifecycle — recurred in 3 PRs

Long-lived monitors/trackers that hold a View or walk the view hierarchy must
stop/free on `destroy()`. Flag removing self-cleanup
(`onDetachedFromWindow() -> destroy()`) from a View reused in a recycling
container (RecyclerView/ViewPager).

```java
// WRONG — tracking runs as long as the container exists, no destroy hook
tracker.start(adView);   // holds adView, never released

// CORRECT — start on activate, stop on destroy(); release the adView reference;
// add a test for the destroy path
@Override public void destroy() { tracker.stop(); adView = null; }
```

## Cross-Platform API Parity — recurred in 3 PRs

For a feature that also exists on prebid-mobile-ios, match property/method
names, position/format handling, and test coverage to the iOS PR — reviewers
link the iOS diff as the spec.

```java
// WRONG — Android exposes getSharedIdentifier(); iOS calls it getSharedId()
// CORRECT — name it getSharedId(); mirror iOS interstitial/rewarded FULLSCREEN
// handling; port the same test cases the linked iOS PR added.
```

## Null-Safety on Nullable Returns — recurred in 2 PRs

Guard values that can be null before dereferencing; annotate nullable getters
with androidx `@Nullable`. Kotlin modules must avoid `!!` on platform types from
these Java getters.

```java
// WRONG — getDeviceAccessConsent() can return null
boolean consent = params.getDeviceAccessConsent();   // NPE risk

// CORRECT — annotate + guard
@Nullable public Boolean getDeviceAccessConsent() { ... }
Boolean c = params.getDeviceAccessConsent();
if (c != null && c) { ... }
```

## No Magic Literals / Idiomatic Guards — recurred in 3 PRs

Named constants over string literals; no Yoda conditions; idiomatic clamps;
sensible fallback; drop redundant null-checks and unused params.

```java
// WRONG
if ("video".equals(type)) { ... }
if (precision > 6) precision = 6; if (precision < 0) precision = 0;

// CORRECT
static final String VIDEO = "video";
if (VIDEO.equals(type) || Utils.isVast(markup)) { ... }
precision = Math.max(Math.min(precision, 6), 0);
```

## Dependency / Module Release Wiring — recurred in 2 PRs

A new dependency must be added to the module's Maven release POM; a new
releasable module needs its `buildPrebidMobile.sh` / `deployPrebidMobile.sh`
entries + a POM; watch for namespace collisions between sibling modules.

```
# WRONG — dep added to build.gradle only; new module reuses
#         namespace org.prebid.mobile.eventhandlers (duplicate-class clash)
# CORRECT — add the dep to scripts/Maven/<module>-pom.xml; register the module
#           in both build scripts + a POM; give it a distinct namespace.
```

---

### Kotlin-idiom candidates — `[MINED-1]`, under-evidenced
From a single endorsed AI-review PR (#925): inject `CoroutineDispatcher` instead
of hardcoding `Dispatchers.Main`; cancel a structured `CoroutineScope(... +
SupervisorJob())` on destroy; use `StandardTestDispatcher` / `advanceUntilIdle`
in coroutine tests. Real but not yet ≥2-PR — promote as the Kotlin modules
accrue review history.
