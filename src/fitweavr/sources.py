from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from fitweavr.data import FitnessData
from fitweavr.enums import FormatType, SourceType
from fitweavr.io import load_activity_file


@dataclass(frozen=True)
class GoogleFitTakeoutSource:
    activities_dir: Path
    source_type: SourceType = SourceType.GOOGLE_FIT

    def iter_files(self, *, format_type: FormatType | str = FormatType.TCX) -> Iterator[Path]:
        resolved_format = FormatType.coerce(format_type)
        yield from sorted(self.activities_dir.glob(f"*.{resolved_format.value}"))

    def load_file(self, path: Path | str) -> FitnessData:
        return load_activity_file(path, source=self.source_type)

    def load_all(
        self,
        *,
        format_type: FormatType | str = FormatType.TCX,
        limit: Optional[int] = None,
    ) -> Iterator[FitnessData]:
        for index, path in enumerate(self.iter_files(format_type=format_type)):
            if limit is not None and index >= limit:
                break
            yield self.load_file(path)
