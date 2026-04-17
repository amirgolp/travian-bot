"""Hero sync — scrape attributes, inventory, adventures; upsert HeroStats.

Extra behaviour when `account.watch_video_bonuses` is on: we click the
"Watch video" bonus buttons before sending an adventure.

Dispatch rules (gated by HeroController being enabled):
  - hero must be at home (we have a home_village_id)
  - health ≥ MIN_HEALTH_PCT (default 40 %); low-HP adventures often time out
    or kill the hero
  - at least one adventure visible with an enabled Explore button
  - max 1 dispatch per sync; the next tick (~15 min) takes the next one
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.pages.hero import HeroPage
from app.browser.session import BrowserSession
from app.core.logging import get_logger
from app.models.account import Account
from app.models.hero import HeroStats
from app.services.hero_item_data import item_info
from app.services.strategy import expected_reward, get_hero_policy

log = get_logger("service.hero")

MIN_HEALTH_PCT = 40


async def sync_hero(
    db: AsyncSession, session: BrowserSession, account_id: int
) -> HeroStats:
    hp = HeroPage(session.page)

    # --- attributes ---
    try:
        await hp.open_attributes()
        attrs = await hp.read_attributes()
        home_did = await hp.read_home_village_did()
        attrs_ok = True
    except Exception as e:  # noqa: BLE001
        log.exception("hero.sync.attributes_failed", err=str(e))
        attrs, home_did, attrs_ok = None, None, False

    # --- inventory ---
    try:
        inv = await hp.read_inventory()
        inv_ok = True
    except Exception as e:  # noqa: BLE001
        log.exception("hero.sync.inventory_failed", err=str(e))
        inv, inv_ok = None, False

    # --- adventures (read first, then maybe dispatch) ---
    adv = None
    adv_ok = False
    dispatched = False
    try:
        await hp.open_adventures()
        adv = await hp.read_adventures()
        adv_ok = True
        dispatched = await _maybe_send_adventure(db, account_id, hp, attrs, home_did, adv.count)
    except Exception as e:  # noqa: BLE001
        log.exception("hero.sync.adventures_failed", err=str(e))

    # Upsert row so partial scrapes still surface.
    existing = (
        await db.execute(select(HeroStats).where(HeroStats.account_id == account_id))
    ).scalar_one_or_none()
    if existing is None:
        existing = HeroStats(account_id=account_id)
        db.add(existing)

    if attrs_ok and attrs is not None:
        existing.health_pct = attrs.health_pct
        existing.experience = attrs.experience
        existing.speed_fph = attrs.speed_fph
        existing.production_per_hour = attrs.production_per_hour
        existing.fighting_strength = attrs.fighting_strength
        existing.off_bonus_pct = attrs.off_bonus_pct
        existing.def_bonus_pct = attrs.def_bonus_pct
        existing.attribute_points = attrs.attribute_points
    if home_did is not None:
        existing.home_village_id = home_did
        existing.status = "home" if not dispatched else "moving"

    if inv_ok and inv is not None:
        def _equip_row(e):
            info = item_info(e.slot, e.item_type_id) if not e.empty else {}
            return {
                "slot": e.slot,
                "empty": e.empty,
                "rarity": e.rarity,
                "level": e.level,
                "item_type_id": e.item_type_id,
                "instance_id": e.instance_id,
                "name": info.get("name"),
                "description": info.get("description"),
            }
        existing.equipment_json = json.dumps(
            [_equip_row(e) for e in inv.equipment], sort_keys=True,
        )
        existing.bag_count = inv.bag_count
        # Full bag contents (consumables) — each entry has its own
        # item_type_id + count so the dashboard can render them as a row.
        existing.bag_items_json = json.dumps(
            [
                {
                    "item_type_id": b.item_type_id,
                    "count": b.count,
                    "name": item_info("bag", b.item_type_id).get("name"),
                    "description": item_info("bag", b.item_type_id).get("description"),
                }
                for b in inv.bag_items
            ],
            sort_keys=True,
        )

    if adv_ok and adv is not None:
        # If we just dispatched, the in-memory count was pre-click; the
        # post-click page will show one fewer. Persist the pre-click value
        # — the next sync will observe the decrement directly.
        existing.adventures_available = adv.count

    if dispatched:
        # The strategy's reward sequence is indexed by *completed adventure
        # number* (a Travian Legends mechanic). Bump the counter BEFORE
        # looking up the expected reward so the log line matches the
        # adventure we just sent.
        existing.adventures_completed = (existing.adventures_completed or 0) + 1
        policy = await get_hero_policy(db, account_id)
        if policy is not None:
            reward = expected_reward(policy, existing.adventures_completed)
            if reward is not None:
                log.info(
                    "hero.adventure.expected_reward",
                    account_id=account_id,
                    adventure_number=existing.adventures_completed,
                    prefer=reward.prefer,
                    post=reward.post,
                )

    existing.observed_at = datetime.now(tz=UTC)
    await db.flush()

    log.info(
        "hero.sync.done",
        account_id=account_id,
        attrs=attrs_ok, inv=inv_ok, adv=adv_ok, dispatched=dispatched,
        health=existing.health_pct, xp=existing.experience,
        bag=existing.bag_count, adventures=existing.adventures_available,
        home_did=existing.home_village_id, points=existing.attribute_points,
    )
    return existing


async def _maybe_send_adventure(
    db: AsyncSession,
    account_id: int,
    hp: HeroPage,
    attrs,
    home_did: int | None,
    available: int,
) -> bool:
    """Decide + execute an adventure dispatch. Returns True if we clicked Explore."""
    reasons: list[str] = []
    if available <= 0:
        reasons.append("no adventures available")
    if home_did is None:
        reasons.append("hero not at home (or status unknown)")
    hp_pct = attrs.health_pct if attrs is not None else None
    if hp_pct is None:
        reasons.append("health unknown")
    elif hp_pct < MIN_HEALTH_PCT:
        reasons.append(f"health {hp_pct}% < {MIN_HEALTH_PCT}% threshold")
    if reasons:
        log.info("hero.adventure.skip", reasons=reasons)
        return False

    # Watch the video bonuses first. If any are ready and playable but don't
    # actually complete, ABORT the dispatch — firing the adventure without
    # them wastes the bonus (-25 % time + harder-for-more-XP). The next tick
    # retries, so nothing is lost by skipping.
    acc = await db.get(Account, account_id)
    if acc is not None and getattr(acc, "watch_video_bonuses", True):
        try:
            ready, watched = await hp.watch_adventure_bonuses()
            log.info("hero.adventure.video_bonuses", ready=ready, watched=watched)
            if ready > watched:
                log.info(
                    "hero.adventure.skip",
                    reasons=[f"video bonuses unfinished ({watched}/{ready})"],
                    hint="will retry next hero tick",
                )
                return False
        except Exception as e:  # noqa: BLE001
            log.exception("hero.adventure.video_bonus_error", err=str(e))
            # Fail safe: if we don't know whether videos watched, skip.
            return False

    # Re-navigate to adventures after the video flow (watch helper may have
    # mutated the DOM by opening/closing modals).
    await hp.open_adventures()
    ok = await hp.send_first_adventure()
    if ok:
        log.info("hero.adventure.sent", account_id=account_id, hp_pct=hp_pct)
    else:
        log.info("hero.adventure.send_declined", reason="no Explore button visible")
    return ok
