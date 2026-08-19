---
name: persistence-and-data
description: Safely design CVIQ file-backed persistence, in-memory upload sessions, serialization, redaction, and data integrity. Use for `.cvmod`, library, config, or model-shape changes.
---

# Persistence And Data

CVIQ uses `.cvmod/` for local config and saved CV data, while upload context uses an in-memory TTL store. Never read real `.cvmod` contents or expose secrets.

Before changing a persisted shape, define ownership, atomicity, corruption behavior, redaction, environment overrides, and compatibility needs. Use temporary data directories in tests. Do not add a database without an explicit requirement.
