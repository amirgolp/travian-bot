# 8. Shadow mode (dry-run)

## Problem

Two related needs that both want the same primitive:

1. **Onboarding.** When a new tenant adds their first Travian account, there's no way for them to "try" the bot without risk. First-run decisions — which villages to farm, which troops to train, which buildings to queue — are high-stakes for a player who's been managing manually for weeks. A dry-run period where the bot computes decisions but doesn't execute them lets the customer watch the bot "play" for 24 h and build trust before handing it the keys.
2. **Testing new heuristics on live traffic.** Before promoting a new farming algorithm from feature-flagged canary to full rollout, we'd like to see what decisions it *would* make on real accounts without actually pulling the trigger. Log what would have happened, compare to current behavior, promote or revert.

Both reduce to the same switch: decide + log, but don't execute.

## Design

A boolean flag that lives at the account level (for onboarding) and at the feature-flag level (for heuristic testing). Either one being true means "shadow: compute but don't execute."

### Schema

```sql
ALTER TABLE accounts ADD COLUMN shadow_mode BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE accounts ADD COLUMN shadow_until TIMESTAMPTZ;  -- optional auto-expiry
```

Per-feature-flag shadow is a naming convention on top of `feature-flags.md`: flags with the suffix `.shadow` run their "new logic" path in shadow mode regardless of account shadow state. Example: `farming.priority_by_last_raid.shadow=true` shadows the new heuristic everywhere.

### Execution path

Every action boundary gets one new decision point, centralized in `app/core/execution.py`:

```python
async def execute_action(ctx: AuditContext, do: Callable[[], Awaitable[T]]) -> ExecResult[T]:
    """Run an action unless shadow mode is on. Always audit-logs.

    `do` is the side-effect — the thing that actually clicks a button or
    submits a form. In shadow mode we skip it and return a synthetic success.
    """
    if ctx.shadow_mode:
        await audit.record(..., result='shadow', shadow=True)
        return ExecResult(shadow=True, value=None)
    start = time.monotonic()
    try:
        value = await do()
        await audit.record(..., result='ok', duration_ms=..., shadow=False)
        return ExecResult(shadow=False, value=value)
    except Exception as e:
        await audit.record(..., result='failed', ...)
        raise
```

Action methods refactor to:

```python
@audit_action("raid.send")
async def send_raid(self, target):
    plan = self._plan_raid(target)          # always runs — pure computation
    return await execute_action(
        AuditContext.current(),
        lambda: self._submit_raid_form(plan),  # only runs if not shadow
    )
```

The split — "plan" is always computed, "submit" is the gated side-effect — is the important architectural commitment. It forces every action path to have a clean separation between decision and effect, which pays dividends well beyond shadow mode (testing, logging, simulation).

### Dashboard surface

**Onboarding view.** When a tenant's account is in shadow mode, the dashboard gets a banner at the top of every page for that account:

> "Shadow mode active — the bot is watching and planning, but not taking actions. Ends in 23h 14m. [Go live now] [Extend 24h]"

A "Planned actions" card on the village detail page shows what the bot *would* have done in the last hour, grouped by action type. For raids: target coords + troop composition + expected bounty. For builds: what it'd upgrade and why. The goal is to let the customer evaluate decision quality without taking any risk.

**Testing view.** An admin page `/admin/shadow-diff` that compares action_log rows where `shadow=true` (feature-flag shadow path) to what was actually executed. Useful only for us, not customers.

### Feature-flag shadow for heuristics

This is subtler than the account-level switch. The existing controller runs, the shadow heuristic runs alongside, and we compare. Two ways:

1. **Simulate-only.** The shadow heuristic logs what it would do. Never executes. Low risk, but doesn't tell us what the shadow heuristic's outcome would have been (did the raid find loot?).
2. **Fork-and-mark.** Hard — we'd need to execute both paths, which means actually sending two raids. Not tenable in production.

Stick with (1). The comparison is decision-level ("old chose tile 42, new chose tile 88") not outcome-level. If we want outcome comparison, run the new heuristic at full rollout on a 5% tenant cohort and aggregate.

## Integration points

- New `app/core/execution.py` — the `execute_action` wrapper + `ExecResult` type.
- `app/core/audit.py` — gains a `shadow` bit on the context, propagated into log rows.
- Action methods across:
  - `app/browser/pages/rally.py:RallyPointPage.send_raid`
  - `app/browser/pages/build.py` — upgrade, destroy
  - `app/browser/pages/training.py` — queue
  - `app/browser/pages/marketplace.py` — send resources
  - `app/browser/pages/hero.py` — adventures, equip
- `app/api/accounts.py` — endpoint `POST /accounts/{id}/shadow?until=...` + `DELETE .../shadow`.
- Dashboard: banner + "Planned actions" card + `/admin/shadow-diff`.

## Tradeoffs / open questions

- **Scraping still runs.** Reads are unaffected — we want the customer to see accurate state on the dashboard during shadow. Only actions are gated.
- **Time-based expiry.** `shadow_until` auto-flips back to active — don't require manual intervention. A nightly job (or a check in the controller loop) enforces it.
- **Customer confusion risk.** Dashboard must be *loud* about shadow state. A quiet indicator gets ignored and customers think the bot is broken. Banner + coloured chrome everywhere.
- **Can we shadow some actions and not others?** Phase 1: all-or-nothing at the account level. More granular (`shadow_actions: ["raid.send"]`) is plausible later but probably unneeded — customers want "watch mode" or "go mode."
- **Determinism for comparison.** The shadow heuristic and real heuristic compute on the same inputs only if invoked at the same tick. Structure the code so they share the snapshot, not re-fetch.
- **Impact on anti-detection.** A shadow account still logs in, scrapes, acts human — it just skips action submission. This is actually good: the account's browser profile warms up, the fingerprint stabilizes, proxy reputation builds, and when we flip to live the transition is invisible to Travian.

## Effort

~2 days. The `execute_action` wrapper is small; most of the work is refactoring the ~15 action methods to use it, which lines up exactly with the audit-log instrumentation work. Consider bundling the two PRs — same touchpoints, same testing, same risk.
