---
name: team-orchestration
description: Coordinate the CVIQ engineering hierarchy, delegate work by ownership, and collect evidence before reporting completion. Use for multi-agent requests and cross-cutting changes.
---

# Team Orchestration

Use `engineering-manager` as the front door. Classify the request, identify the smallest set of specialists, and delegate read-only analysis before write work.

Rules:
- Never run parallel writers against overlapping files.
- Give each write task a file ownership boundary and an acceptance criterion.
- Keep planning, review, QA, and SQA source-read-only.
- Stop for destructive actions, secrets, ambiguous product behavior, or scope expansion.
- Do not report completion without test or verification evidence.

Required report sections: outcome, delegated work, changed files, verification evidence, risks, and follow-up work.
