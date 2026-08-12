import base64
import json
import logging
import re

from ..llm import LLMClient, LLMConfigError, LLMError, image_part, text_part
from .json_util import cap_vision_images, extract_json, parse_suggestions
from .models import AnalysisReport, KeywordMatch, SectionAssessment, Suggestion

logger = logging.getLogger("cviq")

# Backwards-compatible private aliases (these helpers now live in json_util).
_cap_vision_images = cap_vision_images
_extract_json = extract_json
_suggestions = parse_suggestions

_SYSTEM = (
    "You are an expert ATS (Applicant Tracking System) analyst and senior recruiter. "
    "You analyze a candidate's CV — against a target job description when one is "
    "provided, or as a generic ATS-friendliness assessment otherwise — and return a "
    "strict JSON object. Do not output anything outside the JSON. "
    "Only modify content within sections already present in the candidate's CV; "
    "do not invent new top-level sections."
)

_VISION_SYSTEM = (
    "You are an expert ATS (Applicant Tracking System) analyst and senior recruiter. "
    "You are viewing page images of a candidate's SCANNED CV. There is no "
    "machine-readable text available for this document — the only source of truth is "
    "what you can see in the page images. You will transcribe the CV faithfully from "
    "the images and then analyze it against a target job description, returning a "
    "strict JSON object. Do not output anything outside the JSON. "
    "Only modify content within sections already present in the candidate's CV; "
    "do not invent new top-level sections."
)

def structure_context(structure: dict | None, max_chars: int = 6000) -> str:
    """Build a compact, position-aware text description of a document's layout.

    Gives the model layout/positioning awareness WITHOUT images for text-based
    uploads. Returns ``""`` when ``structure`` is ``None``.

    - ``{"kind": "pdf", "blocks": [...]}``: one line per block preserving position:
      ``page={p} x={x0:.0f} y={y0:.0f} size={size:.1f} bold={bold} {text}``
    - ``{"kind": "docx", ...}``: numbered paragraphs with style + bold markers,
      tables summarized as ``[TABLE rxc] row``, headers/footers at the end.

    Truncated to ``max_chars`` with a ``… [truncated]`` suffix.
    """
    if structure is None:
        return ""
    kind = structure.get("kind")
    if kind == "pdf":
        lines: list[str] = []
        for block in structure.get("blocks", []) or []:
            page = block.get("page", 0)
            x0 = block.get("x0", 0.0)
            y0 = block.get("y0", 0.0)
            size = block.get("size", 0.0)
            bold = bool(block.get("bold", False))
            text = (block.get("text", "") or "").strip()
            lines.append(f"page={page} x={x0:.0f} y={y0:.0f} size={size:.1f} bold={bold} {text}")
        result = "\n".join(lines)
    elif kind == "docx":
        parts: list[str] = []
        for i, para in enumerate(structure.get("paragraphs", []) or []):
            text = (para.get("text", "") or "").strip()
            style = para.get("style") or ""
            runs = para.get("runs", []) or []
            bold = any(bool(r.get("bold")) for r in runs)
            marker = " [B]" if bold else ""
            parts.append(f"{i + 1}. [{style}]{marker} {text}")
        for table in structure.get("tables", []) or []:
            rows = table.get("rows", []) or []
            if not rows:
                continue
            ncols = len(rows[0])
            for row in rows:
                cells = [c.strip() for c in row if c and c.strip()]
                parts.append(f"[TABLE {len(rows)}x{ncols}] {' | '.join(cells)}")
        for header in structure.get("headers_text", []) or []:
            parts.append(f"[HEADER] {header}")
        for footer in structure.get("footers_text", []) or []:
            parts.append(f"[FOOTER] {footer}")
        result = "\n".join(parts)
    else:
        return ""
    if len(result) > max_chars:
        result = result[:max_chars] + "… [truncated]"
    return result


def _VISION_ANALYZE_PROMPT(cv_outline: str) -> str:
    """Detailed, instruction-first prompt for analyzing a scanned (image) CV.

    The model only sees the page images, so the prompt must be explicit about
    transcribing first, then analyzing, and must include the exact JSON schema.
    """
    return (
        "You are analyzing a candidate's SCANNED CV. Below you will receive page "
        "images of the CV — one image per page, covering all pages in reading order. "
        "There is NO "
        "machine-readable text for this document — everything you know about the CV "
        "must come from the images.\n\n"
        "Follow these steps IN ORDER:\n\n"
        "STEP 1 — TRANSCRIBE THE CV FAITHFULLY.\n"
        "Read every page image and transcribe the CV exactly as printed: the "
        "candidate's name, contact details (email, phone, address, links), and every "
        "section (summary, experience, education, skills, projects, certifications, "
        "languages) with all of their content, in reading order. Preserve the wording "
        "of the original as closely as possible.\n\n"
        "STEP 2 — BASE ALL ANALYSIS ONLY ON WHAT IS VISIBLE.\n"
        "Do not invent facts, employers, dates, or skills that are not visible in the "
        "images. If any detail is illegible, blurred, cut off, or otherwise "
        "unreadable, say so explicitly in the relevant comment rather than guessing.\n\n"
        "STEP 3 — LAYOUT OBSERVATIONS.\n"
        "Describe the visual layout of the CV concretely and specifically, always "
        "referencing page numbers:\n"
        "  - Single-column or multi-column layout.\n"
        "  - The order of sections and which page each section starts on.\n"
        "  - Where the contact information and section headings sit on the page.\n"
        "  - Any elements that look misplaced, crowded, or overflowing.\n"
        "  - Spacing and alignment issues (ragged margins, inconsistent gaps).\n"
        "  - The approximate font-size hierarchy (which text is largest/smallest).\n\n"
        "STEP 4 — ANALYZE AGAINST THE JOB DESCRIPTION (OR GENERIC ATS-FRIENDLINESS).\n"
        "Assess the CV against the target job description below; if none is provided, "
        "assess its GENERIC ATS-friendliness on its own merits (structure, section "
        "ordering, naming, formatting, contact completeness, action verbs, "
        "quantification, length). Compute an ATS score (0-100), list matched and "
        "missing keywords (must be [] when no job description is provided), score each "
        "section, and provide comments. Include reorder suggestions where the "
        "visible section order would benefit, using type \"reorder\" with "
        "move_from / move_to / target_section populated, and \"rename\" suggestions "
        "for section headings that could be more ATS-friendly.\n\n"
        "Only ADD KEYWORDS and REPHRASE/REWRITE existing content. NEVER invent "
        "experience, projects, employers, education, skills, dates, or other facts. "
        "Never add new top-level sections. Do not invent content that is not present "
        "in the CV.\n\n"
        "Candidate CV outline (sections detected, may be incomplete for scanned "
        "files):\n" + (cv_outline or "(none)") + "\n\n"
        "Target Job Description:\n"
        "{{JOB_DESCRIPTION}}\n\n"
        "Return ONLY a JSON object with the exact shape:\n"
        "{"
        '"ats_score": int 0-100, '
        '"matched_keywords": [{"keyword": str, "count": int}], '
        '"missing_keywords": [{"keyword": str, "count": int}], '
        '"sections": [{"section": "summary|experience|skills|education", "score": int 0-100, "comment": str}], '
        '"comments": [str], '
        '"gaps": [str], '
        '"suggestions": [{"id": str, "section": str, "field": str, "index": int|null, '
        '"type": "rewrite|add|remove|reword|keyword|reorder|rename", '
        '"title": str, "original": str, "suggested": str, "reason": str, '
        '"priority": "high|medium|low", "impact": str, '
        '"move_from": str, "move_to": str, "target_section": str}]}'
    )


def structure_summary(cv) -> str:
    """Short STRUCTURE block describing the sections present in the parsed CVData.

    Lets the model keep suggestions within the candidate's existing sections.
    """
    parts: list[str] = []
    if cv.personal.name:
        parts.append(f"Name: {cv.personal.name}")
    if cv.summary:
        parts.append("Summary (1)")
    if cv.experience:
        roles = ", ".join((e.role or e.company or "role") for e in cv.experience)
        parts.append(f"Experience ({len(cv.experience)} role(s): {roles})")
    if cv.education:
        parts.append(f"Education ({len(cv.education)})")
    if cv.skills:
        groups = ", ".join((g.category or "skills") for g in cv.skills)
        parts.append(f"Skills ({len(cv.skills)} group(s): {groups})")
    if cv.projects:
        parts.append(f"Projects ({len(cv.projects)})")
    if cv.certifications:
        parts.append(f"Certifications ({len(cv.certifications)})")
    if cv.languages:
        parts.append(f"Languages ({len(cv.languages)})")
    if not parts:
        return "Sections present: (none detected)"
    return "Sections present: " + "; ".join(parts)


_ANTI_FABRICATION = (
    "Only ADD KEYWORDS and REPHRASE/REWRITE existing content. NEVER invent "
    "experience, projects, employers, education, skills, dates, or other facts. "
    "Never add new top-level sections. Do not invent content that is not present "
    "in the CV. "
    "Assess the ORDER of sections and the NAMING of section headings; where the CV "
    "would be more ATS-friendly, emit 'reorder' and 'rename' suggestions. "
    "For a section-order change use type \"reorder\" with \"target_section\" set to "
    "the section id (summary|experience|education|skills|projects|certifications|"
    "languages|custom_sections) and \"move_to\" one of \"up\"|\"down\"|\"top\"|"
    "\"bottom\" or a 1-based position string (\"1\" = first). To move an item within "
    "a section use type \"reorder\" with \"section\" set to the section id, "
    "\"move_from\" the item's current 0-based index (as a string) and \"move_to\" "
    "the target 0-based index or \"up\"/\"down\". For a heading-name improvement "
    "use type \"rename\" with \"target_section\" (or \"section\") set to the section "
    "id, \"original\" the current heading and \"suggested\" the improved heading; "
    "for a custom section set \"section\" to \"custom_sections\" and \"index\" to "
    "its 0-based position."
)

_ANALYSIS_SCHEMA = (
    '"ats_score": int 0-100, '
    '"matched_keywords": [{"keyword": str, "count": int}], '
    '"missing_keywords": [{"keyword": str, "count": int}], '
    '"sections": [{"section": "summary|experience|skills|education", "score": int 0-100, "comment": str}], '
    '"comments": [str], '
    '"gaps": [str], '
    '"suggestions": [{"id": str, "section": str, "field": str, "index": int|null, '
    '"type": "rewrite|add|remove|reword|keyword|reorder|rename", '
    '"title": str, "original": str, "suggested": str, "reason": str, '
    '"priority": "high|medium|low", "impact": str, '
    '"move_from": str, "move_to": str, "target_section": str}]}'
)


def _analyze_jd_prompt(job_description: str, md: str, summary: str) -> str:
    """JD-mode analysis prompt: CV (markdown) analyzed against the target JD."""
    return (
        "Given the target job description and the candidate's CV (markdown) below, "
        "produce a JSON object with the exact shape: {"
        + _ANALYSIS_SCHEMA
        + "\n\nTarget Job Description:\n"
        + job_description
        + "\n\nCandidate CV (markdown):\n"
        + md
        + "\n\n"
        + _ANTI_FABRICATION
        + "\n\n"
        + summary
    )


def _analyze_generic_prompt(md: str, summary: str) -> str:
    """Generic-mode analysis prompt: ATS-friendliness of the CV on its own merits."""
    return (
        "Given the candidate's CV (markdown) below — no job description was provided — "
        "produce a GENERIC ATS-friendliness assessment of the CV on its own merits "
        "(structure, section ordering, naming, formatting, contact completeness, "
        "action verbs, quantification, length). Return a JSON object with the exact "
        "shape: {"
        + _ANALYSIS_SCHEMA
        + "\n\nSince no job description was provided, matched_keywords and "
        "missing_keywords must be empty lists ([]), and gaps should list generic ATS "
        "issues (missing standard sections, weak section naming, missing contact "
        "details, etc.).\n\nCandidate CV (markdown):\n"
        + md
        + "\n\n"
        + _ANTI_FABRICATION
        + "\n\n"
        + summary
    )


def _as_int(value, lo: int = 0, hi: int = 100, default: int = 0) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _keywords(data, key: str) -> list[KeywordMatch]:
    out: list[KeywordMatch] = []
    for entry in data.get(key, []) or []:
        if isinstance(entry, str):
            out.append(KeywordMatch(keyword=entry, present=False))
        elif isinstance(entry, dict):
            out.append(
                KeywordMatch(
                    keyword=str(entry.get("keyword", "")),
                    present=bool(entry.get("present", False)),
                    count=_as_int(entry.get("count"), 0, 1000000, 0),
                )
            )
    return out


def _sections(data) -> list[SectionAssessment]:
    out: list[SectionAssessment] = []
    for entry in data.get("sections", []) or []:
        if isinstance(entry, dict):
            out.append(
                SectionAssessment(
                    section=str(entry.get("section", "")),
                    score=_as_int(entry.get("score")),
                    comment=str(entry.get("comment", "")),
                )
            )
    return out


def _build_report(data: dict) -> AnalysisReport:
    return AnalysisReport(
        ats_score=_as_int(data.get("ats_score")),
        matched_keywords=_keywords(data, "matched_keywords"),
        missing_keywords=_keywords(data, "missing_keywords"),
        sections=_sections(data),
        comments=[str(c) for c in data.get("comments", []) or []],
        gaps=[str(c) for c in data.get("gaps", []) or []],
        suggestions=_suggestions(data),
    )


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\d{3}[\s.-]\d{3}[\s.-]\d{4}")


def _generic_fallback_report(text: str) -> AnalysisReport:
    """Heuristic generic ATS assessment (no JD) when the LLM is unavailable.

    Scores the CV on structural completeness: name, contact details, summary,
    experience, education, and skills. Gaps list the missing standard elements.
    Never fabricates content — it only reports what was (or was not) detected.
    """
    lower = text.lower()
    has_name = bool(re.search(r"[A-Z][a-z]+ [A-Z][a-z]+", text))
    has_contact = bool(_EMAIL_RE.search(text)) or bool(_PHONE_RE.search(text))
    has_summary = any(k in lower for k in ("summary", "profile", "objective", "professional summary"))
    has_experience = any(k in lower for k in ("experience", "work history", "employment", "career"))
    has_education = any(k in lower for k in ("education", "university", "degree", "academic"))
    has_skills = any(k in lower for k in ("skills", "technologies", "competencies"))
    present = [has_name, has_contact, has_summary, has_experience, has_education, has_skills]
    score = round(100 * sum(present) / len(present)) if present else 0

    def section_score(found: bool) -> int:
        return 100 if found else 40

    sections = [
        SectionAssessment(
            section="summary",
            score=section_score(has_summary),
            comment="Summary section present." if has_summary else "No summary/profile section detected.",
        ),
        SectionAssessment(
            section="experience",
            score=section_score(has_experience),
            comment="Experience section present." if has_experience else "No experience/work-history section detected.",
        ),
        SectionAssessment(
            section="education",
            score=section_score(has_education),
            comment="Education section present." if has_education else "No education section detected.",
        ),
        SectionAssessment(
            section="skills",
            score=section_score(has_skills),
            comment="Skills section present." if has_skills else "No skills section detected.",
        ),
    ]
    gaps: list[str] = []
    if not has_name:
        gaps.append("Candidate name not detected — CVs should lead with the full name.")
    if not has_contact:
        gaps.append("No contact details (email/phone) detected.")
    if not has_summary:
        gaps.append("No summary/profile section — ATS and recruiters expect a short professional summary near the top.")
    if not has_experience:
        gaps.append("No experience/work-history section detected.")
    if not has_education:
        gaps.append("No education section detected.")
    if not has_skills:
        gaps.append("No skills section detected.")
    return AnalysisReport(
        ats_score=score,
        sections=sections,
        comments=[
            "LLM analysis was unavailable; showing local heuristic results.",
            "No job description provided — this is a generic ATS-friendliness assessment.",
        ],
        gaps=gaps,
    )


def _fallback_report(text: str, job_description: str) -> AnalysisReport:
    if not job_description.strip():
        return _generic_fallback_report(text)
    cv_words = set(re.findall(r"[A-Za-z][A-Za-z-]{2,}", text.lower()))
    jd_words = [_w for _w in re.findall(r"[A-Za-z][A-Za-z-]{2,}", job_description.lower()) if _w not in _STOPWORDS]
    from collections import Counter

    counter = Counter(jd_words)
    matched = []
    missing = []
    for keyword, count in counter.items():
        if keyword in cv_words:
            matched.append(KeywordMatch(keyword=keyword, present=True, count=count))
        else:
            missing.append(KeywordMatch(keyword=keyword, present=False, count=count))
    matched.sort(key=lambda k: -k.count)
    missing.sort(key=lambda k: -k.count)
    total = len(counter)
    score = round(100 * len(matched) / total) if total else 0
    return AnalysisReport(
        ats_score=score,
        matched_keywords=matched[:30],
        missing_keywords=missing[:30],
        sections=[
            SectionAssessment(section="summary", score=score, comment="Fallback heuristic summary score."),
            SectionAssessment(section="skills", score=score, comment="Fallback keyword overlap score."),
        ],
        comments=["LLM analysis was unavailable; showing local heuristic results."],
    )


_STOPWORDS = {
    "the", "and", "for", "with", "you", "will", "our", "your", "are", "that", "this",
    "have", "has", "from", "they", "their", "them", "all", "can", "experience", "years",
}


def analyze(
    cv,
    text: str,
    job_description: str = "",
    client: LLMClient = None,  # required positionally by all callers (4th arg)
    *,
    images: list[bytes] | None = None,
    structure: dict | None = None,
) -> AnalysisReport:
    if client is None:
        raise TypeError("analyze() requires an LLM client")
    mode = "generic" if not job_description.strip() else "jd"
    logger.info(
        "Starting CV analysis (mode=%s, job_description chars=%d)", mode, len(job_description)
    )
    if images:
        jd_placeholder = job_description if job_description.strip() else (
            "(no job description provided — assess generic ATS-friendliness; "
            "matched/missing keywords must be [] and gaps are generic ATS issues)"
        )
        prompt = _VISION_ANALYZE_PROMPT(structure_summary(cv)).replace(
            "{{JOB_DESCRIPTION}}", jd_placeholder
        )
        content: list[dict] = [text_part(prompt)]
        for img in _cap_vision_images(images, "analyze"):
            content.append(image_part(base64.b64encode(img).decode("ascii")))
        messages = [
            {"role": "system", "content": _VISION_SYSTEM},
            {"role": "user", "content": content},
        ]
    else:
        from .markdown import document_markdown

        md = document_markdown(cv, text, structure)
        summary = structure_summary(cv)
        if job_description.strip():
            prompt = _analyze_jd_prompt(job_description, md, summary)
        else:
            prompt = _analyze_generic_prompt(md, summary)
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ]
    try:
        raw = client.chat(messages, temperature=0.2, max_tokens=16000)
    except (LLMConfigError, LLMError):
        raise
    except Exception as exc:
        logger.exception("Unexpected error during CV analysis")
        raise LLMError(f"LLM analysis failed: {exc}") from exc
    try:
        return _build_report(_extract_json(raw))
    except (ValueError, json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.exception("Model returned unparseable analysis JSON")
        raise LLMError(f"Model returned unparseable analysis JSON: {exc}") from exc
