---
name: api-contracts
description: Keep CVIQ frontend, FastAPI routes, Pydantic models, errors, and exports aligned. Use when a change crosses the browser and backend boundary.
---

# API Contracts

Identify the request, response, status, error, and persistence contract before editing either side. Preserve existing route prefixes and stable field meanings. If a contract changes, update the backend, frontend consumers, tests, and documentation together.

Check missing sessions, malformed input, provider errors, empty results, streaming behavior, content types, download headers, and browser retry behavior. Do not hide a backend failure behind a generic successful response.
