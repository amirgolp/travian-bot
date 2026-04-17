# 5. Ban early-warning signals

## Problem

Right now the first signal that an account is in trouble is a hard ban: the login fails, `Account.status = 'banned'`, and we're in post-mortem mode. By then the proxy is also tainted (see [commercial-rollout.md](../commercial-rollout.md)'s ban handling), the customer is angry, and we've lost the window where a simple intervention (pause for 6 h, swap proxy, wipe profile) might have saved them.

Travian's multi-hunter doesn't usually go "fine → banned" instantly. There's almost always a ramp: CAPTCHA challenges, Cloudflare interstitials, 403s on specific endpoints, soft shadowbans where raids silently don't register. Capturing that ramp as a `risk_score` and acting on it proactively turns bans from incidents into near-misses.

## Design

A per-account rolling risk score computed from several signals. When the score crosses a threshold, the controller auto-pauses the account and pages the operator.

### Signals to track

| Signal | Source | Weight | Decay |
| --- | --- | --- | --- |
| HTTP 403 / 429 on navigation | Playwright response listener | high | 6 h |
| Cloudflare interstitial rendered | DOM marker check in `BrowserSession.goto` wrapper | critical | 24 h |
| CAPTCHA page rendered | DOM marker check | critical | 24 h |
| Login succeeded but redirected to an unexpected URL | `login.py` | high | 12 h |
| Expected selector missing after page load | Parser methods (already know when they return empty) | medium | 2 h |
| Raid sent but no report within 2× expected travel time | `ReportsController` reconciliation gap | medium | 6 h |
| Raid report shows "spy / no resources" when tile historically paid | Report aggregation | low | 6 h |
| Sudden drop in raid success rate (vs. 7-day baseline) | Rolling aggregate | low | 24 h |
| Account's IP changed mid-session (proxy instability) | Session tracking | high | 24 h |

Weights are relative, tuned empirically once we have data.

### Score computation

```sql
CREATE TABLE account_risk_events (
  id          BIGSERIAL PRIMARY KEY,
  account_id  BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,       -- e.g. 'cloudflare_challenge'
  weight      INT NOT NULL,
  decay_until TIMESTAMPTZ NOT NULL,
  context     JSONB,               -- free-form: URL, selector, trace_id
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON account_risk_events (account_id, decay_until);
```

Current risk score = sum of `weight` over events where `decay_until > now()`.

A `risk_score` materialized view updated every minute:

```sql
CREATE MATERIALIZED VIEW account_risk_scores AS
SELECT account_id, SUM(weight) AS score, MAX(observed_at) AS last_event
  FROM account_risk_events
 WHERE decay_until > now()
 GROUP BY account_id;
```

### Controller hook

Small mixin on `Controller.run_once`:

```python
async def run_once(self, ctx):
    score = await risk.current_score(ctx.account_id)
    if score >= RISK_CRITICAL:     # e.g. 80
        await self._auto_pause(ctx, reason=f"risk_score={score}")
        return
    if score >= RISK_WARNING:      # e.g. 40
        await self._emit_alert(ctx, score)
        # keep running but at gentler cadence — bump requeue_after
    ...
```

Auto-pause flips `accounts.status = 'paused'` and emits an alert event. Resume is operator-gated (set `paused_until` or manually toggle back to `active`). We deliberately do not auto-resume — if the signal was real, unpausing too soon just re-triggers it.

### Signal collection — minimum viable set

Start with the three highest-weight signals and ramp:

1. **Cloudflare / CAPTCHA DOM markers** in `BrowserSession.goto`. One central choke point, catches most known-bad states.
2. **403 / 429 counter** via Playwright response listener.
3. **Login-redirect anomaly** in `app/browser/login.py`.

The rest can be added incrementally without restructure.

### Alerting integration

Risk events feed the existing alerting (`observability.md`):

- Warning threshold → Grafana dashboard + Discord `#ops`.
- Critical threshold → PagerDuty page.
- Auto-pause action → dashboard banner on the account detail page ("paused due to elevated risk since 14:22").

## Integration points

- New `app/services/risk.py` — event recording, score lookup, threshold config.
- `app/browser/session.py:BrowserSession.goto` — wraps `page.goto`, inspects response/DOM for bad markers, records events.
- `app/browser/login.py` — records redirect anomalies.
- `app/core/reconciler.py:Controller.run_once` — gate dispatch on score.
- New columns on `accounts`: `paused_until TIMESTAMPTZ`, `paused_reason TEXT`.
- Frontend: `VillageDetail.tsx` / account detail show risk score + recent events.

## Tradeoffs / open questions

- **False positives.** A Cloudflare challenge can happen for legitimate reasons; auto-pausing too aggressively annoys customers. Start with alert-only for the first two weeks, tune thresholds, then enable auto-pause.
- **Signal independence.** Many signals co-fire (a 403 usually accompanies a Cloudflare page). Naive summing double-counts; may need an "event group" concept that caps related signals at a max. Not worth solving upfront.
- **What about bans we catch too late?** Even perfect signal collection won't save accounts where Travian bans silently — some bans are only detected on next login attempt. Accept this; the goal is to catch the ramp cases, not eliminate bans.
- **Data retention.** Risk events should stick around for 90 days — long enough to retrospective on what predicted real bans.

## Effort

~1 week. First half is the infrastructure (table, materialized view, controller hook). Second half is collecting the three highest-weight signals and tuning thresholds against live data.
