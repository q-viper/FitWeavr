from __future__ import annotations

from dataclasses import dataclass

from fitweavr.enums import DestinationType, DistanceSource, FormatType, SourceType


@dataclass(frozen=True)
class SourceOption:
    source_type: SourceType
    label: str
    description: str
    input_formats: tuple[FormatType, ...]


@dataclass(frozen=True)
class DestinationOption:
    destination_type: DestinationType
    label: str
    description: str
    output_format: FormatType


@dataclass(frozen=True)
class ConversionProfile:
    route_id: str
    source: SourceOption
    destination: DestinationOption
    input_format: FormatType
    target_format: FormatType
    accepted_suffixes: tuple[str, ...]
    distance_source: DistanceSource = DistanceSource.AUTO

    @property
    def label(self) -> str:
        return f"{self.source.label} to {self.destination.label}"

    @property
    def description(self) -> str:
        suffixes = ", ".join(self.accepted_suffixes)
        return (
            f"Reads {self.source.label} {self.input_format.name} activity files ({suffixes}) "
            f"and writes {self.target_format.name} files for {self.destination.label}."
        )

    @property
    def source_type(self) -> SourceType:
        return self.source.source_type

    @property
    def destination_type(self) -> DestinationType:
        return self.destination.destination_type


SOURCE_OPTIONS: tuple[SourceOption, ...] = (
    SourceOption(
        source_type=SourceType.GOOGLE_FIT,
        label="Google Fit",
        description="Google Fit Takeout activity files exported from the Fit Activities folder.",
        input_formats=(FormatType.TCX,),
    ),
    SourceOption(
        source_type=SourceType.STRAVA,
        label="Strava",
        description="Strava activity exports saved as TCX files.",
        input_formats=(FormatType.TCX,),
    ),
    SourceOption(
        source_type=SourceType.GARMIN_CONNECT,
        label="Garmin Connect",
        description="Garmin Connect activity exports saved as TCX files.",
        input_formats=(FormatType.TCX,),
    ),
    SourceOption(
        source_type=SourceType.ZEPP,
        label="Zepp / Amazfit",
        description="Zepp or Amazfit activity exports saved as TCX files.",
        input_formats=(FormatType.TCX,),
    ),
)

DESTINATION_OPTIONS: tuple[DestinationOption, ...] = (
    DestinationOption(
        destination_type=DestinationType.GARMIN_CONNECT,
        label="Garmin Connect",
        description="Standard FIT activity files for Garmin-compatible import workflows.",
        output_format=FormatType.FIT,
    ),
    DestinationOption(
        destination_type=DestinationType.AMAZFIT_ZEPP,
        label="Zepp / Amazfit",
        description="Standard FIT activity files for Zepp or Amazfit-compatible imports.",
        output_format=FormatType.FIT,
    ),
    DestinationOption(
        destination_type=DestinationType.GENERIC_FIT,
        label="Generic FIT",
        description="Plain FIT activity files for services and tools that accept FIT uploads.",
        output_format=FormatType.FIT,
    ),
)


def supported_sources() -> tuple[SourceOption, ...]:
    return SOURCE_OPTIONS


def supported_destinations() -> tuple[DestinationOption, ...]:
    return DESTINATION_OPTIONS


def supported_profiles() -> tuple[ConversionProfile, ...]:
    profiles: list[ConversionProfile] = []
    for source in SOURCE_OPTIONS:
        for destination in DESTINATION_OPTIONS:
            input_format = source.input_formats[0]
            profiles.append(
                ConversionProfile(
                    route_id=f"{source.source_type.value}-to-{destination.destination_type.value}",
                    source=source,
                    destination=destination,
                    input_format=input_format,
                    target_format=destination.output_format,
                    accepted_suffixes=_suffixes_for(input_format),
                )
            )
    return tuple(profiles)


def default_profile() -> ConversionProfile:
    return get_conversion_profile(
        SourceType.GOOGLE_FIT,
        DestinationType.AMAZFIT_ZEPP,
    )


def get_conversion_profile(
    source: SourceType | str,
    destination: DestinationType | str,
) -> ConversionProfile:
    source_type = SourceType.coerce(source)
    destination_type = DestinationType.coerce(destination)
    for profile in supported_profiles():
        if profile.source_type == source_type and profile.destination_type == destination_type:
            return profile
    raise NotImplementedError(
        f"Conversion from {source_type.name} to {destination_type.name} is not supported."
    )


def source_option(source: SourceType | str) -> SourceOption:
    source_type = SourceType.coerce(source)
    for option in SOURCE_OPTIONS:
        if option.source_type == source_type:
            return option
    raise ValueError(f"No source option is registered for {source_type.name}.")


def destination_option(destination: DestinationType | str) -> DestinationOption:
    destination_type = DestinationType.coerce(destination)
    for option in DESTINATION_OPTIONS:
        if option.destination_type == destination_type:
            return option
    raise ValueError(f"No destination option is registered for {destination_type.name}.")


def _suffixes_for(format_type: FormatType) -> tuple[str, ...]:
    if format_type == FormatType.TCX:
        return (".tcx",)
    if format_type == FormatType.GPX:
        return (".gpx",)
    if format_type == FormatType.FIT:
        return (".fit",)
    if format_type == FormatType.JSON:
        return (".json",)
    return (f".{format_type.value}",)
