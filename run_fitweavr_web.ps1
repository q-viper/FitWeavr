param(
    [string]$Python = "",
    [string]$HostName = "",
    [int]$Port = 0,
    [switch]$Reload,
    [switch]$SkipInstall,
    [switch]$NoVenv
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = $env:PYTHON
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = "python"
}

if ($NoVenv) {
    $RunPython = $Python
}
else {
    $VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        & $Python -m venv .venv
    }
    $RunPython = $VenvPython
}

if (-not $SkipInstall) {
    & $RunPython -m pip install -e .
}

$DependencyCheck = "from importlib import resources; import anyio._backends, fitweavr_web, nicegui; template = resources.files('nicegui') / 'templates' / 'index.html'; raise SystemExit(0 if template.is_file() else 1)"

& $RunPython -c $DependencyCheck | Out-Null
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Write-Error "Runtime dependencies are incomplete. Re-run without -SkipInstall, or repair anyio/nicegui for this Python."
        exit 1
    }
    Write-Host "Repairing runtime dependencies for $RunPython..."
    & $RunPython -m pip install --upgrade --force-reinstall "anyio>=4.0" "nicegui>=2.0.0"
    & $RunPython -m pip install -e .
    & $RunPython -c $DependencyCheck
}

$ArgsList = @(
    "-m",
    "fitweavr_web.cli",
    "--env-file",
    (Join-Path $Root ".env")
)

if ($HostName) {
    $ArgsList += @("--host", $HostName)
}
if ($Port -gt 0) {
    $ArgsList += @("--port", $Port.ToString())
}
if ($Reload) {
    $ArgsList += "--reload"
}

& $RunPython @ArgsList
