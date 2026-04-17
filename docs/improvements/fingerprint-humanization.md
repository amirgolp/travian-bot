# 3. Fingerprint + humanization as a proper library

## Problem

Today, anti-detection lives across a few files — `app/browser/fingerprint.py` (93 lines), `app/browser/stealth.py` (130 lines), `app/browser/humanize.py` (182 lines). Based on sizes alone they're "basic hygiene, not a real stealth layer." The kick-off plan flagged "serious anti-detection" as a hard requirement (decision #3), and that's probably the single biggest anti-ban lever this codebase has not yet pulled.

Three related but distinct problems to fix together, because any one alone leaks enough signal to fingerprint the bot:

1. **Fingerprint is not per-account stable.** Travian's multi-hunter looks at screen size, timezone, locale, fonts, canvas/WebGL hashes, UA, WebRTC leaks. If account #17's fingerprint shifts between sessions, that's a signal. If two accounts share one, that's a bigger signal.
2. **Behavioral patterns are global constants.** `humanize.py` almost certainly has `sleep(random.uniform(0.8, 2.2))` type helpers — identical distribution across accounts. Even if each account looks human individually, a fleet looks like a fleet.
3. **No session-arc modeling.** A real player logs in, is sharp for 20 minutes, slows down, takes a coffee break, comes back slower. Constant-rhythm bots don't do this.

## Design

Reorganize `app/browser/` so the fingerprint and behavior are first-class per-account objects, persisted, and varied.

### Fingerprint — persisted per account

New column on `accounts`:

```sql
ALTER TABLE accounts ADD COLUMN fingerprint_json JSONB;
ALTER TABLE accounts ADD COLUMN fingerprint_generated_at TIMESTAMPTZ;
```

`fingerprint_json` content:

```json
{
  "user_agent": "Mozilla/5.0 ...",
  "platform": "Win32",
  "screen": {"width": 1920, "height": 1080, "color_depth": 24},
  "viewport": {"width": 1536, "height": 864},
  "timezone": "Europe/Berlin",
  "locale": "en-GB",
  "hardware_concurrency": 8,
  "device_memory": 8,
  "webgl_vendor": "Google Inc. (NVIDIA)",
  "webgl_renderer": "ANGLE (NVIDIA, ...)",
  "canvas_noise_seed": "a8f2c...",
  "audio_noise_seed": "0e3b9...",
  "fonts_present": ["Arial", "Calibri", ...],
  "webrtc_policy": "disable_non_proxied_udp"
}
```

Generation rules:

- Sample from realistic joint distributions (Chrome on Windows is way more common than Chrome on Linux — match the empirical mix).
- Never randomly assign `timezone` — it must match the proxy's region (phase 2 picks the proxy; fingerprint should co-follow).
- Once generated, **never regenerate for a living account**. Rotating fingerprint mid-life is a louder signal than a slightly outdated one.

### Stealth patches — applied per context

New `app/browser/stealth/` package with one module per signal family:

```
stealth/
  __init__.py           # apply_all(context, fp)
  user_agent.py         # sets navigator.userAgent, navigator.appVersion, etc.
  webdriver.py          # hides `navigator.webdriver`
  chrome_runtime.py     # patches missing `window.chrome` subtree
  plugins.py            # synthesizes a realistic plugin/mimeType list
  permissions.py        # repairs the permissions.query → notification leak
  canvas.py             # canvas noise injection keyed by fp.canvas_noise_seed
  audio.py              # audiocontext noise keyed by fp.audio_noise_seed
  webgl.py              # WebGL vendor/renderer spoofing
  webrtc.py             # disables non-proxied UDP per policy
  timezone.py           # Date.now + Intl overrides
  fonts.py              # font enumeration guard
```

Each module exports `apply(context, fp)`. The `__init__.apply_all` just calls them in order. Adding a new patch = one file, one test.

### Behavior — per-account profile

New table `account_behavior_profiles`:

```sql
CREATE TABLE account_behavior_profiles (
  account_id           BIGINT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
  typing_wpm           INT,        -- base typing speed
  typing_variance      REAL,       -- stddev as fraction of mean
  click_hesitation_ms  INT,        -- base pause before clicking something new
  inter_page_pause_ms  INT,        -- base pause between navigations
  scroll_probability   REAL,       -- chance to scroll on a new page before acting
  mouse_path_style     TEXT,       -- 'bezier' | 'spline' | 'linear_jitter'
  error_rate           REAL        -- chance to mistype and correct
);
```

Generated once per account (`fingerprint_generated_at` is a good proxy for when). Values drawn from plausible ranges (typing 30–80 wpm, hesitation 200–1200 ms, etc.) — not uniform, but from measured human distributions where available.

The existing `humanize.py` helpers become thin wrappers that read the profile:

```python
async def sleep_action(ctx: BehaviorContext) -> None:
    # Was: sleep(uniform(0.8, 2.2))
    # Now: sleep drawn from log-normal centered on
    #      ctx.profile.inter_page_pause_ms
    ...
```

### Session arc — temporal shaping

Wrap the outer reconcile loop in a "session arc" multiplier that changes behavior over a session's lifetime:

- First 20 minutes: multiplier 0.7 (sharper, faster).
- 20–60 min: 1.0 baseline.
- 60–120 min: 1.15 (getting tired, slower).
- > 120 min: 1.3 + random micro-breaks (insert 30–180 s pauses between page loads).

Keep the arc per-session so a fresh login resets it. Existing break-minutes config already gives us sessions.

### Humanization audit items (not exhaustive)

- Mouse paths: replace teleport-to-click with a bezier path generated from current cursor → target over 200–600 ms. Playwright supports `page.mouse.move(x, y, steps=N)`.
- Typing: replace `page.fill(selector, text)` with a character-by-character loop with per-character delay from the behavior profile. Occasional mistypes + corrections (controlled by `error_rate`).
- Pre-click hover: sometimes hover briefly over a neighbor element before clicking the target. Real users eye-track imperfectly.
- Scroll: occasionally scroll the page before interacting with fold-below elements. Use it as a "reading" signal.

## Integration points

- `app/browser/fingerprint.py` → refactor into `app/browser/fingerprint/generator.py` + `app/browser/fingerprint/persistence.py`.
- `app/browser/stealth.py` → expand into `app/browser/stealth/` package sketched above.
- `app/browser/humanize.py` → gains `BehaviorContext` injected from `BrowserSession`.
- `app/browser/session.py:BrowserSession.__aenter__` — loads fingerprint + behavior profile, applies stealth patches before first navigation.
- `app/models/account.py` — add `fingerprint_json`, `fingerprint_generated_at`.
- New `app/models/behavior_profile.py` — the behavior profile model.
- Migration: backfill existing two accounts with freshly generated fingerprints + profiles.

## Tradeoffs / open questions

- **Stealth patches vs. a third-party library.** `playwright-stealth`, `rebrowser`, `camoufox` ship similar patches. Rolling our own is more work but we can tune to Travian specifically and don't inherit CVE churn from upstream. A hybrid — fork `playwright-stealth`, maintain our own patch set on top — is probably right.
- **How much variance is too much?** Identical fingerprints across accounts = detectable; wildly different fingerprints in the same ASN = also detectable. Co-varying fingerprint with proxy geo fixes the worst case.
- **Mobile fingerprints.** Ignore entirely for phase 1 — Travian on desktop is the common case and mobile UA introduces a lot of extra surface to patch.
- **Validation.** There's no ground-truth "this fingerprint is stealthy" check. CreepJS, fingerprint.com's bot-detection demo, and similar public checkers are useful sanity tests but aren't the thing Travian runs. Manual observation of ban rates on real accounts is the only honest signal.

## Effort

~1–2 weeks, dominated by the stealth package expansion and per-character humanization retrofit. Should land before the first paying customer (phase 2 timeframe).
