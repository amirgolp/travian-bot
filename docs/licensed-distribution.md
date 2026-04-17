# Licensed distribution — sibling path to SaaS

Status: **draft** — written 2026-04-17 as an alternative to [commercial-rollout.md](commercial-rollout.md). Pick one. Do not ship both paths in parallel.

## Why this path

SaaS multi-tenant (the commercial-rollout plan) makes you responsible for: worker uptime, proxy fleet health, customer account bans, billing outages, data residency, abuse moderation, and the legal posture of "we run automation against Travian on behalf of our customers." The operational surface is large, the margins get eaten by proxy costs, and the first bad Saturday night at 50 customers is miserable.

Licensed distribution flips it: you ship installable software, customers run it on their own machines with their own proxies and their own risk. You sell tools; they use them. Much smaller operational surface, better legal posture, zero server costs other than a tiny activation API.

The tradeoff is **support gets harder** (Windows quirks, proxy provider support, customer network issues) and **piracy is now a real concern**. This doc covers both.

## Product shape

**What the customer gets** — a native installer per platform (Windows/Mac/Linux). They run it, enter a license key, connect their Travian account(s) and their proxies, and the bot runs locally. The dashboard is the same React UI we have today, just wrapped in a desktop window instead of served from a server.

**What we run** — a small activation API (license issue/validate/refresh) plus, optionally in v1.1+, a decision API that holds the hard-to-replicate heuristics. Everything else is on the customer's machine.

## Packaging stack

Four pieces. Each is the least-weight option that does the job.

### 1. Tauri shell (Rust)

A small Rust binary that hosts the React dashboard in the system webview and spawns the Python backend as a child process.

- Total Rust shell: **~8 MB** (vs Electron's ~100 MB).
- Uses the OS's native webview (WebView2 on Windows, WKWebView on Mac, WebKitGTK on Linux).
- Has a first-class bundler that produces `.msi` / `.dmg` / `.AppImage` / `.deb` / `.rpm`.
- Includes Tauri's own auto-updater (checks a release manifest, downloads signed patches).
- **Holds the license check in Rust.** Harder to patch than if it lives in Python.

### 2. Python backend via Nuitka

`app/` is compiled to native C via Nuitka and packaged as a single binary executable.

- Nuitka converts Python → C → native. No readable `.py` files shipped. Real anti-piracy lift compared to PyInstaller's "bytecode in a zip."
- Size: ~30–80 MB compiled, depending on what we pull in.
- Playwright works with Nuitka-compiled code (well-tested).
- Keep the Python source in the repo; Nuitka runs in CI, not in dev.

### 3. SQLite instead of Postgres

Single-user, single-machine = SQLite is plenty.

- SQLAlchemy already supports both dialects. The migration is dropping Postgres-specific bits (JSONB → JSON, `FOR UPDATE SKIP LOCKED` → trivial serial-exec, materialized views → regular views or refresh-on-demand).
- File lives in the user's data dir (`%APPDATA%` / `~/Library/Application Support` / `~/.local/share`).
- Auto-backup to a second file on every app start — SQLite corruption is rare but user trust is important.
- Saves ~200 MB of Postgres runtime from every install.

### 4. React dashboard — stays as-is

The current Vite/React SPA becomes the webview's main page. Built once at release time, bundled as static assets inside the Tauri binary. No code changes beyond updating API calls to hit `localhost:<port>` (Tauri picks a random free port for the Python backend, passes it to the webview via IPC).

### Playwright + Chromium

Big one. ~150–250 MB per browser. Two options:

- **Bundle** — installer is 300+ MB. Works fully offline. Long download for the customer first time.
- **Download on first run** — smaller installer (~100 MB), 300 MB streamed from Microsoft's CDN on first launch. Standard practice.

Go with download-on-first-run. Show a progress bar. Cache aggressively so re-installs don't re-download.

### Realistic sizes

| Component | Size |
| --- | --- |
| Tauri shell | ~8 MB |
| Nuitka-compiled backend + deps | ~60 MB |
| Python runtime (bundled via Nuitka) | ~25 MB |
| React SPA build | ~3 MB |
| SQLite | ~1 MB |
| **Installer (no Playwright)** | **~100 MB** |
| Playwright + Chromium (downloaded after install) | ~250 MB |
| **Disk after first run** | **~350 MB** |

Normal for any 2026-era Electron/Tauri product shipping a browser.

## License flow

Three endpoints on a tiny FastAPI service (call it `license-server`). Hosted somewhere cheap (Fly.io, Railway, Hetzner small VPS) — $15–30/month.

### Schema (license server)

```sql
CREATE TABLE licenses (
  id               UUID PRIMARY KEY,
  key_hash         TEXT NOT NULL,           -- argon2-hashed license key
  email            TEXT NOT NULL,
  tier             TEXT NOT NULL,           -- 'starter' | 'pro' | 'unlimited'
  issued_at        TIMESTAMPTZ NOT NULL,
  expires_at       TIMESTAMPTZ,             -- NULL = lifetime; set for subscriptions
  max_activations  INT NOT NULL DEFAULT 1,  -- max concurrent machines
  revoked          BOOLEAN NOT NULL DEFAULT FALSE,
  stripe_customer_id TEXT,
  notes            TEXT
);
CREATE UNIQUE INDEX ON licenses (key_hash);

CREATE TABLE activations (
  id             UUID PRIMARY KEY,
  license_id     UUID NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
  machine_id     TEXT NOT NULL,             -- stable hardware fingerprint hash
  machine_label  TEXT,                      -- customer-provided "MacBook Pro work"
  activated_at   TIMESTAMPTZ NOT NULL,
  last_seen_at   TIMESTAMPTZ NOT NULL,
  revoked_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX ON activations (license_id, machine_id) WHERE revoked_at IS NULL;
```

### Endpoints

```
POST /licenses/activate
  body: { key, machine_id, machine_label, app_version }
  200 : { jwt, jwt_expires_at, tier, features[] }
  409 : { error: "max_activations_reached", active: [{machine_label, last_seen_at}] }
  403 : { error: "revoked" | "expired" }

POST /licenses/refresh
  body: { jwt, machine_id }
  200 : { jwt, jwt_expires_at }
  403 : { error: "revoked" | "expired" }  → client enters read-only mode

POST /licenses/deactivate
  body: { jwt }
  200 : {}                                  → frees an activation slot

GET  /licenses/account  (authenticated by jwt)
  200 : { tier, features, expires_at, machines: [...] }
```

### JWT shape

Signed with ed25519 private key on the server. Client has the public key baked into the Tauri binary.

```json
{
  "sub": "license:01HXY...",
  "iat": 1745000000,
  "exp": 1745604800,           // 7 days from issue
  "machine_id": "sha256:abc...",
  "tier": "pro",
  "features": ["max_accounts=5", "advanced_heuristics", "priority_support"],
  "app_min_version": "1.0.0"
}
```

### Client-side flow

On app launch, the Tauri shell:

1. Reads the local JWT from the OS keychain (Keychain / Credential Manager / libsecret).
2. If missing or expired beyond grace → show license-entry modal, call `/activate`.
3. If present and fresh (< 7 d) → pass features to Python backend, start reconciler.
4. In background, every 24 h → call `/refresh`. If success, replace JWT. If network fail, keep existing JWT.

If phone-home fails for **> 7 days**, the shell downgrades the app to read-only mode (UI shows historical data but reconciler refuses to start). One banner explaining why, one button to retry.

**Why the grace period:** real-world network outages, laptop-on-a-plane scenarios, license-server downtime on our end. 7 days covers almost all legitimate cases and isn't long enough to make piracy meaningfully easier.

### Machine ID

A stable hash of hardware identifiers (CPU ID, primary MAC, machine UUID). Computed in Rust, not Python — harder to forge.

- Users changing hardware will hit the activation cap. Make the "manage activations" screen in-app prominent: customers can deactivate an old machine themselves to free a slot.
- Do **not** bind too tightly — a Docker rebuild or OS reinstall should not burn an activation. Hash CPU + motherboard UUID only; skip disk/MAC which change too often.

## Anti-piracy: layered defense

Be honest about what each layer is worth. Everything below raises the cost to crackers; nothing below is unpickable.

### Layer 0 (free, ship day 1): Packaging hygiene

- Nuitka-compiled Python (no `.py` in the install).
- Tauri shell with license check in Rust.
- Code signing (Apple Developer cert + Windows Authenticode) — ~$200/year combined.
- Hard-coded ed25519 public key in the Rust shell. JWT verification lives in Rust.
- Binary checksum self-check on startup — if the binary has been tampered, refuse to start. (Not serious anti-tamper; just filters out trivial patching.)

Effort: baked into the packaging pipeline. No incremental work.

### Layer 1 (v1): License activation + phone-home

Covered above. Standard.

### Layer 2 (v1, cheap but real): Tiered obfuscation

- PyArmor on top of Nuitka for the most sensitive modules (e.g. anti-detection config, decision weights if we keep any client-side).
- Strip debug symbols and disable Nuitka's `--remove-output`.
- Run through a commercial packer (Themida, VMProtect) for Windows if we're desperate.

Diminishing returns — do the first two, skip the last.

### Layer 3 (v1.1+, the one that actually works): Server-side critical logic

**This is the only layer that's genuinely hard to defeat**, because cracking the client no longer gets you a working bot — it gets you a shell with no decision-making.

Good candidates to move server-side:

- **Fingerprint generation.** Client requests a fresh browser fingerprint for a new account, server returns one from a curated distribution. Client never sees the generator.
- **Humanization profile selection.** Given account metadata, server returns which behavioral distribution to use. Server holds the good distributions; client has no way to make its own.
- **Farming heuristic.** Client sends `{tiles: [...], history: [...], troops_available: {...}}`, server returns ranked target list. This is the biggest moat — good farming ranking is 80% of the bot's value.
- **Ban-risk scoring.** Client sends observed events, server returns score + recommended action. Server tunes the weights.

Keep on client:

- Scraping, DOM interaction, dispatch. Latency-sensitive and stateful; can't round-trip per click.
- Login sessions, cookies, credentials. Must never leave the customer's machine.
- Anything that needs to work in the 7-day offline grace period.

**Protocol shape** (sketch):

```
POST https://decider.travian-bot.app/v1/farming/rank
  Authorization: Bearer <license JWT>
  body: {
    account_fingerprint_id: "...",
    tiles: [{id, x, y, type, pop, last_raid, ...}, ...],
    troops_home: {...},
    current_village: {x, y},
    ...
  }
  200 : {
    ranked: [
      {tile_id, priority, troop_composition, expected_bounty, ...}
    ],
    tokens_used: 523,
    expires_at: "..."     // this decision valid for N minutes
  }
```

Client caches decisions per (account, inputs) for a few minutes — same inputs → same outputs, keeps API costs down and survives brief outages gracefully.

**Ship plan for Layer 3:**

- **V1: don't build it.** Ship fully offline-capable client with `LocalDecider` implementations inline. Validate the product.
- **V1.1 (3–6 months in):** introduce a `Decider` trait in Python: `LocalDecider` (current) + `RemoteDecider` (new). Feature-flagged per license. Premium tier gets `RemoteDecider` (better decisions because the server-side model has more data); free tier keeps `LocalDecider`.
- **V2 (only if piracy is measured):** move the best heuristics to server-only, force all tiers through `RemoteDecider`. Customers get better results; pirates get a cracked client that can't farm well.

The progression is customer-value-driven, not DRM-driven — which is the right framing anyway. "Our server model is smarter than what we can ship in a local binary" is a feature, not a lock.

### Layer 4 (only if piracy is obviously hurting sales): Aggressive

- Per-license encrypted decision blobs (server encrypts output with license-specific key; cracked clients can't decrypt even if they intercept).
- Anti-debug / anti-VM traps. Genuine nuisance to legitimate customers running in containers or debugging their own issues. Use sparingly.
- Watermarking — embed license ID in non-critical decisions so leaked cracks can be traced to the source.

Don't build any of this until you have evidence of real piracy revenue loss. It's all support overhead.

### Anti-patterns (don't do these)

- **Don't block the reconciler on the license check.** If validation takes 200 ms, that's a UX cost every tick. Validate once on startup, periodically in the background. Hot path never touches the license.
- **Don't silently disable features on expiry.** Show a loud, obvious "license expired, click to renew" dialog. Silent degradation is a support nightmare and bad ethics.
- **Don't make hardware binding aggressive.** Trying to catch every hardware change makes the "customer changed their laptop" support ticket volume unmanageable. Be generous with activations; catch re-selling via the `activations.last_seen_at` heartbeat — if 5 different machines are using the same license concurrently, that's the pattern worth stopping, not a single reinstall.
- **Don't phone home synchronously on every reconcile.** Once per day is plenty. More than that is customer-hostile.

## Distribution pipeline

GitHub Actions matrix build on tag push, uploads signed installers to GitHub Releases, auto-updater notifies running clients.

### Build matrix

| OS | Output | Signing |
| --- | --- | --- |
| `ubuntu-latest` | `.AppImage`, `.deb` | GPG sign (no trusted CA exists for Linux) |
| `macos-latest` | `.dmg` (universal — Intel + Apple Silicon) | Apple Developer ID + notarization |
| `windows-latest` | `.msi` | Authenticode (EV cert if budget allows) |

### Workflow outline

```yaml
# .github/workflows/release.yml
on:
  push:
    tags: ['v*']
jobs:
  build-backend:
    strategy:
      matrix: { os: [ubuntu-latest, macos-latest, windows-latest] }
    steps:
      - checkout
      - setup-python (3.12)
      - install nuitka, app deps
      - nuitka --standalone --onefile app/main.py -o travian-bot-backend
      - upload-artifact: backend-${{ matrix.os }}

  build-installer:
    needs: build-backend
    strategy:
      matrix: { os: [ubuntu-latest, macos-latest, windows-latest] }
    steps:
      - checkout
      - setup-node, build dashboard
      - download backend-${{ matrix.os }}
      - tauri build --bundles native
      - sign (platform-specific)
      - upload-release-asset
```

### Auto-updater

Tauri has built-in support. On app start, GET a manifest URL:

```json
// https://releases.travian-bot.app/latest.json
{
  "version": "1.4.2",
  "notes": "...",
  "pub_date": "2026-04-17",
  "platforms": {
    "darwin-aarch64": { "url": "...", "signature": "..." },
    "windows-x86_64": { "url": "...", "signature": "..." },
    "linux-x86_64":   { "url": "...", "signature": "..." }
  }
}
```

Manifest is served from S3/R2 + CloudFront/Cloudflare. Signed with our ed25519 key (same one used for license JWTs, conveniently). Tauri verifies signature before applying.

Rollout discipline:

- Beta channel (opt-in) — early adopters get `1.4.2-beta.3` one week before stable.
- Staged rollout — new version goes to 10% of licenses for 48 h, then 100%. Tauri doesn't do this natively; we add a tiny random-gated check in the manifest.
- Rollback plan — keep old manifest serving for 30 days. If a release is bad, flip the manifest back; existing clients keep running the old version.

## Hosting requirements (yours, not customers')

| Service | Purpose | Cost estimate |
| --- | --- | --- |
| License server (FastAPI + Postgres) | Activation, refresh, deactivation | $15–30/mo (Fly.io small) |
| Release manifest host (static) | Auto-updater endpoint | $1–5/mo (S3 + CloudFront) |
| Status page | Customers check if "is the license API down?" | $0 (UptimeRobot free) |
| Support forum (Discord) | Community support | $0 |
| Documentation site (mkdocs → static) | Onboarding docs | $0 (Cloudflare Pages) |
| Decider API (only if/when Layer 3 ships) | Server-side heuristics | $50–200/mo initially |

Total: **under $50/month** through v1. Scales roughly linearly with license count (server load) but stays small even at 10k customers.

## Support model

The biggest hidden cost of this path. Budget for it.

- **Tier 1 — Discord community** (free). All customers get access. Volunteers / power users answer most questions. You lurk and escalate.
- **Tier 2 — Email/ticket** (paid tiers). Response within 48 h business days.
- **Tier 3 — Priority** (top tier). Response within 8 business hours, direct Discord channel.

Build a self-serve diagnostics page in the app itself:

- **"Copy diagnostic report"** button that collects: OS, app version, last 500 log lines, proxy latency, Travian server reachability. One click, goes to clipboard. Customer pastes in support thread.
- **Known issues** panel — pulls from a /status endpoint with current known-broken states (e.g. "Travian changed the rally-point DOM on 2026-05-12, fix in 1.4.3").

Documentation focus areas, in order of support-ticket reduction:

1. Proxy setup guide (which providers, how to configure, how to test).
2. "My bot got banned" — what to do, what's recoverable, how to start fresh.
3. License management — activate, transfer, what happens on hardware change.
4. Onboarding wizard — the bot's first 30 minutes should feel guided.

## Diff vs. commercial-rollout

What changes from the SaaS plan:

| Phase | SaaS version | Licensed version |
| --- | --- | --- |
| Phase 1 (shardable workers) | Postgres leases, many pods | **Gone.** Customer's installer runs one process for their accounts. |
| Phase 2 (proxy-per-account) | Full proxy fleet + assignment | **Customer-configured.** Keep the `proxies` table + UI; they point at their own vendor. |
| Phase 3 (tenants + auth + billing) | Stripe subscriptions + tenant scoping | **Replaced** by license activation + Stripe-for-one-time-or-renewal. No tenant scoping (single-user install). |
| Phase 3 NATS | Async jobs infrastructure | **Gone.** No async jobs in a single-user install. |

What stays from the improvements plan:

- **Observability** — opt-in telemetry with a big "share anonymous usage data?" checkbox at install. Different product, same needs.
- **Fingerprint + humanization** — matters more, not less. This is the anti-ban floor regardless of distribution model.
- **Selector regression tests** — unchanged.
- **Ban early-warning** — runs in-process now; alerts surface in the app UI rather than Grafana.
- **Feature flags** — becomes license-tier gating + dev flags. Less runtime rollout machinery, more compile-time / license-encoded features.
- **Audit log** — still valuable. Customer owns their data now; we don't see it unless they share.
- **Shadow mode** — still valuable for onboarding.

## Tradeoffs / open questions

- **VAT / sales tax.** Selling software to EU/UK/etc. means VAT obligations. Two practical options: register in each relevant tax jurisdiction (painful) or use a merchant-of-record like Paddle or LemonSqueezy (they handle VAT, charge ~5%). Strongly prefer the latter unless you're high-volume US-only.
- **Refund policy.** Be generous in v1 — 14-day no-questions refund. Piracy isn't meaningfully worsened by refunds, and goodwill compounds.
- **Beta vs stable cadence.** Monthly stable, weekly beta is a decent default. Avoid shipping stable on Fridays.
- **EULA / ToS.** Need a real EULA before selling. Makes clear: (a) customer assumes Travian-ToS risk, (b) no warranty of fitness, (c) single-user license, no redistribution. Talk to a lawyer before v1.
- **Refund-on-ban?** Customer asks "Travian banned my account, I want my money back." Answer no, with empathy. Document this upfront in onboarding.
- **Multi-machine use.** Power users will want the same license on desktop + laptop. Default to `max_activations=2` for all tiers; solves 90% of this without a "family plan" SKU.
- **Enterprise tier?** Probably premature until someone actually asks. A simple `license_tier=enterprise` + a quote-based sale covers the first few.
- **Legal jurisdiction.** Incorporating somewhere sensible matters more here than for SaaS (you're selling globally). Delaware LLC or UK Ltd are the usual choices; talk to someone before shipping.

## What NOT to do

- **Don't build your own payment processing.** Paddle or LemonSqueezy, full stop. Stripe Checkout if you insist on direct, but you'll deal with VAT yourself.
- **Don't build your own auto-updater.** Tauri's is fine; we tune the manifest, not the update mechanism.
- **Don't build a web-dashboard mode.** "Sometimes customers want to check their bot from their phone." Maybe in v2. For v1, desktop-only is simpler and fine.
- **Don't promise SLAs.** "The bot runs on your machine; it's up to you to keep it running." Document this explicitly.
- **Don't start with Layer 3 anti-piracy.** Ship, validate, add only on evidence.

## Effort

First shippable v1 — rough estimate, solo developer:

| Stream | Effort |
| --- | --- |
| Tauri shell + Python sidecar wiring | 1.5 weeks |
| Nuitka build pipeline, cross-platform | 1 week |
| SQLite migration + code cleanup | 3–5 days |
| License server + activation flow | 2 weeks |
| Auto-updater wiring | 3 days |
| Code signing setup (certs, workflows) | 3 days |
| Installer UX / first-run wizard | 1 week |
| EULA, ToS, refund policy, support-doc MVP | 3–5 days |
| Paddle/LemonSqueezy integration | 3 days |
| **Total** | **~7–9 weeks** |

Plus ~2 weeks of beta with early users before general release. Total wall-clock to first dollar: call it 10–12 weeks from the decision to commit to this path.

Compared to SaaS (~6–10 weeks for phase 1–3), not cheaper upfront — the savings compound later (no server costs, no on-call, no multi-tenant debugging).
