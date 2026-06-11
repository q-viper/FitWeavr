# FitWeavr

<p align="center">
  <img src="src/fitweavr_web/assets/fit_weavr.png" alt="FitWeavr logo" width="360">
</p>

**FitWeavr** is an OOP-first Python package and simple NiceGUI web app for converting fitness activity exports between importable formats.

The current engine reads TCX activity files and writes standard FIT activity files. Sources, destinations, input formats, and output formats are separated into conversion profiles so new parsers and writers can be added cleanly.

## Install Locally

```powershell
python -m pip install -e .
```

## Run The Web App

Windows PowerShell:

```powershell
.\run_fitweavr_web.ps1
```

Choose a specific Python:

```powershell
.\run_fitweavr_web.ps1 -Python "C:\Path\To\python.exe"
```

Run without creating a virtual environment:

```powershell
.\run_fitweavr_web.ps1 -NoVenv -Python "python"
```

macOS/Linux:

```sh
sh ./run_fitweavr_web.sh
```

Choose a specific Python:

```sh
PYTHON=/usr/bin/python3 sh ./run_fitweavr_web.sh
sh ./run_fitweavr_web.sh --python /usr/bin/python3
```

Run without creating a virtual environment:

```sh
FITWEAVR_NO_VENV=1 sh ./run_fitweavr_web.sh
sh ./run_fitweavr_web.sh --no-venv
```

Runner-only flags such as `--python`, `--no-venv`, and `--skip-install` should appear before app flags:

```sh
sh ./run_fitweavr_web.sh --python python --skip-install --port 8090
```

If you see `ModuleNotFoundError: No module named 'anyio._backends'` or a NiceGUI `TemplateNotFound: index.html` error, run without `--skip-install` so the runner can repair the Python environment:

```sh
sh ./run_fitweavr_web.sh --python python
```

Installed entry point:

```powershell
fitweavr-web --env-file .env
```

The app exposes source and destination selectors, single or multi-file upload, per-file progress/errors, individual FIT downloads, and a ZIP download. Uploads are saved into the session temp folder first, converted sequentially, and then removed from temp storage after the ZIP is finalized.

## Supported Routes

All current routes accept `.tcx` uploads and emit `.fit` downloads:

| Source | Destination |
| --- | --- |
| Google Fit | Garmin Connect |
| Google Fit | Zepp / Amazfit |
| Google Fit | Generic FIT |
| Strava | Garmin Connect |
| Strava | Zepp / Amazfit |
| Strava | Generic FIT |
| Garmin Connect | Garmin Connect |
| Garmin Connect | Zepp / Amazfit |
| Garmin Connect | Generic FIT |
| Zepp / Amazfit | Garmin Connect |
| Zepp / Amazfit | Zepp / Amazfit |
| Zepp / Amazfit | Generic FIT |

The full list comes from `fitweavr.routes.supported_profiles()`.

## Python API

```python
from pathlib import Path

from fitweavr import DestinationType, SourceType, get_conversion_profile, load_activity_file

profile = get_conversion_profile(
    SourceType.GOOGLE_FIT,
    DestinationType.AMAZFIT_ZEPP,
)

data = load_activity_file(
    Path(r"C:\path\to\activity.tcx"),
    source=profile.source_type,
    format_type=profile.input_format,
)

converted = data.convert(
    to=profile.target_format,
    distance_source=profile.distance_source,
)
result = converted.save(Path("fit_output"), overwrite=True)

print(result.converted_count)
print(result.files)
```

## Batch CLI

```powershell
fitweavr --input "C:\path\to\Takeout\Fit\Activities" --output ".\zepp_fit_output" --overwrite
```

The CLI writes one FIT file per activity and creates `conversion_manifest.csv`.

## Runtime Settings

Runtime limits are read from `.env`:

```dotenv
FITWEAVR_MAX_SESSIONS=20
FITWEAVR_SESSION_TIMEOUT_SECONDS=1800
FITWEAVR_MAX_UPLOAD_MB=100
FITWEAVR_WORK_DIR=.fitweavr_sessions
FITWEAVR_HOST=127.0.0.1
FITWEAVR_PORT=8080
FITWEAVR_STORAGE_SECRET=change-this-secret-for-shared-deployments
```

The footer shows visitor IP, session id, session time remaining, and active session count.
