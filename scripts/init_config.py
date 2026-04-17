"""Bootstrap script.

What it does (interactive-ish, but non-destructive):
  1. Creates `.env` from `.env.example` if missing.
  2. Generates and writes a SECRET_KEY (Fernet) if missing.
  3. Validates DATABASE_URL by trying a connection (once the DB is up).
  4. Ensures the browser profiles dir exists.
  5. Runs `playwright install chromium` so the browser binary is in place.

Run:
    python -m scripts.init_config
    python -m scripts.init_config --check   (validate only, no writes)
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import click
from cryptography.fernet import Fernet
from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"

console = Console()


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _write_env(path: Path, values: dict[str, str]) -> None:
    lines: list[str] = []
    # Preserve section comments from .env.example for readability.
    if ENV_EXAMPLE_PATH.exists():
        for line in ENV_EXAMPLE_PATH.read_text().splitlines():
            s = line.strip()
            if s.startswith("#") or not s:
                lines.append(line)
                continue
            k = s.split("=", 1)[0].strip()
            lines.append(f"{k}={values.get(k, '')}")
    else:
        for k, v in values.items():
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n")


def _ensure_env_file() -> dict[str, str]:
    if not ENV_PATH.exists():
        if not ENV_EXAMPLE_PATH.exists():
            console.print("[red].env.example missing — cannot scaffold .env[/red]")
            sys.exit(1)
        ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text())
        console.print(f"[green]created[/green] {ENV_PATH.relative_to(ROOT)} from .env.example")
    return _read_env(ENV_PATH)


def _ensure_secret_key(values: dict[str, str]) -> bool:
    if values.get("SECRET_KEY"):
        return False
    values["SECRET_KEY"] = Fernet.generate_key().decode()
    console.print("[green]generated[/green] SECRET_KEY")
    return True


def _ensure_profiles_dir(values: dict[str, str]) -> None:
    p = Path(values.get("BROWSER_PROFILES_DIR", "./.profiles"))
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    console.print(f"[green]ok[/green] profiles dir at {p}")


async def _check_db(url: str) -> bool:
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        eng = create_async_engine(url)
        async with eng.connect() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        await eng.dispose()
        return True
    except Exception as e:
        console.print(f"[yellow]db check failed:[/yellow] {e}")
        return False


def _install_playwright() -> None:
    console.print("installing Chromium for Playwright (first run only)...")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        console.print("[green]ok[/green] playwright chromium installed")
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]playwright install failed:[/yellow] {e}")


@click.command()
@click.option("--check", is_flag=True, help="Validate config without writing.")
@click.option("--skip-playwright", is_flag=True, help="Skip `playwright install chromium`.")
def main(check: bool, skip_playwright: bool) -> None:
    values = _ensure_env_file() if not check else _read_env(ENV_PATH)
    if not check:
        changed = _ensure_secret_key(values)
        _ensure_profiles_dir(values)
        if changed:
            _write_env(ENV_PATH, values)

    db_url = values.get("DATABASE_URL", "")
    if db_url:
        ok = asyncio.run(_check_db(db_url))
        console.print(("[green]ok[/green]" if ok else "[yellow]skip[/yellow]") + f" DB: {db_url}")

    if not check and not skip_playwright:
        _install_playwright()

    console.print("\n[bold]next steps[/bold]:")
    console.print("  1. docker compose up -d      # start postgres")
    console.print("  2. uvicorn app.main:app      # run the API")
    console.print("  3. POST /accounts            # add your first account")


if __name__ == "__main__":
    main()
