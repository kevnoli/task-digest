from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from task_digest.config import Settings
from task_digest.logging import configure_logging
from task_digest.models import DigestKind
from task_digest.scheduler import check_health, serve
from task_digest.service import execute_once
from task_digest.settings_store import (
    DigestSettingKey,
    SettingsStore,
    SettingsStoreError,
    apply_runtime_settings,
    effective_digest_settings,
)

app = typer.Typer(
    name="task-digest",
    help="Send scheduled Vikunja task digests to Telegram.",
    no_args_is_help=True,
)
settings_app = typer.Typer(help="View or change persisted non-secret digest settings.")
app.add_typer(settings_app, name="settings")


def _load_settings(*, force_dry_run: bool = False) -> Settings:
    try:
        settings = Settings(dry_run=True) if force_dry_run else Settings()
        settings = apply_runtime_settings(settings, SettingsStore(settings.settings_database_path))
    except (ValidationError, SettingsStoreError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=2) from exc
    configure_logging(settings.log_level, settings.secret_values())
    return settings


@settings_app.command("show")
def settings_show() -> None:
    """Show effective digest preferences and whether SQLite overrides them."""

    settings = _load_settings()
    store = SettingsStore(settings.settings_database_path)
    persisted = store.get_all()
    typer.echo(f"Database: {store.path}")
    for key, value in effective_digest_settings(settings).items():
        source = "sqlite" if key.value in persisted else "environment/default"
        typer.echo(f"{key.value}={value} ({source})")


@settings_app.command("set")
def settings_set(
    key: Annotated[DigestSettingKey, typer.Argument(help="Setting to persist.")],
    value: Annotated[str, typer.Argument(help="New value.")],
) -> None:
    """Validate and persist one digest preference in SQLite."""

    settings = _load_settings()
    store = SettingsStore(settings.settings_database_path)
    try:
        normalized, _candidate = store.set(key, value, settings)
    except SettingsStoreError as exc:
        typer.echo(f"Settings error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Saved {key.value}={normalized} in {store.path}")
    typer.echo("Restart the service to apply scheduler changes.")


@settings_app.command("reset")
def settings_reset(
    key: Annotated[DigestSettingKey, typer.Argument(help="Setting override to remove.")],
) -> None:
    """Remove one SQLite override and return to the environment/default value."""

    settings = _load_settings()
    store = SettingsStore(settings.settings_database_path)
    removed = store.reset(key)
    status = "Removed" if removed else "No override existed for"
    typer.echo(f"{status} {key.value}")
    typer.echo("Restart the service to apply scheduler changes.")


@app.command("run")
def run_command(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the digest and do not call Telegram."),
    ] = False,
    kind: Annotated[
        DigestKind,
        typer.Option("--kind", help="Digest behavior to use for this one-shot run."),
    ] = DigestKind.MORNING,
) -> None:
    """Fetch, render, and send one digest, then exit."""

    settings = _load_settings(force_dry_run=dry_run)
    try:
        asyncio.run(execute_once(settings, kind))
    except Exception as exc:
        typer.echo(f"Digest failed: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("serve")
def serve_command() -> None:
    """Run the timezone-aware morning/evening scheduler continuously."""

    settings = _load_settings()
    try:
        asyncio.run(serve(settings))
    except Exception as exc:
        typer.echo(f"Service failed: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("healthcheck")
def healthcheck_command() -> None:
    """Validate the local service heartbeat without calling external APIs."""

    path = Path(os.environ.get("HEARTBEAT_PATH", "/tmp/task-digest/heartbeat"))
    raw_max_age = os.environ.get("HEARTBEAT_MAX_AGE_SECONDS", "90")
    try:
        max_age = int(raw_max_age)
    except ValueError as exc:
        typer.echo("Unhealthy: HEARTBEAT_MAX_AGE_SECONDS must be an integer", err=True)
        raise typer.Exit(code=1) from exc
    healthy, message = check_health(path, max_age)
    typer.echo(message, err=not healthy)
    if not healthy:
        raise typer.Exit(code=1)
