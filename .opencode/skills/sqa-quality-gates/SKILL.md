---
name: sqa-quality-gates
description: Apply CVIQ evidence-based quality gates across requirements, tests, browser checks, accessibility, security, exports, and documentation. Use for SQA and release decisions.
---

# SQA Quality Gates

A change is ready only when:
- Acceptance criteria are mapped to evidence.
- Automated tests pass or documented environment limitations exist.
- Changed browser flows are verified at relevant desktop and mobile sizes.
- Essential workflows remain accessible.
- Code and security reviews have no unresolved blockers.
- PDF and DOCX exports remain deterministic, selectable, and ATS-extractable when affected.
- No credentials, real `.cvmod` data, or generated artifacts were exposed.
- Documentation accurately states what was verified.

Block critical and high-impact defects, broken primary flows, data loss, secret exposure, inaccessible essential tasks, and unverifiable release claims.
