# travian-bot

Multi-account Travian **Legends** (T4.6) automation with Playwright + FastAPI + PostgreSQL, driven by a per-account reconciler loop and a React dashboard.

Pipelines (each runs as a controller under the reconciler):
- **Farming** — farm lists, raid-report ingestion, jittered dispatch, oasis-animal avoidance.
- **Building** — prioritised upgrade queue per village, prereq-aware solver against [app/data/buildings.yaml](app/data/buildings.yaml).
- **Training** — troop-goal queue per village, barracks/stable/workshop dispatch.
- **Hero** — adventures (incl. video bonus), attribute spend, health/XP sync.
- **Reports** — parses battle/raid reports into structured rows.
- **Maintenance** — villages sync, troops sync, map scan, world-SQL refresh.

## Quickstart

```bash
# 1. deps
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

# 2. bootstrap (.env + SECRET_KEY + playwright chromium)
python -m scripts.init_config   # or: travian-init

# 3. postgres
docker compose up -d

# 4. API (starts all ACTIVE accounts automatically)
./run_api                       # or: uvicorn app.main:app --reload

# 5. dashboard (separate shell)
./run_gui                       # or: cd dashboard && yarn dev
```

## Adding an account

```bash
curl -X POST http://127.0.0.1:8000/accounts -H 'content-type: application/json' -d '{
  "label": "my-main",
  "server_url": "https://ts1.x1.international.travian.com",
  "username": "myname",
  "password": "...",
  "active_hours": "07:30-23:45"
}'
```

- `server_url` is parsed and the game version is auto-detected (Legends only today).
- The password is encrypted at rest with Fernet using `SECRET_KEY`.
- Each account runs in its own Playwright context (own cookies, fingerprint, mouse state) — several servers/accounts run in parallel in one process.

## Architecture

```
AccountManager
 └── AccountWorker (one per ACTIVE account)
      └── ControllerLoop → reconciler ticks controllers in priority order
           ├── FarmingController     → app/services/farming.py
           ├── BuildingController    → app/services/building.py
           ├── TrainingController    → app/services/training.py
           ├── HeroController        → app/services/hero.py
           ├── ReportsController     → app/services/reports.py
           ├── VillagesController    → app/services/villages.py
           ├── TroopsController      → app/services/troops.py
           ├── MapScanController     → app/services/map_scan.py
           ├── WorldSQLController    → app/services/world_sql.py
           └── MaintenanceController → misc housekeeping
```

Each controller declares a cadence and a `reconcile()` method. The loop picks whichever controller is due, runs one tick, and yields — so a slow farming pass can't starve hero adventures.

## Anti-detection, briefly

Fingerprint layer ([app/browser/fingerprint.py](app/browser/fingerprint.py), [app/browser/stealth.py](app/browser/stealth.py)):
- deterministic per-account UA + viewport + screen + timezone + locale
- patched `navigator.webdriver`, `window.chrome`, plugins, WebGL vendor, permissions, canvas noise
- persistent context per account (no login spike on restart)

Behavioral layer ([app/browser/humanize.py](app/browser/humanize.py)):
- log-normal action delays with a 5 % long-tail for "user got distracted"
- curved (cubic Bezier) mouse movement with variable-speed steps
- typo-and-backspace typing cadence
- active-hour window with sleep outside it; capped sessions (`MAX_SESSION_MINUTES`) and real breaks between sessions
- occasional tangent clicks (hero / reports / map) to avoid "functional-paths only" pattern

Proxies are deferred; add them on `BrowserSession` once needed. The commercial-rollout doc ([docs/commercial-rollout.md](docs/commercial-rollout.md)) covers the plan.

## REST surface

Routers mounted in [app/main.py](app/main.py):

| prefix          | purpose                                           |
|-----------------|---------------------------------------------------|
| `/accounts`     | CRUD + start/stop workers                         |
| `/villages`     | village list + per-village detail                 |
| `/farmlists`    | farm lists and their slots                        |
| `/build`        | building orders and queue inspection              |
| `/troop_goals`  | per-village training goals                        |
| `/hero`         | hero state, items, adventures                     |
| `/reports`      | parsed battle/raid reports                        |
| `/map_tiles`    | scanned tile cache                                |

Schemas live in [app/api/schemas.py](app/api/schemas.py).

## Project layout

```
app/
├── api/            # FastAPI routers
├── browser/        # Playwright, stealth, humanize, login, video_bonus
│   └── pages/      # dorf, build, rally, reports, hero, sidebar, training
├── core/           # config, logging, crypto, account_manager, reconciler
├── data/           # buildings.yaml (editable)
├── db/             # async SQLAlchemy engine + session
├── models/         # tables (account, village, farmlist, build, hero, …)
└── services/
    ├── controllers/  # one per pipeline; scheduled by the reconciler
    └── *.py          # pipeline implementations + data loaders
dashboard/          # React + Vite UI
docs/               # rollout / licensing / path-decision notes
scripts/            # init_config (more CLIs go here)
samples/            # captured DOM snapshots used when refining selectors
tests/
```

## Extending selectors

Every page object has a nested `Selectors` class. When the game's DOM shifts or your specific skin differs, edit those constants — not methods. The [samples/](samples/) tree holds captured HTML from a live Legends account; refine selectors against it rather than guessing.

## Building pipeline

- Catalog in [app/data/buildings.yaml](app/data/buildings.yaml) — editable.
- `POST /build/orders` adds a step (`building_key`, `target_level`, optional `slot`, `priority`).
- The solver ticks per account, reads live queue length from `dorf1`, and builds the highest-priority unblocked order whose prereqs are met.
- Blocked orders stay in the queue with a human-readable `blocked_reason`.

## Training pipeline

- `POST /troop_goals` sets a target count for a given troop in a village.
- TrainingController checks barracks/stable/workshop availability, resources, and dispatches the largest batch that fits.
- Goals persist across restarts; the controller stops dispatching once the running total (home + in-transit) meets the goal.

## Health / safety switches

- `PAUSE_BOT` empty file at repo root: worker loops see it and pause.  *(hook — not auto-enforced yet; easy to wire into AccountWorker if you want it.)*
- Mark an account `status=paused` via DB to stop it without a restart; `/accounts/{id}/stop` stops its worker immediately.

## License & disclaimer

Automation of Travian accounts violates the game's ToS. Use at your own risk. This repo is for research and personal experimentation.
