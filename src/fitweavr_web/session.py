from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fitweavr_web.config import AppSettings


class TooManySessionsError(RuntimeError):
    pass


class SessionExpiredError(RuntimeError):
    pass


@dataclass
class FileRecord:
    id: str
    session_id: str
    original_name: str
    input_path: Path
    status: str = "uploaded"
    message: str = "Waiting"
    output_paths: list[Path] = field(default_factory=list)
    records: int = 0
    laps: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_finished(self) -> bool:
        return self.status in {"converted", "failed"}


@dataclass
class SessionState:
    id: str
    root_dir: Path
    ip_address: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    archive_path: Optional[Path] = None

    @property
    def input_dir(self) -> Path:
        return self.root_dir / "input"

    @property
    def output_dir(self) -> Path:
        return self.root_dir / "output"

    def remaining_seconds(self, timeout_seconds: int) -> int:
        elapsed = datetime.now(timezone.utc) - self.last_seen_at
        return max(0, int(timeout_seconds - elapsed.total_seconds()))


class SessionStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def create_session(self, state: SessionState) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                (id, root_dir, ip_address, created_at, last_seen_at, archive_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    state.id,
                    str(state.root_dir),
                    state.ip_address,
                    _dt_to_text(state.created_at),
                    _dt_to_text(state.last_seen_at),
                    str(state.archive_path) if state.archive_path else None,
                ),
            )

    def get_session(self, session_id: str) -> Optional[SessionState]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return _state_from_row(row) if row else None

    def touch_session(self, session_id: str, *, ip_address: Optional[str] = None) -> None:
        now = _dt_to_text(datetime.now(timezone.utc))
        with self._lock, self._connect() as conn:
            if ip_address is None:
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                    (now, session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ?, ip_address = ? WHERE id = ?",
                    (now, ip_address, session_id),
                )

    def update_archive(self, session_id: str, archive_path: Optional[Path]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET archive_path = ? WHERE id = ?",
                (str(archive_path) if archive_path else None, session_id),
            )

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM files WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def count_active_sessions(self, cutoff: datetime) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM sessions WHERE last_seen_at >= ?",
                (_dt_to_text(cutoff),),
            ).fetchone()
        return int(row["count"])

    def expired_sessions(self, cutoff: datetime) -> list[SessionState]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE last_seen_at < ?",
                (_dt_to_text(cutoff),),
            ).fetchall()
        return [_state_from_row(row) for row in rows]

    def add_file(self, record: FileRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files
                (id, session_id, original_name, input_path, status, message, output_paths,
                 records, laps, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _record_values(record),
            )

    def list_files(self, session_id: str) -> list[FileRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM files WHERE session_id = ? ORDER BY created_at, original_name",
                (session_id,),
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def get_file(self, file_id: str) -> Optional[FileRecord]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return _record_from_row(row) if row else None

    def update_file(
        self,
        file_id: str,
        *,
        status: Optional[str] = None,
        message: Optional[str] = None,
        output_paths: Optional[list[Path]] = None,
        records: Optional[int] = None,
        laps: Optional[int] = None,
    ) -> None:
        record = self.get_file(file_id)
        if record is None:
            return
        if status is not None:
            record.status = status
        if message is not None:
            record.message = message
        if output_paths is not None:
            record.output_paths = output_paths
        if records is not None:
            record.records = records
        if laps is not None:
            record.laps = laps
        record.updated_at = datetime.now(timezone.utc)

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE files
                SET status = ?, message = ?, output_paths = ?, records = ?, laps = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    record.status,
                    record.message,
                    _paths_to_text(record.output_paths),
                    record.records,
                    record.laps,
                    _dt_to_text(record.updated_at),
                    record.id,
                ),
            )

    def clear_files(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM files WHERE session_id = ?", (session_id,))

    def reset_file_outputs(self, session_id: str) -> None:
        now = _dt_to_text(datetime.now(timezone.utc))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE files
                SET status = 'uploaded', message = 'Waiting', output_paths = '[]',
                    records = 0, laps = 0, updated_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    root_dir TEXT NOT NULL,
                    ip_address TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    archive_path TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    output_paths TEXT NOT NULL,
                    records INTEGER NOT NULL,
                    laps INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn


class SessionManager:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.settings.work_dir.mkdir(parents=True, exist_ok=True)
        self.store = SessionStore(self.settings.work_dir / "fitweavr.sqlite3")

    def create(self, *, ip_address: str) -> SessionState:
        self.cleanup_expired()
        if self.active_count >= self.settings.max_sessions:
            raise TooManySessionsError("The service is busy. Please try again shortly.")
        session_id = uuid.uuid4().hex
        state = SessionState(
            id=session_id,
            root_dir=self.settings.work_dir / session_id,
            ip_address=ip_address,
        )
        state.input_dir.mkdir(parents=True, exist_ok=True)
        state.output_dir.mkdir(parents=True, exist_ok=True)
        self.store.create_session(state)
        return state

    def require(
        self,
        session_id: str,
        *,
        ip_address: Optional[str] = None,
        touch: bool = True,
    ) -> SessionState:
        self.cleanup_expired()
        state = self.store.get_session(session_id)
        if state is None:
            raise SessionExpiredError("Your session expired. Refresh the page to start again.")
        if self._is_expired(state):
            self.close(session_id)
            raise SessionExpiredError("Your session expired. Refresh the page to start again.")
        if touch:
            self.store.touch_session(session_id, ip_address=ip_address)
            state = self.store.get_session(session_id) or state
        return state

    def peek(self, session_id: str) -> Optional[SessionState]:
        self.cleanup_expired()
        state = self.store.get_session(session_id)
        if state is None or self._is_expired(state):
            return None
        return state

    def cleanup_expired(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.settings.session_timeout_seconds)
        for state in self.store.expired_sessions(cutoff):
            self.close(state.id)

    def close(self, session_id: str) -> None:
        state = self.store.get_session(session_id)
        if state is not None:
            shutil.rmtree(state.root_dir, ignore_errors=True)
        self.store.delete_session(session_id)

    @property
    def active_count(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.settings.session_timeout_seconds)
        return self.store.count_active_sessions(cutoff)

    def _is_expired(self, state: SessionState) -> bool:
        elapsed = datetime.now(timezone.utc) - state.last_seen_at
        return elapsed.total_seconds() > self.settings.session_timeout_seconds


def _record_values(record: FileRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.session_id,
        record.original_name,
        str(record.input_path),
        record.status,
        record.message,
        _paths_to_text(record.output_paths),
        record.records,
        record.laps,
        _dt_to_text(record.created_at),
        _dt_to_text(record.updated_at),
    )


def _state_from_row(row: sqlite3.Row) -> SessionState:
    return SessionState(
        id=row["id"],
        root_dir=Path(row["root_dir"]),
        ip_address=row["ip_address"],
        created_at=_text_to_dt(row["created_at"]),
        last_seen_at=_text_to_dt(row["last_seen_at"]),
        archive_path=Path(row["archive_path"]) if row["archive_path"] else None,
    )


def _record_from_row(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        id=row["id"],
        session_id=row["session_id"],
        original_name=row["original_name"],
        input_path=Path(row["input_path"]),
        status=row["status"],
        message=row["message"],
        output_paths=[Path(path) for path in json.loads(row["output_paths"])],
        records=int(row["records"]),
        laps=int(row["laps"]),
        created_at=_text_to_dt(row["created_at"]),
        updated_at=_text_to_dt(row["updated_at"]),
    )


def _paths_to_text(paths: list[Path]) -> str:
    return json.dumps([str(path) for path in paths])


def _dt_to_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _text_to_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
