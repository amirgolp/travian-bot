# 6. Per-tenant staged rollouts (feature flags)

## Problem

Once the service has paying customers, a controller change — say a new farming heuristic — can't safely go to every tenant at once. Shipping a subtle bug to the whole fleet is a refund event. We want:

- Turn a new behavior on for tenant #1 (operator's own accounts) for a week.
- Expand to a 10% cohort of paying tenants.
- Full rollout only after signals look clean.
- Ability to instantly kill a feature everywhere if ban rate spikes.

Standard feature-flag problem. Off-the-shelf solutions (LaunchDarkly, Unleash, Flagsmith) are overkill for the tenant counts we'll hit in year one — and they become a runtime dependency for the critical path. A hand-rolled solution is ~100 lines and sufficient through the first thousand tenants.

## Design

### Schema

```sql
CREATE TABLE feature_flags (
  key          TEXT PRIMARY KEY,                -- e.g. 'farming.priority_by_last_raid'
  description  TEXT,
  default_on   BOOLEAN NOT NULL DEFAULT FALSE,  -- applies when no override exists
  rollout_pct  INT NOT NULL DEFAULT 0,          -- 0..100, stable-hash rollout
  killed       BOOLEAN NOT NULL DEFAULT FALSE,  -- emergency kill switch (overrides all)
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE feature_flag_overrides (
  key       TEXT NOT NULL REFERENCES feature_flags(key) ON DELETE CASCADE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  enabled   BOOLEAN NOT NULL,
  reason    TEXT,                               -- why this override exists
  expires_at TIMESTAMPTZ,                       -- optional TTL
  PRIMARY KEY (key, tenant_id)
);
```

Two orthogonal controls:

- `rollout_pct` is a stable-hash roll: `hash(tenant_id + flag_key) mod 100 < rollout_pct`. Deterministic — the same tenant is either always in or always out at a given percentage.
- `overrides` wins over `rollout_pct` wins over `default_on`. `killed=true` beats everything.

### Evaluation — in-process cached

Feature flags must be cheap to evaluate (called from every controller tick). All flags cached in memory, refreshed every 30 s from Postgres:

```python
# app/core/feature_flags.py

class FeatureFlags:
    def __init__(self, db_url: str):
        self._cache: dict[str, FlagState] = {}
        self._task: asyncio.Task | None = None

    async def start(self): ...   # refresh loop
    async def stop(self): ...

    def is_enabled(self, key: str, tenant_id: int) -> bool:
        flag = self._cache.get(key)
        if flag is None or flag.killed:
            return False
        override = flag.overrides.get(tenant_id)
        if override is not None and (override.expires_at is None or override.expires_at > now()):
            return override.enabled
        if hash_rollout(tenant_id, key) < flag.rollout_pct:
            return True
        return flag.default_on
```

Every worker pod and the API pod run their own instance; the 30-s refresh lag is acceptable for every flag we'll use (nothing is safety-critical at sub-minute granularity).

`killed=true` is the exception — when it's flipped, we want every pod to respect it within seconds. Two ways:

- Accept the 30-s lag (simplest).
- Push via the NATS control-plane bus from phase 3 (`control.flags.invalidate`) — workers refresh immediately.

Start with the 30-s lag; use NATS once it's there.

### Usage at call sites

```python
from app.core.feature_flags import flags

async def reconcile(self, ctx):
    if flags.is_enabled("farming.priority_by_last_raid", ctx.tenant_id):
        targets = self._new_priority_logic(...)
    else:
        targets = self._old_priority_logic(...)
    ...
```

Rule: every flag removal PR must also remove the dead branch. Dead branches rot faster than anyone expects.

### Admin UI (small)

A dashboard page at `/admin/flags`:

- Table of flags with default_on / rollout_pct / killed toggles.
- Per-flag: list of tenant overrides, add/remove/set expiry.
- Audit trail of who changed what (just log it — the table itself doesn't need a history column until we have compliance needs).

### Naming convention

Flag keys use dotted namespaces: `area.specific_behavior`. Examples:

- `farming.priority_by_last_raid`
- `farming.oasis_skip_empty_tier`
- `training.prefer_cavalry_over_infantry`
- `stealth.mouse_bezier_paths`
- `ui.show_risk_score_on_village`

Namespace = rough owner area. Keeps the list skimmable in the admin UI.

## Integration points

- New `app/core/feature_flags.py` — the evaluator + refresher.
- `app/main.py` — start/stop the refresher alongside other services.
- `app/api/admin/flags.py` — CRUD admin routes (auth-gated via phase 3 tenant scoping, restricted to `is_operator=true` users).
- New dashboard page `dashboard/src/pages/AdminFlags.tsx`.
- `app/core/reconciler.py:ControllerContext` — gains `tenant_id` for flag evaluation.

## Tradeoffs / open questions

- **Flag lifecycle discipline.** Without a policy, flags accumulate forever. Add a "created_at" column and a quarterly cleanup review. Flags older than 90 days should either be fully rolled out + code cleaned, or documented as "permanently configurable."
- **Account-level overrides?** Not in v1. Tenant-level is sufficient — a tenant's accounts are their own concern. If someone wants per-account flipping, they can open two tenants.
- **Flag evaluation in tight loops.** `is_enabled` should be O(1) dict lookup + O(1) hash. Avoid putting it inside DOM-iteration loops; evaluate once per reconcile tick, branch at the top.
- **Pre-phase-3 usage.** Before tenants exist, we can use `account_id` as the rollout key. Migration when phase 3 lands: rollout state is ephemeral per-tenant anyway, no data migration needed.

## Effort

~2 days. Tables + evaluator + admin API is a day; minimal admin UI is the second day. Can ship incrementally — get the evaluator working with just default_on/killed first, add rollout_pct + overrides in follow-up PRs.
