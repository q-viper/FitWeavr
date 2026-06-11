from __future__ import annotations

import csv
import math
import re
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from garmin_fit_sdk import Decoder, Encoder, Profile, Stream

from fitweavr.data import FitData
from fitweavr.enums import DistanceSource
from fitweavr.models import (
    ActivityData,
    ConversionReport,
    LapData,
    LapSummary,
    NormalizedLap,
    SaveResult,
    TrackPoint,
)


class FitDataWriter:
    def __init__(self, distance_source: DistanceSource = DistanceSource.AUTO) -> None:
        self.normalizer = DistanceNormalizer(distance_source)
        self.activity_writer = FitActivityWriter()

    def save(
        self,
        data: FitData,
        destination: Path,
        *,
        overwrite: bool = False,
        validate: bool = True,
        write_manifest: bool = True,
    ) -> SaveResult:
        reports: list[ConversionReport] = []
        total = len(data.activities)
        for index, activity in enumerate(data.activities):
            output_path = self._output_path(data, activity, destination, index, total)
            if output_path.exists() and not overwrite:
                reports.append(
                    ConversionReport(
                        source_path=activity.source_path or data.source.path,
                        output_path=output_path,
                        converted=False,
                        skipped=True,
                        warnings=("Output exists; use overwrite=True to regenerate.",),
                    )
                )
                continue
            reports.append(
                self._write_activity(
                    activity,
                    output_path,
                    validate=validate,
                )
            )

        if write_manifest and destination.suffix.lower() != ".fit":
            self._write_manifest(destination, reports)
        return SaveResult(destination=destination, reports=tuple(reports))

    def _write_activity(
        self,
        activity: ActivityData,
        output_path: Path,
        *,
        validate: bool,
    ) -> ConversionReport:
        try:
            normalized_laps = [self.normalizer.normalize(lap) for lap in activity.laps]
            valid, validation_errors = self.activity_writer.write(
                activity,
                normalized_laps,
                output_path,
                validate=validate,
            )
            warnings = [
                warning
                for normalized_lap in normalized_laps
                for warning in normalized_lap.warnings
            ]
            warnings.extend(validation_errors)
            return ConversionReport(
                source_path=activity.source_path,
                output_path=output_path,
                converted=True,
                skipped=False,
                records=sum(len(lap.points) for lap in normalized_laps),
                laps=len(normalized_laps),
                distance_sources=tuple(lap.distance_source for lap in normalized_laps),
                validated=valid,
                warnings=tuple(warnings),
            )
        except Exception as exc:
            return ConversionReport(
                source_path=activity.source_path,
                output_path=output_path,
                converted=False,
                skipped=False,
                error=f"FIT write failed: {exc}",
            )

    def _output_path(
        self,
        data: FitData,
        activity: ActivityData,
        destination: Path,
        index: int,
        total: int,
    ) -> Path:
        if destination.suffix.lower() == ".fit":
            if total > 1:
                raise ValueError("Cannot save multiple activities into one .fit file path.")
            return destination

        source_path = activity.source_path or data.source.path
        stem = source_path.stem if source_path is not None else "activity"
        suffix = f"_{index + 1}" if total > 1 else ""
        return destination / f"{stem}{suffix}.fit"

    def _write_manifest(self, destination: Path, reports: list[ConversionReport]) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        manifest_path = destination / "conversion_manifest.csv"
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


class DistanceNormalizer:
    def __init__(self, source: DistanceSource = DistanceSource.AUTO) -> None:
        self.source = source

    def normalize(self, lap: LapData) -> NormalizedLap:
        warnings: list[str] = []
        points = [point.clone() for point in lap.trackpoints]
        if not points:
            points = self._synthetic_points(lap)
            warnings.append("No trackpoints found; created synthetic start/end records.")

        chosen_source = self._choose_distance_source(lap, points)
        if chosen_source == DistanceSource.GPS.value:
            gps_distances = self._gps_distances(points)
            distances = self._interpolate_distances(points, gps_distances)
            distances = self._scale_to_summary_distance(distances, lap.distance_m)
        else:
            distances = self._tcx_distances(points, lap.distance_m)

        for point, distance in zip(points, distances):
            point.fit_distance_m = distance

        self._fill_speeds(points)
        summary = self._summarize(lap, points)
        return NormalizedLap(
            lap=lap,
            points=points,
            summary=summary,
            distance_source=chosen_source,
            warnings=warnings,
        )

    def _choose_distance_source(self, lap: LapData, points: list[TrackPoint]) -> str:
        gps_total = self._gps_total(points)
        if self.source == DistanceSource.GPS:
            return DistanceSource.GPS.value if gps_total > 1 else DistanceSource.TCX.value
        if self.source == DistanceSource.TCX:
            return DistanceSource.TCX.value
        if gps_total <= 1:
            return DistanceSource.TCX.value
        return DistanceSource.GPS.value if self._tcx_distance_looks_flat(lap, points) else DistanceSource.TCX.value

    def _tcx_distance_looks_flat(self, lap: LapData, points: list[TrackPoint]) -> bool:
        distances = [p.distance_m for p in points if p.distance_m is not None]
        if len(distances) < 3:
            return True
        rounded_unique = {round(distance, 1) for distance in distances}
        if len(rounded_unique) <= 3 and (lap.distance_m or max(distances)) > 100:
            return True

        summary_distance = lap.distance_m or max(distances)
        if summary_distance <= 0:
            return False
        first_large_index = next(
            (
                idx
                for idx, point in enumerate(points)
                if point.distance_m is not None and point.distance_m >= summary_distance * 0.9
            ),
            None,
        )
        return first_large_index is not None and first_large_index <= max(1, len(points) // 5)

    def _synthetic_points(self, lap: LapData) -> list[TrackPoint]:
        start_time = lap.effective_start_time or datetime.now(timezone.utc)
        timer_time = max(lap.total_time_s or 1.0, 1.0)
        end_time = start_time + timedelta(seconds=timer_time)
        distance = max(lap.distance_m or 0.0, 0.0)
        return [
            TrackPoint(timestamp=start_time, distance_m=0.0),
            TrackPoint(timestamp=end_time, distance_m=distance),
        ]

    def _tcx_distances(
        self, points: list[TrackPoint], summary_distance_m: Optional[float]
    ) -> list[float]:
        raw = [point.distance_m for point in points]
        if not any(distance is not None for distance in raw):
            return self._linear_distances(points, summary_distance_m or 0.0)

        distances: list[float] = []
        previous = 0.0
        for distance in raw:
            if distance is None:
                distances.append(previous)
                continue
            clean = max(float(distance), previous)
            distances.append(clean)
            previous = clean

        if summary_distance_m is not None and summary_distance_m > distances[-1]:
            distances[-1] = summary_distance_m
        return distances

    def _gps_distances(self, points: list[TrackPoint]) -> list[Optional[float]]:
        distances: list[Optional[float]] = []
        previous_position: Optional[tuple[float, float]] = None
        cumulative = 0.0
        for point in points:
            if point.has_position:
                position = (point.latitude_deg or 0.0, point.longitude_deg or 0.0)
                if previous_position is not None:
                    cumulative += _haversine_m(previous_position, position)
                previous_position = position
                distances.append(cumulative)
            else:
                distances.append(None)
        return distances

    def _gps_total(self, points: list[TrackPoint]) -> float:
        distances = [distance for distance in self._gps_distances(points) if distance is not None]
        return distances[-1] if distances else 0.0

    def _interpolate_distances(
        self, points: list[TrackPoint], known: list[Optional[float]]
    ) -> list[float]:
        known_indexes = [idx for idx, distance in enumerate(known) if distance is not None]
        if not known_indexes:
            return self._tcx_distances(points, None)

        distances = [0.0] * len(points)
        first = known_indexes[0]
        for idx in range(first + 1):
            distances[idx] = known[first] or 0.0

        for left, right in zip(known_indexes, known_indexes[1:]):
            left_distance = known[left] or 0.0
            right_distance = known[right] or left_distance
            left_time = points[left].timestamp
            right_time = points[right].timestamp
            span_s = max((right_time - left_time).total_seconds(), 0.0)
            for idx in range(left, right + 1):
                ratio = 1.0 if span_s == 0 else max(
                    0.0,
                    min(1.0, (points[idx].timestamp - left_time).total_seconds() / span_s),
                )
                distances[idx] = left_distance + (right_distance - left_distance) * ratio

        last = known_indexes[-1]
        for idx in range(last, len(points)):
            distances[idx] = known[last] or distances[last]
        return distances

    def _scale_to_summary_distance(
        self, distances: list[float], summary_distance_m: Optional[float]
    ) -> list[float]:
        if not distances:
            return distances
        gps_total = distances[-1]
        if summary_distance_m is None or summary_distance_m <= 0 or gps_total <= 0:
            return distances
        ratio = summary_distance_m / gps_total
        if 0.2 <= ratio <= 5.0:
            return [distance * ratio for distance in distances]
        return distances

    def _linear_distances(
        self, points: list[TrackPoint], summary_distance_m: float
    ) -> list[float]:
        if len(points) == 1:
            return [max(summary_distance_m, 0.0)]
        start = points[0].timestamp
        end = points[-1].timestamp
        duration_s = max((end - start).total_seconds(), 0.0)
        if duration_s == 0:
            return [0.0 for _ in points[:-1]] + [max(summary_distance_m, 0.0)]
        return [
            max(summary_distance_m, 0.0)
            * max(0.0, min(1.0, (point.timestamp - start).total_seconds() / duration_s))
            for point in points
        ]

    def _fill_speeds(self, points: list[TrackPoint]) -> None:
        speed_cap = self._speed_cap(points)
        previous: Optional[TrackPoint] = None
        for point in points:
            if previous is None or point.fit_distance_m is None or previous.fit_distance_m is None:
                previous = point
                continue
            elapsed_s = (point.timestamp - previous.timestamp).total_seconds()
            distance_delta_m = point.fit_distance_m - previous.fit_distance_m
            if elapsed_s > 0 and distance_delta_m >= 0:
                speed = distance_delta_m / elapsed_s
                if 0 <= speed <= speed_cap:
                    point.fit_speed_mps = speed
            previous = point

    def _speed_cap(self, points: list[TrackPoint]) -> float:
        if len(points) < 2:
            return 45.0
        first_distance = points[0].fit_distance_m or 0.0
        last_distance = points[-1].fit_distance_m or first_distance
        elapsed_s = max((points[-1].timestamp - points[0].timestamp).total_seconds(), 1.0)
        avg_speed = max((last_distance - first_distance) / elapsed_s, 0.0)
        return min(45.0, max(5.0, avg_speed * 4.0))

    def _summarize(self, lap: LapData, points: list[TrackPoint]) -> LapSummary:
        start_time = lap.effective_start_time or points[0].timestamp
        end_time = lap.effective_end_time or points[-1].timestamp
        elapsed_time_s = max((end_time - start_time).total_seconds(), 0.0)
        timer_time_s = max(lap.total_time_s or elapsed_time_s or 1.0, 1.0)
        distance_m = max(lap.distance_m or points[-1].fit_distance_m or 0.0, 0.0)
        heart_rates = [p.heart_rate_bpm for p in points if p.heart_rate_bpm is not None]
        avg_hr = lap.avg_heart_rate_bpm or _average_int(heart_rates)
        max_hr = lap.max_heart_rate_bpm or (max(heart_rates) if heart_rates else None)
        min_hr = min(heart_rates) if heart_rates else None
        speeds = [p.fit_speed_mps for p in points if p.fit_speed_mps is not None]
        avg_speed = distance_m / timer_time_s if timer_time_s > 0 and distance_m > 0 else None
        max_speed = max(speeds) if speeds else avg_speed
        ascent, descent = self._ascent_descent(points)
        return LapSummary(
            start_time=start_time,
            end_time=end_time,
            elapsed_time_s=elapsed_time_s,
            timer_time_s=timer_time_s,
            distance_m=distance_m,
            calories=lap.calories,
            avg_heart_rate_bpm=avg_hr,
            max_heart_rate_bpm=max_hr,
            min_heart_rate_bpm=min_hr,
            avg_speed_mps=avg_speed,
            max_speed_mps=max_speed,
            total_ascent_m=ascent,
            total_descent_m=descent,
            start_position=_first_position(points),
            end_position=_last_position(points),
            intensity=_map_intensity(lap.intensity),
        )

    def _ascent_descent(self, points: list[TrackPoint]) -> tuple[Optional[int], Optional[int]]:
        ascent = 0.0
        descent = 0.0
        previous_altitude: Optional[float] = None
        for point in points:
            if point.altitude_m is None:
                continue
            if previous_altitude is not None:
                delta = point.altitude_m - previous_altitude
                if delta > 0.3:
                    ascent += delta
                elif delta < -0.3:
                    descent += abs(delta)
            previous_altitude = point.altitude_m
        return (
            int(round(ascent)) if ascent > 0 else None,
            int(round(descent)) if descent > 0 else None,
        )


class FitActivityWriter:
    def write(
        self,
        activity: ActivityData,
        normalized_laps: list[NormalizedLap],
        output_path: Path,
        *,
        validate: bool = True,
    ) -> tuple[bool, list[str]]:
        start_time = activity.start_time
        end_time = activity.end_time or start_time
        if start_time is None or end_time is None:
            raise ValueError("Activity has no usable timestamps.")

        encoder = Encoder()
        sport = SportMapper.map_sport(activity.sport_name)
        sub_sport = SportMapper.map_sub_sport(activity.sport_name)
        serial_number = self._serial_number(activity.source_path)

        self._write_file_headers(encoder, start_time, serial_number)
        self._write_event(encoder, start_time, "timer", "start")
        for normalized_lap in normalized_laps:
            for point in normalized_lap.points:
                self._write_record(encoder, point)
        self._write_event(encoder, end_time, "timer", "stop_all")

        for index, normalized_lap in enumerate(normalized_laps):
            self._write_lap(encoder, index, normalized_lap.summary, sport, sub_sport)

        session_summary = self._session_summary(normalized_laps)
        self._write_session(encoder, session_summary, sport, sub_sport, len(normalized_laps))
        self._write_activity(encoder, session_summary.end_time, session_summary.timer_time_s)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(bytes(encoder.close()))

        errors: list[str] = []
        valid = True
        if validate:
            valid, errors = self._validate(output_path)
        return valid, errors

    def _write_file_headers(
        self, encoder: Encoder, start_time: datetime, serial_number: int
    ) -> None:
        encoder.on_mesg(
            Profile["mesg_num"]["FILE_ID"],
            {
                "type": "activity",
                "manufacturer": "development",
                "product": 1,
                "serial_number": serial_number,
                "time_created": start_time,
                "product_name": "FitWeavr",
            },
        )
        encoder.on_mesg(
            Profile["mesg_num"]["FILE_CREATOR"],
            {"software_version": 100, "hardware_version": 1},
        )
        encoder.on_mesg(
            Profile["mesg_num"]["DEVICE_INFO"],
            {
                "timestamp": start_time,
                "manufacturer": "development",
                "product": 1,
                "serial_number": serial_number,
                "software_version": 1.0,
                "source_type": "local",
                "product_name": "FitWeavr",
            },
        )

    def _write_record(self, encoder: Encoder, point: TrackPoint) -> None:
        message: dict[str, object] = {"timestamp": point.timestamp}
        if point.has_position:
            message["position_lat"] = _semicircles(point.latitude_deg or 0.0)
            message["position_long"] = _semicircles(point.longitude_deg or 0.0)
        if point.altitude_m is not None:
            message["altitude"] = point.altitude_m
            message["enhanced_altitude"] = point.altitude_m
        if point.heart_rate_bpm is not None:
            message["heart_rate"] = point.heart_rate_bpm
        if point.fit_distance_m is not None:
            message["distance"] = max(point.fit_distance_m, 0.0)
        if point.fit_speed_mps is not None:
            message["speed"] = point.fit_speed_mps
            message["enhanced_speed"] = point.fit_speed_mps
        encoder.on_mesg(Profile["mesg_num"]["RECORD"], message)

    def _write_event(
        self, encoder: Encoder, timestamp: datetime, event: str, event_type: str
    ) -> None:
        encoder.on_mesg(
            Profile["mesg_num"]["EVENT"],
            {"timestamp": timestamp, "event": event, "event_type": event_type},
        )

    def _write_lap(
        self,
        encoder: Encoder,
        index: int,
        summary: LapSummary,
        sport: str,
        sub_sport: str,
    ) -> None:
        message = self._summary_message(summary, sport, sub_sport)
        message.update(
            {
                "message_index": index,
                "timestamp": summary.end_time,
                "event": "lap",
                "event_type": "stop",
                "intensity": summary.intensity,
            }
        )
        encoder.on_mesg(Profile["mesg_num"]["LAP"], message)

    def _write_session(
        self,
        encoder: Encoder,
        summary: LapSummary,
        sport: str,
        sub_sport: str,
        lap_count: int,
    ) -> None:
        message = self._summary_message(summary, sport, sub_sport)
        message.update(
            {
                "message_index": 0,
                "timestamp": summary.end_time,
                "event": "session",
                "event_type": "stop",
                "trigger": "activity_end",
                "first_lap_index": 0,
                "num_laps": lap_count,
            }
        )
        encoder.on_mesg(Profile["mesg_num"]["SESSION"], message)

    def _write_activity(
        self, encoder: Encoder, end_time: datetime, total_timer_time_s: float
    ) -> None:
        encoder.on_mesg(
            Profile["mesg_num"]["ACTIVITY"],
            {
                "timestamp": end_time,
                "total_timer_time": total_timer_time_s,
                "num_sessions": 1,
                "type": "manual",
                "event": "activity",
                "event_type": "stop",
            },
        )

    def _summary_message(
        self, summary: LapSummary, sport: str, sub_sport: str
    ) -> dict[str, object]:
        message: dict[str, object] = {
            "start_time": summary.start_time,
            "total_elapsed_time": summary.elapsed_time_s,
            "total_timer_time": summary.timer_time_s,
            "total_distance": summary.distance_m,
            "sport": sport,
            "sub_sport": sub_sport,
        }
        optional_values = {
            "total_calories": summary.calories,
            "avg_heart_rate": summary.avg_heart_rate_bpm,
            "max_heart_rate": summary.max_heart_rate_bpm,
            "min_heart_rate": summary.min_heart_rate_bpm,
            "avg_speed": summary.avg_speed_mps,
            "max_speed": summary.max_speed_mps,
            "total_ascent": summary.total_ascent_m,
            "total_descent": summary.total_descent_m,
        }
        message.update({key: value for key, value in optional_values.items() if value is not None})
        if summary.start_position is not None:
            message["start_position_lat"] = _semicircles(summary.start_position[0])
            message["start_position_long"] = _semicircles(summary.start_position[1])
        if summary.end_position is not None:
            message["end_position_lat"] = _semicircles(summary.end_position[0])
            message["end_position_long"] = _semicircles(summary.end_position[1])
        return message

    def _session_summary(self, normalized_laps: list[NormalizedLap]) -> LapSummary:
        if not normalized_laps:
            raise ValueError("No laps to summarize.")

        first = normalized_laps[0].summary
        last = normalized_laps[-1].summary
        timer_time = sum(lap.summary.timer_time_s for lap in normalized_laps)
        elapsed_time = max((last.end_time - first.start_time).total_seconds(), timer_time)
        distance = sum(lap.summary.distance_m for lap in normalized_laps)
        calories_values = [lap.summary.calories for lap in normalized_laps if lap.summary.calories]
        heart_rates = [
            lap.summary.avg_heart_rate_bpm
            for lap in normalized_laps
            if lap.summary.avg_heart_rate_bpm is not None
        ]
        max_heart_rates = [
            lap.summary.max_heart_rate_bpm
            for lap in normalized_laps
            if lap.summary.max_heart_rate_bpm is not None
        ]
        min_heart_rates = [
            lap.summary.min_heart_rate_bpm
            for lap in normalized_laps
            if lap.summary.min_heart_rate_bpm is not None
        ]
        ascents = [
            lap.summary.total_ascent_m
            for lap in normalized_laps
            if lap.summary.total_ascent_m is not None
        ]
        descents = [
            lap.summary.total_descent_m
            for lap in normalized_laps
            if lap.summary.total_descent_m is not None
        ]
        max_speeds = [
            lap.summary.max_speed_mps
            for lap in normalized_laps
            if lap.summary.max_speed_mps is not None
        ]
        return LapSummary(
            start_time=first.start_time,
            end_time=last.end_time,
            elapsed_time_s=elapsed_time,
            timer_time_s=timer_time,
            distance_m=distance,
            calories=sum(calories_values) if calories_values else None,
            avg_heart_rate_bpm=_average_int(heart_rates),
            max_heart_rate_bpm=max(max_heart_rates) if max_heart_rates else None,
            min_heart_rate_bpm=min(min_heart_rates) if min_heart_rates else None,
            avg_speed_mps=distance / timer_time if timer_time > 0 and distance > 0 else None,
            max_speed_mps=max(max_speeds) if max_speeds else None,
            total_ascent_m=sum(ascents) if ascents else None,
            total_descent_m=sum(descents) if descents else None,
            start_position=first.start_position,
            end_position=last.end_position,
            intensity="active",
        )

    def _validate(self, output_path: Path) -> tuple[bool, list[str]]:
        stream = Stream.from_file(str(output_path))
        decoder = Decoder(stream)
        errors: list[str] = []
        if not decoder.is_fit():
            errors.append("Generated file is not recognized as FIT.")
        if not decoder.check_integrity():
            errors.append("Generated file failed FIT integrity check.")
        stream = Stream.from_file(str(output_path))
        messages, decoder_errors = Decoder(stream).read()
        if decoder_errors:
            errors.extend(str(error) for error in decoder_errors)
        for required in ("file_id_mesgs", "record_mesgs", "lap_mesgs", "session_mesgs", "activity_mesgs"):
            if required not in messages:
                errors.append(f"Missing required FIT message group: {required}")
        return not errors, errors

    def _serial_number(self, source_path: Optional[Path]) -> int:
        value = zlib.crc32(str(source_path or "fitweavr").encode("utf-8")) & 0xFFFFFFFF
        return value or 1


class SportMapper:
    _SPORT_ALIASES = {
        "walking": "walking",
        "walk": "walking",
        "walkin": "walking",
        "paced_w": "walking",
        "paced_walk": "walking",
        "running": "running",
        "run": "running",
        "cycling": "cycling",
        "biking": "cycling",
        "bikin": "cycling",
        "bike": "cycling",
        "hiking": "hiking",
        "swimming": "swimming",
        "rowing": "rowing",
        "elliptical": "fitness_equipment",
        "other": "generic",
        "workout": "training",
        "training": "training",
    }
    _SUB_SPORT_ALIASES = {
        "walking": "generic",
        "walk": "generic",
        "walkin": "generic",
        "running": "generic",
        "run": "generic",
        "cycling": "generic",
        "biking": "generic",
        "bikin": "generic",
        "hiking": "generic",
        "other": "generic",
    }

    @classmethod
    def map_sport(cls, tcx_sport: str) -> str:
        normalized = _normalize_name(tcx_sport)
        candidate = cls._SPORT_ALIASES.get(normalized, normalized)
        return candidate if _profile_type_has_value("sport", candidate) else "generic"

    @classmethod
    def map_sub_sport(cls, tcx_sport: str) -> str:
        normalized = _normalize_name(tcx_sport)
        candidate = cls._SUB_SPORT_ALIASES.get(normalized, "generic")
        return candidate if _profile_type_has_value("sub_sport", candidate) else "generic"


def _first_position(points: list[TrackPoint]) -> Optional[tuple[float, float]]:
    for point in points:
        if point.has_position:
            return (point.latitude_deg or 0.0, point.longitude_deg or 0.0)
    return None


def _last_position(points: list[TrackPoint]) -> Optional[tuple[float, float]]:
    for point in reversed(points):
        if point.has_position:
            return (point.latitude_deg or 0.0, point.longitude_deg or 0.0)
    return None


def _semicircles(degrees: float) -> int:
    return int(round(degrees * (2**31) / 180.0))


def _haversine_m(left: tuple[float, float], right: tuple[float, float]) -> float:
    radius_m = 6_371_000.0
    lat1 = math.radians(left[0])
    lon1 = math.radians(left[1])
    lat2 = math.radians(right[0])
    lon2 = math.radians(right[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _normalize_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return normalized.strip("_") or "generic"


def _profile_type_has_value(type_name: str, value: str) -> bool:
    return value in set(Profile["types"].get(type_name, {}).values())


def _map_intensity(value: str) -> str:
    normalized = _normalize_name(value)
    if normalized in {"rest", "resting"}:
        return "rest"
    if normalized in {"warmup", "warm_up"}:
        return "warmup"
    if normalized in {"cooldown", "cool_down"}:
        return "cooldown"
    return "active"


def _average_int(values: Iterable[Optional[int]]) -> Optional[int]:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return int(round(sum(clean) / len(clean)))
