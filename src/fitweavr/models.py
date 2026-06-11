from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fitweavr.enums import FormatType, SourceType


@dataclass(frozen=True)
class SourceMetadata:
    source_type: SourceType = SourceType.UNKNOWN
    path: Optional[Path] = None
    original_format: Optional[FormatType] = None
    name: Optional[str] = None


@dataclass
class TrackPoint:
    timestamp: datetime
    distance_m: Optional[float] = None
    heart_rate_bpm: Optional[int] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    altitude_m: Optional[float] = None
    fit_distance_m: Optional[float] = None
    fit_speed_mps: Optional[float] = None

    @property
    def has_position(self) -> bool:
        return self.latitude_deg is not None and self.longitude_deg is not None

    def clone(self) -> "TrackPoint":
        return TrackPoint(**self.__dict__)

    def merge(self, other: "TrackPoint") -> "TrackPoint":
        return TrackPoint(
            timestamp=self.timestamp,
            distance_m=_max_optional(self.distance_m, other.distance_m),
            heart_rate_bpm=other.heart_rate_bpm or self.heart_rate_bpm,
            latitude_deg=other.latitude_deg if other.latitude_deg is not None else self.latitude_deg,
            longitude_deg=other.longitude_deg
            if other.longitude_deg is not None
            else self.longitude_deg,
            altitude_m=other.altitude_m if other.altitude_m is not None else self.altitude_m,
        )


@dataclass
class LapData:
    start_time: Optional[datetime]
    total_time_s: Optional[float]
    distance_m: Optional[float]
    calories: Optional[int]
    avg_heart_rate_bpm: Optional[int]
    max_heart_rate_bpm: Optional[int]
    intensity: str = "active"
    trigger_method: str = "manual"
    trackpoints: list[TrackPoint] = field(default_factory=list)

    @property
    def effective_start_time(self) -> Optional[datetime]:
        if self.start_time is not None:
            return self.start_time
        return self.trackpoints[0].timestamp if self.trackpoints else None

    @property
    def effective_end_time(self) -> Optional[datetime]:
        if self.trackpoints:
            return self.trackpoints[-1].timestamp
        if self.start_time is not None and self.total_time_s is not None:
            return self.start_time + timedelta(seconds=self.total_time_s)
        return self.effective_start_time


@dataclass
class ActivityData:
    sport_name: str
    laps: list[LapData]
    activity_id: Optional[datetime] = None
    source_path: Optional[Path] = None

    @property
    def start_time(self) -> Optional[datetime]:
        for lap in self.laps:
            if lap.effective_start_time is not None:
                return lap.effective_start_time
        return self.activity_id

    @property
    def end_time(self) -> Optional[datetime]:
        for lap in reversed(self.laps):
            if lap.effective_end_time is not None:
                return lap.effective_end_time
        return self.start_time


@dataclass(frozen=True)
class LapSummary:
    start_time: datetime
    end_time: datetime
    elapsed_time_s: float
    timer_time_s: float
    distance_m: float
    calories: Optional[int]
    avg_heart_rate_bpm: Optional[int]
    max_heart_rate_bpm: Optional[int]
    min_heart_rate_bpm: Optional[int]
    avg_speed_mps: Optional[float]
    max_speed_mps: Optional[float]
    total_ascent_m: Optional[int]
    total_descent_m: Optional[int]
    start_position: Optional[tuple[float, float]]
    end_position: Optional[tuple[float, float]]
    intensity: str


@dataclass
class NormalizedLap:
    lap: LapData
    points: list[TrackPoint]
    summary: LapSummary
    distance_source: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConversionReport:
    source_path: Optional[Path]
    output_path: Optional[Path]
    converted: bool
    skipped: bool
    records: int = 0
    laps: int = 0
    distance_sources: tuple[str, ...] = ()
    validated: bool = False
    warnings: tuple[str, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class SaveResult:
    destination: Path
    reports: tuple[ConversionReport, ...]

    @property
    def files(self) -> tuple[Path, ...]:
        return tuple(
            report.output_path
            for report in self.reports
            if report.output_path is not None and report.converted
        )

    @property
    def converted_count(self) -> int:
        return sum(1 for report in self.reports if report.converted)

    @property
    def skipped_count(self) -> int:
        return sum(1 for report in self.reports if report.skipped)

    @property
    def failed_count(self) -> int:
        return sum(1 for report in self.reports if report.error)

    @property
    def is_success(self) -> bool:
        return self.failed_count == 0 and all(
            report.validated or not report.converted for report in self.reports
        )


def _max_optional(left: Optional[float], right: Optional[float]) -> Optional[float]:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None
