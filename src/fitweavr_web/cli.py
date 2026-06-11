from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from loguru import logger

from fitweavr_web.app import run
from fitweavr_web.config import AppSettings


cli = typer.Typer(help="FitWeavr web app for fitness data conversion.")


@cli.command()
def serve(
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a .env file."),
    host: Optional[str] = typer.Option(None, "--host", help="Host override."),
    port: Optional[int] = typer.Option(None, "--port", help="Port override."),
    reload: bool = typer.Option(False, "--reload", help="Enable NiceGUI reload mode."),
) -> None:
    settings = AppSettings.from_env(env_file)
    if host is not None or port is not None:
        settings = AppSettings(
            app_name=settings.app_name,
            max_sessions=settings.max_sessions,
            session_timeout_seconds=settings.session_timeout_seconds,
            max_upload_mb=settings.max_upload_mb,
            work_dir=settings.work_dir,
            host=host or settings.host,
            port=port or settings.port,
            storage_secret=settings.storage_secret,
        )
    logger.info("Starting FitWeavr on {}:{}", settings.host, settings.port)
    run(settings, reload=reload)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
