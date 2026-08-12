$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $FallbackPy = "C:\Users\moosa\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $FallbackPy) {
        & $FallbackPy -m venv .venv
    }

    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Host "[CVIQ] No .venv found. Install Python 3.12 and create it manually:"
        Write-Host "  python -m venv .venv"
        Write-Host "  .\.venv\Scripts\Activate.ps1"
        Write-Host "  pip install -r requirements.txt"
        exit 1
    }
}

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
