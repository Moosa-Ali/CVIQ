---
name: fastapi-backend
description: Implement and review CVIQ FastAPI routes, Pydantic contracts, services, error handling, and backend tests. Use for app/routes and app/services changes.
---

# FastAPI Backend

Follow the existing router and service separation. Validate request inputs at the boundary, keep business logic in services, return stable response shapes, and preserve useful HTTP error semantics.

Check:
- Success, validation, missing-session, provider-failure, and export-failure paths.
- Request size and uploaded-file handling.
- Secret redaction and absence of credential logging.
- Deterministic behavior where AI is not required.
- Tests using `FakeLLM` and disposable data.

Use the explicit `.venv\Scripts\python.exe` interpreter for verification on Windows.
