@echo off
set ROOT=%~dp0
cd /d "%ROOT%"

set PYTHON=
set PYTHON_ARGS=

rem Reuse an existing virtual environment if present.
if exist ".venv\Scripts\python.exe" goto run

rem ------------------------------------------------------------------
rem  Locate a usable Python interpreter (3.12+ preferred).
rem  Ordered: python -> python3 -> py -3.12 -> py -3
rem  Each candidate is probed with a real Python command to skip
rem  Microsoft Store stubs that only open the Store.
rem ------------------------------------------------------------------
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set PYTHON=python
)
if defined PYTHON goto found_python

where python3 >nul 2>nul
if not errorlevel 1 (
    python3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set PYTHON=python3
)
if defined PYTHON goto found_python

where py >nul 2>nul
if errorlevel 1 goto no_python

py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set PYTHON=py
    set PYTHON_ARGS=-3.12
    goto found_python
)

py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set PYTHON=py
    set PYTHON_ARGS=-3
    goto found_python
)

:no_python
echo [CVIQ] Python 3.12+ was not found on this system.
echo [CVIQ] Please install Python 3.12+ from https://www.python.org/downloads/
echo [CVIQ] and make sure it is available on your PATH, then run this script again.
pause
exit /b 1

:found_python
echo [CVIQ] Creating virtual environment...
%PYTHON% %PYTHON_ARGS% -m venv .venv
if errorlevel 1 goto venv_failed

if not exist ".venv\Scripts\python.exe" goto venv_failed

echo [CVIQ] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto install_failed

goto run

:venv_failed
echo [CVIQ] Failed to create the virtual environment. See the errors above.
pause
exit /b 1

:install_failed
echo [CVIQ] Failed to install dependencies. See the errors above.
pause
exit /b 1

:run
echo [CVIQ] Starting server at http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000