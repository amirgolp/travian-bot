# travian-bot

Multi-account Travian **Legends** (T4.6) automation with Playwright + FastAPI + PostgreSQL.

Two pipelines:
- **Farming** — create farm lists, maintain them from raid reports, dispatch on jittered intervals.
- **Building** — prioritised upgrade queue per village, prereq-aware solver (reads `app/data/buildings.yaml`), modifiable via REST.

## Quickstart

```bash
# 1. deps
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

# 2. bootstrap config (.env + SECRET_KEY + playwright chromium)
python -m scripts.init_config

# 3. postgres
docker compose up -d

# 4. run the API (starts all ACTIVE accounts automatically)
uvicorn app.main:app --reload
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
- Every active account runs in its own Playwright context (own cookies, fingerprint, mouse state) — so several servers/accounts can run in parallel in one process.

## Anti-detection, briefly

Fingerprint layer (`app/browser/fingerprint.py`, `stealth.py`):
- deterministic per-account UA + viewport + screen + timezone + locale
- patched `navigator.webdriver`, `window.chrome`, plugins, WebGL vendor, permissions, canvas noise
- persistent context per account (no login spike on restart)

Behavioral layer (`app/browser/humanize.py`):
- log-normal action delays with a 5 % long-tail for "user got distracted"
- curved (cubic Bezier) mouse movement with variable-speed steps
- typo-and-backspace typing cadence
- active-hour window with sleep outside it; capped sessions (`MAX_SESSION_MINUTES`) and real breaks between sessions
- occasional tangent clicks (hero / reports / map) to avoid "functional-paths only" pattern

Proxies are deferred; add them on `BrowserSession` once needed.

## Project layout

```
app/
├── api/            # FastAPI routers
├── browser/        # Playwright, stealth, page objects
│   └── pages/      # dorf, build, rally, reports
├── core/           # config, logging, crypto, account manager
├── data/           # buildings.yaml (editable)
├── db/             # async SQLAlchemy engine + session
├── models/         # tables
└── services/       # farming + building pipelines
scripts/            # init_config, future CLIs
```

## Extending selectors

Every page object has a nested `Selectors` class. When the game's DOM shifts or your specific skin differs, edit those constants — not methods. The selectors shipped here are educated guesses based on public Legends templates; **you're expected to refine them from real DOM samples**.

## Building pipeline

- Catalog in [app/data/buildings.yaml](app/data/buildings.yaml) — editable.
- POST `/build/orders` adds a step (`building_key`, `target_level`, optional `slot`, `priority`).
- The solver ticks per account, reads live queue length from `dorf1`, and builds the highest-priority unblocked order whose prereqs are met.
- Blocked orders stay in the queue with a human-readable `blocked_reason`.

## Health / safety switches

- `PAUSE_BOT` empty file at repo root: worker loops see it and pause.  *(hook — not auto-enforced yet; easy to wire into AccountWorker if you want it.)*
- Mark an account `status=paused` via DB to stop it without a restart; `/accounts/{id}/stop` stops its worker immediately.

## License & disclaimer

Automation of Travian accounts violates the game's ToS. Use at your own risk. This repo is for research and personal experimentation.
