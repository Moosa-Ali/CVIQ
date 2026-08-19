---
name: windows-fastapi-runtime
description: Run and validate the CVIQ FastAPI application on Windows using its explicit virtual-environment interpreter and safe disposable data. Use for local runtime and browser verification.
---

# Windows FastAPI Runtime

Do not assume `python` is on PATH. Use:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use reload only for development when appropriate. Browser QA must set a disposable `CVMOD_DATA_DIR`, avoid real credentials, bind locally, and stop the process after verification. The smoke test is:

```powershell
$env:PYTHONPATH = "C:\D-Drive\Work\CVIQ"
& ".\.venv\Scripts\python.exe" tests\smoke_test.py
```
