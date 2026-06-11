from __future__ import annotations

from pathlib import Path
from typing import Optional

from fitweavr.data import FitnessData, TcxData
from fitweavr.enums import FormatType, SourceType
from fitweavr.models import SourceMetadata


def load_activity_file(
    path: Path | str,
    *,
    source: SourceType | str = SourceType.UNKNOWN,
    format_type: Optional[FormatType | str] = None,
) -> FitnessData:
    activity_path = Path(path)
    resolved_format = FormatType.coerce(format_type) if format_type else _format_from_path(activity_path)
    source_metadata = SourceMetadata(
        source_type=SourceType.coerce(source),
        path=activity_path,
        original_format=resolved_format,
        name=activity_path.stem,
    )

    if resolved_format == FormatType.TCX:
        return TcxData.from_path(activity_path, source=source_metadata)
    raise NotImplementedError(f"Loading {resolved_format.name} files is not implemented yet.")


def _format_from_path(path: Path) -> FormatType:
    suffix = path.suffix.lower().lstrip(".")
    if not suffix:
        raise ValueError(f"Cannot infer format from path without extension: {path}")
    return FormatType.coerce(suffix)
