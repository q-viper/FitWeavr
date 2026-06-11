#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

PYTHON_BIN=${PYTHON:-python}
USE_VENV=${FITWEAVR_NO_VENV:-0}
SKIP_INSTALL=${FITWEAVR_SKIP_INSTALL:-0}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --python)
            shift
            PYTHON_BIN=${1:?"--python requires a Python executable"}
            ;;
        --python=*)
            PYTHON_BIN=${1#*=}
            ;;
        --no-venv)
            USE_VENV=1
            ;;
        --skip-install)
            SKIP_INSTALL=1
            ;;
        *)
            break
            ;;
    esac
    shift
done

find_venv_python() {
    if [ -f ".venv/bin/python" ]; then
        printf '%s\n' ".venv/bin/python"
    elif [ -f ".venv/Scripts/python.exe" ]; then
        printf '%s\n' ".venv/Scripts/python.exe"
    else
        printf '%s\n' ""
    fi
}

if [ "$USE_VENV" = "1" ]; then
    RUN_PYTHON=$PYTHON_BIN
else
    RUN_PYTHON=$(find_venv_python)
    if [ -z "$RUN_PYTHON" ]; then
        "$PYTHON_BIN" -m venv .venv
        RUN_PYTHON=$(find_venv_python)
    fi
    if [ -z "$RUN_PYTHON" ]; then
        echo "Could not find .venv Python after creating the environment." >&2
        exit 1
    fi
    if ! "$RUN_PYTHON" -c "import sys; print(sys.executable)" >/dev/null 2>&1; then
        echo "Could not execute $RUN_PYTHON; falling back to $PYTHON_BIN." >&2
        RUN_PYTHON=$PYTHON_BIN
    fi
fi

if [ "$SKIP_INSTALL" != "1" ]; then
    "$RUN_PYTHON" -m pip install -e .
fi

check_runtime_deps() {
    "$RUN_PYTHON" -c "from importlib import resources; import anyio._backends, fitweavr_web, nicegui; template = resources.files('nicegui') / 'templates' / 'index.html'; raise SystemExit(0 if template.is_file() else 1)"
}

if ! check_runtime_deps >/dev/null 2>&1; then
    if [ "$SKIP_INSTALL" = "1" ]; then
        echo "Runtime dependencies are incomplete. Re-run without --skip-install, or repair anyio/nicegui for this Python." >&2
        "$RUN_PYTHON" -c "import sys; print(sys.executable)" >&2
        exit 1
    fi
    echo "Repairing runtime dependencies for $RUN_PYTHON..." >&2
    "$RUN_PYTHON" -m pip install --upgrade --force-reinstall "anyio>=4.0" "nicegui>=2.0.0"
    "$RUN_PYTHON" -m pip install -e .
    check_runtime_deps
fi

"$RUN_PYTHON" -m fitweavr_web.cli --env-file "$ROOT/.env" "$@"
