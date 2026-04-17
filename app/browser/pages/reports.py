"""Reports page — parses incoming raid outcomes to feed farmlist maintenance."""
from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Page

from app.browser.humanize import read_page
from app.core.logging import get_logger

log = get_logger("page.reports")


@dataclass
class ReportSummary:
    travian_id: str
    outcome: str        # "win" | "loss" | "empty" | "defense" | "scout" | "other"
    target_x: int | None
    target_y: int | None
    bounty_total: int | None
    raw_html: str
    # Path-relative href from the list row's subject link — fed back to
    # `open_detail` to read bounty / casualties from the full report page.
    detail_href: str | None = None


class ReportsPage:
    """Selectors calibrated from samples/legend/reports-1 (Legends 2026-04-16).

    The list page renders `table.row_table_data` with one `<tr>` per report.
    Outcome is encoded by the `iReport1..4` class on an `<img>` inside the row:
      iReport1 = green (win), iReport2 = yellow (won but losses),
      iReport3 = red (loss / total defeat), iReport4 = defense
    The report detail URL lives on the subject link inside the row (the one
    whose href starts with `?id=<numeric>%7C...&s=1`). `a.reportInfoIcon`
    jumps to the rally-point send screen pre-filled with that target.
    """
    class Selectors:
        ROW = "table.row_table_data tr"
        # Subject link: href matches the hash-suffix pattern `?id=<num>%7C...&s=1`
        SUBJECT_LINK = 'a[href^="?id="][href*="&s=1"]'
        INFO_ICON = "a.reportInfoIcon"
        OUTCOME_GREEN = ".iReport1"
        OUTCOME_YELLOW = ".iReport2"
        OUTCOME_RED = ".iReport3"
        OUTCOME_DEFENSE = ".iReport4"

    def __init__(self, page: Page):
        self.page = page

    def _origin(self) -> str:
        return "/".join(self.page.url.split("/", 3)[:3])

    async def open(self) -> None:
        url = f"{self._origin()}/report.php"
        log.debug("reports.open", url=url)
        await self.page.goto(url, wait_until="domcontentloaded")
        await read_page(self.page, words=60)

    async def list_recent(self, limit: int = 30) -> list[ReportSummary]:
        s = self.Selectors
        rows = self.page.locator(s.ROW)
        n = min(await rows.count(), limit)
        out: list[ReportSummary] = []
        for i in range(n):
            row = rows.nth(i)
            link = row.locator(s.SUBJECT_LINK).first
            if await link.count() == 0:
                continue
            href = await link.get_attribute("href") or ""
            # href shape: `?id=<num>%7C<hash>&s=1`  ->  keep the numeric id
            rid_raw = href.split("id=", 1)[-1].split("&", 1)[0]
            rid = rid_raw.split("%7C", 1)[0]

            if await row.locator(s.OUTCOME_GREEN).count():
                outcome = "win"
            elif await row.locator(s.OUTCOME_YELLOW).count():
                outcome = "loss"             # won but with losses
            elif await row.locator(s.OUTCOME_RED).count():
                outcome = "defense"          # total defeat
            elif await row.locator(s.OUTCOME_DEFENSE).count():
                outcome = "defense"
            else:
                outcome = "other"
            out.append(ReportSummary(
                travian_id=rid, outcome=outcome,
                target_x=None, target_y=None, bounty_total=None,
                raw_html=await row.inner_html(),
                detail_href=href,
            ))
        log.debug("reports.list.read", rows=n, parsed=len(out))
        return out

    async def read_detail(self, detail_href: str) -> str:
        """Navigate to one report's detail page and return its inner HTML.

        `detail_href` is the relative `?id=<num>%7C<hash>&s=1` we captured
        from the list row. Resolving it against the current origin avoids
        hard-coding `/report.php` (Legends may re-route without notice).
        """
        origin = self._origin()
        # The href is already relative to `/report.php`; build the full URL.
        if detail_href.startswith("http"):
            url = detail_href
        elif detail_href.startswith("?"):
            url = f"{origin}/report.php{detail_href}"
        else:
            url = f"{origin}/{detail_href.lstrip('/')}"
        log.debug("reports.detail.open", url=url)
        await self.page.goto(url, wait_until="domcontentloaded")
        await read_page(self.page, words=40)
        # Scope to the report content pane if present; some skins dump the
        # entire body content here, which is fine for a coarse-grained parse.
        container = self.page.locator("#reportContent, #content").first
        if await container.count():
            return await container.inner_html()
        return await self.page.content()
