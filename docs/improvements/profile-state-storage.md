# 2. Browser profile state storage

## Problem

Phase 1 (shardable workers) assumes any worker pod can pick up any account from the lease table. But Playwright's per-account state (cookies, localStorage, IndexedDB, fingerprint-related init scripts) lives in a profile directory on the pod's local disk.

Consequence: if account #17 last ran on pod-A and pod-A dies, pod-B grabs the lease but starts from a cold profile. Result is:

- A fresh login (which Travian sees as "different device, different time of day" → suspicious).
- Any in-progress session tokens / CSRF tokens / hero adventure dialog state are gone.
- Warm-up delay on every lease handoff — more handoffs, more logins, higher ban surface.

Solve before phase 1 ships externally. Two design options below, recommend (a) initially.

## Option A — Sticky leases (consistent-hash routing)

Profile stays on one pod. Account only moves to another pod when the first one dies. Lease claim logic pins `account_id → worker_id` via consistent hashing of `(account_id, worker_id_ring)`.

**Schema changes:** none to the `account_leases` table itself.

**Claim logic:**

```python
# Pseudocode for the claim loop
workers = get_live_workers()                  # heartbeat within 2 min
ring = HashRing(workers)                      # standard ketama/jump-hash
for account_id in unassigned_or_orphaned():
    preferred_worker = ring.get_node(account_id)
    if preferred_worker == self.worker_id:
        try_claim(account_id)
```

Workers discover peers via a `workers` table (insert row on boot, delete/heartbeat-expire on shutdown).

**Pros:**

- Profile stays warm almost always. A handoff only happens when a pod genuinely dies.
- No new storage infra.
- Natural load balancing at stable fleet size.

**Cons:**

- When you scale from N→N+1 workers, roughly 1/N of accounts rebalance onto the new pod and have to cold-start. Mitigated by rolling the rebalance over ~10 minutes instead of all at once.
- A pod death still loses the profile — any in-flight session has to re-login. Acceptable for this use case.

## Option B — Serialize profile to object storage

On graceful shutdown or before lease release, tar up the profile dir and upload to S3/R2 under `profiles/<account_id>.tar.gz`. On lease acquire, download and extract before spawning the BrowserSession.

**Schema additions:**

```sql
ALTER TABLE accounts
  ADD COLUMN profile_etag TEXT,
  ADD COLUMN profile_uploaded_at TIMESTAMPTZ;
```

`profile_etag` is S3's returned etag — lets workers detect "the profile I uploaded is still the latest" vs. "someone else overwrote it" (shouldn't happen under leases, but cheap to guard against).

**Flow:**

```
acquire_lease(account_id) →
  if profile_etag is not None:
    download s3://profiles/<account_id>.tar.gz → /tmp/profile-<account_id>
    verify etag
  spawn BrowserSession(profile_dir=/tmp/profile-<account_id>)
  ...work...
release_lease →
  tar up profile, upload, update profile_etag
```

**Pros:**

- Any pod can run any account without warm-up penalty.
- Survives pod death (though we lose whatever accumulated since last upload).
- Opens the door to periodic backups — a nightly snapshot is a trivial extension.

**Cons:**

- Per-handoff overhead: typical Playwright profile is 50–200 MB → 5–10 s download+extract on gigabit. Tolerable but not free.
- Requires one of: S3, R2, MinIO, or similar. One more piece of infra.
- Egress cost if compute is in a different cloud than storage.
- Data residency concerns (profile contains customer cookies) — may matter for EU tenants.

## Recommended path

**Phase 1:** ship sticky leases. Simpler, no new storage dep, works well while the fleet is small.

**Revisit Option B** when one of:

- Fleet size crosses ~50 pods (rebalance churn on scale events gets noticeable).
- We need nightly profile backups for disaster recovery.
- A customer asks for "move my accounts between regions" features.

The migration from A → B is additive: the serialize/upload code can be added without removing the sticky logic.

## Integration points

- `app/browser/session.py:BrowserSession.__aenter__` — reads profile dir path from a new `app/core/profile_storage.py` module.
- `app/core/reconciler.py:ControllerLoop.run_until` — on exit, trigger `profile_storage.release(account_id, profile_dir)`.
- New `app/core/lease.py` — claim/heartbeat/release loop, plus consistent-hash logic for Option A.

## Tradeoffs / open questions

- **What gets serialized.** Only `cookies` + `localStorage` + chosen init scripts, or the whole `--user-data-dir`? Whole-dir is robust but large; selective is 10× smaller but requires knowing what Travian cares about. Start whole-dir, optimize later.
- **Profile corruption recovery.** If a profile goes bad (Travian marks it suspicious), we want a "wipe and re-login" escape hatch. Add a `force_fresh_profile` column; the next lease acquire deletes the blob and starts clean.
- **Encryption at rest.** Profile contains session cookies ≈ account credentials. Object storage should be server-side encrypted (default on S3/R2).

## Effort

- Option A (sticky leases): ~1 week including consistent-hash logic, `workers` table, rebalance smoothing.
- Option B (S3 serialization): ~2 weeks on top of A, including migration and testing.
