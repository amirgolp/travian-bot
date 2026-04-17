# Commercial rollout plan

Status: **draft** — written 2026-04-17, before shipping anything in this plan. Revise as decisions land.

## Goal

Turn travian-bot from a personal multi-account tool (2 accounts on one laptop) into a private paid service where account count can grow fast without a rewrite.

## Current state (2026-04-17)

- Single Python process runs the API + the `AccountManager`, which holds one `AccountWorker` per active account in memory. Workers die with the process.
- One Playwright browser context per account. No proxies — all accounts share the operator's home IP.
- FastAPI routes under `app/api/` take `account_id` / `village_id` directly with no tenant scoping.
- Postgres runs via `docker-compose`. Dashboard is a Vite/React SPA served separately.
- Reconciler + controllers (`VillagesController`, `TroopsController`, `MapScanController`, etc.) are the mature part of the codebase and should stay untouched through the platform work below.

## Three-phase rollout

Each phase is designed to ship independently and leave the app in a working state. Order matters: we refuse to take money before phase 3.

### Phase 1 — Shardable workers (~1 week)

Goal: any number of worker processes on any number of VMs can run accounts. One pod dying does not take accounts offline for more than ~30s.

**New table:**

```sql
CREATE TABLE account_leases (
  account_id   BIGINT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
  worker_id    UUID,
  leased_until TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ
);
CREATE INDEX ON account_leases (leased_until);
```

Every active account gets a row (trigger on `accounts` insert, or a backfill job on startup).

**Worker loop:**

- Env: `WORKER_ID` (uuid, stable per pod), `WORKER_SLOTS` (e.g. 20).
- Every 10s: if `own_count < WORKER_SLOTS`, claim more with:

```sql
UPDATE account_leases
   SET worker_id = $me, leased_until = now() + interval '2 min',
       heartbeat_at = now()
 WHERE account_id IN (
   SELECT l.account_id
     FROM account_leases l JOIN accounts a ON a.id = l.account_id
    WHERE a.status = 'active'
      AND (l.worker_id IS NULL OR l.leased_until < now())
    FOR UPDATE SKIP LOCKED
    LIMIT $need
 ) RETURNING account_id;
```

- Each returned `account_id` → spawn the existing `AccountWorker`.
- Heartbeat every 30s: `UPDATE ... SET leased_until = now() + 2 min WHERE worker_id = $me AND account_id = ANY($live)`.
- SIGTERM: stop workers, `UPDATE ... SET leased_until = now() WHERE worker_id = $me` so peers grab the accounts within one scan tick.

**Why `SKIP LOCKED`:** Postgres gives us a queue without a broker. Scaling the worker fleet is `kubectl scale --replicas=N` (or docker-compose scale).

**API pod stops running workers.** The API reads the same DB and shows status based on `account_leases.heartbeat_at` freshness.

**Control plane:** "start/stop an account" = flip `accounts.status` in DB. Workers notice on the next scan — no direct RPC between API and workers.

**Risks to plan for:**

- **Playwright profile dirs live on local disk.** Either (a) pin `account_id → worker` via consistent-hash leases instead of SKIP LOCKED, or (b) serialize profile state to object storage (S3) on worker shutdown and load on lease acquire. (a) is simpler; go with that unless we need dynamic rebalancing.
- **Lease table churn:** ~500 accounts × 30s heartbeat = ~1000 writes/min. Autovacuum tuning matters more than the raw writes.

### Phase 2 — Proxy-per-account (~1 week)

Goal: every account logs in from a dedicated, sticky IP. No external user logs in sharing an IP with another account.

**New table + FK:**

```sql
CREATE TABLE proxies (
  id              BIGSERIAL PRIMARY KEY,
  url             TEXT NOT NULL,           -- "http://user:pass@host:port"
  kind            TEXT NOT NULL,           -- "residential" | "isp" | "datacenter"
  region          TEXT,                    -- rough geo tag for matching
  healthy         BOOLEAN NOT NULL DEFAULT TRUE,
  last_error_at   TIMESTAMPTZ,
  notes           TEXT,
  assigned_count  INT NOT NULL DEFAULT 0,
  assigned_limit  INT NOT NULL DEFAULT 1   -- 1 for residential, 2-3 for ISP
);
ALTER TABLE accounts ADD COLUMN proxy_id BIGINT REFERENCES proxies(id);
```

**Assignment rules:**

- On account creation, pick the cheapest `kind` that matches the server's region and has `assigned_count < assigned_limit`.
- **Sticky forever.** Never rotate a living account's proxy. Travian fingerprints IP↔account pairs; a mid-life IP change is worse than no rotation.
- Only reassign when a proxy is marked unhealthy AND we've human-decided to replace it. Auto-reassign masks bans.

**Wiring:** `BrowserSession.__aenter__` reads `account.proxy` and passes it to `browser.new_context(proxy={...})`. Single code site.

**Health observation:** wrap each `reconcile()` call with a "proxy observation" — 2 consecutive login/navigation failures → set `proxies.healthy = false`, page the operator, stop the affected account until a human resolves it. Do not auto-swap.

### Phase 3 — Tenants + auth + billing (~1-2 weeks)

Goal: multi-user safety. A customer can only see their own accounts. Payment failure pauses their workers.

**Model:**

```sql
CREATE TABLE tenants (
  id                 BIGSERIAL PRIMARY KEY,
  email              TEXT UNIQUE NOT NULL,
  stripe_customer_id TEXT,
  plan               TEXT NOT NULL DEFAULT 'free',
  max_accounts       INT NOT NULL DEFAULT 1,
  status             TEXT NOT NULL DEFAULT 'active' -- active|past_due|cancelled
);
CREATE TABLE users (
  id         BIGSERIAL PRIMARY KEY,
  tenant_id  BIGINT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email      TEXT UNIQUE NOT NULL,
  pw_hash    TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE accounts ADD COLUMN tenant_id BIGINT REFERENCES tenants(id);
```

**Tenant scoping — do it in one place:**

- FastAPI dependency `current_tenant()` from a session cookie or JWT.
- SQLAlchemy event on `before_execute` that injects `WHERE tenant_id = :t` on every SELECT/UPDATE that touches a tenant-scoped table. One choke point, hard to forget.
- Writes: server sets `tenant_id` from the session, never trusts it from the client.

**Plan enforcement:** `max_accounts` is checked only on the "create account" path. Everything else is already gated by scoping, so a downgrade just means they can't add more.

**Billing:** Stripe Checkout + webhooks. `checkout.session.completed` → set `plan`, `max_accounts`. `invoice.payment_failed` → `status = 'past_due'`. A nightly job pauses all `AccountStatus.ACTIVE` accounts for tenants in `past_due`.

**Migration:** operator's existing accounts become tenant #1. Add the column nullable → backfill → set NOT NULL. Zero user-visible change.

**NATS JetStream (introduced in this phase):**

Phase 3 is the first phase where the app actually has async jobs worth queueing — Stripe webhooks, outbound email, ban-event alerts, weekly report aggregations. We pull in NATS now because doing it inline in the API request handler is how you end up with "why did the webhook retry silently drop my customer's subscription renewal?" incidents.

- Single binary alongside the existing docker-compose stack. Clusterable later; one node is fine through hundreds of tenants.
- Subject layout: `jobs.email.send`, `jobs.alerts.ban`, `jobs.billing.webhook`, `jobs.reports.weekly`. Durable pull consumers per subject with explicit ack + retry policy.
- Reuse NATS core (no JetStream) for the control-plane signal bus: `control.account.<id>.pause` / `.resume`. This kills the 10s `accounts.status` polling latency for ops actions without adding a second piece of infra.
- A workers-side consumer subscribes to `control.account.*` and maps signals into the existing `AccountWorker` stop/apply_toggles path.

Explicitly **not** using NATS for:

- The AccountWorker lease itself — that's Postgres SKIP LOCKED (phase 1). JetStream messages assume quick ack, not multi-hour holds.
- Replacing the reconciler. The reconciler is "is the world how I want it?"; NATS is "process this message." Different shapes.

## Dealing with IP bans

Three failure modes, different responses:

### 1. Game-level account ban (Travian locks the login)

- The account is dead; no proxy rotation saves it. Mark `Account.status = 'banned'`, stop the worker, alert the operator.
- **The proxy is now tainted.** Any new account assigned to that same IP inherits the reputation. Mark `proxies.notes = 'tainted: account <id> banned YYYY-MM-DD'` and retire it (drop `assigned_limit` to 0). Do not recycle for at least 30 days; ideally never.
- **The fingerprint is also tainted.** If we're reusing browser fingerprints across accounts, rotate it. One ban should not compromise a second account.

### 2. IP-level block (Cloudflare challenge, rate-limit, 403)

- Stop that account's worker immediately — retrying through the same IP makes it worse.
- If the proxy is datacenter-grade, the provider likely got listed; swap to a cold-standby residential proxy.
- If residential, the IP itself is likely flagged. Soft-retire it for a cool-off (24–72 h). Reassign the account to a warm spare.
- Track `proxies.last_error_at` so the same proxy doesn't get re-used during cool-off.

### 3. Provider-level flag (your whole proxy pool from one vendor goes red)

- Rare but happens when anti-bot vendors distribute blocklists. Need at least two proxy vendors. Auto-failover at the account level (swap proxy_id) with the same sticky-forever caveat — only swap when the current one is observed-dead.

### Operational primitives this needs

- **Warm-spare pool:** keep ~20% of the fleet unassigned as ready replacements. `assigned_limit = 1` by default but `assigned_count = 0` on warm spares.
- **Staged canaries:** when a new proxy vendor is onboarded, run a non-customer "sacrificial" account on each IP first. If it survives 7 days, graduate the IP to the assignable pool. Never put a paying customer on a fresh vendor.
- **Cooldown audit log:** `proxy_events` table recording assigns/retires/bans with reason. Useful for both ops and post-mortems.
- **Post-ban fingerprint reset:** the anti-detection layer (fingerprint + behavioral profile) should be per-`(account, proxy)` pair. A retired proxy takes its fingerprint with it.

### What we tell customers

- Up front: bans are possible; this is against Travian ToS; we do everything we can but can't promise zero bans. SLA is on the bot running, not on the game not banning.
- Operationally: if a customer's account is banned, their proxy is gone too. Next account they add gets a fresh proxy — no reputation bleed.

## Deployment — AWS vs alternatives

**AWS is usable, but it's not the cheapest fit, and the exit IP doesn't matter anyway.**

The worker traffic exits via the assigned residential/ISP proxy, so Travian never sees the worker's actual IP. That means the compute provider choice is purely about price and ops maturity, not anti-detection.

### AWS-first architecture

| Layer | Service | Notes |
| --- | --- | --- |
| API pod(s) | ECS Fargate behind ALB | Small; 0.5 vCPU / 1 GB is plenty. |
| Worker pods | ECS Fargate OR EC2 ASG | Fargate is simpler, EC2 is ~40% cheaper at steady state. |
| DB | RDS Postgres | Start at db.t4g.medium; readers later. |
| Browser profile state | S3 | Object-per-account, loaded on lease acquire. |
| Secrets | Secrets Manager | Account creds, proxy creds, Stripe keys. |
| Logs/metrics | CloudWatch + X-Ray | Swap in Grafana/Loki if you want richer. |
| Scheduled jobs | Already in-process (reconciler) | No EventBridge needed. |
| Queues (future) | SQS if we ever need async fan-out | Not needed in phase 1–3. |

**Why this works:** Fargate's killer feature for us is per-task IAM + isolation. One banned account blowing up doesn't affect noisy neighbors because each worker task is its own cgroup.

**Why it hurts:** Playwright is CPU-heavy. At scale, ~20 accounts per vCPU is a realistic ceiling. Fargate is ~$0.04 / vCPU-hour; at 100 accounts that's ~$175/month in compute alone, before RDS, proxies, and egress. Hetzner/OVH dedicated CPU boxes are 3–5× cheaper for the same workload.

### Cheaper alternative

- **Hetzner CCX33** (8 vCPU dedicated, 32 GB RAM, ~€50/month) hosts ~150 accounts comfortably.
- **Neon / Supabase / RDS** for Postgres (keep the DB managed — don't self-host at this scale).
- **R2 / S3** for profile blobs.
- **Cloudflare** in front of the API.

This trades some ops maturity for cost. At 50+ accounts it pays for itself; at 500+ it's a meaningful margin difference.

### Recommendation

- Phase 1: deploy workers on whatever you already know (probably AWS if the team is AWS-native).
- Revisit compute cost at 100 paying accounts. By then the workload shape is known and a migration to Hetzner/OVH is a week of work.
- DB on managed service from day one, regardless of compute provider.
- Proxies from a specialist vendor (Oxylabs, Bright Data, Soax) — do not try to DIY residential IPs.

### Not worth over-engineering early

- Multi-region. Travian servers are per-region; assign proxies regionally, let compute sit wherever.
- Autoscaling workers. Account counts grow in human time (signups), not request bursts. Set replicas manually, revisit monthly.
- Kubernetes. ECS + Fargate is enough through phase 3. EKS is justified only if the team already runs K8s elsewhere.

## Playwright alternatives — considered, declined

The question came up: would swapping Playwright for something else reduce ban risk? Short answer: no, and the alternatives almost all lose more than they gain. Longer answer per option:

| Option | Notes | Verdict |
| --- | --- | --- |
| **Puppeteer (Node)** | Same browser-automation model, weaker anti-detect posture out of the box, would force a Python→Node rewrite of the scraper half. | Skip. |
| **Selenium / undetected-chromedriver** | Older, WebDriver protocol is more fingerprintable than CDP, community maintenance is patchy. | Skip. |
| **Nodriver / zendriver (Python, CDP-direct)** | Modern async Python lib, no WebDriver footprint, stealthier defaults. Smaller ecosystem; some APIs Playwright has are missing. | Worth a spike only if Playwright stealth starts losing. |
| **Rod (Go) / chromedp** | Fastest, leanest, best memory profile — but a full rewrite in Go. | Skip unless the team is Go-native. |
| **Camoufox** (patched Firefox for anti-detect) | Real project, maintained, designed for this use case. Can drive via Playwright (Firefox engine). | Plausible pairing *with* Playwright. |
| **Raw CDP / BiDi** | Lowest level, most control. Also most code to maintain. | Only if we hit a detection vector Playwright can't touch. |
| **HTTP-only (reverse-engineer AJAX)** | Fastest + cheapest compute. But Travian's anti-bot runs on endpoints too (cookies, CSRF tokens, timing patterns, tabindex state). Building a convincing HTTP client is arguably harder than driving a real browser. | Skip for core interactions; consider for pure reads (map tiles, reports list) where the AJAX endpoint is already stable. |

**The real insight:** the library is not the lever. Anti-detection lives in three places, in descending order of impact:

1. **IP reputation** — proxy quality and stickiness (phase 2).
2. **Behavioral signals** — mouse movement, dwell time, click ordering, between-page pauses. This is `app/browser/humanize.py` territory, not the library choice.
3. **Browser fingerprint** — screen size, timezone, fonts, canvas, WebGL. Per-account stable, varied across accounts.

Swapping Playwright for zendriver might buy you a 5% stealth improvement and costs you weeks of migration. Tightening humanization and adding per-account fingerprint persistence probably buys 40% and costs a week.

**Recommendation:** stay on Playwright. Invest in a stealth layer on top (there are several — `playwright-stealth`, manual patches from the Camoufox/rebrowser projects). Treat Camoufox as a Plan B if Chromium-based stealth starts losing to Travian's multi-hunter.

## Other improvements worth considering

Not part of phases 1–3 but worth scoping before the first paying customer. Ordered by expected impact. Each has a full design sketch under [improvements/](improvements/README.md).

### 1. Observability stack (before phase 3) — [sketch](improvements/observability.md)

Structured logs already exist. What's missing:

- **Metrics** (Prometheus) — per-account reconciler tick duration, controller error counts, proxy error rate, Playwright context memory. Needed the first time something goes slow at scale.
- **Log aggregation** (Loki or CloudWatch) — grep across pods instead of sshing into each one.
- **Dashboards** (Grafana) — one page per tenant would be gold for support.
- **Alerting** — PagerDuty or Discord webhook fed by Prometheus alerts. Ban events, lease starvation, webhook retry exhaustion.

Budget: ~3 days to wire, pays back the first incident.

### 2. Browser profile state in object storage (blocker for true phase 1) — [sketch](improvements/profile-state-storage.md)

Currently Playwright profile dirs live on local disk. Horizontal scaling assumes any worker can pick up any account, but the profile stays on the pod that last ran it. Options:

- **Sticky leases** — consistent-hash account → worker so the profile stays warm. Simpler, limits rebalancing.
- **Serialize to S3/R2** on shutdown, hydrate on acquire. More flexible, ~5–10s warm-up per handoff.

Go sticky first; revisit if rebalancing becomes important.

### 3. Fingerprint + humanization as a proper library (before inviting outsiders) — [sketch](improvements/fingerprint-humanization.md)

Audit `app/browser/humanize.py` and the session setup for:

- Per-account stable fingerprint (screen, TZ, locale, fonts, UA) serialized in the DB.
- Behavioral profile per account — typing cadence, between-click pauses, scroll patterns — not global constants.
- Mouse-path generation (bezier/spline-based, not straight jumps).
- Session-level rhythm: active/break windows already exist; extend to "tired" late-session degradation so a profile doesn't look like a fresh-start machine 8 hours in.

This is the single biggest anti-ban lever we haven't pulled yet.

### 4. Selector regression tests from `samples/` (ongoing) — [sketch](improvements/selector-regression-tests.md)

The `samples/` directory already contains captured HTML. Wire a pytest suite that replays each page parser against the saved HTML and asserts the extracted shape. Catches Travian-side DOM changes before production workers hit them.

Budget: a day to set up, a lot of night-time debugging saved.

### 5. Ban early-warning signals (phase 3 or after) — [sketch](improvements/ban-early-warning.md)

Signals worth tracking per account, aggregated into a `risk_score`:

- 403s / Cloudflare challenges in the last hour.
- Unusual redirect patterns on login.
- CAPTCHA pages rendered.
- Unexpected empty scrapes (page loaded but expected selector missing).
- Sudden drop in raid success rate.

When the score crosses a threshold, pause the worker proactively and alert. Catches bans before the account is formally locked, sometimes letting you intervene (swap proxy, cool down) in time.

### 6. Per-tenant staged rollouts (phase 3) — [sketch](improvements/feature-flags.md)

When we change a controller — say a new farming heuristic — we don't want to ship it to every tenant simultaneously. A simple feature-flag system (`tenant_feature_flags` table, YAML-loaded defaults) lets us canary a change on tenant #1 (our own accounts) for a week before broader rollout. LaunchDarkly/Unleash are overkill; a hand-rolled 100-line solution is enough through the first thousand tenants.

### 7. Audit log of every in-game action (phase 3) — [sketch](improvements/audit-log.md)

One table, append-only: `account_id`, `ts`, `action` (e.g. `raid.send`, `building.upgrade`), `target`, `result`, `trace_id`. Enables:

- Customer disputes ("my bot attacked the wrong target") resolved by reading the log.
- Debugging production-only behavior that doesn't reproduce locally.
- Future ML: feed risk-score signals into a ban predictor.

Cheap to add (a decorator on the action methods), valuable forever.

### 8. Dry-run / shadow mode — [sketch](improvements/shadow-mode.md)

Boolean flag on account: `shadow_mode=true` means the bot computes what it *would* do but never submits to Travian. Useful for:

- Onboarding new tenants: run shadow for 24 h, show them what it would have done, then flip to live.
- Testing new heuristics on live traffic without risk.

Small patch; big perception win.

## Open questions

- **Fingerprint rotation policy:** is a fingerprint per-account or per-`(account, proxy)`? Today it's unclear — audit the anti-detection code before phase 2 ships.
- **Account purchase flow:** do customers bring their own Travian credentials, or do we provision accounts for them? Answer changes what goes in the "create account" UI and how plan caps are enforced.
- **Data retention:** if a tenant cancels, do we delete their raid history immediately or keep it for 30 days?
- **Incident communication:** status page? email-only? Where do ban announcements go?

## What is explicitly **not** in this plan

- Migrating the controllers. They stay as-is; changing them during platform work multiplies risk.
- Changing the selectors / DOM-parsing strategy. Unrelated to scaling.
- Building a mobile app or exposing a public API. Dashboard is enough for v1.
