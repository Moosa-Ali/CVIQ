---
name: codebase-analysis
description: Map CVIQ execution paths, ownership boundaries, dependencies, and regression risk. Use before changing unfamiliar backend, frontend, export, persistence, or LLM code.
---

# Codebase Analysis

Trace the actual path from frontend event to API route, service, persistence or export, and response rendering. Read adjacent tests and shared utilities.

Return exact file paths, symbols, data shapes, side effects, error behavior, and shared-file conflicts. Treat `AGENTS.md`, `docs/SRS.md`, and `docs/design.md` as authoritative project context.

Do not propose a new framework, database, build step, or persistence layer unless the requirement demands it.
