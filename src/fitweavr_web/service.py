from __future__ import annotations

import csv
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from loguru import logger

from fitweavr.models import ConversionReport
from fitweavr.routes import ConversionProfile
from fitweavr import load_activity_file
from fitweavr_web.config import AppSettings
from fitweavr_web.session import FileRecord, SessionState, SessionStore


@dataclass(frozen=True)
class ConversionSummary:
    converted_count: int
    failed_count: int
    archive_path: Path | None
    reports: tuple[ConversionReport, ...]


class WebConversionService:
    def __init__(self, settings: AppSettings, store: SessionStore) -> None:
        self.settings = settings
        self.store = store

    def list_files(self, state: SessionState) -> list[FileRecord]:
        return self.store.list_files(state.id)

    def prepare_upload(
        self,
        state: SessionState,
        filename: str,
        size: int | None,
        *,
        profile: ConversionProfile,
    ) -> Path:
        self._validate_upload(filename, size, profile=profile)
        safe_name = self._safe_filename(filename)
        return self._unique_path(state.input_dir / safe_name)

    def register_upload(
        self,
        state: SessionState,
        filename: str,
        input_path: Path,
        *,
        profile: ConversionProfile,
    ) -> FileRecord:
        if not input_path.exists():
            raise ValueError(f"Uploaded temp file was not written: {input_path}")
        self._validate_upload(filename, input_path.stat().st_size, profile=profile)
        record = FileRecord(
            id=uuid.uuid4().hex,
            session_id=state.id,
            original_name=filename,
            input_path=input_path,
            status="uploaded",
            message=f"Saved to temp storage for {profile.label}",
        )
        self.store.add_file(record)
        logger.info("Stored upload {}", input_path)
        return record

    def save_upload(
        self,
        state: SessionState,
        filename: str,
        content: bytes,
        *,
        profile: ConversionProfile,
    ) -> FileRecord:
        target = self.prepare_upload(state, filename, len(content), profile=profile)
        target.write_bytes(content)
        return self.register_upload(state, filename, target, profile=profile)

    def clear(self, state: SessionState) -> None:
        shutil.rmtree(state.input_dir, ignore_errors=True)
        shutil.rmtree(state.output_dir, ignore_errors=True)
        state.input_dir.mkdir(parents=True, exist_ok=True)
        state.output_dir.mkdir(parents=True, exist_ok=True)
        self.store.clear_files(state.id)
        self.store.update_archive(state.id, None)

    def reset_outputs(self, state: SessionState) -> None:
        shutil.rmtree(state.output_dir, ignore_errors=True)
        state.output_dir.mkdir(parents=True, exist_ok=True)
        self.store.reset_file_outputs(state.id)
        self.store.update_archive(state.id, None)

    def mark_converting(self, file_id: str) -> None:
        self.store.update_file(file_id, status="converting", message="Converting...")

    def convert_record(
        self,
        state: SessionState,
        file_id: str,
        *,
        profile: ConversionProfile,
    ) -> FileRecord:
        record = self.store.get_file(file_id)
        if record is None:
            raise ValueError("Uploaded file record was not found.")

        try:
            logger.info("Converting {} with {}", record.input_path, profile.route_id)
            data = load_activity_file(
                record.input_path,
                source=profile.source_type,
                format_type=profile.input_format,
            )
            result = data.convert(
                to=profile.target_format,
                distance_source=profile.distance_source,
            ).save(
                state.output_dir,
                overwrite=True,
                validate=True,
                write_manifest=False,
            )
            failed = [report for report in result.reports if report.error]
            warnings = [
                warning
                for report in result.reports
                for warning in report.warnings
            ]
            if failed:
                message = " | ".join(report.error or "Conversion failed" for report in failed)
                self.store.update_file(file_id, status="failed", message=message)
            else:
                message = (
                    f"{profile.label}: converted {result.converted_count} "
                    f"{profile.target_format.name} file(s)."
                )
                if warnings:
                    message = f"{message} Warnings: {' | '.join(warnings)}"
                self.store.update_file(
                    file_id,
                    status="converted",
                    message=message,
                    output_paths=list(result.files),
                    records=sum(report.records for report in result.reports),
                    laps=sum(report.laps for report in result.reports),
                )
        except Exception as exc:
            logger.exception("Conversion failed for {}", record.input_path)
            self.store.update_file(file_id, status="failed", message=str(exc))

        updated = self.store.get_file(file_id)
        if updated is None:
            raise ValueError("Converted file record disappeared.")
        return updated

    def finalize_archive(self, state: SessionState, *, profile: ConversionProfile) -> Path:
        files = self.store.list_files(state.id)
        reports = self._reports_from_records(files)
        manifest = self._write_manifest(state.output_dir, reports, profile=profile)
        output_files = [
            output_path
            for record in files
            for output_path in record.output_paths
            if output_path.exists()
        ]
        archive_path = self._create_archive(state.output_dir, output_files, manifest)
        self.store.update_archive(state.id, archive_path)
        self.cleanup_inputs(state)
        return archive_path

    def summarize(self, state: SessionState) -> ConversionSummary:
        files = self.store.list_files(state.id)
        archive_path = state.archive_path if state.archive_path and state.archive_path.exists() else None
        return ConversionSummary(
            converted_count=sum(len(record.output_paths) for record in files if record.status == "converted"),
            failed_count=sum(1 for record in files if record.status == "failed"),
            archive_path=archive_path,
            reports=tuple(self._reports_from_records(files)),
        )

    def _reports_from_records(self, files: Iterable[FileRecord]) -> list[ConversionReport]:
        reports: list[ConversionReport] = []
        for record in files:
            reports.append(
                ConversionReport(
                    source_path=record.input_path,
                    output_path=record.output_paths[0] if record.output_paths else None,
                    converted=record.status == "converted",
                    skipped=False,
                    records=record.records,
                    laps=record.laps,
                    validated=record.status == "converted",
                    warnings=() if record.status != "converted" else (record.message,),
                    error=record.message if record.status == "failed" else None,
                )
            )
        return reports

    def _write_manifest(
        self,
        output_dir: Path,
        reports: Iterable[ConversionReport],
        *,
        profile: ConversionProfile,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "conversion_manifest.csv"
        with manifest_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "source",
                    "output",
                    "source_type",
                    "destination_type",
                    "input_format",
                    "output_format",
                    "converted",
                    "skipped",
                    "validated",
                    "records",
                    "laps",
                    "distance_sources",
                    "warnings",
                    "error",
                ],
            )
            writer.writeheader()
            for report in reports:
                writer.writerow(
                    {
                        "source": str(report.source_path or ""),
                        "output": str(report.output_path or ""),
                        "source_type": profile.source_type.value,
                        "destination_type": profile.destination_type.value,
                        "input_format": profile.input_format.value,
                        "output_format": profile.target_format.value,
                        "converted": report.converted,
                        "skipped": report.skipped,
                        "validated": report.validated,
                        "records": report.records,
                        "laps": report.laps,
                        "distance_sources": ";".join(report.distance_sources),
                        "warnings": " | ".join(report.warnings),
                        "error": report.error or "",
                    }
                )
        return manifest_path

    def _create_archive(self, output_dir: Path, output_files: list[Path], manifest: Path) -> Path:
        archive_path = output_dir / "fitweavr-files.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in output_files:
                archive.write(path, arcname=path.name)
            archive.write(manifest, arcname=manifest.name)
        return archive_path

    def cleanup_inputs(self, state: SessionState) -> None:
        shutil.rmtree(state.input_dir, ignore_errors=True)
        state.input_dir.mkdir(parents=True, exist_ok=True)

    def _validate_upload(
        self,
        filename: str,
        size: int | None,
        *,
        profile: ConversionProfile,
    ) -> None:
        if size is not None and size > self.settings.max_upload_bytes:
            raise ValueError(f"{filename} is larger than {self.settings.max_upload_mb} MB.")
        suffix = Path(filename).suffix.lower()
        if suffix not in profile.accepted_suffixes:
            accepted = ", ".join(profile.accepted_suffixes)
            raise ValueError(f"{profile.label} accepts {accepted} files.")

    def _safe_filename(self, filename: str) -> str:
        name = Path(filename).name
        safe = re.sub(r"[^A-Za-z0-9._+ -]+", "_", name).strip()
        return safe or "activity.tcx"

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        for index in range(2, 10_000):
            candidate = path.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not allocate a unique filename for {path.name}.")
