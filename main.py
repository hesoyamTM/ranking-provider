from __future__ import annotations

import asyncio
import logging

import typer
from dotenv import load_dotenv

from src.application import build_application

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = typer.Typer(help="Provider ranking agent CLI")


@app.command()
def migrate() -> None:
    """Apply database migrations."""
    asyncio.run(build_application().migrate())


@app.command("sync-once")
def sync_once() -> None:
    """Fetch the provider once, embed and persist."""
    asyncio.run(build_application().run_once())


@app.command()
def run() -> None:
    """Apply migrations and start the periodic sync worker."""

    async def _run() -> None:
        application = build_application()
        await application.migrate()
        await application.run_forever()

    asyncio.run(_run())


if __name__ == "__main__":
    app()
