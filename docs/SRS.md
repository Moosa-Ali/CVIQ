# CVIQ — Software Requirements Specification (SRS)

**Version:** 3.0
**Status:** Approved (Phase 1 of SDLC) — v1 implementation complete (2026-08-04); v2 superseded v1 — parse session store, vision fallback for scanned PDFs (2026-08-08); v2.1 superseded v2 — Markdown CV extraction for the AI, optional JD / dual-mode analysis, layout & rename suggestions, gaps report, no-fabrication guarantees (2026-08-09); **v2.2 superseded v2.1** — PDF and DOCX uploads accepted, format-agnostic Markdown analysis, per-format fidelity editing with a labeled regenerated-export fallback (2026-08-09); **v3.0 supersedes v2.2 — content/presentation separation, structural rewrite** — a single canonical CV JSON schema is the only object the AI reads and writes; uploads are parsed and LLM-classified into that schema (with confidence flags), not kept as editable files; CV designs are a one-time-converted HTML/CSS (Jinja2) template layer; all PDF/DOCX output is produced by a deterministic, AI-free rendering pipeline (Jinja2 → PyMuPDF Story / python-docx); a JD parser produces structured `JDProfile`; a gap-analysis engine returns structured diffs (deterministic + semantic); suggestions are structured diffs (`field_path`) applied by patching the JSON then re-rendering; fidelity-preserving in-place editing of uploaded files is REMOVED (2026-08-10); **v3.1 supersedes v3.0 — UX remediation** (2026-08-11): `reorder`/`rename` suggestions are now APPLIED (item- and section-level) and `layout` suggestions are no longer generated; Chat 2.0 history-aware assistant with applicable proposed edits; `section_order`/`section_titles` added to the canonical schema and honored by all renderers; unified template catalog (builtin + gallery) with a server-rendered live preview; library overwrite + metadata; upload/LLM safeguards (`/api/health`, size limits, friendly errors, unified session expiry)
**Date:** 2026-08-10

> **Revision (v3.1, 2026-08-11):** This is an implementation-status revision of v3.0 — no
> requirements were removed. The UX remediation is complete and verified (smoke suite green,
> `ALL SMOKE TESTS PASSED`, runtime API sanity passed). The affected FRs below carry
> "(Implemented 2026-08-11)" annotations; new FRs (FR-3.7, FR-5.7, FR-7.7, FR-8.5) document
> the added API surface (15 MB upload guard is FR-3.7, chat 2.0 contract FR-5.7, live
> preview FR-7.7, health FR-8.5, library overwrite/metadata FR-6.4 annotation).

---

## 1. Introduction

### 1.1 Purpose
This document specifies the requirements for **CVIQ**, a single-user web
application that lets a user create a résumé/CV from scratch using modern,
ATS-friendly designs, or upload an existing CV (PDF or DOCX) and optimize it
against a target Job Description (JD). The product is built on a
**content/presentation separation**: every CV is a single canonical JSON object
(the only thing the AI ever reads or writes), and every PDF/DOCX the user
downloads is rendered deterministically from that object through an HTML/CSS
template layer — no AI, no in-place editing of uploaded files. LLM analysis
targets the canonical object — JD-specific when a JD is provided, otherwise a
generic ATS-friendliness assessment — and produces structured, reviewable
suggestions (diffs) the user previews and applies; applying a suggestion patches
the canonical JSON and re-renders the updated PDF/DOCX through the same
deterministic pipeline. An uploaded CV is used only as input evidence: it is
parsed and classified into the canonical schema, never edited in its original
form.

### 1.2 Scope
In scope:
- Create a CV from scratch via a structured, guided form using multiple
  ATS-friendly designs (an HTML/CSS template layer).
- Upload an existing CV (PDF or DOCX; TXT and any other type are rejected with a
  clear error), extract its text and layout, and **classify** it into the
  canonical CV schema (`CVData`), surfacing low-confidence fields for user
  confirmation.
- Optionally enter/paste a Job Description; parse it into a structured
  `JDProfile` for keyword/ATS analysis.
- LLM-powered analysis of the canonical CV against the JD (JD mode) or as a
  generic ATS-friendliness assessment when no JD is provided (generic mode).
- Automatic gap analysis — deterministic rules (no AI) plus optional semantic
  (LLM) checks — returning structured diffs.
- LLM-generated, editable suggestions, each a structured diff (`field_path` +
  `rationale`) that patches the canonical JSON; diff preview before apply.
- Rich in-browser CV editor with live preview (all fields incl.
  `custom_sections`).
- Deterministic, AI-free export of the canonical CV to PDF and DOCX through the
  template layer (multi-page PDF, letter size).
- Configuration of the LLM provider (AWS Bedrock or OpenRouter API key).

Out of scope (v3.0):
- Multi-user accounts/authentication.
- Cloud storage / collaboration.
- Automated direct application submission.
- Server-side persistence of full CV history beyond a local library (the parse
  session store is memory-only with a ~30 min TTL and is not persistence).
- Guaranteed job placement or guaranteed ATS ranking.
- Non-PDF/DOCX uploads — DOC/TXT and any other extension — are rejected at parse
  time (uploads accept PDF and DOCX only; PDF/DOCX export for from-scratch and
  upload-derived CVs remains fully supported, FR-7).
- Direct image uploads / rasterization of `.png`, `.jpg`, or other graphic CVs.
- **In-place editing of uploaded files — REMOVED in v3.0.** Uploaded PDFs/DOCX
  are input-only: they are parsed/classified into the canonical schema and are
  never edited, overlaid, or "preserved"; every final PDF/DOCX is freshly
  rendered from the canonical JSON (FR-7, FR-12). The v2.2 fidelity-export
  pipeline (`/api/export/preserved`, preserve/overlay/fill modules) no longer
  exists.

### 1.3 Definitions / Acronyms
- **CV / Résumé**: the document produced and edited by the app.
- **JD (Job Description)**: the target job posting text used for tailoring.
- **ATS (Applicant Tracking System)**: software that parses and ranks CVs.
- **LLM**: Large Language Model used for analysis, classification, and writing.
- **Canonical CV schema / `CVData`**: the single source of truth for every CV —
  one JSON object with `personal`, `summary`, `experience[]`, `education[]`,
  `skills[]` (category + skills), `projects[]`, `certifications[]`,
  `languages[]`, and an extensible `custom_sections[]` (title + bullets). The AI
  reads and writes ONLY this object — never raw file bytes (FR-3.3).
- **Parsing**: extracting text and layout from an uploaded PDF (PyMuPDF text
  blocks with page/Y coordinates) or DOCX (python-docx paragraphs with Word
  styles), producing an intermediate structure (FR-3.2).
- **Classification**: the LLM step that maps the extracted structure/blocks into
  the canonical schema, validated with Pydantic before storing; a heuristic
  fallback runs when no LLM is configured. Scanned PDFs are classified from page
  images via vision (FR-3.3). The `classification` response source is `"llm"` or
  `"heuristic"` only; vision use is signaled separately by `image_mode` (FR-3.4).
- **Confidence flag**: a low-confidence / needs-review marker produced by
  classification: `{field_path, level, reason}` (e.g.
  `experience[0].bullets[1]`); surfaced in the UI for user confirmation
  (FR-3.5).
- **Suggestion**: a single, actionable, editable edit to the canonical CV,
  represented as a **structured diff** with a `field_path` (e.g.
  `experience[0].bullets[1]`) and a `rationale`. Types: `rewrite` | `add` |
  `remove` | `reword` | `keyword` | `reorder` | `rename`. Applying a suggestion
  patches the canonical JSON (including `custom_sections`) and the updated
  PDF/DOCX is produced only by re-running the rendering pipeline (FR-5).
- **Gap / `GapDiff`**: a structured finding from the gap-analysis engine:
  `{field_path, issue, suggested_value, rationale, kind:
  deterministic|semantic, severity}` — e.g. a missing section, an
  ATS-breaking element, or a missing JD keyword (FR-9).
- **`JDProfile`**: the structured parse of a Job Description —
  `{role_title, required_skills, nice_to_have_skills, must_have_keywords,
  seniority_signals, requirements}` — produced by the JD parser (LLM forced into
  JSON, deterministic keyword fallback) (FR-13).
- **Tailored CV**: the canonical CV JSON after applying accepted suggestions.
- **HTML/CSS template layer**: the CV designs converted one time into
  Jinja2+CSS print templates under `app/templates_html/` (modern, classic,
  minimal, awesome-cv, deedy-resume, cvresume, universal-resume, newfuture-cv;
  other gallery designs map to a fallback template). Templates are stored
  separately from user data and bind to every canonical field (FR-14).
- **Deterministic rendering**: the AI-free pipeline `CVData → HTML (Jinja2,
  escaped) → PDF (PyMuPDF Story, multi-page, letter)` and
  `CVData → DOCX (python-docx, schema-driven)`; rendering never calls the LLM
  and is unit-testable (FR-7).
- **Session ID**: identifier returned by parse for a bounded, memory-only
  (~30 min TTL) server-side cache of the parsed upload context (text, structure,
  classified `CVData`, confidence flags, page images), reused by
  analyze/tailor/gaps calls (FR-10).
- **Vision path**: LLM classification/analysis of a scanned (image-based) PDF
  from rendered page images when the PDF has no meaningful extractable text
  (FR-11).
- **Markdown CV document**: the layout-aware Markdown rendering of the canonical
  CV schema — `# Name`, `## <section heading>`, `- bullet` lines, plus an
  `## OBSERVED LAYOUT` subsection carrying parse-time layout evidence (detected
  sections in actual reading order). This is the text form the LLM receives for
  classify/analyze/tailor prompts instead of raw file bytes (FR-3.6).
- **Gaps**: structured findings (GapDiffs) from the gap-analysis engine
  (FR-9), surfaced in the analysis report (FR-4.3) as an informational summary
  distinct from `suggestions` (FR-5).
- **Dual mode**: analysis runs in JD mode (a JD is provided — keyword/ATS fit,
  driven by `JDProfile`) or generic mode (no JD — ATS-friendliness assessment of
  the CV on its own merits) (FR-4.1).

### 1.4 Users
A single end-user (job seeker) who manages their own CV locally. No accounts.

---

## 2. Overall Description

### 2.1 Product Perspective
- Web app; FastAPI backend, vanilla JS/CSS single-page frontend served by the backend.
- LLM provider is user-configurable at runtime:
  - **AWS Bedrock**: via AWS credentials (access key / secret, region, model id).
  - **OpenRouter**: via an API key.
- The backend is structured around content/presentation separation: canonical
  `CVData` JSON (content) is produced/edited/analyzed; the HTML/CSS template
  layer plus the deterministic renderers (`services/export/render_html.py`,
  `pdf_render.py`, `docx_export.py`) turn it into presentation (PDF/DOCX).
- All artifact generation (analysis, tailoring, from-scratch drafting,
  classification, gap semantics, JD parsing) calls the configured provider;
  rendering and deterministic gap checks never do.

### 2.2 Product Functions (high level)
1. Configure and test the LLM provider.
2. Create a CV from scratch (guided form + AI drafting assist; all canonical
   fields incl. `custom_sections`).
3. Upload and parse an existing CV (PDF or DOCX — DOC/TXT and any other type
   rejected with a clear error), obtaining a classified canonical CV, confidence
   flags, and a `session_id`.
4. Parse a Job Description (optional) into a structured `JDProfile`.
5. Run analysis against a JD (optional) or as a generic ATS assessment.
6. Run gap analysis — deterministic rules always, semantic LLM checks when
   configured — returning structured diffs.
7. Review suggestions (structured diffs with `field_path` + `rationale`),
   preview them, and apply them to the working canonical CV; the updated
   PDF/DOCX is re-rendered deterministically.
8. Edit the canonical CV data directly with live preview.
9. Export the final canonical CV to PDF or DOCX via the deterministic rendering
   pipeline (optional template, fallback mapping).

### 2.3 Constraints
- Must run on Windows, single machine, from the existing repo.
- Must not require a Node build step (frontend served statically by FastAPI).
- Secrets (API keys / AWS credentials) stored on disk must not be committed or
  logged. Best-effort obfuscation with user opt-in.
- **The AI never writes to PDF/DOCX and never receives raw file bytes to edit.**
  The AI's only I/O is the canonical CV JSON (and the JD text for `JDProfile`).
  PDFs and DOCX are final rendering outputs only, produced by the deterministic,
  AI-free pipeline (FR-7, FR-12).
- **Every AI suggestion is a diff** with a `field_path` and `rationale`, shown
  to the user before apply; nothing is ever auto-applied silently (FR-5.5).
- Rendering (JSON → HTML → PDF/DOCX) is deterministic and unit-testable; it must
  work with zero AI calls and with no LLM configured (FR-7.6).
- Analysis must never invent contact info that was not supplied.
- The LLM must never fabricate content: it may only ADD KEYWORDS and
  REPHRASE/REWRITE existing content; it must NEVER invent experience, projects,
  employers, education, skills, dates, or new top-level sections (FR-12.4).
- No external Office tooling (LibreOffice, MS Office automation, pywin32) is
  required; DOCX export uses `python-docx`, PDF output uses PyMuPDF (`fitz.Story`
  page-stitching pipeline; a reportlab renderer remains only as a defensive
  legacy fallback).
- Vision behavior depends on a vision-capable LLM model; the default Claude
  Sonnet 4.x models provide this. If the configured model cannot accept image
  content blocks, the scanned-PDF path must fail with a clear error.

### 2.4 Assumptions & Dependencies
- Python 3.12 environment already present (`.venv`).
- External package additions: `boto3`, `python-docx`, `pymupdf>=1.24` (PDF page
  rendering for scanned-PDF vision + PyMuPDF Story for HTML→PDF),
  `pypdf`, `jinja2` (HTML template layer; ships with FastAPI), `httpx`
  (or `requests`). `reportlab` is retained only as a legacy fallback renderer.
- Default LLM models are vision-capable (Claude Sonnet 4.x).
- LLM services require the user to supply real credentials/keys; heuristic
  fallbacks (parse classification, JD keyword extraction, deterministic gap
  rules, rendering) operate without any LLM.

---

## 3. Functional Requirements

### FR-1 — LLM Provider Configuration
- **FR-1.1** The user shall be able to select the provider: `bedrock` or `openrouter`.
- **FR-1.2** For OpenRouter: supply an API key and optionally a custom model id
  (default `anthropic/claude-sonnet-4-6` or similar). Provide a health/test call.
- **FR-1.3** For Bedrock: supply `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_REGION`, and a model id (e.g. `anthropic.claude-sonnet-4-5-v2-0`). Provide
  a test call using the Bedrock Converse API.
- **FR-1.4** Configuration shall persist across restarts (local config file).
- **FR-1.5** The backend shall expose a non-streaming `chat(prompt)` interface
  common to both providers, with retry and clear error messages.
- **FR-1.6** Secrets shall be redacted when the config is read back by the UI.

### FR-2 — Create CV from Scratch
- **FR-2.1** Provide a multi-section guided form covering ALL canonical fields:
  Personal, Summary, Work Experience, Education, Skills, Projects,
  Certifications, Languages, and extensible **`custom_sections[]`** (arbitrary
  title + bullets).
  - *(Implemented 2026-08-11): the Build form AND the Editor render
    `custom_sections` (title + bullets, add/remove/reorder/duplicate); the
    `emptyCV()` default includes the field, and the live preview/export show
    them.*
- **FR-2.2** Offer multiple designs from the HTML/CSS template layer (FR-14),
  with different accent colors/layouts, all ATS-parseable (selectable,
  non-image text with standard headings; some designs use table/two-column
  layouts that remain extractable). Designs with no dedicated HTML
  template fall back per the gallery mapping (FR-14.2).
- **FR-2.3** Provide an "AI assist" action that drafts a professional summary
  and/or rewrites bullet points using the configured LLM; AI output is written
  back to the canonical JSON only.
- **FR-2.4** The created CV shall be stored as the canonical `CVData` JSON for
  editing/export — the same single source of truth used by uploads (FR-3).

### FR-3 — Upload & Parse to the Canonical Schema
- **FR-3.1** Accept uploads of `.pdf` and `.docx`. Reject `.doc`, `.txt`, and
  any other extension with a clear error at parse time — `parser.parse_file`
  raises a `ValueError` and `POST /api/cv/parse` returns HTTP 400 with that
  message. (PDF/DOCX export, FR-7, is unaffected — only upload input is
  restricted.)
- **FR-3.2** Extract text AND layout from the upload: PDFs via PyMuPDF (flat
  line dicts with `page`/Y coordinates, font size/name, bold) and DOCX via
  `python-docx` (paragraphs with Word styles/run formatting, tables as
  pipe-joined rows, headers/footers), producing an intermediate structure. For
  scanned/image-based PDFs with no meaningful extractable text (<≈40 chars total
  or pages with zero text), fall back to a vision path: render pages to PNG
  (~150 DPI) and have the LLM classify them (FR-11).
- **FR-3.3** An **LLM classification step** shall map the extracted blocks into
  the canonical `CVData` schema; the output is validated with Pydantic before
  storing. When no LLM is configured, a deterministic heuristic classifier shall
  be used instead. Scanned PDFs are classified from page images (vision). The
  resulting object is the canonical CV — subsequent analysis and editing operate
  ONLY on it, never on the uploaded bytes.
- **FR-3.4** Return the canonical structured data, the raw extracted text, a
  confidence indicator, page count, the `classification` source
  (`"llm"` | `"heuristic"`), a **`confidence_flags`** list
  (`{field_path, level, reason}` for low-confidence fields), `image_mode`
  (true when a scanned PDF was classified via vision — the signal that the
  vision path was used), and a `session_id` to the UI.
- **FR-3.5** Low-confidence fields (`confidence_flags`) shall be surfaced in the
  UI for user confirmation/correction before further AI-assisted steps.
- **FR-3.6** The parser shall render the canonical schema into the Markdown CV
  document — `# Name`, `## <section heading>`, `- bullet` lines, plus an
  `## OBSERVED LAYOUT` subsection carrying the parse-time layout evidence
  (detected sections in actual reading order; which standard sections were NOT
  detected). This Markdown (a serialization of the canonical object — not raw
  file bytes) is what the LLM receives for classify (FR-3.3), analyze (FR-4),
  gaps (FR-9), and tailor-suggest (FR-5) prompts. Implemented in
  `app/services/cv/markdown.py` (`cv_to_markdown` and structure-aware variants).
- **FR-3.7** *(Implemented 2026-08-11)* A 15 MB server-side upload cap guards
  `POST /api/cv/parse`: the file is read at most `cap + 1` bytes and oversized
  uploads return HTTP **413** with the message
  `File too large — maximum upload size is 15 MB.` (no unbounded buffering).

### FR-4 — Job Description & Analysis
- **FR-4.1** Accept JD text (paste/type) into the optimize flow. The JD is
  OPTIONAL: analysis supports dual mode — JD-specific or generic (no-JD).
- **FR-4.2** Run an LLM analysis from the Markdown CV document (FR-3.6) of the
  canonical CV, against the JD when provided (JD mode — keyword/ATS fit based on
  the parsed `JDProfile`, FR-13), or as a GENERIC ATS-friendliness assessment of
  the CV on its own merits — structure, section ordering, naming, formatting,
  contact completeness, action verbs, quantification, length (generic mode).
- **FR-4.3** Return a structured analysis containing:
  - An overall ATS-match score (0–100).
  - Matched keywords/phrases, and missing/under-represented keywords from the
    JD (empty lists in generic mode, where no JD was provided).
  - Section-level assessments (summary, experience, skills, education).
  - Free-form comments for human/AI/ATS screening.
  - `gaps` — the informational summary of the gap-analysis engine's findings
    (FR-9), human-readable and distinct from `suggestions` (FR-5). The full
    structured GapDiffs are available from `POST /api/cv/gaps` (FR-9.3).
  - `suggestions` — actionable suggestions surfaced in the UI with a one-click
    "Import N analysis suggestions" action into the Review step
    *(Implemented 2026-08-11)*.
  - `session_warning` — a structured warning string set by the route when a
    supplied upload `session_id` was missing/expired (never a 404);
    empty otherwise *(Implemented 2026-08-11)*.
- **FR-4.4** In JD mode, the analysis must reference concrete JD terms (from the
  `JDProfile`), not generic advice.

### FR-5 — Tailoring Suggestions (structured diffs)
- **FR-5.1** Generate an ordered list of suggestions. Each suggestion is a
  structured diff carrying:
  - `field_path` (JSON-ish path into the canonical CV, e.g.
    `experience[0].bullets[1]`, `personal.email`, `custom_sections[0].bullets[2]`).
  - `section`, `type` (`rewrite` | `add` | `remove` | `reword` | `keyword` | `reorder` | `rename`).
  - `original` and `suggested` values (where applicable).
  - `rationale` explaining the change (shown in the UI before apply).
  - `priority` (high/medium/low) and predicted impact.
  - For `reorder` type: `move_from`/`move_to`/`target_section` defining array
    movement within the canonical JSON.
  - *(Implemented 2026-08-11)* `reorder` and `rename` suggestions are now
    APPLIED: item-level reorder via `move_from`/`move_to` indices (up/down
    relative moves supported), section-level reorder via `target_section` +
    `move_to` of `up|down|top|bottom|N`; `rename` updates `section_titles`
    (standard sections) or a custom-section title. `layout` suggestions are no
    longer generated (removed from prompts) and are a parseable no-op on apply.
    Apply is idempotent: append-style suggestions dedupe identical items and
    re-applying an unchanged set yields `applied == 0` with an unchanged CV.
- **FR-5.2** The user shall preview each suggestion as a diff
  (before/after with `rationale`) and may accept, reject, or edit it before
  applying (FR-6.5).
- **FR-5.3** Applying suggestions patches the canonical JSON — including
  `custom_sections` — and the updated PDF/DOCX is produced ONLY by re-running
  the deterministic rendering pipeline (FR-7); no alternative file-editing path
  exists.
- **FR-5.4** The user may regenerate suggestions or run further tailored
  rewrites on selected content.
- **FR-5.5** The app shall never auto-apply changes without explicit user
  action (diff preview first).
- **FR-5.6** Placement/analysis: the AI shall analyze the canonical CV's section
  order, section-naming quality, and use of standard ATS-friendly headings, and
  suggest improvements via `reorder`/`rename` suggestions. In generic (no-JD)
  mode, no `keyword` suggestions are emitted.
  - *(Implemented 2026-08-11)* Section-order and heading-rename suggestions
    apply; every renderer (all 8 Jinja2 templates + the builtin fallback)
    honors `CVData.section_order` (default order when empty) and
    `CVData.section_titles` (heading overrides) via the `stitle(section_id,
    default)` template global; output is byte-identical when both are empty.
    Sections absent from a custom order are appended in default order, so no
    content is ever hidden.
- **FR-5.7** *(Implemented 2026-08-11 — Chat 2.0)* Free-form assistant chat:
  `POST /api/cv/tailor/chat` accepts `{cv, job_description?, session_id?,
  target?: {section, field?, index?}, messages: [{role: "user"|"assistant",
  content}]}` and returns `{reply, proposed_edits: [Suggestion],
  session_warning}`. The backend sends the full conversation (last ~20
  messages) plus the current CV, JD and edit `target`; the model responds with
  strict JSON and `proposed_edits` are applicable through the existing
  `/api/cv/tailor/apply` machinery. The scanned-PDF vision path is preserved
  (page images attached to the first user message). Expired/missing sessions
  NEVER 404 — the request degrades with a structured `session_warning`. The
  legacy `{segment, context}` body is still accepted (deprecated): it
  synthesizes one user message; a request with neither `messages` nor
  `segment` returns 400.

### FR-6 — CV Editor & Live Preview
- **FR-6.1** Provide an editor for all canonical CV sections (incl.
  `custom_sections`) backed by the structured JSON.
  - *(Implemented 2026-08-11)* The Editor covers every canonical field incl.
    `custom_sections` (title + bullets, add/remove/reorder/duplicate),
    ↑/↓ reorder on all items and bullets, collapsible cards with jump-nav,
    auto-growing bullet textareas, and inline field validation (email/URL/date,
    "Present/current" date toggle, skills chip editor).
- **FR-6.2** Provide a live rendered preview of the selected design.
  - *(Implemented 2026-08-11)* The preview is SERVER-RENDERED: `POST
    /api/export/preview` (FR-7.7) returns the real Jinja2 HTML for the current
    `CVData` + template id, shown in a debounced iframe with zoom / fit-width
    controls and an A4 page frame — WYSIWYG vs. the downloaded PDF.
- **FR-6.3** Show suggestion status (applied/rejected) alongside affected items.
  - *(Implemented 2026-08-11)* Applied cards are locked ("Applied ✓", actions
    disabled), accepted/applied/rejected states carry text badges + colors,
    apply-accepted filters already-applied ids, and a stale-suggestion warning
    appears after CV edits.
- **FR-6.4** Persist the working CV locally (library) so the user can reopen it —
  always as canonical JSON.
  - *(Implemented 2026-08-11)* `POST /api/library` accepts `{name, cv, meta}`
    (meta returned by list/get); **`PUT /api/library/{cid}`** overwrites an
    existing entry (name/cv/meta, id preserved, 404 if missing); a 5 MB
    per-entry guard returns **413** for oversized CV payloads; duplicate names
    trigger a client-side warning/"Save as new vs overwrite" prompt
    (backend `library.name_exists` helper).
- **FR-6.5** Provide a diff/preview UI step that shows suggestions
  (before/after + `rationale`) before they are applied; apply is explicit and
  patches the canonical JSON (FR-5.2, FR-5.5).
  - *(Implemented 2026-08-11)* The Review step adds Accept all / Reject all,
    priority + section filters, a pending-count badge, and analysis-report
    suggestions importable in one click; ATS Check (validate) and Re-analyze
    (score delta) live in the Editor toolbar.

### FR-7 — Deterministic Export (render pipeline, no AI)
- **FR-7.1** Export the current canonical CV to **PDF** (server-side): `CVData →
  HTML (Jinja2, escaped) → PDF via PyMuPDF `fitz.Story` — multi-page, letter
  size, 0.5-in margins; deterministic (same input → same bytes). A builtin
  single-column modern fallback renderer is used if a template file is missing.
- **FR-7.2** Export to **DOCX** (Word): `CVData → python-docx`, schema-driven;
  section headings, bullet lists, skills lines, and `custom_sections[]` all
  rendered; base font Calibri, ~0.5-in margins.
- **FR-7.3** `POST /api/export/pdf|docx` accept an optional `template` parameter
  selecting the HTML template id; `POST /api/templates/{tpl_id}/export/pdf|docx`
  renders the gallery design's mapped HTML template (render-mode, FR-14). All
  output is freshly rendered from the canonical JSON.
  - *(Implemented 2026-08-11)* `template` accepts ANY catalog id (FR-14.5):
  `POST /api/export/pdf|docx` and `/api/templates/{id}/export/*` share the same
  resolution path (`resolve_render_template`), so builtin
  (`modern`/`classic`/`minimal`) and gallery ids are interchangeable. DOCX
  export colors the name + section headings with the CV's accent (the DOCX
  remains an honest generic schema-driven layout).
- **FR-7.4** Exports must remain ATS-parseable: selectable, non-image text with
  standard headings (some templates use table/two-column layouts that stay
  extractable).
- **FR-7.5** Both exports render `custom_sections` and every other canonical
  field; empty sections are omitted.
- **FR-7.6** Rendering must work with zero AI calls (no LLM configured) and be
  unit-testable; the AI is never involved in PDF/DOCX generation (FR-12.1).
- **FR-7.7** *(Implemented 2026-08-11)* **Live preview endpoint:**
  `POST /api/export/preview` `{cv, template?}` → `{html}` — the full standalone
  HTML document rendered by the real template (catalog id, same resolution path
  as FR-7.3), served with `Cache-Control: no-store`. Powers the Editor's live
  preview iframe (FR-6.2); no LLM involvement.

### FR-8 — UI/UX
- **FR-8.1** Modern, responsive, intuitive SPA with clear primary flows: Build,
  Optimize, Editor/Preview, Export, Settings.
  - *(Implemented 2026-08-11)* The SPA lands on a **Home** dashboard; nav order
    is Home / Build / Optimize / Editor / My CVs / Settings (Settings last).
    Unknown hashes redirect home with a toast, and Build state persists across
    navigation.
- **FR-8.2** Guided onboarding when no LLM is configured yet (heuristic
  classification and deterministic analysis remain available).
  - *(Implemented 2026-08-11)* A global dismissible banner ("AI features need a
    provider") appears on every view until configured, and AI-only actions are
    gated (`gateLLM`): they explain the requirement and route to Settings
    instead of failing at click time.
- **FR-8.3** Loading states and helpful error messages for all async actions.
  - *(Implemented 2026-08-11)* Skeleton loaders for primary waits; LLM errors
    mapped to friendly text (401 → invalid API key, 429 → rate limited,
    timeouts) server-side (`friendly_llm_error`, applied across
    analyze/suggest/chat/assist/gaps) and client-side.
- **FR-8.4** Surface parse indicators wherever relevant (parse results, editor):
  `classification` (`llm`|`heuristic`), `confidence_flags`
  (`{field_path, level, reason}`), and `image_mode` (true when a scanned PDF was
  classified via vision).
- **FR-8.5** *(Implemented 2026-08-11)* **Health probe:** `GET /api/health` →
  `{status: "ok"}` — liveness check with no LLM involvement (used by launchers /
  operational tooling).

### FR-9 — Gap Analysis Engine
- **FR-9.1** Provide a two-layer gap-analysis engine
  (`app/services/cv/gaps.py`):
  - **Deterministic checks** (rules, no AI): missing sections or contact info;
    ATS-breaking elements (images, tables, multi-column layouts, non-standard
    headings); unquantified bullets.
  - **Semantic checks** (LLM, only when configured): missing skills vs. the
    `JDProfile` (FR-13), weak phrasing, unquantified achievements.
- **FR-9.2** Both layers return structured diffs — `GapDiff`
  `{field_path, issue, suggested_value, rationale, kind:
  deterministic|semantic, severity}` — targeting the canonical JSON
  (including `custom_sections`).
- **FR-9.3** Expose `POST /api/cv/gaps` (body `{cv, job_description?,
  session_id?}`) returning `{jd_profile, deterministic, semantic, mode}` —
  `deterministic` and `semantic` are `[GapDiff]` lists, `jd_profile` is the
  parsed `JDProfile` (or null), and `mode` is `"jd"` | `"generic"`. The
  deterministic layer runs without an LLM; the semantic layer requires a
  configured provider (clear error otherwise).
  - *(Implemented 2026-08-11)* Response adds `session_warning` (missing/expired
    session degrades, never 404); `jd_profile` is surfaced in the UI as a
    summary card inside the unified gaps panel.
- **FR-9.4** Gap findings are informational and actionable: a summary appears in
  the analysis report (FR-4.3), and each GapDiff may be converted into a
  suggestion (FR-5) or applied directly as a diff.

### FR-10 — Parse Session Store (upload context)
- **FR-10.1** `POST /api/cv/parse` shall return a `session_id` together with
  `{cv, text, confidence, page_count, classification, confidence_flags,
  image_mode}` (FR-3.4).
- **FR-10.2** The server shall cache per session: extracted text, extracted
  structure, the classified canonical `CVData`, confidence flags, and page
  images (scanned PDFs only).
- **FR-10.3** The cache is in-memory only, single-user, bounded, with a TTL of
  ≈30 minutes; nothing is written to disk. It is NOT persistence and never
  provides file bytes for editing — the canonical JSON is the working object.
- **FR-10.4** `analyze`, `tailor/suggest`, `tailor/chat`, and `gaps` endpoints
  shall accept an optional `session_id` to reuse the parsed/classified upload
  context (forward-compatible body shape: `session_id: str = ""`).
  - *(Implemented 2026-08-11 — unified expiry semantics)* All four endpoints
    resolve sessions through the same `_resolve_session` helper: a missing or
    expired session NEVER 404s — the request degrades to the self-contained
    body and a structured `session_warning` is returned in the response
    (`AnalysisReport.session_warning`, and `session_warning` on suggest/gaps/
    chat). For scanned-PDF sessions the vision path is preserved (page images)
    while the session is alive.

### FR-11 — Vision Support, Classification & Detailed Prompts (scanned PDFs)
- **FR-11.1** LLM messages may contain content blocks: text blocks plus image
  blocks (base64 PNG). Provider translation: OpenRouter uses the OpenAI
  `image_url` data-URI format; Bedrock Converse uses the `image` block.
- **FR-11.2** Text-based PDF and DOCX uploads send the ENTIRE Markdown CV
  document (FR-3.6) to the model as text for classification/analysis — explicitly
  no images, never raw bytes.
- **FR-11.3** Image-based (scanned) PDFs — no meaningful extractable text
  (<≈40 chars total or zero-text pages) — shall render every page to PNG
  (~150 DPI) and send the page images to the model for vision classification
  into the canonical schema (FR-3.3).
- **FR-11.4** All LLM prompts, especially the classification/vision prompt,
  shall explicitly specify exactly what the model must do. The vision prompt
  must be a dedicated, detailed prompt instructing the model to: (1) transcribe
  the visible CV content faithfully into the canonical JSON schema; (2) mark
  low-confidence fields as confidence flags; (3) with a JD provided, assess
  keyword/ATS fit and positioning — without one, evaluate generic
  ATS-friendliness (dual mode, FR-4.1); (4) emit suggestions in the same schema
  as text-based mode. It must not be a reword of the text prompt. The prompt
  must include the no-fabrication and constraints (FR-12).
- **FR-11.5** Non-PDF/DOCX uploads — DOC/TXT and any other extension, plus
  direct image uploads (`.png`/`.jpg`) — are out of scope: reject with a clear
  error message at parse time (FR-3.1).
- **FR-11.6** The UI shall show `image_mode` (true when vision is used) and the
  `classification` source.

### FR-12 — AI Constraints (non-negotiable)
- **FR-12.1** The AI NEVER writes to PDF/DOCX. Final documents exist only as
  outputs of the deterministic rendering pipeline (FR-7).
- **FR-12.2** The AI NEVER receives raw file bytes to edit. Its input is the
  canonical CV JSON (as the Markdown CV document, FR-3.6) and, for
  scanned-PDF classification, page images — which are used for transcription
  only.
- **FR-12.3** Every suggestion produced by the AI is a structured diff with a
  `field_path` and `rationale`, shown to the user before apply; nothing is
  auto-applied silently (FR-5.5).
- **FR-12.4** No-fabrication guarantee: all analyze (FR-4), tailor-suggest
  (FR-5), classification/vision (FR-11), and rewrite prompts shall explicitly
  constrain the model — it may only ADD KEYWORDS and REPHRASE/REWRITE existing
  content and must NEVER invent experience, projects, employers, education,
  skills, dates, or new top-level sections.

### FR-13 — JD Parser (structured `JDProfile`)
- **FR-13.1** When a JD is provided, the backend shall parse it into a
  structured `JDProfile` `{role_title, required_skills, nice_to_have_skills,
  must_have_keywords, seniority_signals, requirements}` via an LLM prompt
  forced into JSON (validated with Pydantic).
- **FR-13.2** When no LLM is configured, a deterministic keyword-extraction
  fallback (token/section heuristics) shall produce the `JDProfile`.
- **FR-13.3** The `JDProfile` feeds keyword matching in analysis (FR-4.4), the
  semantic gap checks (FR-9.1), and keyword suggestions (FR-5.6).
  - *(Implemented 2026-08-11)* `JDProfile` is surfaced in the UI: the gaps panel
    shows a "We understood the job as …" summary card (role title,
    must-have/nice-to-have skills, keywords) alongside the structured
    `GapDiff`s.

### FR-14 — HTML/CSS Template Layer
- **FR-14.1** CV designs (gallery) shall be converted one time into Jinja2+CSS
  print templates under `app/templates_html/`: currently `modern`, `classic`,
  `minimal`, `awesome-cv`, `deedy-resume`, `cvresume`, `universal-resume`,
  `newfuture-cv`; all other gallery designs map to a fallback template
  (`modern`/`minimal`/`classic` per the gallery mapping).
- **FR-14.2** Gallery metadata (`app/services/templates.py`, `scan_templates`)
  shall expose for each design: `render_template` (the HTML template id used to
  render final files) and `converted` (true when the design has a dedicated HTML
  template, false for fallback-mapped designs).
- **FR-14.3** Templates shall bind to every canonical field
  (incl. `custom_sections[]`) and be stored separately from user data
  (`app/templates_html/`, a data directory, not a package). User content is
  always escaped (Jinja2 autoescape / `html.escape`).
- **FR-14.4** A missing/absent template file must never break rendering: the
  builtin single-column modern fallback renderer (`render_html._render_fallback`)
  is used instead, so export never raises (FR-7.1).
- **FR-14.5** *(Implemented 2026-08-11 — unified catalog)* `GET /api/templates`
  returns ONE catalog: the 3 builtin entries
  (`id` modern/classic/minimal, `source: "builtin"`) followed by every gallery
  design (`source: "gallery"`, all previous keys kept). The Editor and Export
  consume the same catalog, and friendly labels ("Dedicated design" /
  "Closest match") replace leaked `converted`/fallback jargon in the picker.

---

## 4. Non-Functional Requirements

- **NFR-1 Performance**: analysis/tailoring responses should stream or return
  within reasonable time; non-blocking UI. Rendering (PDF/DOCX) completes
  without LLM latency.
- **NFR-2 Security**: never log keys/credentials; redact in GET responses; no
  secrets in the repo; uploaded file bytes stay in the memory-only session store
  and are never persisted or sent to the LLM as edit input.
- **NFR-3 Reliability**: graceful errors with actionable messages when the LLM
  provider is misconfigured or down — heuristic classification, deterministic
  gap checks, and rendering must still work (FR-3.3, FR-9.1, FR-7.6).
- **NFR-4 Maintainability**: modular backend (llm, parse/classify, analyze,
  gaps, tailor, render, export, routes), documented data model; the render
  pipeline is a pure, stateless JSON→bytes path.
- **NFR-5 Testability**: pure functions for parse/classify/analyze/gaps/tailor/
  render/export unit-testable; deterministic render tests (same input → same
  bytes); JSON→render→parse-back round-trip; gap-rule tests without an LLM;
  classification fallback tests; smoke tests for endpoints.

---

## 5. Acceptance Criteria (Summary)
1. A user can configure OpenRouter or Bedrock and verify connectivity (FR-1).
2. A user can build a CV from scratch — including `custom_sections` — and export
   it to PDF and DOCX via the deterministic pipeline (FR-2, FR-7, FR-14).
3. A user can upload a PDF or DOCX CV and get a classified canonical CV with
   `confidence_flags` surfaced for review; TXT and any other-format uploads are
   rejected with a clear error (FR-3, FR-6).
4. Given a CV and an optional JD, the app returns a scored analysis (JD-specific
   via `JDProfile` or generic ATS-friendliness) with actionable, editable
   suggestions (FR-4, FR-5, FR-13).
5. Gap analysis returns structured diffs with `field_path`/`rationale`
   (`kind: deterministic|semantic`, `severity`); the deterministic layer works
   with no LLM configured (FR-9).
6. Every AI suggestion is shown as a diff before apply; applying patches the
   canonical JSON and the updated PDF/DOCX is re-rendered — nothing is
   auto-applied silently (FR-5, FR-6.5, FR-12.3).
7. PDF/DOCX exports are deterministic, AI-free, ATS-parseable, multi-page
   (PDF), and render `custom_sections`; the optional `template` parameter and
   `/api/templates/{id}/export/pdf|docx` render-mode endpoints work, with a
   fallback template for unconverted designs (FR-7, FR-14).
8. A user can upload a scanned PDF, and the AI classifies it from page images
   using a dedicated, detailed vision prompt, producing the canonical schema
   with confidence flags (FR-3, FR-11).
9. No in-place editing exists: uploaded files are never edited/overlaid/
   "preserved"; the AI never writes to PDF/DOCX and never fabricates content
   (FR-7, FR-12).
10. A parsed CV round-trips: the classified canonical JSON re-renders and can be
    parsed back to a consistent object (FR-3, FR-7, NFR-5).
11. *(Implemented 2026-08-11 — UX remediation acceptance)* A user can preview
    the live server-rendered design while editing, converse with the
    history-aware Assistant and apply its proposed edits, reorder/rename
    sections (including custom sections), re-analyze to see a score delta, and
    save/overwrite CVs with metadata from My CVs (FR-5.7, FR-6, FR-7.7,
    FR-14.5); drafts autosave locally and survive refresh (FR-6.4).