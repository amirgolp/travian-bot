"""Page Objects for Travian Legends.

Each page object wraps one screen of the game. Selectors are declared as class-
level constants so they can be updated as the UI shifts without chasing them
through the codebase. Methods return typed data (dataclasses / primitives),
never Playwright Locators, so the service layer doesn't touch the DOM.

When adding a new page:
1. Add a class here with a clear constructor: `def __init__(self, page, village): ...`
2. Put every selector in a nested `Selectors` class at the top.
3. Method names describe *game concepts* (e.g. `current_resources`, `queue_upgrade`),
   not DOM operations (`click_button`).
"""
