from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fitweavr.enums import DistanceSource, FormatType
from fitweavr.models import ActivityData, SaveResult, SourceMetadata


@dataclass
class FitnessData:
    source: SourceMetadata
    activities: list[ActivityData]
    format: FormatType = field(init=False)

    def convert(
        self,
        to: FormatType | str,
        *,
        distance_source: DistanceSource | str = DistanceSource.AUTO,
    ) -> "FitnessData":
        from fitweavr.converters import ConverterRegistry

        return ConverterRegistry.default().convert(
            self,
            to=FormatType.coerce(to),
            distance_source=DistanceSource.coerce(distance_source),
        )

    def save(
        self,
        destination: Path | str,
        *,
        overwrite: bool = False,
        validate: bool = True,
        write_manifest: bool = True,
    ) -> SaveResult:
        from fitweavr.writers import WriterRegistry

        writer = WriterRegistry.default().get(self.format)
        return writer.save(
            self,
            Path(destination),
            overwrite=overwrite,
            validate=validate,
            write_manifest=write_manifest,
        )


@dataclass
class TcxData(FitnessData):
    format: FormatType = field(init=False, default=FormatType.TCX)

    @classmethod
    def from_path(
        cls,
        path: Path | str,
        *,
        source: SourceMetadata,
    ) -> "TcxData":
        from fitweavr.formats.tcx import TcxParser

        return TcxParser().parse_file(Path(path), source=source)


@dataclass
class FitData(FitnessData):
    distance_source: DistanceSource = DistanceSource.AUTO
    format: FormatType = field(init=False, default=FormatType.FIT)

    def save(
        self,
        destination: Path | str,
        *,
        overwrite: bool = False,
        validate: bool = True,
        write_manifest: bool = True,
    ) -> SaveResult:
        from fitweavr.formats.fit import FitDataWriter

        return FitDataWriter(distance_source=self.distance_source).save(
            self,
            Path(destination),
            overwrite=overwrite,
            validate=validate,
            write_manifest=write_manifest,
        )
