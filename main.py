from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import typer
from dotenv import load_dotenv

from src.application import build_application

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

app = typer.Typer(help="Provider ranking agent CLI")


def _run(coro: Coroutine[Any, Any, None]) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


@app.command()
def migrate() -> None:
    """Apply database migrations."""
    _run(build_application().migrate())


@app.command("sync-once")
def sync_once() -> None:
    """Fetch the provider once, embed and persist."""
    _run(build_application().run_once())


@app.command()
def run() -> None:
    """Apply migrations and start the periodic sync worker."""

    _run(build_application().run_forever())


if __name__ == "__main__":
    app()
