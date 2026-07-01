# iOS Review Patterns (prebid-mobile-ios)

Recurring reviewer asks mined from merged PR reviews in prebid-mobile-ios
(Swift / iOS SDK). Recurrence counts cite distinct PRs. `[MINED-1]` = one strong
PR, generalized; `[INFERRED]` = from `.swiftlint.yml` / repo conventions.

## Test New Public Behavior — recurred in 5 PRs

New public API, bug fixes, and new delegate/callback methods must ship with
tests that assert the new behavior (unit test + optional InternalTestApp
integration/UI case), not just that it compiles.

```swift
// WRONG — new public NativeAd price parsing, no test touches it
public var price: String? { nativeAdMarkup.price }

// CORRECT — a test asserting the parsed value
func testCreatesNativeAdWithPrice() {
    XCTAssertEqual(ad.nativeAdMarkup.price, "1.50")
}
```

Changing observable logic (e.g. obstruction/view-exposure) with the relevant
test file left unmodified is the tell.

## Minimize Public Surface, Preserve ABI — recurred in 3 PRs

Keep the public API minimal and stable. Don't expose implementation detail as
`public`, and when hardening an existing public property don't change or remove
it — add a private backing store and keep the public signature identical.

```swift
// WRONG — making customHeaders thread-safe by changing its access / adding a
// second public accessor, breaking anyone referencing customHeaders
public var customHeadersQueue: DispatchQueue

// CORRECT — private backing store, public signature unchanged
private var _customHeaders: [String: String] = [:]
public var customHeaders: [String: String] {
    get { queue.sync { _customHeaders } }
    set { queue.sync { _customHeaders = newValue } }
}
```

## Remove Dead / Redundant Code — recurred in 3 PRs

Flag re-declared private methods, a second getter beside an existing property,
duplicate methods, state that is set but never reset, and re-checks in a callee
whose only caller already checked the condition.

```swift
// WRONG — shouldIgnoreView re-checked in a callee the caller already gated
func handle(_ view: UIView) {
    guard !shouldIgnore(view) else { return }
    process(view)
}
func process(_ view: UIView) {
    guard !shouldIgnore(view) else { return }   // redundant: caller already checked
    ...
}

// CORRECT — check once at the single entry point
```

## Document Public Methods — recurred in 3 PRs

Public/protocol methods and non-obvious logic need doc comments matching the
notation already used in the same file (purpose, params, why).

```swift
// WRONG — new public plugin-renderer method, no header comment
public func register(_ renderer: PrebidMobilePluginRenderer) { ... }

// CORRECT
/// Registers a plugin renderer for the given ad formats.
/// - Parameter renderer: the renderer to register; replaces any existing one.
public func register(_ renderer: PrebidMobilePluginRenderer) { ... }
```

## Apply the Fix to All Parallel Paths — recurred in 2 PRs

When fixing one code path, apply the same change to sibling paths (GAM-primary
vs Prebid-rendering, interstitial vs banner) and confirm the untouched branch
still behaves.

```swift
// WRONG — patch secondaryAdReceived, leave the identical bug in primaryAdReceived
// CORRECT — fix both handlers; note "verified interstitial path still calls adLoaded()"
```

## No print() — Use the Framework Logger — recurred in 2 PRs

Never use `print(...)` for diagnostics in SDK code; use `PBMLogError` /
`PBMLogWarn` / `PBMLogInfo`.

```swift
// WRONG
print("plugin renderer not found")
// CORRECT
PBMLogError("plugin renderer not found")
```

## Balance Resource Lifecycle — `[MINED-1]`, high-value

Every add to a long-lived collection/registry (lockers, observers, caches)
needs a matching removal, or an explicit note on why lifetime is bounded —
otherwise flag a probable leak (iOS retain-cycle class).

```swift
// WRONG — appended on open, no path removes it
windowLockers.append(locker)

// CORRECT — remove on dismiss/completion, or document bounded lifetime
completion = { [weak self] in self?.windowLockers.removeAll { $0 === locker } }
```

## Single Source of Truth — No Duplicated Write Paths — recurred in 2 PRs

Refactor near-identical branches so shared data is written once; duplicated
merge/write paths are a point of future drift.

```swift
// WRONG — resultImp written in three near-identical branches
// CORRECT — build resultImp once via a helper covering all cases
```

---

### Severity anchors from `.swiftlint.yml` — `[INFERRED]`
- `force_cast` / `force_try` are **disabled** here — a blanket "no force-unwrap"
  rule will be noisy; scope force-unwrap findings to genuinely risky optionals.
- `identifier_name` and `nesting` are disabled — don't flag short names / nesting.
- `line_length: 250`, `function_body_length / type_body_length: 100` — flag only
  materially over these.
