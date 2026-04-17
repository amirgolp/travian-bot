# 4. Selector regression tests from `samples/`

## Problem

The scraper depends on dozens of CSS selectors calibrated against specific Travian DOM shapes — see e.g. the `Selectors` class in [app/browser/pages/dorf.py](../../app/browser/pages/dorf.py#L47). When Travian ships a DOM change (they do, irregularly), the scraper breaks silently — fields come back zero, controllers succeed-with-empty-results, `troops_observed_at` gets stamped on an empty scrape, and the bot cheerfully does nothing useful for hours.

The `samples/legend/` directory already contains captured HTML for each scraped page (dorf1, dorf2, rally-point tabs, marketplace, hero adventures, etc.). We have the raw material for a fast regression suite — it just isn't wired up.

## Design

A pytest suite that replays each saved HTML page through its corresponding parser and asserts the extracted shape.

### Fixture layout

Keep the current `samples/legend/<page_name>/` directory convention. Each sample sits next to an expected-shape JSON:

```
samples/legend/dorf1/
  page.html                 # the raw page as captured
  expected.json             # parsed shape we assert against
  metadata.json             # capture date, Travian version, any quirks
```

`expected.json` is hand-written when the sample is first captured, then locked in. Example for dorf1:

```json
{
  "resources": {"wood": 4520, "clay": 3890, "iron": 4102, "crop": 2201,
                "warehouse_cap": 10000, "granary_cap": 10000},
  "build_queue": [
    {"name": "Woodcutter", "level": 5, "finishes_in_seconds": 612}
  ],
  "field_levels": [
    {"slot": 1, "gid": 1, "level": 5},
    {"slot": 2, "gid": 1, "level": 4},
    ...18 entries total
  ]
}
```

### Playwright-free parser harness

Playwright wants a live page, but our parsers already work through locators. Two options:

1. **Lightweight harness** — stand up a real Chromium page via Playwright but load it from a `file://` URL pointing at the saved HTML. Real DOM, real selectors, no network. Slower (~300 ms per test) but zero parser changes.
2. **Extract pure parsers** — split each page class into (a) a Playwright caller that gets text/HTML and (b) pure Python functions that take strings/lxml trees. Faster tests, but significant refactor.

Start with (1). Reconsider (2) if the suite runtime becomes painful.

### Test shape

```python
# tests/parsers/test_dorf1.py
@pytest.mark.asyncio
async def test_dorf1_resources(page, sample_dorf1):
    await page.goto(sample_dorf1.file_url)
    dorf1 = Dorf1Page(page)
    res = await dorf1.read_resources()
    assert res.wood == sample_dorf1.expected["resources"]["wood"]
    assert res.warehouse_cap == sample_dorf1.expected["resources"]["warehouse_cap"]
    ...
```

One test file per page class, one test per reader method. Conftest supplies the Playwright page fixture (session-scoped browser, function-scoped page).

### Capture helper

A small CLI to refresh samples against a live account (for the operator, not CI):

```
python scripts/capture_sample.py --page dorf1 --out samples/legend/dorf1
```

Saves the HTML, runs the current parser, emits an `expected.json` as a starting point. Operator reviews and commits.

## Integration points

- New `tests/parsers/` directory.
- New `tests/parsers/conftest.py` — Playwright browser session fixture, `sample_*` loaders.
- `pyproject.toml` — dev dep `pytest-playwright` if not already there.
- CI: run on every PR that touches `app/browser/pages/` or `samples/legend/`.
- New `scripts/capture_sample.py` — sample refresh helper.

## Tradeoffs / open questions

- **Sample drift.** Travian changes DOM; our saved samples eventually don't reflect production. When a test fails, two possibilities: parser regression, or sample staleness. A "sample age" field in `metadata.json` + a nightly "is this page still rendering the same" canary job catches the second case before it becomes a mystery.
- **Dynamic state.** Some pages depend on server state (rally point with active movements). Save multiple samples per page — one "empty" state, one "rich" state — and test both.
- **Don't assert timestamps.** Use structural/shape assertions, not exact timer text, because saved HTML freezes a clock.
- **Keep samples anonymized.** The capture helper should strip PII from saved HTML (village coords, player names) via a configurable pass. We publish nothing, but that keeps the repo safe to open-source later if we change our minds.

## Effort

~1 day to bootstrap the harness + tests for 2–3 pages. Then incrementally add ~1 hour of tests per page class as they're touched. Full coverage of the current 10–15 page readers in ~1 week of calendar time, but it doesn't need to be done in one pass.
