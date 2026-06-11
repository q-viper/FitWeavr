from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fitweavr.enums import DistanceSource, FormatType, SourceType
from fitweavr.models import ConversionReport, SaveResult
from fitweavr.sources import GoogleFitTakeoutSource


@dataclass(frozen=True)
class ConversionConfig:
    input_dir: Path
    output_dir: Path
    distance_source: DistanceSource = DistanceSource.AUTO
    overwrite: bool = False
    validate: bool = True
    limit: Optional[int] = None
    source_type: SourceType = SourceType.GOOGLE_FIT


class BatchConverter:
    def __init__(self, config: ConversionConfig) -> None:
        self.config = config

    def convert_all(self) -> SaveResult:
        source = GoogleFitTakeoutSource(
            activities_dir=self.config.input_dir,
            source_type=self.config.source_type,
        )
        files = list(source.iter_files(format_type=FormatType.TCX))
        if self.config.limit is not None:
            files = files[: self.config.limit]

        reports: list[ConversionReport] = []
        for index, path in enumerate(files, start=1):
            print(f"[{index}/{len(files)}] {path.name}")
            try:
                data = source.load_file(path)
                fit_data = data.convert(
                    to=FormatType.FIT,
                    distance_source=self.config.distance_source,
                )
                result = fit_data.save(
                    self.config.output_dir,
                    overwrite=self.config.overwrite,
                    validate=self.config.validate,
                    write_manifest=False,
                )
                reports.extend(result.reports)
            except Exception as exc:
                reports.append(
                    ConversionReport(
                        source_path=path,
                        output_path=None,
                        converted=False,
                        skipped=False,
                        error=str(exc),
                    )
                )
        self._write_manifest(reports)
        return SaveResult(destination=self.config.output_dir, reports=tuple(reports))

    def _write_manifest(self, reports: list[ConversionReport]) -> None:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.config.output_dir / "conversion_manifest.csv"
        with manifest_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "source",
                    "output",
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
