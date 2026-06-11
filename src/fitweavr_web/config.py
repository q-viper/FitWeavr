from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppSettings:
    app_name: str = "FitWeavr"
    max_sessions: int = 20
    session_timeout_seconds: int = 1800
    max_upload_mb: int = 100
    work_dir: Path = Path(".fitweavr_sessions")
    host: str = "127.0.0.1"
    port: int = 8080
    storage_secret: str = "fitweavr-local-storage-secret"

    @classmethod
    def from_env(cls, env_file: Optional[Path | str] = None) -> "AppSettings":
        load_dotenv(env_file)
        return cls(
            max_sessions=_int_env("FITWEAVR_MAX_SESSIONS", 20),
            session_timeout_seconds=_int_env("FITWEAVR_SESSION_TIMEOUT_SECONDS", 1800),
            max_upload_mb=_int_env("FITWEAVR_MAX_UPLOAD_MB", 100),
            work_dir=Path(os.getenv("FITWEAVR_WORK_DIR", ".fitweavr_sessions")),
            host=os.getenv("FITWEAVR_HOST", "127.0.0.1"),
            port=_int_env("FITWEAVR_PORT", 8080),
            storage_secret=os.getenv("FITWEAVR_STORAGE_SECRET", "fitweavr-local-storage-secret"),
        )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value
