from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from fitweavr.batch import BatchConverter, ConversionConfig
from fitweavr.enums import DistanceSource, SourceType


DEFAULT_INPUT_DIR = Path(
    r"C:\Users\Viper\Downloads\takeout-20260611T084114Z-3-001\Takeout\Fit\Activities"
)
DEFAULT_OUTPUT_DIR = Path.cwd() / "zepp_fit_output"


def parse_args(argv: Optional[list[str]] = None) -> ConversionConfig:
    parser = argparse.ArgumentParser(
        description="Convert Google Fit Takeout TCX activity files to FIT files."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_DIR, help="Folder of .tcx files.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Folder where .fit files will be written.",
    )
    parser.add_argument(
        "--distance-source",
        choices=[item.value for item in DistanceSource],
        default=DistanceSource.AUTO.value,
        help="Use TCX distance, GPS-derived distance, or auto-select per lap.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Regenerate existing .fit files.")
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip Garmin SDK decode/integrity validation after writing.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Convert only the first N TCX files; useful for testing.",
    )
    args = parser.parse_args(argv)
    return ConversionConfig(
        input_dir=args.input,
        output_dir=args.output,
        distance_source=DistanceSource.coerce(args.distance_source),
        overwrite=args.overwrite,
        validate=not args.no_validate,
        limit=args.limit,
        source_type=SourceType.GOOGLE_FIT,
    )


def main(argv: Optional[list[str]] = None) -> int:
    config = parse_args(argv)
    if not config.input_dir.exists():
        print(f"Input folder does not exist: {config.input_dir}", file=sys.stderr)
        return 2

    result = BatchConverter(config).convert_all()
    failed = [report for report in result.reports if report.error]
    invalid = [report for report in result.reports if report.converted and not report.validated]

    print()
    print(f"Converted: {result.converted_count}")
    print(f"Skipped:   {result.skipped_count}")
    print(f"Failed:    {len(failed)}")
    print(f"Output:    {config.output_dir}")
    print(f"Manifest:  {config.output_dir / 'conversion_manifest.csv'}")

    if failed:
        print("\nFailures:")
        for report in failed[:20]:
            source_name = report.source_path.name if report.source_path else "unknown"
            print(f"- {source_name}: {report.error}")
        if len(failed) > 20:
            print(f"- ... {len(failed) - 20} more, see manifest.")
    if invalid:
        print("\nValidation warnings:")
        for report in invalid[:20]:
            print(f"- {report.output_path}: {' | '.join(report.warnings)}")
        if len(invalid) > 20:
            print(f"- ... {len(invalid) - 20} more, see manifest.")
    return 1 if failed or invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
