from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from fitweavr.data import FitData, FitnessData, TcxData
from fitweavr.enums import DistanceSource, FormatType


class DataConverter(Protocol):
    source_format: FormatType
    target_format: FormatType

    def convert(
        self,
        data: FitnessData,
        *,
        distance_source: DistanceSource,
    ) -> FitnessData:
        ...


class TcxToFitConverter:
    source_format = FormatType.TCX
    target_format = FormatType.FIT

    def convert(
        self,
        data: FitnessData,
        *,
        distance_source: DistanceSource,
    ) -> FitData:
        if not isinstance(data, TcxData):
            raise TypeError(f"TcxToFitConverter expected TcxData, got {type(data).__name__}")
        return FitData(
            source=data.source,
            activities=data.activities,
            distance_source=distance_source,
        )


class ConverterRegistry:
    def __init__(self) -> None:
        self._converters: dict[tuple[FormatType, FormatType], DataConverter] = {}

    @classmethod
    def default(cls) -> "ConverterRegistry":
        registry = cls()
        registry.register(TcxToFitConverter())
        return registry

    def register(self, converter: DataConverter) -> None:
        self._converters[(converter.source_format, converter.target_format)] = converter

    def convert(
        self,
        data: FitnessData,
        *,
        to: FormatType,
        distance_source: DistanceSource = DistanceSource.AUTO,
    ) -> FitnessData:
        if data.format == to:
            if isinstance(data, FitData):
                return replace(data, distance_source=distance_source)
            return data
        key = (data.format, to)
        if key not in self._converters:
            raise NotImplementedError(f"Conversion from {data.format.name} to {to.name} is not implemented.")
        return self._converters[key].convert(data, distance_source=distance_source)
