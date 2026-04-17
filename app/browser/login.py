"""Login flow for Travian Legends.

Detection notes:
- We check for an already-authenticated session before typing anything.
  Logging in on every start is a strong behavioral signal.
- The login form is entered via `human_type` so keystroke timing looks natural.
- Selectors live here (not scattered) so the user can update them as the UI changes.
"""
from __future__ import annotations

from playwright.async_api import Page

from app.browser.humanize import human_click, human_type, read_page, sleep_action
from app.core.crypto import decrypt
from app.core.logging import get_logger
from app.models.account import Account

log = get_logger("browser.login")


# --- Selectors. User will refine these from real DOM samples. ---
class LoginSelectors:
    # Login page
    USER_INPUT = 'input[name="name"]'
    PASS_INPUT = 'input[name="password"]'
    SUBMIT_BTN = 'button[type="submit"]'
    # Post-login marker — present on dorf1 but not on login page
    LOGGED_IN_MARKER = "#resourcesContainer, .villageList, #sidebarBoxVillagelist"
    # Error flash
    LOGIN_ERROR = ".error, #loginError"


async def is_logged_in(page: Page) -> bool:
    try:
        await page.wait_for_selector(LoginSelectors.LOGGED_IN_MARKER, timeout=2500)
        return True
    except Exception:
        return False


async def login(page: Page, account: Account) -> None:
    """Navigate to the server and sign in if not already authenticated."""
    log.debug("login.goto", account=account.label, url=account.server_url)
    await page.goto(account.server_url, wait_until="domcontentloaded")
    await sleep_action()

    if await is_logged_in(page):
        log.info("login.reused_session", account=account.label)
        return

    log.info("login.submitting", account=account.label, username=account.username)
    await read_page(page, words=40)

    user_input = page.locator(LoginSelectors.USER_INPUT).first
    pass_input = page.locator(LoginSelectors.PASS_INPUT).first
    try:
        await user_input.wait_for(state="visible", timeout=15000)
    except Exception as e:
        log.error("login.form_missing", account=account.label, err=str(e))
        raise

    await human_type(page, user_input, account.username)
    await sleep_action(scale=0.6)
    await human_type(page, pass_input, decrypt(account.password_encrypted))
    await sleep_action(scale=0.6)

    submit = page.locator(LoginSelectors.SUBMIT_BTN).first
    await human_click(page, submit)
    log.debug("login.submitted", account=account.label)

    try:
        await page.wait_for_selector(LoginSelectors.LOGGED_IN_MARKER, timeout=20000)
    except Exception as e:
        err = page.locator(LoginSelectors.LOGIN_ERROR).first
        msg = None
        try:
            if await err.count():
                msg = (await err.text_content() or "").strip()
        except Exception:
            pass
        log.error("login.failed", account=account.label, reason=msg or str(e))
        raise RuntimeError(f"Login failed for {account.label}: {msg or e}") from e

    log.info("login.ok", account=account.label)
