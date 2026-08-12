# CVIQ — System Design Document

**Version:** 3.0
**Status:** Approved (Phase 2 of SDLC) — v1 implementation complete (2026-08-04); v2 superseded v1 (2026-08-08); v2.1 superseded v2 — Markdown CV extraction, optional-JD dual mode, layout/rename suggestions, gaps report, no-fabrication prompt constraints (2026-08-09); **v2.2 superseded v2.1** — PDF and DOCX uploads accepted, format-agnostic Markdown analysis, per-format fidelity editing with a labeled regenerated-export fallback (2026-08-09); **v3.0 supersedes v2.2 — content/presentation separation, structural rewrite** — canonical CV JSON as the single source of truth (AI reads/writes only this); uploads are parsed and LLM-classified into the schema with confidence flags; an HTML/CSS (Jinja2) template layer replaces template-code duplication and gallery fill; all PDF/DOCX output is deterministic and AI-free (Jinja2 → PyMuPDF Story / python-docx); JD parser (JDProfile), gap-analysis engine (deterministic + semantic), and suggestions-as-diffs (field_path/rationale, apply = patch JSON then re-render); fidelity-preserving in-place editing REMOVED (preserve/overlay/fill modules and `/api/export/preserved` deleted) (2026-08-10); **v3.1 supersedes v3.0 — UX remediation** (2026-08-11): `section_order`/`section_titles` added to the canonical schema and honored by all renderers; `reorder`/`rename` suggestions applied (item- and section-level; `layout` removed from prompts, no-op on apply); Chat 2.0 assistant contract (`{reply, proposed_edits, session_warning}`); unified template catalog + server-rendered live preview (`POST /api/export/preview`); library overwrite (PUT) + metadata; upload safeguards (15 MB / 5 MB caps, friendly LLM errors, `/api/health`, unified session expiry via `_resolve_session`)
**Date:** 2026-08-10

This document is the authoritative contract for the implementation phases
(backend and frontend). Deviations must be documented.

## Deviations from v1

- **Fidelity-preserving export for uploads.** v1 regenerated a generic
  single-column template from `CVData` on export. v2 edited the user's original
  uploaded file in place (DOCX: python-docx paragraph/run edits; PDF:
  coordinate-overlay redaction + text insert via PyMuPDF) and saved it under a
  new name. The generic template exporters were retained for the from-scratch
  (Build) path only.
- **Vision fallback only for scanned PDFs.** v2 never sends screenshots of
  text-based documents to the model. Page images are used only when a PDF has no
  meaningful extractable text (scanned, <≈40 chars total). Direct image uploads
  and TXT/other-extension uploads are out of scope and rejected with a clear
  error; DOCX uploads were rejected in v2.1 but accepted as of v2.2.
- **Session store added.** Parse caches upload context (bytes, text, structure,
  `CVData`, page images) in a bounded, in-memory, ~30 min TTL store keyed by
  `session_id`; analyze/tailor/fidelity-export reuse it.
- **Message content blocks added.** The LLM abstraction passes text + image
  content blocks, translated per provider (OpenRouter `image_url` data-URI,
  Bedrock Converse `image`).
- **PyMuPDF added.** `pymupdf>=1.24` provides PDF page rendering (scanned-PDF
  vision) and coordinate-overlay editing (fidelity export). No pywin32 or
  LibreOffice dependency.

> **Note (v3.0):** the v2 fidelity deviations are themselves superseded by v3.0 —
> in-place editing of uploaded files is REMOVED (see v3.0 Changes). PyMuPDF is
> retained, now for scanned-PDF vision AND the deterministic HTML→PDF
> (`fitz.Story`) pipeline.

---

## v2.2 Changes (this version … superseded by v3.0)

Historical record of v2.2 (format-agnostic Markdown analysis, per-format
fidelity editing). All fidelity-export content below is **removed in v3.0** —
see the v3.0 Changes section. v2.2's PDF/DOCX upload acceptance, dual-mode
analysis, layout/rename suggestions, gaps report, and no-fabrication guarantees
remain in force (evolved, not removed).

- **PDF and DOCX uploads (restored).** `parser.parse_file` accepts `.pdf` and
  `.docx`; DOC/TXT and any other extension are rejected with a clear
  `ValueError` → HTTP 400 at `POST /api/cv/parse`. (v2.1 had restricted uploads
  to PDF-only; v2.2 lifted that restriction.)
- **Markdown CV extraction for the AI (format-agnostic).**
  `services/cv/markdown.py` renders the parsed structure as Markdown for BOTH
  upload formats: `# Name`, `## <section heading>`, `- bullets`, plus an
  `## OBSERVED LAYOUT` subsection. Analyze and tailor-suggest prompts consume
  the same Markdown regardless of source format, not raw text.
- **Optional Job Description / dual mode.** `job_description` is optional in
  the analyze/tailor request bodies. JD mode analyzes keyword/ATS fit; generic
  mode (no JD) assesses ATS-friendliness on the CV's own merits.
- **Layout & placement analysis.** `Suggestion.type` gains `rename` alongside
  `reorder`/`layout`.
- **Gaps reported separately.** `AnalysisReport.gaps: [str]` lists
  human-readable gaps — informational, distinct from `suggestions`.
- **No-fabrication guarantee.** All analyze/tailor/vision/rewrite prompts
  constrain the model (SRS FR-12.4).
- **Per-format fidelity editing** (`docx_preserve.py`, `pdf_overlay.py`,
  `preserve.py`, `X-CVIQ-Rejections` header, regenerated-export fallback) —
  **REMOVED in v3.0.**

---

## v3.0 Changes (this version)

Structural rewrite around **content/presentation separation** — the docs move
from v2.2 to v3.0 as a rewrite of the affected sections, not a version bump:

- **Canonical CV schema (single source of truth).** One JSON object
  (`CVData`) — `personal`, `summary`, `experience[]`, `education[]`, `skills[]`,
  `projects[]`, `certifications[]`, `languages[]`, plus the extensible
  **`custom_sections[]`** (title + bullets). The AI reads and writes ONLY this
  object — never raw file bytes (SRS FR-12.2).
- **Uploads only parse to the schema.** PDF/DOCX uploads are parsed (text +
  layout via PyMuPDF / python-docx; scanned PDFs via vision/OCR), then an **LLM
  classification step** maps the extracted blocks into the canonical schema,
  validated with Pydantic before storing; low-confidence fields surface as
  `confidence_flags` `{field_path, level, reason}` for user confirmation. A
  heuristic fallback classifies when no LLM is configured. Vision classification
  still supports scanned PDFs — signaled by `image_mode` (SRS FR-3).
- **HTML/CSS template layer.** The CV designs (gallery) are converted one time
  into Jinja2+CSS print templates under `app/templates_html/` (currently
  `modern`, `classic`, `minimal`, `awesome-cv`, `deedy-resume`, `cvresume`,
  `universal-resume`, `newfuture-cv`; the remaining gallery designs map to a
  fallback template). Templates are stored separately from user data, bind to
  every canonical field, and are never edited. Gallery metadata
  (`services/templates.py`) exposes `render_template` + `converted` (SRS FR-14).
- **Deterministic rendering pipeline (no AI).** JSON → HTML (Jinja2, escaped) →
  PDF via **PyMuPDF Story** (multi-page, letter) in
  `services/export/render_html.py` + `pdf_render.py`; JSON → DOCX via
  `python-docx` (schema-driven, renders `custom_sections`) in
  `docx_export.py`. Rendering works with zero AI calls and is unit-testable.
  **Direct editing of uploaded files is REMOVED**: `/api/export/preserved`,
  `preserve.py`, `docx_preserve.py`, `pdf_overlay.py`, `template_fill.py` are
  all deleted; PDFs/DOCX are only ever final rendering outputs, never editing
  targets (SRS FR-7).
- **JD parser.** `job_description` → structured **`JDProfile`**
  `{role_title, required_skills, nice_to_have_skills, must_have_keywords,
  seniority_signals, requirements}` via LLM forced into JSON, with a
  deterministic keyword fallback (SRS FR-13).
- **Gap-analysis engine** (`app/services/cv/gaps.py`): (a) **deterministic
  checks** (rules, no AI) — missing sections/contact, ATS-breaking elements
  (images, tables, multi-column, non-standard headings), unquantified bullets;
  (b) **semantic checks** (LLM) — missing skills, weak phrasing, unquantified
  achievements. Both return structured `GapDiff`s
  `{field_path, issue, suggested_value, rationale, kind:
  deterministic|semantic, severity}`. New endpoint `POST /api/cv/gaps` (SRS FR-9).
- **Suggestions are structured diffs; apply = patch JSON then re-render.**
  `Suggestion` carries `field_path` (e.g. `experience[0].bullets[1]`) and
  `rationale`; applying a suggestion patches the canonical JSON (including
  `custom_sections`) and the updated PDF/DOCX is produced ONLY by re-running the
  rendering pipeline. A diff/preview UI step shows suggestions before applying —
  never auto-apply silently (SRS FR-5, FR-6.5).
- **Exports.** `/api/export/pdf|docx` (with optional `template`) and
  `/api/templates/{id}/export/pdf|docx` (renders the gallery template's HTML
  design). No fill-overlay warnings anymore (SRS FR-7, FR-14).
- **AI constraints (non-negotiable).** AI never writes to PDF/DOCX; AI never
  receives raw file bytes to edit; every suggestion is a diff with rationale
  shown before apply; rendering is AI-free and deterministic (SRS FR-12).

---

## 1. Architecture Overview

```
Browser (Vanilla SPA)
        │  fetch('/api/...')  JSON
        ▼
FastAPI app (app/main.py) ── static files ──► frontend/ (index.html, styles.css, app.js)
   │
   ├─ routes/config.py       ── LLM config
   ├─ routes/cv.py           ── parse, analyze, gaps, tailor, chat, apply, assist, validate, library
   ├─ routes/export.py       ── pdf / docx download (optional template; render-mode only)
   ├─ routes/templates.py    ── template gallery: list, previews, x/export/pdf|docx (render-mode)
   │
   ├─ services/llm/          ── provider abstraction (text + image content blocks)
   │     ├─ base.py          ── LLMClient protocol + registry + factory
   │     ├─ openrouter.py    ── OpenAI-compatible via httpx (image_url data-URI)
   │     ├─ bedrock.py       ── AWS Bedrock Converse via boto3 (image block)
   │     └─ config_store.py  ── persistence + redaction
   ├─ services/cv/
   │     ├─ models.py        ── Pydantic CVData (+custom_sections), ConfidenceFlag,
   │     │                      Suggestion (field_path/rationale), AnalysisReport,
   │     │                      JDProfile, GapDiff (v3)
   │     ├─ parser.py        ── PDF/DOCX → text + layout structure; vision fallback for scanned PDFs;
   │     │                      LLM classification → canonical CVData (heuristic fallback) + confidence_flags
   │     ├─ markdown.py      ── canonical CVData → Markdown CV document (OBSERVED LAYOUT) for LLM prompts
   │     ├─ gaps.py          ── gap engine: deterministic rules + semantic LLM checks → list[GapDiff]  (v3)
   │     ├─ jd_parser.py     ── job_description → JDProfile (LLM forced JSON; keyword fallback)  (v3)
   │     ├─ session.py       ── bounded in-memory parse sessions (TTL ≈30 min)
   │     ├─ analyzer.py      ── canonical CV markdown + optional JDProfile → AnalysisReport (dual mode)
   │     ├─ tailor.py        ── generate suggestions (diffs), apply (patch JSON → re-render)
   │     ├─ template_detect.py─ template/accent detection from parsed CV
   │     ├─ writer.py        ── CVData → plain-text fallback for legacy prompt paths
   │     └─ library.py       ── .cvmod/local library persistence (canonical JSON only)
   ├─ services/export/       ── DETERMINISTIC RENDER PIPELINE (no AI)
   │     ├─ render_html.py   ── CVData → HTML (Jinja2 templates_html/*.html.j2, escaped;
   │     │                      builtin single-column fallback when a file is missing)
   │     ├─ pdf_render.py    ── HTML → PDF via PyMuPDF fitz.Story (multi-page, letter, margins)
   │     ├─ pdf_export.py    ── export_pdf (Story primary; reportlab legacy fallback only)
   │     └─ docx_export.py   ── CVData → DOCX via python-docx (schema-driven; custom_sections)
   ├─ templates_html/        ── HTML/CSS template layer: modern/classic/minimal/awesome-cv/
   │                             deedy-resume/cvresume/universal-resume/newfuture-cv (*.html.j2)
   ├─ services/templates.py  ── gallery metadata: scan/preview + render_template + converted (fallback map)
   └─ config.py              ── app settings (paths, defaults)
```

- Single process, single user. In-memory working state plus a small local JSON
  library (`.cvmod/`) for persisting config + saved CVs (canonical JSON only).
- No build step: FastAPI mounts `frontend/` as static and serves `index.html` at `/`.
- PDF page rendering (scanned-PDF vision) and HTML→PDF pagination use
  **PyMuPDF** (`pymupdf`); no external Office tooling (no LibreOffice / pywin32).
- There is no `services/export/preserve.py`, `docx_preserve.py`,
  `pdf_overlay.py`, or `services/export/template_fill.py` — all deleted in v3.0.

### 1.1 Data flow (v3.0)

```
 Upload (PDF/DOCX)  ──parse──▶  extracted text + layout structure (PyMuPDF/python-docx)
        │                          │ scanned PDF → page images → vision/OCR
        ▼                          ▼
  LLM classification ──Pydantic──▶  canonical CV JSON (CVData) + confidence_flags
  (heuristic fallback)              │   ▲                                  │
        │                           │   │ (user confirmation of flags)    │
        ▼                           │   │                                  ▼
  JD text ── JD parser ──▶ JDProfile│   └──[user edits canonical JSON]──┐  analyze / gaps / tailor
        │                           │       (editor or applied diffs)  │     (LLM reads Markdown CV doc,
        ▼                           ▼                                  ▼      never raw bytes)
  /api/cv/gaps ──▶ GapDiffs ──▶ suggestions (diffs: field_path + rationale) ─▶ patch JSON + re-render
                                                                                    │
                                                                                    ▼
                                    render pipeline (AI-free): CVData → HTML (Jinja2) → PDF (Story)
                                                          CVData → DOCX (python-docx)
```

---

## 2. Data Model

### 2.1 CVData (canonical schema — single source of truth)
```python
class PersonalInfo(BaseModel):
    name: str = ""
    title: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    website: str = ""
    linkedin: str = ""
    github: str = ""

class DateRange(BaseModel):
    start: str = ""   # "2018-05" or "May 2018"
    end: str = ""     # "2021-09" / "Present"

class ExperienceItem(BaseModel):
    company: str = ""
    role: str = ""
    location: str = ""
    dates: DateRange = DateRange()
    bullets: list[str] = []

class EducationItem(BaseModel):
    institution: str = ""
    degree: str = ""
    field: str = ""
    dates: DateRange = DateRange()
    gpa: str = ""

class ProjectItem(BaseModel):
    name: str = ""
    link: str = ""
    description: str = ""
    bullets: list[str] = []

class CertificationItem(BaseModel):
    name: str = ""
    issuer: str = ""
    year: str = ""

class LanguageItem(BaseModel):
    name: str = ""
    level: str = ""

class SkillGroup(BaseModel):
    category: str = ""
    skills: list[str] = []

class CustomSection(BaseModel):        # v3 — extensible sections
    title: str = ""                    # may be empty (bare-bullet section)
    bullets: list[str] = []            # may be empty (heading-only section)

class CVData(BaseModel):
    template: str = "modern"
    accent: str = "#2563eb"
    personal: PersonalInfo = PersonalInfo()
    summary: str = ""
    experience: list[ExperienceItem] = []
    education: list[EducationItem] = []
    skills: list[SkillGroup] = []
    projects: list[ProjectItem] = []
    certifications: list[CertificationItem] = []
    languages: list[LanguageItem] = []
    custom_sections: list[CustomSection] = []   # v3
    section_order: list[str] = []               # v3.1 — permutation of DEFAULT_SECTION_ORDER; empty = default order
    section_titles: dict[str, str] = {}         # v3.1 — section id → heading override (empty = template default)
```
- **Canonical rule (v3):** this object IS the CV. Parsing, classification,
  analysis, suggestions, editing, and rendering all operate on it. The AI's I/O
  is restricted to this object (serialized as the Markdown CV document for
  prompts); raw file bytes are never AI-visible edit input (SRS FR-12.2).
- **Section order & headings (v3.1):** `models.py` defines
  `DEFAULT_SECTION_ORDER = [summary, experience, education, skills, projects,
  certifications, languages, custom_sections]` and `ARRAY_SECTIONS` /
  `TITLED_SECTIONS` helpers. `render_html` passes `section_order`,
  `section_titles` and an `stitle(section_id, default)` global into every
  Jinja2 template AND the builtin fallback renderer; all 8 templates honor
  both. Empty `section_order`/`section_titles` produce byte-identical output
  (verified by smoke tests). Sections missing from a custom order are appended
  afterwards in default order, so no content is ever hidden.

### 2.2 Analysis, Suggestions & Confidence
```python
class KeywordMatch(BaseModel):
    keyword: str
    present: bool
    count: int = 0          # occurrences in CV text

class SectionAssessment(BaseModel):
    section: str            # "summary"|"experience"|"skills"|"education"|...
    score: int              # 0-100
    comment: str

class ConfidenceFlag(BaseModel):        # v3 — produced by parse classification
    field_path: str = ""               # JSON-ish path, e.g. "experience[0].bullets[1]"
    level: str = "low"                 # low | medium
    reason: str = ""

class Suggestion(BaseModel):           # v3 — a structured diff into the canonical JSON
    id: str                 # uuid
    section: str
    field: str              # e.g. "summary", "bullets", "skills"
    index: int | None       # index into a list field (legacy locator, superseded by field_path)
    type: str               # rewrite|add|remove|reword|keyword|reorder|rename
    title: str
    original: str = ""
    suggested: str = ""
    reason: str = ""                   # human-readable summary
    rationale: str = ""                # v3 — the diff rationale shown in the UI before apply
    field_path: str = ""               # v3 — "personal.email", "experience[0].bullets[1]", "custom_sections[0].bullets[2]"
    priority: str = "medium"           # high|medium|low
    impact: str = ""                   # e.g. "ATS keyword match", "readability"
    # reorder targets (empty for other types):
    move_from: str = ""                # "section.field[index]"
    move_to: str = ""                  # "section.field[index]"
    target_section: str = ""

class AnalysisReport(BaseModel):
    ats_score: int          # 0-100
    matched_keywords: list[KeywordMatch] = []   # [] in generic (no-JD) mode
    missing_keywords: list[KeywordMatch] = []   # [] in generic (no-JD) mode
    sections: list[SectionAssessment] = []
    comments: list[str] = []
    gaps: list[str] = []    # informational summary of gap-engine findings (FR-9); structured diffs via /api/cv/gaps
    suggestions: list[Suggestion] = []          # surfaced in the UI; one-click "Import N …" into Review (v3.1)
    session_warning: str = ""                   # v3.1 — set by the route when a supplied session was missing/expired
```
- **Suggestion application semantics (v3.1):** `apply_suggestion` handles
  `reorder` (item-level: `move_from`/`move_to` indices, relative `up`/`down`
  supported; section-level: `target_section` + `move_to` of
  `up|down|top|bottom|N`) and `rename` (writes `section_titles` for standard
  sections, or renames a `custom_sections[i]` title). `layout` is ACCEPTED as a
  parseable no-op (type retained for backwards compatibility; no longer
  generated — removed from the suggest/analyze prompts). Append-style
  suggestions are deduped (trimmed, case-insensitive) so apply is IDEMPOTENT:
  re-applying an unchanged set yields `applied == 0` and an unchanged CV.
- **Chat 2.0 (v3.1):** `tailor.chat_assist` sends the last ~20 messages + CV
  JSON + JD + `target` in the system prompt; the model must return strict JSON
  `{"reply": str, "proposed_edits": [Suggestion]}` (`extract_json`); malformed
  output degrades to `(raw_text, [])`, dropped edits never 500. Vision path
  preserved: scanned-PDF sessions attach capped page images to the first user
  message.

### 2.3 JDProfile & GapDiff (v3)
```python
class JDProfile(BaseModel):            # produced by the JD parser (LLM forced JSON; keyword fallback)
    role_title: str = ""
    required_skills: list[str] = []
    nice_to_have_skills: list[str] = []
    must_have_keywords: list[str] = []
    seniority_signals: list[str] = []
    requirements: list[str] = []

class GapDiff(BaseModel):              # produced by the gap-analysis engine (services/cv/gaps.py)
    field_path: str = ""               # same JSON-ish path convention as Suggestion.field_path
    issue: str = ""                    # e.g. "missing section: summary", "image present (ATS-breaking)"
    suggested_value: str = ""          # offered fix, where applicable
    rationale: str = ""                # why this matters for ATS/human screening
    kind: str = ""                     # "deterministic" | "semantic"
    severity: str = ""                 # high | medium | low
```
- `JDProfile` feeds keyword matching in analysis (SRS FR-4.4), semantic gap
  checks (FR-9.1), and `keyword` suggestions (FR-5.6).
- `GapDiff`s are returned by `POST /api/cv/gaps` inside its `deterministic` /
  `semantic` lists (FR-9.3); each GapDiff may become a `Suggestion`
  (field_path + suggested_value) or be applied directly as a diff — both paths
  patch canonical JSON then re-render.

### 2.4 SessionEntry (parse session cache)
```python
class SessionEntry(BaseModel):
    session_id: str
    created_at: float          # unix ts; drives ≈30 min TTL eviction
    original_name: str
    mime: str                  # "pdf" | "docx"
    extracted_text: str
    structure: dict            # document structure (paragraphs/runs/spans/coords) — classification input
    cv: CVData                 # classified canonical CV (v3 — the working object)
    confidence_flags: list[ConfidenceFlag] = []   # v3
    classification: str        # v3 — "llm" | "heuristic" (vision signaled via image_mode)
    page_images: list[bytes] | None  # ~150-DPI PNG renders — scanned PDFs only
    page_count: int
    confidence: float
    image_mode: bool           # true when vision was used (scanned PDF)
```
- The session store (`services/cv/session.py`) is an in-memory dict keyed by
  `session_id`, bounded (single-user app, 300 MiB total) with a ~30 min TTL.
  `session_stats()` reports live stats (retained; used by the smoke tests).
  Nothing is written to disk — the store is not persistence.
- **v3:** sessions hold parse/classification evidence; they never hand file
  bytes to an editor — the canonical `cv` is the working object (FR-10).
- **v3.1 — unified expiry semantics:** `routes/cv.py::_resolve_session`
  centralizes lookup for analyze/suggest/chat/gaps. A missing or expired
  session NEVER 404s: the request degrades to its self-contained body and a
  structured `session_warning` ("Upload session expired — re-upload for best
  results on scanned PDFs.") is returned on the response. Only the vision path
  strictly needs the stored page images, so degradation costs text flows
  nothing.

---

## 3. LLM Client Abstraction

Interface:
```python
# Content-block convention: each message may carry text and/or image blocks.
#   {"role": "user", "content": [
#       {"type": "text",  "text": "..."},
#       {"type": "image", "data": "<base64 PNG>", "media_type": "image/png"},
#   ]}

class LLMClient(Protocol):
    provider_name: str
    def chat(self, messages: list[dict], temperature: float = 0.4,
             max_tokens: int = 2048) -> str: ...
    def test(self) -> None: ...   # raises LLMError on failure
```

- **Content blocks.** Any LLM message may contain text blocks and image blocks
  (base64 PNG). Providers translate these:
  - **OpenRouter** (`openrouter.py`): `httpx.post("https://openrouter.ai/api/v1/chat/completions")`
    with `Authorization: Bearer <key>`. Text → `{"type":"text",...}`; image →
    OpenAI `image_url` data-URI
    (`{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`).
    Model default `anthropic/claude-sonnet-4-6` (configurable).
  - **Bedrock** (`bedrock.py`): boto3 `bedrock-runtime` client; use the **Converse
    API** (`client.converse(modelId=..., messages=...)`). Text →
    `{"text":...}`; image → Converse `image` block
    (`{"image":{"format":"png","source":{"bytes":<binary>}}}`). Model default
    `anthropic.claude-sonnet-4-5-v2-0` (configurable).
- The provider flattens the first text block of the response into a single
  string for `chat()`.
- Model defaults must be vision-capable since the scanned-PDF classification
  path requires image content blocks; the Claude Sonnet 4.x defaults above
  already are.
- `services/llm/__init__.py::get_client(cfg)` returns the client or raises a
  clear `LLMConfigError` when the provider is unconfigured.
- Retries: 1 retry on transient HTTP errors; surface the final error message.

### 3.1 LLMConfig + storage
```python
class LLMConfig(BaseModel):
    provider: str = "openrouter"        # "openrouter" | "bedrock"
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4-6"
    bedrock_access_key: str = ""
    bedrock_secret_key: str = ""
    bedrock_region: str = ""
    bedrock_model: str = "anthropic.claude-sonnet-4-5-v2-0"
```
- Persisted to `.cvmod/config.json`.
- `redact()` returns a copy with secret fields replaced by `"***"` (or a
  boolean `configured` flag) whenever secrets are returned to the UI.
- Secrets should also be readable from environment variables as an override
  (e.g. `OPENROUTER_API_KEY`, `AWS_ACCESS_KEY_ID`).

### 3.2 Prompt classes (v3)
Prompts are structured by capability; all are built from the canonical object
(never raw bytes) and explicitly constrain the model (SRS FR-12):
1. **Classification (text uploads).** Input: the extracted structure rendered
   as the Markdown CV document (FR-3.6). Output: canonical `CVData` JSON +
   `confidence_flags` list; heuristic fallback when no LLM configured (FR-3.3).
2. **Vision classification (scanned PDFs).** Input: page images (~150-DPI PNG)
   + a dedicated, detailed vision prompt: transcribe faithfully into the
   canonical schema, mark low-confidence fields as flags, then — with a JD —
   assess keyword/ATS fit (dual mode). Never a reword of the text prompt
   (FR-11.4).
3. **Analyze.** Input: Markdown CV document + optional `JDProfile`. Output:
   `AnalysisReport` (dual mode; gaps summary; no keyword content in generic
   mode) (FR-4).
4. **Tailor-suggest.** Input: Markdown CV document + optional `JDProfile`.
   Output: `Suggestion[]` diffs with `field_path` + `rationale` (no `keyword`
   suggestions in generic mode) (FR-5). **v3.1:** placement suggestions are
   `reorder`/`rename` only — `layout` was removed from the type list in the
   suggest and analyze prompts (still parseable on input, no-op on apply).
5. **JD parser.** Input: raw JD text. Output: `JDProfile` forced into JSON;
   deterministic keyword fallback when no LLM configured (FR-13).
6. **Gap semantics.** Input: Markdown CV document + optional `JDProfile`.
   Output: semantic `GapDiff[]` (weak phrasing, missing skills, unquantified
   achievements) (FR-9.1).
7. **Assist/rewrite segments.** From-scratch drafting and segment rewrites.

---

## 4. REST API Contract

All responses JSON unless noted. Errors: `{"detail": "..."}` with appropriate
HTTP status (400 config/parse/unsupported-format — including uploads that are
neither PDF nor DOCX, 404 session expired/unknown/template — **v3.1: 404 no
longer applies to expired sessions; those degrade with `session_warning`** —
413 payload/upload too large, 502 upstream LLM, 422 validation).
Friendly LLM errors: 401-adjacent → "Invalid API key", 429 → "Rate limited",
timeouts → "timed out", mapped in analyze/suggest/chat/assist/gaps.

### Config
| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/config` | – | Redacted config + `configured` flags |
| POST | `/api/config` | `LLMConfig` | Save config |
| POST | `/api/config/test` | `LLMConfig` | Test connectivity, returns `{ok, message, model}` |
| GET | `/api/health` | – | **v3.1:** liveness probe → `{status: "ok"}`; no LLM involvement |
| GET | `/api/meta` | – | Available templates + accents + defaults |

### CV parse, analysis & gaps
| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/cv/parse` | multipart `file` (`.pdf` or `.docx`) | → `{cv: CVData, text, confidence, page_count, classification: "llm"\|"heuristic", confidence_flags: [ConfidenceFlag], image_mode, session_id}`; the upload is extracted then classified into the canonical schema (LLM or heuristic; `image_mode` true when a scanned PDF was classified via vision); creates a ~30 min in-memory session (FR-3, FR-10). DOC/TXT (and any other extension) rejected with HTTP 400 (FR-3.1). **v3.1:** uploads >15 MB are read at most cap+1 bytes and rejected with HTTP 413 `File too large — maximum upload size is 15 MB.` |
| POST | `/api/cv/analyze` | `{cv, job_description?, session_id?}` | → `AnalysisReport` (JD-specific or generic ATS assessment; keyword matching via `JDProfile`; includes `gaps` summary and `suggestions`); with `session_id` the parsed/classified context is reused. **v3.1:** response carries `session_warning` when the session was missing/expired (never 404) |
| POST | `/api/cv/gaps` | `{cv, job_description?, session_id?}` | → `{jd_profile, deterministic: [GapDiff], semantic: [GapDiff], mode: "jd"\|"generic", session_warning}` from the gap engine — deterministic checks run with no LLM; semantic checks require a configured provider (FR-9.3). **v3.1:** `session_warning` added; `jd_profile` surfaced in the UI |
| POST | `/api/cv/tailor/suggest` | `{cv, job_description?, session_id?}` | → `{suggestions: [Suggestion], session_warning}` — diffs with `field_path` + `rationale` (dual mode; no `keyword` suggestions without a JD). **v3.1:** `reorder`/`rename` suggestions apply; `layout` no longer generated |
| POST | `/api/cv/tailor/apply` | `{cv, suggestions: [Suggestion]}` | Patches the canonical JSON (incl. `custom_sections`, `section_order`, `section_titles`) → `{cv: CVData, applied: int}`; the updated PDF/DOCX is produced ONLY by re-running export (FR-5.3). **v3.1:** idempotent — duplicate appends deduped; re-applying an unchanged set yields `applied == 0` |
| POST | `/api/cv/tailor/chat` | `{cv, job_description?, session_id?, target?: {section, field?, index?}, messages: [{role, content}]}` | **Chat 2.0 (v3.1):** history-aware assistant — sends last ~20 messages + CV + JD + `target`; → `{reply, proposed_edits: [Suggestion], session_warning}`. Proposed edits apply via `/api/cv/tailor/apply`. Vision path preserved for scanned-PDF sessions; expired sessions degrade (never 404). Legacy `{segment, context}` body still accepted (deprecated; synthesizes one user message); neither `messages` nor `segment` → 400 |
| POST | `/api/cv/assist` | `{kind: summary\|bullets\|optimize*, cv, job_description?, content?}` | AI draft assist for from-scratch. **v3.1:** kinds incl. `optimize_skills` (grouped `Category: skill, skill` lines) and `optimize_project` (`Description:` + `Bullets:` + `- ` lines) |
| POST | `/api/cv/validate` | `{cv}` | Local checks + light heuristics → `{warnings:[...]}` |

### Library (working CV persistence — canonical JSON only)
| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/library` | – | list saved CVs `[{id, name, updated, meta}]` |
| POST | `/api/library` | `{name, cv, meta?}` | save CV → `{id}` (meta stored and returned by list/get) |
| PUT | `/api/library/{cid}` | `{name?, cv, meta?}` | **v3.1:** overwrite an existing entry — id preserved, `updated` refreshed, 404 if `cid` missing; duplicate-name warnings served to the frontend via the `library.name_exists` helper |
| GET | `/api/library/{cid}` | – | → `{name, cv, meta, updated}` |
| DELETE | `/api/library/{cid}` | – | remove (404 if missing) |

**v3.1 guards:** every save/update validates the CV payload ≤ 5 MB (HTTP 413
`CV payload too large — maximum saved CV size is 5 MB.`). The SPA offers
"Saving as new vs overwrite" when the name already exists (PUT vs POST).

### Templates (gallery)
| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/templates` | – | **v3.1 — unified catalog:** → `{templates: [ ... ]}` — the 3 builtin entries first (`id` modern/classic/minimal, `source: "builtin"`, `render_template`/`converted` set), then every gallery design (`source: "gallery"`, all previous keys kept: `id, name, file, pages, preview_url, render_template, converted`). 11 entries total when the 8-design gallery is present |
| GET | `/api/templates/{tpl_id}/preview/{page}` | – | PNG preview of the gallery PDF page (design inspiration only) |
| POST | `/api/templates/{tpl_id}/export/pdf` | `{cv}` | **Render-mode:** renders the design's mapped HTML template via the deterministic pipeline — the gallery PDF is never filled/overlaid (FR-7.3, FR-14) |
| POST | `/api/templates/{tpl_id}/export/docx` | `{cv}` | Render-mode DOCX export (schema-driven; **v3.1:** name + section headings colored with the CV accent) |

### Export
| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/export/preview` | `{cv, template?}` | **v3.1:** server-rendered live preview → `{html}` — the full standalone HTML document for ANY catalog id (builtin or gallery), resolved via the same `resolve_render_template` path as export; `Cache-Control: no-store`; powers the Editor iframe |
| POST | `/api/export/pdf` | `{cv, template?}` | `application/pdf` attachment via `Response` — deterministic `CVData → HTML (Jinja2) → PDF (PyMuPDF Story, multi-page, letter)`; `template` accepts ANY catalog id (v3.1) or defaults to `cv.template` (FR-7) |
| POST | `/api/export/docx` | `{cv, template?}` | `application/vnd...wordprocessingml.document` — deterministic `CVData → python-docx` (schema-driven; `custom_sections`; FR-7.2). **v3.1:** generic layout kept but name + section headings now use the CV accent (honest labeling); `template` accepted for API parity |
| ~~POST~~ | ~~`/api/export/preserved`~~ | – | **REMOVED (v3).** Fidelity in-place editing of uploaded files no longer exists; uploads are never editing targets. |

- **Removed in v3.0:** `/api/export/preserved`, the `X-CVIQ-Rejections`
  response header, `fidelity_mode`/`"original"`-path semantics, and fill-overlay
  template exports with overflow warnings. There is exactly ONE export path —
  deterministic rendering from the canonical JSON; suggestions are applied by
  patching `cv` (client-side `tailor/apply` or manual edits) and re-exporting.
- `fidelity_mode` is gone: upload-based and from-scratch CVs are exported the
  same way (render pipeline), so no labeling distinction is needed.

---

## 5. Templates (HTML/CSS layer)

### 5.1 HTML template layer
- CV designs are implemented as **Jinja2+CSS print templates** in
  `app/templates_html/<id>.html.j2` (a data directory, not a package):
  `modern`, `classic`, `minimal`, `awesome-cv`, `deedy-resume`, `cvresume`,
  `universal-resume`, `newfuture-cv`.
- Templates bind to every canonical field (personal, summary, experience,
  education, skills, projects, certifications, languages, `custom_sections[]`).
  User content is always escaped (Jinja2 `autoescape`; `html.escape` in the
  builtin fallback).
- Missing/absent template files never break rendering: `render_html` falls back
  to a builtin single-column modern renderer (`_render_fallback`) (SRS FR-14.4).
- Templates are stored separately from user data (`.cvmod/` library holds only
  canonical JSON) and are never edited by the app.

### 5.2 Gallery metadata (`services/templates.py`)
- `scan_templates()` scans the gallery PDF dir (`Templates/`, path overridable)
  and returns per-design metadata:
  ```python
  {
    "id": "awesome-cv", "name": "Awesome CV", "file": "awesome-cv",
    "pages": 1, "size_bytes": ...,
    "preview_url": "/api/templates/awesome-cv/preview/0",
    "render_template": "awesome-cv",   # HTML template used to render final files
    "converted": True,                 # dedicated HTML template exists
  }
  ```
- **Converted list (v3):** `RENDER_TEMPLATES = {modern, classic, minimal,
  awesome-cv, deedy-resume, cvresume, universal-resume, newfuture-cv}`.
- **Fallback mapping (v3):** `GALLERY_RENDER_MAP` maps the remaining gallery
  designs (`altacv`, `moderncv`, `twentyseconds`, `resume-ng`,
  `simple-resume-cv`, …) to a fallback HTML template (`modern`/`minimal`/
  `classic`); unknown ids fall back to `modern`.
- Gallery PDFs are used ONLY for `preview_url` (design inspiration); exports
  always render from canonical JSON via the mapped HTML template. There is no
  `template_fill.py` overlay path anymore.
- **Unified catalog (v3.1):** `GET /api/templates` returns the builtin entries
  (`modern`/`classic`/`minimal`, `source: "builtin"`, `render_template` and
  `converted: True` set) followed by every gallery design (`source:
  "gallery"`, all prior keys kept) — 23 entries with the full gallery.
  `resolve_render_template(template_id, default)` resolves ANY catalog id to
  the HTML render template and backs `/api/export/pdf|docx`,
  `/api/export/preview`, and `/api/templates/{id}/export/*` alike. The SPA
  picker labels designs "Dedicated design" (dedicated HTML template) vs
  "Closest match — &lt;template&gt;" (fallback-mapped) — no leaked jargon.

---

## 6. Export Design (deterministic rendering pipeline — no AI)

Single, AI-free pipeline for every export (from-scratch and upload-derived):

```
CVData (canonical JSON)
   │
   ├─► render_html.render_html(cv, template_id)
   │        Jinja2 (app/templates_html/*.html.j2) — escaped, section-driven,
   │        custom_sections rendered; builtin fallback if the file is absent
   │        ▼
   │    pdf_render.html_to_pdf(html)
   │        PyMuPDF fitz.Story + DocumentWriter — paginates onto letter-size
   │        pages (0.5-in margins) across multiple pages when content overflows;
   │        deterministic (same input → same PDF bytes)
   │
   └─► docx_export.export_docx(cv)
            python-docx — heading paragraphs, bullet lists, skills lines,
            certifications, languages, custom_sections; base font Calibri,
            ~0.5-in margins
```

- **PDF** (`services/export/pdf_render.py`): `fitz.Story(html=..., user_css=...)`
  placed into a letter `fitz.Rect` (fallback `Rect(0,0,612,792)`), pages written
  via `fitz.DocumentWriter`; the loop continues while `story.place()` reports
  `more` (multi-page overflow). `render_pdf(cv, template_id, page_size="letter")`
  composes `render_html` + `html_to_pdf`. If the Story pipeline ever fails,
  `pdf_export.export_pdf` falls back to the legacy reportlab renderer
  (`export_pdf_legacy`) so PDF export never breaks — that renderer is a
  defensive fallback only, not the v3 pipeline.
- **DOCX** (`services/export/docx_export.py`): schema-driven; header (name,
  title, contact line), uppercase accent-colored section headings, bullet
  paragraphs, skills lines, and `custom_sections[]` (title heading + bullet
  lines). Renders `custom_sections` regardless of content shape (title-only,
  bullets-only, or both).
- **DOCX accent (v3.1):** `export_docx(cv, template="", accent="")` colors the
  name and section headings with the CV's accent (default `cv.accent`). The
  `template` parameter is accepted for API parity with the unified catalog but
  the DOCX remains an honest generic schema-driven layout — no pretend
  per-template fidelity.
- **Live preview (v3.1):** `POST /api/export/preview` returns the rendered
  HTML document (`render_html` output) for any catalog id with
  `Cache-Control: no-store`; the Editor displays it in a debounced iframe, so
  the on-screen preview matches the downloaded PDF byte-for-byte in content
  (FR-6.2).
- Both paths consume ONLY `CVData` → identical content and ATS-safe selectable
  text. Rendering is pure/stateless: no LLM calls, no file-system writes, no
  in-place edits.
- **Removed (v3):** §6.1 "Preserved (fidelity) export" — `preserve.py`,
  `docx_preserve.py`, `pdf_overlay.py`, `/api/export/preserved`,
  `X-CVIQ-Rejections`, and fill-overlay warnings. There is no scenario in which
  an uploaded file is edited; all output is freshly rendered.

---

## 7. UI/UX Design (frontend SPA)

Views (`window.location.hash` routing, no framework; nav order: **Home /
Build / Optimize / Editor / My CVs / Settings** — Settings last):
1. **Home** (`#/`, default landing) — dashboard cards for Build / Optimize /
   Editor / My CVs with short descriptions; unknown hashes redirect home with
   a toast; brand click goes home (not Settings as in earlier revisions).
2. **Build (from scratch)** — two-stage flow (state persists across
   navigation; reset only via an explicit "Start over"): (a) unified template
   picker served from `GET /api/templates` (3 builtin blank designs + the 20
   gallery designs with "Dedicated design" / "Closest match" labels); (b)
   guided sectioned form covering ALL canonical fields incl. `custom_sections`
   (title + bullets, add/remove/reorder/duplicate). Per-section AI actions
   (summary, bullets, `optimize_skills` → `Category: skill, skill` lines,
   `optimize_project` → `Description:` + `Bullets:` + `- ` lines) ALWAYS
   preview the AI output first with **Replace / Append / Cancel** — nothing
   overwrites silently, and the form re-renders after applying.
3. **Optimize** — 5-step wizard (**v3.1** — see §7.3 for the loop):
   - a. **Upload / Select CV** — PDF/DOCX upload (15 MB cap → HTTP 413) or
     pick from library; parse results incl. `confidence`,
     `classification` (`llm`/`heuristic`), `image_mode` (scanned-PDF vision),
     and a `confidence_flags` "review these fields" checklist.
   - b. **Analyze** (JD optional) — dual-mode score dashboard (ATS score,
     matched/missing keywords, section bars, comments) + `jd_profile` summary
     card ("We understood the job as …") + the **unified gaps panel**
     (deterministic + semantic GapDiffs and the report's gap strings,
     de-duplicated; each gap convertible to a suggestion).
   - c. **Review** — suggestion cards with diff preview (`field_path` +
     `rationale`), accept/reject/edit; **Accept all / Reject all**, priority +
     section filters, pending-count badge on the Review pill; one-click
     **Import N analysis suggestions** from the report; a **stale-suggestion
     warning** appears once the CV is edited after generation.
   - d. **Apply** — explicit; idempotent (re-applying an unchanged set yields
     `applied == 0`); applied cards are locked ("Applied ✓", actions
     disabled). Apply patches the canonical JSON incl. `section_order` /
     `section_titles` (`reorder`/`rename` suggestions, v3.1).
   - e. **Results & Export** — final step: new score + **delta vs base**
     (62 → 84), sparkline of `scoreHistory`, applied-changes summary,
     **PDF / DOCX** export buttons, **Re-analyze**, **Keep editing**
     (→ Editor), and the Assistant.
   The dockable **Assistant** panel is available from the header on **every**
   step (see §7.4).
4. **Editor/Preview** — two-pane: left form (all canonical fields incl.
   `custom_sections`; ↑/↓ reorder on every item and bullet, duplicate-item,
   collapsible cards with jump-nav, auto-growing bullet textareas, inline
   email/URL/date validation, "Present/current" date toggle, skills chip
   editor); right **server-rendered live preview** — debounced
   `POST /api/export/preview` rendered into an A4-framed iframe with
   zoom / fit-width controls (WYSIWYG vs. the exported PDF). Toolbar: **ATS
   Check** (validate), **Re-analyze** (score delta vs last analysis),
   **Assistant**, **Save to library** (**Ctrl+S**).
5. **My CVs** (`#/library`) — card list with `meta` chips (template, ATS
   score, updated date); actions Open / Rename / Duplicate / PDF / Delete;
   save-with-overwrite: when the chosen name already exists the SPA prompts
   "Overwrite (PUT) or Save as new (POST)". `DELETE /api/library/{cid}` is
   wired (no dead endpoint).
6. **Settings** — last in nav; provider toggle, credential fields (password
   inputs, masked), model inputs, Test, save. Until a provider is configured:
   global dismissible banner ("AI features need a provider") on every view +
   AI actions gated (explained toast → Settings).
7. Shared components: top nav; skeleton loaders for primary waits (parse,
   analysis, suggestions, gaps, library); toasts (errors `role="alert"`,
   honest messages, no emoji-in-text duplication); modal with entrance
   animation, focus trap, initial focus, `aria-modal` + `aria-labelledby`;
   inline SVG icon set (`frontend/assets/icons.js`, loaded before app.js);
   score color semantics <50 red / 50–74 amber / ≥75 green; global
   `:focus-visible`; contrast-safe `--text-faint` (#64748b, ≥4.5:1).

### 7.2 Draft persistence (autosave)
**v3.1 (implemented).** The SPA autosaves its full working state to
`localStorage['cviq.draft.v1']`, debounced (~1 s): `{cv, opt (JD, report,
suggestions, appliedIds, scoreHistory, sessionWarning, suggestionsStale,
baseScore), chat (CHAT_LOG), build (stage/templateId), updatedAt}`. On load,
a **"Resume where you left off?"** modal offers Resume (restore all state,
lands on the Editor) or Start fresh (clears the draft). A `beforeunload`
guard warns when `DRAFT_DIRTY` is set, a "Unsaved changes" indicator shows in
the UI, and **Ctrl+S** saves the working CV to the library directly.
Client-side only — the backend session store is untouched by drafts.

### 7.3 Optimize flow — improvement loop, not a conveyor belt
The v3.1 Optimize wizard is the 5-step loop above (Upload → Analyze → Review →
Apply → **Results & Export**). Each analyze/re-analyze appends the ATS score
to `OPT_STATE.scoreHistory` (persisted in the draft); the Results step shows
the delta against `baseScore`, the sparkline, and a summary of what was
applied. Re-analyzing after applying/edits closes the loop — "your score went
62 → 84" is now a real moment.

### 7.4 Assistant panel — Chat 2.0 (v3.1)
The free-chat wizard step is replaced by a **dockable Assistant drawer**,
available from the Optimize header (every step) and the Editor toolbar:
- Sends the full `CHAT_LOG` history (last ~20), the current CV, JD, and an
  optional edit **target** (`{section, field?, index?}` — set via
  quick-target chips: Summary, Experience 1 bullets, Skills, …).
- `POST /api/cv/tailor/chat` returns `{reply, proposed_edits, session_warning}`;
  proposed edits render as **diff cards** (field, before → after, rationale)
  with Apply / Dismiss — Apply calls `/api/cv/tailor/apply` and updates the
  CV + preview immediately. `session_warning` (expired upload session) shows
  as a banner in the panel.
- Reply bubbles render markdown-ish text with a **copy button**; **Enter**
  sends, **Shift+Enter** inserts a newline (hinted in the UI). Chat history
  persists inside the draft (`cviq.draft.v1` → `chat`).

Visual language: light, airy, one accent color, rounded cards, subtle shadows,
system font stack, responsive (stack panes on narrow screens).

---

## 8. Security & Data Handling
- Secrets returned to UI are always redacted (FR-1.6).
- No secrets in logs. `LLMConfig` is stored in the git-ignored `.cvmod/`
  directory (config + library); nothing is committed.
- Uploaded files are read into memory and kept only in the in-memory session
  store (`services/cv/session.py`) for the ~30 min TTL; the store is bounded and
  single-process. Nothing is written to disk — "no persistence of uploads" holds
  (the session store is not persistence).
- **v3:** raw file bytes are never sent to the LLM and never edited. The LLM
  receives only the canonical object (as Markdown) or page images (scanned-PDF
  transcription). There is no byte-level file editing surface anywhere in the
  backend.
- `.cvmod/` directory (config + library) is added to `.gitignore`.
- **Robustness (v3.1):**
  - Uploads are capped at **15 MB** (`routes/cv.py` reads at most cap+1 bytes;
    HTTP 413 `File too large — maximum upload size is 15 MB.`); library CV
    payloads are capped at **5 MB** per entry (413). No unbounded buffering.
  - LLM failures map to friendly text via `services/llm/base.py::
    friendly_llm_error` in analyze/suggest/chat/assist/gaps: 401 → "Invalid
    API key", 429 → "Rate limited", timeouts → "timed out". The SPA mirrors
    the mapping (`friendlyError()`) for its own client-side failures.
  - `GET /api/health` → `{status: "ok"}` — liveness probe with no LLM
    involvement.
  - `templates_dir` resolution is case-tolerant (`config.py` validator finds
    `templates/` when `Templates` is absent — case-sensitive filesystems
    work).
  - CORS defaults to `*` (`CVMOD_CORS_ORIGINS` env override) — fine for the
    localhost single-user deployment; lock down if ever served on a LAN.

---

## 9. Dependencies (requirements.txt additions)
```
boto3>=1.34
python-docx>=1.1
reportlab>=4.0     # legacy fallback renderer only (export_pdf_legacy)
pypdf>=4.0
pymupdf>=1.24      # scanned-PDF page rendering + PDF page stitching (fitz.Story)
httpx>=0.27
```
- `pymupdf>=1.24` is the primary PDF stack: page rendering for scanned-PDF
  vision AND the deterministic HTML→PDF `fitz.Story` pipeline (multi-page,
  letter).
- `jinja2` is required for the HTML template layer — it already ships with
  FastAPI/Starlette; pin explicitly only if needed.
- **REMOVED in v3.0:** no `pywin32`, no LibreOffice, no overlay/preserve
  modules. `llama-cpp-python`, `huggingface-hub` are not used.
- Keep existing: `fastapi`, `uvicorn[standard]`, `pydantic-settings`,
  `python-multipart`.

---

## 10. Testing Strategy (v3.0)
- **Canonical schema / models:** Pydantic validation of `CVData` (+
  `custom_sections`), `Suggestion.field_path`/`rationale`, `ConfidenceFlag`,
  `JDProfile`, `GapDiff`.
- **Parsing & classification:**
  - Parser fixtures (PDF with text blocks; DOCX with styled paragraphs/List
    styles, tables, headers) → intermediate structure; TXT/other uploads
    rejected (HTTP 400).
  - **Classification fallback tests:** with no LLM configured, the heuristic
    classifier produces a valid canonical `CVData`; with a `FakeLLM`, the LLM
    classification output is validated (Pydantic) and low-confidence fields are
    surfaced as `confidence_flags`.
  - Vision: text-based uploads send NO images; scanned-PDF uploads send PNG
    content blocks (FakeLLM capture test).
- **JD parser:** `JDProfile` forced-JSON output validated (FakeLLM) +
    deterministic keyword fallback without an LLM.
- **Gap engine:** gap-rules tests — deterministic checks (missing
  sections/contact, images/tables/multi-column, non-standard headings,
  unquantified bullets) run with NO LLM and return `GapDiff`s with
  `kind: "deterministic"` and severity; semantic checks (FakeLLM) return
  `kind: "semantic"`; `/api/cv/gaps` endpoint smoke test.
- **Suggestions / apply:** `tailor/apply` patches canonical JSON (incl.
  `custom_sections`) via `field_path`; diff preview payloads carry `rationale`.
- **Deterministic render tests:** `render_html` (escaped output, fallback
  renderer, `custom_sections` in all shapes) → `html_to_pdf` (same input →
  same bytes; multi-page overflow onto letter pages); `export_docx` produces a
  readable document. Round-trip: `CVData → render → PDF/text → parse-back`
  yields a consistent canonical object (FR-3/FR-7/NFR-5).
- **Session:** unit test for the session store — TTL expiry and bounded-cache
  eviction; `session_id` reuse across analyze/tailor/gaps.
- **Integration/smoke:** FastAPI `TestClient` against endpoints with a mocked
  LLM client (inject `get_client`): parse→gaps→analyze→suggest→apply→export.
- **Smoke suite (v3.1, extended):** S17 asserts the **11-entry unified
  catalog** (3 builtin + 8 gallery); S19–S27 cover item/section
  `reorder` + `rename` apply, idempotent apply (re-apply → `applied == 0`),
  chat 2.0 contract incl. expired-session 200 + `session_warning`,
  `/api/export/preview` (html + `no-store`), catalog `source` fields, gallery-
  id PDF export, library PUT + `meta` round-trip, 413 upload guard,
  `/api/health`, and `section_order` honored across multiple templates +
  the builtin fallback renderer.
- **Manual:** full wizard in browser with a mock/real provider (including a
  scanned-PDF fixture, a PDF with custom fonts, a DOCX upload with styled
  paragraphs, a CV with `custom_sections`, plus the generic no-JD flow); verify
  all gallery designs render via their mapped HTML template (converted vs.
  fallback).