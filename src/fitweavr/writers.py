from __future__ import annotations

from typing import Protocol

from fitweavr.data import FitnessData
from fitweavr.enums import FormatType
from fitweavr.models import SaveResult


class DataWriter(Protocol):
    def save(self, data: FitnessData, *args, **kwargs) -> SaveResult:
        ...


class WriterRegistry:
    def __init__(self) -> None:
        self._writers: dict[FormatType, DataWriter] = {}

    @classmethod
    def default(cls) -> "WriterRegistry":
        from fitweavr.formats.fit import FitDataWriter

        registry = cls()
        registry.register(FormatType.FIT, FitDataWriter())
        return registry

    def register(self, format_type: FormatType, writer: DataWriter) -> None:
        self._writers[format_type] = writer

    def get(self, format_type: FormatType) -> DataWriter:
        if format_type not in self._writers:
            raise NotImplementedError(f"Saving {format_type.name} data is not implemented.")
        return self._writers[format_type]
