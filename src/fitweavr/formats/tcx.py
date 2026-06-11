from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from fitweavr.data import TcxData
from fitweavr.models import ActivityData, LapData, SourceMetadata, TrackPoint


class TcxParser:
    def parse_file(self, path: Path, *, source: SourceMetadata) -> TcxData:
        root = ET.parse(path).getroot()
        activities_parent = _first_descendant(root, "Activities")
        activity_nodes = _children(activities_parent, "Activity") if activities_parent is not None else []
        activities = [
            self._parse_activity(path, activity_node)
            for activity_node in activity_nodes
        ]
        return TcxData(
            source=source,
            activities=[activity for activity in activities if activity.laps],
        )

    def _parse_activity(self, source_path: Path, node: ET.Element) -> ActivityData:
        sport_name = node.attrib.get("Sport", "Other")
        activity_id = _parse_datetime(_child_text(node, "Id"))
        laps = [self._parse_lap(lap_node) for lap_node in _children(node, "Lap")]
        return ActivityData(
            source_path=source_path,
            sport_name=sport_name,
            activity_id=activity_id,
            laps=laps,
        )

    def _parse_lap(self, node: ET.Element) -> LapData:
        start_time = _parse_datetime(node.attrib.get("StartTime"))
        points: list[TrackPoint] = []
        for track_node in _children(node, "Track"):
            for point_node in _children(track_node, "Trackpoint"):
                point = self._parse_trackpoint(point_node)
                if point is not None:
                    points.append(point)

        return LapData(
            start_time=start_time,
            total_time_s=_float_or_none(_child_text(node, "TotalTimeSeconds")),
            distance_m=_float_or_none(_child_text(node, "DistanceMeters")),
            calories=_int_or_none(_child_text(node, "Calories")),
            avg_heart_rate_bpm=_int_or_none(
                _child_text(node, "AverageHeartRateBpm", "Value")
            ),
            max_heart_rate_bpm=_int_or_none(
                _child_text(node, "MaximumHeartRateBpm", "Value")
            ),
            intensity=(_child_text(node, "Intensity") or "Active").lower(),
            trigger_method=(_child_text(node, "TriggerMethod") or "Manual").lower(),
            trackpoints=self._merge_duplicate_timestamps(points),
        )

    def _parse_trackpoint(self, node: ET.Element) -> Optional[TrackPoint]:
        timestamp = _parse_datetime(_child_text(node, "Time"))
        if timestamp is None:
            return None
        return TrackPoint(
            timestamp=timestamp,
            distance_m=_float_or_none(_child_text(node, "DistanceMeters")),
            heart_rate_bpm=_int_or_none(_child_text(node, "HeartRateBpm", "Value")),
            latitude_deg=_float_or_none(_child_text(node, "Position", "LatitudeDegrees")),
            longitude_deg=_float_or_none(_child_text(node, "Position", "LongitudeDegrees")),
            altitude_m=_float_or_none(_child_text(node, "AltitudeMeters")),
        )

    def _merge_duplicate_timestamps(self, points: list[TrackPoint]) -> list[TrackPoint]:
        merged: dict[datetime, TrackPoint] = {}
        for point in sorted(points, key=lambda item: item.timestamp):
            if point.timestamp in merged:
                merged[point.timestamp] = merged[point.timestamp].merge(point)
            else:
                merged[point.timestamp] = point
        return list(merged.values())


def _children(node: ET.Element, local_name: str) -> list[ET.Element]:
    return [child for child in list(node) if _local_name(child.tag) == local_name]


def _first_descendant(node: ET.Element, local_name: str) -> Optional[ET.Element]:
    return next((child for child in node.iter() if _local_name(child.tag) == local_name), None)


def _child_text(node: ET.Element, *path: str) -> Optional[str]:
    current = node
    for name in path:
        matches = _children(current, name)
        if not matches:
            return None
        current = matches[0]
    if current.text is None:
        return None
    text = current.text.strip()
    return text or None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float_or_none(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _int_or_none(value: Optional[str]) -> Optional[int]:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    rounded = int(round(parsed))
    return rounded if 0 <= rounded <= 255 else None
