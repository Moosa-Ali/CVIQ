---
name: requirements-analysis
description: Analyze user goals, SRS requirements, edge cases, and acceptance criteria for CVIQ. Use when a request is ambiguous or changes user-visible behavior.
---

# Requirements Analysis

Read `docs/SRS.md`, `docs/design.md`, the affected UI, routes, services, and tests. Identify what is explicit versus inferred.

For each requirement define:
- Actor and goal.
- Trigger and preconditions.
- Normal flow.
- Empty, loading, validation, failure, and recovery states.
- Data and security implications.
- Observable acceptance criteria.

Ask only questions that block a safe implementation. Prefer documented existing behavior over invention.
