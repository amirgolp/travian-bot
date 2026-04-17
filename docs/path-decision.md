# Path decision — SaaS vs Licensed Desktop

A decision aid, not a recommendation. Two sibling plans exist — [commercial-rollout.md](commercial-rollout.md) (SaaS) and [licensed-distribution.md](licensed-distribution.md) (licensed desktop). Pick one. Building both in parallel doubles the work and ships neither.

## Quick summary

| | SaaS | Licensed Desktop |
| --- | --- | --- |
| **You run** | Worker fleet, DB, proxies, billing, support | Small activation API, release manifest, support |
| **Customer runs** | A browser tab | A desktop installer on their machine |
| **Customer provides** | Credit card | License fee + their own proxies + their own compute |
| **Revenue model** | Monthly/annual subscription | One-time, annual renewal, or hybrid |
| **Upfront build** | ~6–10 weeks (phases 1–3) | ~7–9 weeks (packaging + license server) |
| **Time to first dollar** | ~10–12 weeks | ~10–12 weeks |
| **Monthly ops cost** | Scales with customers ($5–20/customer) | Flat ~$50/month regardless of customer count |
| **On-call burden** | High — their bot down = your page | Low — their bot down = their problem |
| **Piracy risk** | None | Real, manageable with layered defense |
| **Legal posture** | Weaker — you run automation for them | Stronger — you sell tools, they automate |
| **Support burden** | Medium — one environment you control | High — every customer has different OS/proxy/network |
| **Scalability ceiling** | Engineering — need shardable workers, proxy fleet | Operational — hire support, write docs |
| **Margins at scale** | Compressed by proxy + compute costs | Near 100% (minus Paddle/Stripe fees) |

## Decision matrix

Score each row for your situation. More "SaaS ↑" = pick SaaS. More "Licensed ↑" = pick Licensed.

| Question | SaaS ↑ if... | Licensed ↑ if... |
| --- | --- | --- |
| **Your target customer** | Non-technical ("sign up and go") | Technical enough to install software, configure proxies |
| **Your appetite for on-call** | You enjoy (or can hire) 24/7 ops | You want evenings back |
| **Your cash flow** | Comfortable covering $5–20/customer in infra before they pay | Prefer near-zero marginal cost per customer |
| **Your moat** | "We run it so you don't have to" | "We make the best automation tool" |
| **Your ban-risk tolerance** | Willing to eat occasional customer bans as a cost of business | Want customers to own the ban risk (their IP, their account) |
| **Your billing setup appetite** | Comfortable with Stripe subs, webhooks, dunning | Prefer one-time or annual renewal, Paddle handles it |
| **Your legal concerns about Travian ToS** | Accept running "automation-as-a-service" is more exposed | Want the "we sell tools" defense |
| **Your marketing reach** | Organic SEO + content + paid ads | Niche communities, word-of-mouth, Discord-first |
| **Your support appetite** | "I want one environment to debug" | "I can handle Windows/Mac/Linux support threads" |
| **Your feature shipping cadence** | You want every customer on the latest version automatically | Some customers on v1.2 while others are on v1.4 is fine |
| **Your exit story** | Acquisition by someone wanting the customer base + MRR | Lifestyle business, high margin, acquirer-neutral |

## Signposts

### Pick SaaS if:

- You enjoy running infrastructure and have strong ops instincts (or a co-founder who does).
- Target customers are Travian players who aren't willing to run software locally — younger, less technical, on phones.
- You want MRR as a lifestyle metric and an acquisition target.
- You're OK being the one holding the bag when a proxy vendor goes red or Travian changes their anti-bot.
- You have the runway to absorb proxy + compute costs for 50+ customers before profitability.

### Pick Licensed Desktop if:

- You want to minimize ongoing operations and avoid being on-call.
- Target customers are serious Travian players comfortable with technical setup.
- You want the legal posture of "we sell software" rather than "we run a service against Travian."
- You want near-zero marginal cost per customer and high per-unit margins.
- You'd rather deal with piracy (a solvable problem) than proxy-fleet management (never solved).
- You're OK with a less "pressable" narrative for investors / acquirers.

## What-if scenarios

### "What if I start licensed and switch to SaaS later?"

Possible but painful. SaaS requires multi-tenant scoping that licensed doesn't have. Roughly a 4–6 week bolt-on — most of commercial-rollout phase 3 — plus the proxy-fleet and worker-sharding work (phases 1–2). Customer data migration is nontrivial (SQLite → Postgres). But: your first 100 licensed customers don't want to switch to SaaS; they bought a product, not a service. So this is really "launch a separate SaaS for a different market," not "migrate existing customers."

### "What if I start SaaS and switch to licensed later?"

Much easier direction. You already have the full runtime; you just package it for desktop. Strip out phase 1/3 infra, add the Tauri+Nuitka pipeline, add the license server. Roughly 4–5 weeks. Existing SaaS customers can be offered a "buy lifetime desktop version for $X" upgrade if you decide to shut SaaS down.

### "What if I ship both?"

Don't. Here's why:

- Every feature ships twice (SaaS UI + desktop UI, though the dashboard code is shared most of the time).
- Support covers two product surfaces with different failure modes.
- Pricing communication gets confusing ("which should I buy?").
- You split the single developer focus between two products before either has product-market fit.
- The revenue models pull in different directions — SaaS optimizes for MRR retention, licensed optimizes for one-time conversion. These reward different marketing, different features, different support styles.

If you really want both eventually: SaaS first, licensed as a side product for the "I want to run it myself" segment after SaaS is stable (12+ months in). Or licensed first, SaaS as a later expansion to reach non-technical users.

### "Can I do SaaS on a licensed-style distribution?"

Yes — a private SaaS where you host the customer's instance on your infrastructure but it's running the licensed codebase. Useful for a small number of high-paying customers who want "installed" semantics but not the local-install work. Call it the "enterprise hosted" tier. Cheap to offer because it's the same binary you sell; just you run it.

This is a reasonable v2 option: ship licensed first, add "hosted licensed" as a premium tier later. Don't do it at v1 — it fragments the ops story before you have learned anything.

## Reversibility — rough ranking

From easiest-to-pivot to hardest:

1. **Licensed → Hosted Licensed** (3–5 days, same binary).
2. **SaaS → Licensed** (4–5 weeks, strip multi-tenancy, add packaging).
3. **Licensed → SaaS** (6–10 weeks, full phase 1–3 retrofit).
4. **Either → Completely different product** (rewrite).

This argues for licensed-first on pure optionality: it keeps more doors open more cheaply.

## The honest version

If I were choosing for a solo developer with a finished bot and no team:

- **Go licensed.** The ops burden of SaaS eats solo developers alive the moment you get past 30 customers. Licensed lets you focus on product and marketing, not pager rotations. The piracy risk is real but bounded; the ops risk of SaaS is unbounded and compounding.
- **Go SaaS** only if (a) you already have a co-founder or budget for an ops engineer, (b) your target market is meaningfully broader than what "download software, configure proxies" reaches, or (c) you have a specific reason to believe the SaaS MRR story will unlock growth that licensed can't.

Either works. The main risk is not picking either and flailing between them.

## Once you decide

- **Delete the plan you're not using.** Or at minimum, stop referencing it in PR discussions. The sibling plan exists in git history if you ever want it back.
- **Commit the decision in writing** (update [commercial-rollout.md](commercial-rollout.md) or [licensed-distribution.md](licensed-distribution.md) with a `Status: selected` header).
- **Delete or reshape the improvements docs** that only apply to the path you didn't pick ([improvements/README.md](improvements/README.md) maps each item to a phase — some are cross-cutting, some are SaaS-only).
- **Revise memory.** The `project_commercial_roadmap.md` memory should reflect the chosen path so future sessions don't re-relitigate.
