@echo off
set ROOT=%~dp0
cd /d "%ROOT%"

if exist ".venv\Scripts\python.exe" goto run

set FALLBACK_PY=C:\Users\moosa\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
if exist "%FALLBACK_PY%" (
  "%FALLBACK_PY%" -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo [CVIQ] No .venv found. Install Python 3.12 and create it manually:
  echo   python -m venv .venv
  echo   .\.venv\Scripts\Activate.ps1
  echo   pip install -r requirements.txt
  exit /b 1
)

:run
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
