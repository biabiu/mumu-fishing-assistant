$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -eq 0) { & py -3 -m venv (Join-Path $Root ".venv") }
}
if (-not (Test-Path (Join-Path $Root ".venv\Scripts\python.exe"))) {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) { throw "Python 3.10+ not found. Install Python from https://www.python.org/downloads/windows/ and enable the py launcher." }
    & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) { throw "Python 3.10 or newer is required." }
    & python -m venv (Join-Path $Root ".venv")
}
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $Root "requirements.txt")
Write-Host "Dependencies installed. To open the experimental always-on-top controller, run:"
Write-Host "  & `"$VenvPython`" `"$(Join-Path $PSScriptRoot 'controller_windows.py')`""
