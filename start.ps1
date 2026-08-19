$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

# Reuse an existing virtual environment if present.
if (Test-Path $VenvPython) {
    & $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
    exit
}

# ------------------------------------------------------------------
#  Locate a usable Python interpreter (3.12+ preferred).
#  Ordered: python -> python3 -> py -3.12 -> py -3
#  Each candidate is probed with a real Python command to skip
#  Microsoft Store stubs that only open the Store.
# ------------------------------------------------------------------
$PyExe = $null
$PyArgs = @()

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    & python -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $PyExe = "python"
    }
}

if (-not $PyExe) {
    $python3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($python3) {
        & python3 -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $PyExe = "python3"
        }
    }
}

if (-not $PyExe) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & py -3.12 -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $PyExe = "py"
            $PyArgs = @("-3.12")
        } else {
            & py -3 -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $PyExe = "py"
                $PyArgs = @("-3")
            }
        }
    }
}

if (-not $PyExe) {
    Write-Host "[CVIQ] Python 3.12+ was not found on this system."
    Write-Host "[CVIQ] Please install Python 3.12+ from https://www.python.org/downloads/"
    Write-Host "[CVIQ] and make sure it is available on your PATH, then run this script again."
    Write-Host "Press any key to continue..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

# ------------------------------------------------------------------
#  Create the virtual environment and install dependencies.
# ------------------------------------------------------------------
Write-Host "[CVIQ] Creating virtual environment..."
if ($PyArgs.Count -gt 0) {
    & $PyExe @PyArgs -m venv .venv
} else {
    & $PyExe -m venv .venv
}
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) {
    Write-Host "[CVIQ] Failed to create the virtual environment. See the errors above."
    Write-Host "Press any key to continue..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

Write-Host "[CVIQ] Installing dependencies..."
& $VenvPython -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[CVIQ] Failed to install dependencies. See the errors above."
    Write-Host "Press any key to continue..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

Write-Host "[CVIQ] Starting server at http://127.0.0.1:8000"
& $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000