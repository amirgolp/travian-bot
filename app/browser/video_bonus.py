"""Watch "video bonus" ads before critical actions.

Legends surfaces a 25 %-time-bonus video next to every Upgrade button, and two
adventure-tuning videos on the adventures page. A human who's optimising plays
them every time; so does this bot (when `account.watch_video_bonuses` is on).

Flow per button:
  1. Locate the `<button class="videoFeatureButton">` (or `.watchReady .video...`
     in the adventures dialog).
  2. Humanised click.
  3. A modal opens with an HTML5 video + a centered "Play" overlay — click it.
  4. Wait up to ~130 s for the source button to transition to disabled
     (`disabled` attr, or an added `.watched` / `.disabled` class). That's
     Travian's signal that the bonus was granted.
  5. If the modal is still present, click its close button to tidy up.

Success signal: the ad content is a cross-origin `<iframe>` (oadts.com) that
autoplays, so we can't observe the video element directly. What we CAN see
is `#dialogOverlay.dialogVisible` containing `.dialog.videoFeature` — when
Travian awards the bonus it tears that dialog down. We treat the dialog
going away as "bonus granted", with a button-state fallback in case some
skins flip `disabled` / `.watched` instead.

Timing (taken from the user's notes on the live UI):
  - Ad server can take up to ~10 s after Play before content actually starts.
  - Some videos run ~100 s total.
  - We poll every 2 s with a 130 s ceiling, and log timings.

This helper is deliberately forgiving: if any step fails we log a warning and
return cleanly so the caller's primary action (upgrade / dispatch adventure)
still runs.
"""
from __future__ import annotations

import asyncio
import time

from playwright.async_api import Locator, Page

from app.browser.humanize import human_click, sleep_action
from app.core.logging import get_logger

log = get_logger("browser.video_bonus")


# --- Selectors (refine from a real "modal open" sample when available) ---
# Visible source button sitting next to Upgrade / Explore.
SRC_BUTTON = "button.videoFeatureButton:not([disabled])"
# The adventure-page boxes wrap two specific videos (duration-reduction,
# increased-difficulty). Each has `watchReady` when available.
ADVENTURE_BOX_READY = ".videoFeatureBonusBox.watchReady"
# Opened modal: generic Travian dialog, plus a few common variants the game
# has used for the video ad. The first match wins.
MODAL_ROOT_CANDIDATES = [
    ".dialog.videoFeatureDialog",
    ".videoFeatureDialog",
    ".dialog:has(video)",
    ".dialog:has(iframe)",
    "#videoFeatureDialog",
]
MODAL_CLOSE_BUTTON = (
    ".videoFeatureDialog .dialogCancelButton, "
    ".videoFeatureDialog .close, "
    ".dialog .dialogCancelButton, "
    ".dialog button.close"
)
# Live video dialog — its disappearance from the DOM is the bonus-granted
# signal. Scoped to `.dialogVisible` so a cached/hidden wrapper doesn't
# masquerade as "still open".
VIDEO_MODAL = ".dialogOverlay.dialogVisible .dialog.videoFeature"
# Google IMA SDK play button — rendered inline on the host page (no iframe).
# Clicking it is the user-gesture that starts the ad; without it the modal
# hangs on the fallback "Force a reload" placeholder forever.
IMA_PLAY_BUTTON = ".atg-gima-big-play-button-outer, .atg-gima-big-play-button"

MAX_WAIT_S = 130.0       # hard cap for one watch
POLL_INTERVAL_S = 2.0
PLAY_GRACE_S = 2.5       # small human "I'm finding the play button" pause


async def _try_click_ima_in_frames(page: Page, timeout_s: float) -> bool:
    """Find the Google IMA play button inside any ad iframe and click it.

    The build-page video bonus (same as the adventures one) serves its ad in
    a cross-origin `#videoArea` iframe from media.oadts.com. The play
    trigger `.atg-gima-big-play-button-outer` sits inside that iframe, not
    on the host page. Escalates through normal click → force click → JS
    dispatchEvent since IMA overlays often fail actionability checks even
    when they're clickable.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for frame in page.frames:
            try:
                loc = frame.locator(IMA_PLAY_BUTTON).first
                if await loc.count() == 0:
                    continue
                try:
                    await loc.click(timeout=1500)
                    log.info(
                        "video_bonus.ad_play.clicked",
                        mode="ima_click", frame_url=frame.url[:80],
                    )
                    return True
                except Exception:
                    pass
                try:
                    await loc.click(timeout=1500, force=True)
                    log.info(
                        "video_bonus.ad_play.clicked",
                        mode="ima_force", frame_url=frame.url[:80],
                    )
                    return True
                except Exception:
                    pass
                try:
                    await loc.evaluate(
                        "el => el.dispatchEvent(new MouseEvent('click', "
                        "{bubbles: true, cancelable: true, view: window}))"
                    )
                    log.info(
                        "video_bonus.ad_play.clicked",
                        mode="ima_js", frame_url=frame.url[:80],
                    )
                    return True
                except Exception:
                    pass
            except Exception:
                continue
        await asyncio.sleep(0.5)
    log.debug("video_bonus.ad_play.no_trigger_found", frames=len(page.frames))
    return False


async def _wait_bonus_granted(page: Page, button: Locator, timeout_s: float) -> bool:
    """Poll for either signal that the bonus was granted:

    * the video modal (`.dialogOverlay.dialogVisible .dialog.videoFeature`)
      has been torn down by Travian — the primary signal; OR
    * the source button became disabled / got `.watched` / was removed —
      a fallback for skins that flip the button state instead (not observed
      in current samples but cheap to keep).
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if await page.locator(VIDEO_MODAL).count() == 0:
                return True
        except Exception:
            pass
        try:
            if await button.count() == 0:
                return True
            if await button.is_disabled(timeout=500):
                return True
            cls = (await button.get_attribute("class")) or ""
            if any(marker in cls for marker in ("watched", "done", "videoFeatureWatched")):
                return True
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL_S)
    return False


async def _close_modal_if_open(page: Page) -> None:
    try:
        closer = page.locator(MODAL_CLOSE_BUTTON).first
        if await closer.count() > 0 and await closer.is_visible(timeout=500):
            await human_click(page, closer)
            log.debug("video_bonus.modal.closed")
    except Exception as e:  # noqa: BLE001
        log.debug("video_bonus.modal.close_failed", err=str(e))


async def _watch_one(page: Page, button: Locator) -> bool:
    """Click one video button and wait until the bonus is granted. Returns success."""
    start = time.monotonic()
    try:
        label_hint = (await button.get_attribute("class") or "")[:60]
    except Exception:
        label_hint = ""
    log.info("video_bonus.watch.start", hint=label_hint)

    try:
        await human_click(page, button)
    except Exception as e:  # noqa: BLE001
        log.warning("video_bonus.click_src_failed", err=str(e))
        return False

    # Small grace period for the modal/player to mount.
    await asyncio.sleep(PLAY_GRACE_S)

    # Click the IMA play button inside the ad iframe. Required — without a
    # user gesture the IMA player sits on a "Force a reload" placeholder and
    # the modal never closes. The iframe is cross-origin
    # (`media.oadts.com/...`) but Playwright can reach into it via
    # `page.frames`. Escalates through click strategies; IMA overlays often
    # fail the default actionability checks.
    await _try_click_ima_in_frames(page, timeout_s=30.0)

    # Wait for the bonus-granted signal (modal torn down, or button state flip).
    granted = await _wait_bonus_granted(
        page, button, MAX_WAIT_S - (time.monotonic() - start)
    )

    # Close the modal if the game didn't auto-close it.
    await _close_modal_if_open(page)
    await sleep_action()

    elapsed = time.monotonic() - start
    if granted:
        log.info("video_bonus.watch.ok", seconds=round(elapsed, 1))
    else:
        log.warning("video_bonus.watch.timeout", seconds=round(elapsed, 1))
    return granted


async def watch_all_available(page: Page, *, scope: Locator | None = None, limit: int = 3) -> int:
    """Watch every currently-available video bonus in `scope` (or the whole page).

    Returns the count actually watched. Safe to call when there are no videos —
    exits near-instantly. Never raises; failures are logged.
    """
    container = scope if scope is not None else page.locator("body")
    # Re-query each iteration because earlier clicks may have disabled a button
    # (which would drop it from our `:not([disabled])` match).
    watched = 0
    for _ in range(limit):
        buttons = container.locator(SRC_BUTTON)
        n = await buttons.count()
        if n == 0:
            break
        first = buttons.first
        try:
            if not await first.is_visible(timeout=500):
                break
        except Exception:
            break
        ok = await _watch_one(page, first)
        if not ok:
            # Don't loop forever on an unresponsive modal.
            break
        watched += 1
    if watched:
        log.info("video_bonus.summary", watched=watched)
    return watched
