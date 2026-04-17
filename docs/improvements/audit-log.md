# 7. In-game action audit log

## Problem

Currently the bot takes actions in-game — sends raids, queues upgrades, trains troops, sends resources — and leaves no durable record of exactly what happened. The state we *can* reconstruct (BuildOrder rows, farmlist dispatch timestamps, report ingests) answers questions like "did we queue this?" but not:

- "My bot attacked the wrong target at 14:32 — what happened?" (customer dispute)
- "We pushed a new farming heuristic on Tuesday; what changed in the actual actions it took?" (post-deploy analysis)
- "Which accounts sent raids during the 5-minute Cloudflare incident, and did any succeed?" (incident forensics)
- "Train an ML ban predictor on action sequences leading up to real bans." (long-term)

All four of these want an append-only, queryable, per-action log. Today we reconstruct with log greps — slow, incomplete, doesn't survive log rotation.

## Design

One append-only table, written by a decorator on action methods. Zero coordination, zero mutation, easy to shard later.

### Schema

```sql
CREATE TABLE action_log (
  id          BIGSERIAL PRIMARY KEY,
  account_id  BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  village_id  BIGINT REFERENCES villages(id) ON DELETE SET NULL,
  ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
  action      TEXT NOT NULL,       -- 'raid.send', 'build.upgrade', 'train.queue', ...
  target      JSONB,               -- action-specific target: {"x": 12, "y": -45} or {"slot": 3} etc.
  params      JSONB,               -- what was requested: {"troops": {"t1": 10}, "list_id": 5}
  result      TEXT NOT NULL,       -- 'ok' | 'failed' | 'skipped' | 'shadow'
  result_info JSONB,               -- free-form diagnostics: error message, Travian response class
  trace_id    UUID,                -- ties to reconciler trace (ties logs/metrics/actions together)
  duration_ms INT,                 -- end-to-end action time
  shadow      BOOLEAN NOT NULL DEFAULT FALSE  -- see shadow-mode.md
);

CREATE INDEX ON action_log (account_id, ts DESC);
CREATE INDEX ON action_log (action, ts DESC);
CREATE INDEX ON action_log (trace_id);
```

`trace_id` is the glue: every reconcile tick generates one, propagates through logs, metrics, and action_log rows. Makes incident reconstruction one SQL query.

### Canonical action names

Dot-namespaced, like feature flags. Seed list:

| Action | Target shape | Params |
| --- | --- | --- |
| `raid.send` | `{"x", "y", "tile_id"}` | `{"troops": {...}, "list_id"}` |
| `raid.cancel` | `{"movement_id"}` | `{}` |
| `build.upgrade` | `{"slot", "gid"}` | `{"target_level"}` |
| `build.destroy` | `{"slot"}` | `{}` |
| `train.queue` | `{"building_gid"}` | `{"troops": {...}}` |
| `market.send` | `{"x", "y", "village_id"}` | `{"resources": {...}}` |
| `hero.adventure` | `{"adventure_id"}` | `{"difficulty"}` |
| `hero.equip` | `{"slot", "item_id"}` | `{}` |
| `map.scan.tile` | `{"x", "y"}` | `{}` |

Add a new action name exactly when a new action verb appears in the code. Don't retrofit scrapes — they're not actions, just reads.

### Decorator

Central wrapper in `app/core/audit.py`:

```python
def audit_action(name: str):
    def wrap(fn):
        @wraps(fn)
        async def inner(self, *args, **kwargs):
            ctx = AuditContext.current()   # contextvar set at reconcile start
            start = time.monotonic()
            try:
                result = await fn(self, *args, **kwargs)
                await record(
                    account_id=ctx.account_id,
                    action=name,
                    target=fn.audit_target(self, *args, **kwargs),
                    params=fn.audit_params(self, *args, **kwargs),
                    result='ok',
                    duration_ms=int((time.monotonic() - start) * 1000),
                    trace_id=ctx.trace_id,
                    shadow=ctx.shadow_mode,
                )
                return result
            except Exception as e:
                await record(..., result='failed', result_info={"err": str(e)})
                raise
        return inner
    return wrap
```

Usage:

```python
@audit_action("raid.send")
async def send_raid(self, target: RaidTarget) -> bool:
    ...
```

`audit_target` / `audit_params` are small callables declared alongside (or inferred from arg names). Don't log the raw args — we want predictable JSON shapes, not mystery blobs.

### Retention and volume

Back-of-envelope at 100 accounts, moderate activity:

- ~20 raids/hour/account × 100 = 2000 rows/hour = ~50k rows/day.
- Plus builds, training, hero, market — call it 100k rows/day across the fleet.
- At 100 bytes/row that's ~10 MB/day, 3.5 GB/year. Postgres handles this effortlessly.

Retention: keep hot indefinitely for phase 3. When the table gets big (>100M rows), partition by month; drop partitions older than 2 years.

### Query surface

Three public views used by dashboards and admin:

- `/api/audit/account/{id}?from=...&to=...&action=...` — customer-facing recent history.
- `/api/audit/trace/{trace_id}` — admin, incident forensics.
- `/api/audit/stats?tenant_id=...&window=7d` — aggregate counts by action + result.

## Integration points

- New `app/models/action_log.py`.
- New `app/core/audit.py` — context, decorator, `record()` helper.
- `app/core/reconciler.py:Controller.run_once` — set up the `AuditContext` for this tick (account_id, trace_id, shadow).
- Instrument the action methods: `RallyPointPage.send_raid`, `BuildPage.upgrade`, `TrainingController.queue`, etc. Roughly 15–20 call sites.
- New API routes under `app/api/audit.py`, gated by tenant scoping.
- Dashboard: `VillageDetail.tsx` gets a "Recent actions" card reading from the account-filtered endpoint.

## Tradeoffs / open questions

- **Synchronous writes.** The decorator writes to Postgres in the hot path of every action. At current scale that's fine; at 10k actions/min we'd want a batched async writer (action log goes to an in-memory queue; a background task flushes every 200 ms). Ship sync first, batch later.
- **What counts as "result"?** We stamp `ok` when the action method returns without exception. But "Travian rendered an error banner" isn't the same as an exception. Each action method should normalize — have `send_raid` return a structured `RaidOutcome` and let the decorator read `.result_code`.
- **PII.** Action logs contain target village coords and names. Treat like customer data — tenant-scoped access, delete-on-tenant-delete cascade.
- **Don't log scrapes.** Scraping is high-volume and mostly boring. Audit log is for *actions we took*, not *things we read*. Keeping that line clean keeps the table useful.
- **Does ML want per-keystroke timing?** No, that's too fine-grained — it belongs with humanization telemetry (separate). Action log is verb-level.

## Effort

~2 days. Schema + decorator + context plumbing is a day. Instrumenting the ~15 action call sites is a second day. API + dashboard surface follows as needed.
