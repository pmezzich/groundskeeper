# Skills — the planning leg

Groundskeeper's CLI is the **review / enforcement** leg of the harness (it
compiles `.claude/rules` and grades PRs against them). These skills are the
other legs, packaged as repeatable Claude Code skills that work in any
session — no dependence on the conversation that built them.

## `groundwork/` — the planning leg

Pre-work investigation for a `prebid/salesagent` issue, run **before any code
is written**. It encodes the verify-and-trace / anti-momentum discipline plus
the exact patterns Chris and Constantine enforce in review, so that following
it on a fresh issue pre-empts what they would otherwise flag.

**What it does:** given an issue, it runs gated phases —
ground-truth (spec/SDK pin + term-grounding), verify-claims-against-code,
honest re-scope (code vs test, distrust the "good first issue" label),
harness capability, per-transport request-shape parity, reuse/DRY inventory,
hidden-decision detection, test-faithfulness + structural-guard compliance,
and open-PR entanglement — then emits a structured plan with a **GO / NO-GO**.

**How it was built:** the methodology was mined from the PRs that landed
first-try (#1432, #1452) and the #1417 entanglement check, refined through an
adversarial critic loop, and validated against a **held-out issue (#1411)** it
was never built from. On that issue it caught a hidden REST-transport blocker
(the "4 transports" requirement was structurally impossible) and a
no-model-representation design fork ("observation" maps to nothing on the
pinned SDK) — both of which would have sunk a naive implementation. See
[`groundwork/_validation.md`](groundwork/_validation.md).

### Using it

Copy or symlink into the target repo's skill directory:

```bash
cp -r skills/groundwork /path/to/salesagent/.claude/skills/
```

Then invoke on an issue — "plan issue #N", "scope this ticket", "what does
this issue actually require", "is this really a good-first-issue".

## Roadmap (the full loop)

- **plan** — `groundwork` skill ✓
- **review / enforce** — groundskeeper CLI ✓
- **code-gen** — TBD (slots between the two; pairs with the plan→code→review
  structure)
