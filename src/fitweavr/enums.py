from __future__ import annotations

from enum import Enum


class _StringEnum(str, Enum):
    @classmethod
    def coerce(cls, value: "_StringEnum | str") -> "_StringEnum":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        for member in cls:
            if normalized in {member.value.lower(), member.name.lower()}:
                return member
        choices = ", ".join(member.name for member in cls)
        raise ValueError(f"Unknown {cls.__name__}: {value!r}. Expected one of: {choices}")


class FormatType(_StringEnum):
    TCX = "tcx"
    FIT = "fit"
    GPX = "gpx"
    JSON = "json"


class SourceType(_StringEnum):
    GOOGLE_FIT = "google_fit"
    GARMIN_CONNECT = "garmin_connect"
    ZEPP = "zepp"
    STRAVA = "strava"
    UNKNOWN = "unknown"


class DestinationType(_StringEnum):
    GARMIN_CONNECT = "garmin_connect"
    AMAZFIT_ZEPP = "amazfit_zepp"
    GENERIC_FIT = "generic_fit"


class DistanceSource(_StringEnum):
    AUTO = "auto"
    TCX = "tcx"
    GPS = "gps"
