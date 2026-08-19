---
name: code-review
description: Perform defect-focused read-only review of CVIQ changes, diffs, surrounding code, regressions, and missing tests. Use before declaring implementation complete.
---

# Code Review

Review the actual diff and its callers, consumers, persistence effects, and tests. Prioritize correctness, data loss, security, API compatibility, error handling, concurrency, user-visible regressions, and missing coverage.

Report findings first with severity, file and line, impact, and remediation direction. Do not turn preferences into defects. If no findings exist, state residual risks and testing gaps.
