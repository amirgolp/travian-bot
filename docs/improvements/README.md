# Improvements — design sketches

Eight improvement proposals referenced from [commercial-rollout.md](../commercial-rollout.md). Each is a design sketch only — none are implemented yet. Ordered roughly by expected impact and by when in the rollout they should land.

| # | File | Impact | Best phase | Rough effort |
| --- | --- | --- | --- | --- |
| 1 | [observability.md](observability.md) | High — unblocks incident response at scale | Before phase 3 | ~3 days |
| 2 | [profile-state-storage.md](profile-state-storage.md) | High — unblocks true horizontal scaling | Phase 1 | ~1 week (sticky) / ~2 weeks (S3) |
| 3 | [fingerprint-humanization.md](fingerprint-humanization.md) | Highest anti-ban lever untouched | Before external users (phase 2) | ~1–2 weeks |
| 4 | [selector-regression-tests.md](selector-regression-tests.md) | Medium — prevents Travian DOM drift breaking prod | Ongoing | ~1 day to bootstrap |
| 5 | [ban-early-warning.md](ban-early-warning.md) | High — catches bans before accounts lock | Phase 3 or after | ~1 week |
| 6 | [feature-flags.md](feature-flags.md) | Medium — safer controller rollouts | Phase 3 | ~2 days |
| 7 | [audit-log.md](audit-log.md) | High long-term — disputes + ML feedstock | Phase 3 | ~2 days |
| 8 | [shadow-mode.md](shadow-mode.md) | Medium — onboarding polish + risk-free testing | Phase 3 or later | ~2 days |

**Ground rules for all of these:**

- None require changes to the controller/reconciler abstractions.
- None require a second service process (except #1's exporters, which are sidecars).
- Each should be shippable independently — no cross-improvement dependencies except where noted.
- Everything stays per-account isolated; no cross-account shared state is introduced.
