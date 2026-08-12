import base64
import json
import logging
import re
import uuid

from ..llm import LLMClient, image_part, text_part
from .json_util import cap_vision_images, extract_json, parse_suggestions
from .models import (
    ARRAY_SECTIONS,
    DEFAULT_SECTION_ORDER,
    TITLED_SECTIONS,
    CertificationItem,
    CustomSection,
    CVData,
    EducationItem,
    ExperienceItem,
    LanguageItem,
    ProjectItem,
    SkillGroup,
    Suggestion,
)
from .writer import cv_to_text

logger = logging.getLogger("cviq")

_SYSTEM = (
    "You are an expert ATS recruiter and CV writer. You produce tailored suggestions "
    "as a strict JSON object. Do not output anything outside the JSON. "
    "Only modify content within sections already present in the candidate's CV; "
    "do not invent new top-level sections."
)

_VISION_SYSTEM = (
    "You are an expert ATS recruiter and CV writer. You are viewing page images of a "
    "candidate's SCANNED CV. There is no machine-readable text available for this "
    "document — the only source of truth is what you can see in the page images. You "
    "will transcribe the CV faithfully from the images and then produce tailored "
    "suggestions against a target job description, returning a strict JSON object. Do "
    "not output anything outside the JSON. Only modify content within sections already "
    "present in the candidate's CV; do not invent new top-level sections."
)


def _VISION_SUGGEST_PROMPT(cv_outline: str) -> str:
    """Detailed, instruction-first prompt for tailoring a scanned (image) CV.

    The model only sees the page images, so the prompt must be explicit about
    transcribing first, then making layout observations, then emitting suggestions
    (including layout/reorder suggestions) in the exact JSON schema.
    """
    return (
        "You are tailoring a candidate's SCANNED CV to a target job description. "
        "Below you will receive page images of the CV — one image per page, covering "
        "all pages in reading order. There is NO machine-readable text for this "
        "document — everything you know about the CV must come from the images.\n\n"
        "Follow these steps IN ORDER:\n\n"
        "STEP 1 — TRANSCRIBE THE CV FAITHFULLY.\n"
        "Read every page image and transcribe the CV exactly as printed: the "
        "candidate's name, contact details, and every section (summary, experience, "
        "education, skills, projects, certifications, languages) with all of their "
        "content, in reading order. Preserve the original wording as closely as "
        "possible.\n\n"
        "STEP 2 — BASE ALL ANALYSIS ONLY ON WHAT IS VISIBLE.\n"
        "Do not invent facts, employers, dates, or skills that are not visible in the "
        "images. If any detail is illegible, blurred, cut off, or otherwise "
        "unreadable, say so explicitly in the relevant reason/comment rather than "
        "guessing.\n\n"
        "STEP 3 — LAYOUT OBSERVATIONS.\n"
        "Describe the visual layout of the CV concretely and specifically, always "
        "referencing page numbers:\n"
        "  - Single-column or multi-column layout.\n"
        "  - The order of sections and which page each section starts on.\n"
        "  - Where the contact information and section headings sit on the page.\n"
        "  - Any elements that look misplaced, crowded, or overflowing.\n"
        "  - Spacing and alignment issues (ragged margins, inconsistent gaps).\n"
        "  - The approximate font-size hierarchy (which text is largest/smallest).\n\n"
        "STEP 4 — EMIT TAILORED SUGGESTIONS.\n"
        "Against the target job description below (or, if none is provided, assess "
        "the CV's GENERIC ATS-friendliness on its own merits), produce up to 10 "
        "concrete, tailored edits. Every suggestion must reference concrete terms "
        "from the job description when one is provided. Where the visible section "
        "order would benefit, include reorder suggestions using type \"reorder\" "
        "with move_from / move_to / target_section populated, and "
        "\"rename\" suggestions for section headings that could be more ATS-friendly "
        "(compare the observed heading names and order against standard ATS "
        "headings).\n\n"
        "Only ADD KEYWORDS and REPHRASE/REWRITE existing content. NEVER invent "
        "experience, projects, employers, education, skills, dates, or other facts. "
        "Never add new top-level sections. Do not invent content that is not present "
        "in the CV.\n\n"
        "Candidate CV outline (sections detected, may be incomplete for scanned "
        "files):\n" + (cv_outline or "(none)") + "\n\n"
        "Target Job Description:\n"
        "{{JOB_DESCRIPTION}}\n\n"
        "Return ONLY a JSON object with key \"suggestions\": a list of up to 10 "
        "objects of the exact shape:\n"
        '{"id": str, "section": str, "field": str, "index": int|null, '
        '"type": "rewrite|add|remove|reword|keyword|reorder|rename", '
        '"title": str, "original": str, "suggested": str, "reason": str, '
        '"priority": "high|medium|low", "impact": str, '
        '"move_from": str, "move_to": str, "target_section": str, '
        '"field_path": str, "rationale": str}\n'
        '"field_path" uses JSON-ish syntax so edits can be applied automatically, '
        'e.g. "personal.email", "summary", "experience[0].bullets[1]", '
        '"skills[0].skills", "custom_sections[0].bullets[0]". Leave "field_path" '
        'empty when it does not apply. "rationale" may duplicate "reason".'
    )

# Canonical section name -> aliases the LLM may emit (matched case-insensitively).
_SECTION_ALIASES = {
    "summary": {"summary", "profile", "professional summary", "objective", "about"},
    "experience": {"experience", "work experience", "employment", "work history", "career history"},
    "education": {"education", "academic background"},
    "skills": {"skills", "technical skills", "core competencies", "competencies", "technologies"},
    "projects": {"projects", "project experience", "select projects"},
    "certifications": {"certifications", "certificates", "licenses", "certification"},
    "languages": {"languages", "language"},
    "personal": {"contact", "personal details", "contact information"},
}

# Canonical field name -> aliases the LLM may emit (matched case-insensitively).
_FIELD_ALIASES = {
    "bullets": {"bullets", "bullet_points", "bullet points", "bullets_rewrite", "bulletpoints"},
    "skills": {"skills", "technical_skills", "technical skills"},
    "summary": {"summary", "professional_summary", "professional summary"},
    "degree": {"degree", "degree_name"},
    "institution": {"institution", "institution_name"},
    "role": {"role", "job_title", "role_title"},
    "company": {"company", "company_name"},
    "location": {"location"},
    "dates": {"dates", "date_range"},
    "description": {"description", "project_description"},
    "name": {"name", "certification_name", "language_name"},
    "level": {"level", "language_level"},
    "issuer": {"issuer"},
    "year": {"year"},
    "field": {"field", "field_of_study"},
    "gpa": {"gpa"},
    "link": {"link"},
}


def _normalize_suggestion(suggestion: Suggestion) -> tuple[str, str]:
    """Map LLM-emitted section/field aliases to the canonical names apply logic uses."""
    section = (suggestion.section or "").strip().lower()
    field = (suggestion.field or "").strip().lower()

    for canonical, aliases in _SECTION_ALIASES.items():
        if section in aliases:
            section = canonical
            break

    # personal_info.<field> -> personal field
    if field.startswith("personal_info."):
        field = field.split(".", 1)[1]
    for canonical, aliases in _FIELD_ALIASES.items():
        if field in aliases:
            field = canonical
            break

    return section, field


_SUGGESTION_SCHEMA = (
    '"id": str, "section": str, "field": str, "index": int|null, '
    '"type": "rewrite|add|remove|reword|keyword|reorder|rename", '
    '"title": str, "original": str, "suggested": str, "reason": str, '
    '"priority": "high|medium|low", "impact": str, '
    '"move_from": str, "move_to": str, "target_section": str, '
    '"field_path": str, "rationale": str'
)

_FIELD_PATH_INSTRUCTION = (
    '"field_path" uses JSON-ish syntax so edits can be applied automatically, '
    'e.g. "personal.email", "summary", "experience[0].bullets[1]", '
    '"skills[0].skills", "custom_sections[0].bullets[0]". Leave "field_path" '
    'empty when it does not apply. "rationale" may duplicate "reason".'
)

_TAILOR_ANTI_FABRICATION = (
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


def _suggest_jd_prompt(job_description: str, md: str, summary: str) -> str:
    """JD-mode tailor prompt: CV (markdown) tailored against the target JD."""
    return (
        "Given the target job description and the candidate's CV (markdown) below, "
        'return a JSON object with key "suggestions": a list of up to 10 objects of '
        "shape {" + _SUGGESTION_SCHEMA + "}. "
        + _FIELD_PATH_INSTRUCTION
        + " "
        "Every suggestion must reference concrete terms from the job description.\n\n"
        "Target Job Description:\n" + job_description
        + "\n\nCandidate CV (markdown):\n" + md
        + "\n\n" + _TAILOR_ANTI_FABRICATION
        + "\n\n" + summary
    )


def _suggest_generic_prompt(md: str, summary: str) -> str:
    """Generic-mode tailor prompt: improvements to the CV on its own merits."""
    return (
        "Given the candidate's CV (markdown) below — no job description was provided — "
        "produce GENERIC ATS-friendliness improvement suggestions for the CV on its "
        'own merits. Return a JSON object with key "suggestions": a list of up to 10 '
        "objects of shape {" + _SUGGESTION_SCHEMA + "}. "
        + _FIELD_PATH_INSTRUCTION
        + " "
        "Since no job description was provided, do not emit 'keyword' suggestions "
        "(there are no keywords to match); focus on structure, section ordering, "
        "heading naming, formatting, and content quality.\n\n"
        "Candidate CV (markdown):\n" + md
        + "\n\n" + _TAILOR_ANTI_FABRICATION
        + "\n\n" + summary
    )


def generate_suggestions(
    cv: CVData,
    text: str,
    job_description: str = "",
    client: LLMClient = None,  # required positionally by all callers (4th arg)
    *,
    images: list[bytes] | None = None,
    structure: dict | None = None,
) -> list[Suggestion]:
    if client is None:
        raise TypeError("generate_suggestions() requires an LLM client")
    mode = "generic" if not job_description.strip() else "jd"
    logger.info(
        "Generating tailored suggestions (mode=%s, job_description chars=%d)",
        mode,
        len(job_description),
    )
    from .analyzer import structure_summary
    from .markdown import document_markdown

    if images:
        jd_placeholder = job_description if job_description.strip() else (
            "(no job description provided — assess generic ATS-friendliness; "
            "do not emit 'keyword' suggestions and do not invent content)"
        )
        prompt = _VISION_SUGGEST_PROMPT(structure_summary(cv)).replace(
            "{{JOB_DESCRIPTION}}", jd_placeholder
        )
        content: list[dict] = [text_part(prompt)]
        for img in cap_vision_images(images, "tailor suggest"):
            content.append(image_part(base64.b64encode(img).decode("ascii")))
        messages = [
            {"role": "system", "content": _VISION_SYSTEM},
            {"role": "user", "content": content},
        ]
    else:
        md = document_markdown(cv, text, structure)
        summary = structure_summary(cv)
        if job_description.strip():
            prompt = _suggest_jd_prompt(job_description, md, summary)
        else:
            prompt = _suggest_generic_prompt(md, summary)
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ]
    try:
        raw = client.chat(messages, temperature=0.3, max_tokens=16000)
        data = extract_json(raw)
        suggestions = parse_suggestions(data)
    except Exception:
        logger.exception("LLM suggestion generation failed; falling back to keyword suggestions")
        return _keyword_suggestions(cv, text or cv_to_text(cv), job_description)
    if not suggestions:
        return _keyword_suggestions(cv, text or cv_to_text(cv), job_description)
    return suggestions


def _keyword_suggestions(cv: CVData, text: str, job_description: str) -> list[Suggestion]:
    import re

    from .analyzer import _STOPWORDS
    from collections import Counter

    # No job description: no keywords to match — never fabricate keyword suggestions.
    if not job_description.strip():
        return []
    cv_words = set(re.findall(r"[A-Za-z][A-Za-z-]{2,}", text.lower()))
    counter = Counter(w for w in re.findall(r"[A-Za-z][A-Za-z-]{2,}", job_description.lower()) if w not in _STOPWORDS)
    suggestions: list[Suggestion] = []
    for keyword, _count in counter.most_common(6):
        if keyword in cv_words:
            continue
        suggestions.append(
            Suggestion(
                id=str(uuid.uuid4()),
                section="skills",
                field="skills",
                index=None,
                type="keyword",
                title=f"Add keyword: {keyword}",
                original="",
                suggested=keyword,
                reason=f"The job description emphasizes '{keyword}', which is missing from your CV.",
                priority="high",
                impact="ATS keyword match",
            )
        )
    return suggestions


_FIELD_PATH_RE = re.compile(
    r"^(?P<root>[a-z_]+)(?:\[(?P<index>\d+)\])?"
    r"(?:\.(?P<field>[a-z_]+)(?:\[(?P<sub>\d+)\])?)?"
    r"(?:\.(?P<subfield>[a-z_]+))?$"
)

_DATE_RANGE_FIELDS = ("dates",)


def _parse_field_path(path: str) -> tuple[str, int | None, str | None, int | None, str | None] | None:
    """Parse ``field_path`` into ``(root, index, field, sub, subfield)``.

    Supports: ``summary``, ``personal.<field>``, ``experience[0].bullets[1]``,
    ``education[0].degree``, ``skills[0].skills``, ``custom_sections[0].bullets``,
    and the sub-field forms ``experience[0].dates.start`` /
    ``education[1].dates.end`` (write the DateRange sub-field). Returns ``None``
    for malformed paths (caller then leaves the CV unchanged).
    """
    match = _FIELD_PATH_RE.match((path or "").strip().lower())
    if not match:
        return None
    parts = match.groupdict()
    index = int(parts["index"]) if parts["index"] is not None else None
    sub = int(parts["sub"]) if parts["sub"] is not None else None
    return parts["root"], index, parts["field"], sub, parts["subfield"]


def _split_edit(suggested: str) -> list[str]:
    return [line.strip() for line in suggested.splitlines() if line.strip()]


def _apply_list_edit(target: list[str], suggestion: Suggestion, sub: int | None) -> bool:
    """Shared bullet/skill-list edit: returns True when the path was handled."""
    stype = suggestion.type
    suggested = (suggestion.suggested or "").strip()
    original = (suggestion.original or "").strip()
    if sub is not None:
        if not (0 <= sub < len(target)):
            return False
        if stype == "remove":
            del target[sub]
        elif suggested:
            target[sub] = suggested
        return True
    if stype in ("rewrite", "reword") and suggested:
        target[:] = _split_edit(suggested)
    elif stype in ("add", "keyword") and suggested:
        for line in _split_edit(suggested):
            if not _contains_line(target, line):
                target.append(line)
    elif stype == "remove" and original:
        target[:] = [b for b in target if b != original]
    return True


def _apply_dates(dates, suggested: str, subfield: str | None) -> None:
    """Write a DateRange from a suggestion — whole range OR one sub-field.

    ``experience[i].dates`` writes both parts (``"2020 - 2022"`` syntax);
    ``experience[i].dates.start`` / ``dates.end`` write exactly one sub-field.
    An empty / unset ``subfield`` keeps the current full-range behavior.
    """
    if subfield == "start":
        dates.start = suggested
    elif subfield == "end":
        dates.end = suggested
    elif suggested:
        parts = [p.strip() for p in suggested.split("-", 1)]
        dates.start = parts[0]
        if len(parts) > 1:
            dates.end = parts[1]


# ---------------------------------------------------------------------------
# Structural suggestions (reorder / rename) + idempotent-append helpers
# ---------------------------------------------------------------------------


def _contains_line(target: list[str], value: str) -> bool:
    """Trimmed, case-insensitive membership test — keeps appends idempotent."""
    needle = (value or "").strip().lower()
    return any((line or "").strip().lower() == needle for line in target)


def _contains_named(items, name: str) -> bool:
    """Trimmed, case-insensitive ``name`` test for certification/language items."""
    needle = (name or "").strip().lower()
    return any((getattr(item, "name", "") or "").strip().lower() == needle for item in items)


def _parse_position(value) -> int | None:
    """Parse an integer position from an int or a string; None when unparseable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_section_id(value: str) -> str:
    """Map a section name/alias to its canonical section id (best effort)."""
    v = (value or "").strip().lower()
    for canonical, aliases in _SECTION_ALIASES.items():
        if v in aliases:
            return canonical
    return v


def _move_item(items: list, frm: int, to: int) -> bool:
    """Move ``items[frm]`` to position ``to`` (clamped). True when the list changed."""
    if not (0 <= frm < len(items)):
        return False
    to = max(0, min(len(items) - 1, to))
    if to == frm:
        return False
    item = items.pop(frm)
    items.insert(to, item)
    return True


def _apply_reorder(new: CVData, suggestion: Suggestion) -> CVData:
    """Apply a ``reorder`` suggestion.

    Item-level: ``section`` names an array section and ``move_from`` parses as a
    0-based index; ``move_to`` is a 0-based index or ``up``/``down`` relative to
    ``move_from``. Section-level: ``target_section`` is a known section id and is
    moved within ``cv.section_order`` (materialized to the default order when
    empty); ``move_to`` is ``up``/``down``/``top``/``bottom`` or a 1-based
    position string. Unparseable requests leave the CV unchanged.
    """
    section, _field = _normalize_suggestion(suggestion)
    move_to = (suggestion.move_to or "").strip()
    low = move_to.lower()

    # Item-level move within an array section.
    frm = _parse_position(suggestion.move_from)
    if section in ARRAY_SECTIONS and frm is not None:
        items = getattr(new, section, None)
        if isinstance(items, list):
            if low == "up":
                to = frm - 1
            elif low == "down":
                to = frm + 1
            else:
                to = _parse_position(move_to)
            if to is not None:
                _move_item(items, frm, to)
        return new

    # Section-level move within cv.section_order.
    target = _normalize_section_id(suggestion.target_section)
    if target not in DEFAULT_SECTION_ORDER:
        return new
    order = list(new.section_order) if new.section_order else list(DEFAULT_SECTION_ORDER)
    if target not in order:
        return new
    frm = order.index(target)
    if low == "up":
        to = frm - 1
    elif low == "down":
        to = frm + 1
    elif low == "top":
        to = 0
    elif low == "bottom":
        to = len(order) - 1
    else:
        pos = _parse_position(move_to)
        if pos is None:
            return new
        to = pos - 1  # 1-based position string
    to = max(0, min(len(order) - 1, to))
    if to == frm:
        return new
    new_order = list(order)
    item = new_order.pop(frm)
    new_order.insert(to, item)
    if new_order != list(new.section_order):
        new.section_order = new_order
    return new


def _apply_rename(new: CVData, suggestion: Suggestion) -> CVData:
    """Apply a ``rename`` suggestion.

    Standard section (``target_section``/``section`` is a titled section id):
    record a heading override in ``cv.section_titles``. Custom section
    (``custom``/``custom_sections`` with a valid ``index``): rename that custom
    section's own ``title``. ``original`` carries the old heading (informational).
    """
    suggested = (suggestion.suggested or "").strip()
    if not suggested:
        return new
    section, _field = _normalize_suggestion(suggestion)
    if section in ("custom", "custom_sections"):
        idx = suggestion.index
        items = new.custom_sections
        if isinstance(idx, int) and 0 <= idx < len(items):
            if items[idx].title != suggested:
                items[idx].title = suggested
        return new
    target = _normalize_section_id(suggestion.target_section) or section
    if target in TITLED_SECTIONS and new.section_titles.get(target) != suggested:
        new.section_titles[target] = suggested
    return new


def _apply_field_path(new: CVData, suggestion: Suggestion) -> CVData | None:
    """Apply a suggestion via its ``field_path``. Returns the modified CV when the
    path was handled, else ``None`` (the caller keeps the CV unchanged)."""
    parsed = _parse_field_path(suggestion.field_path)
    if parsed is None:
        return None
    root, index, field, sub, subfield = parsed
    stype = suggestion.type
    suggested = (suggestion.suggested or "").strip()
    original = (suggestion.original or "").strip()

    # A nested sub-field is only meaningful on DateRange ("dates.start/end");
    # any other "x.y" suffix is an unknown path — silent no-op, never a crash.
    if subfield is not None and field != "dates":
        return None

    if root == "summary":
        if stype == "remove":
            new.summary = ""
        elif stype == "add" and suggested:
            new.summary = (new.summary + " " + suggested).strip()
        elif suggested:
            new.summary = suggested
        elif original:
            new.summary = original
        return new

    if root == "personal":
        p = new.personal
        if field and field in ("name", "title", "email", "phone", "location", "website", "linkedin", "github"):
            if stype == "remove":
                setattr(p, field, "")
            else:
                setattr(p, field, suggested or original)
        return new

    if root == "experience":
        items = new.experience
        if index is not None and 0 <= index < len(items):
            item = items[index]
            if field == "bullets":
                if not _apply_list_edit(item.bullets, suggestion, sub) and sub is not None:
                    return None
            elif field == "company" or field == "role" or field == "location":
                if suggested or original:
                    setattr(item, field, suggested or original)
            elif field in _DATE_RANGE_FIELDS:
                _apply_dates(item.dates, suggested, subfield)
        elif stype == "add" and suggested and field == "bullets":
            items.append(ExperienceItem(bullets=_split_edit(suggested)))
        return new

    if root == "education":
        items = new.education
        if index is not None and 0 <= index < len(items):
            item = items[index]
            if field in ("degree", "institution", "field", "gpa"):
                if suggested or original:
                    setattr(item, field, suggested or original)
            elif field in _DATE_RANGE_FIELDS:
                _apply_dates(item.dates, suggested, subfield)
        elif stype == "add" and field in ("institution", "degree") and suggested:
            items.append(EducationItem(**{field: suggested}))
        return new

    if root == "projects":
        items = new.projects
        if index is not None and 0 <= index < len(items):
            item = items[index]
            if field in ("name", "link", "description"):
                if stype == "remove":
                    setattr(item, field, "")
                elif suggested or original:
                    setattr(item, field, suggested or original)
            elif field == "bullets":
                if not _apply_list_edit(item.bullets, suggestion, sub) and sub is not None:
                    return None
        elif stype == "add" and field == "bullets" and suggested and sub is None:
            items.append(ProjectItem(bullets=_split_edit(suggested)))
        return new

    if root == "skills":
        groups = new.skills
        if index is not None and 0 <= index < len(groups):
            group = groups[index]
            if field == "category":
                if suggested or original:
                    group.category = suggested or original
            elif field == "skills":
                _apply_list_edit(group.skills, suggestion, sub)
        elif sub is None and stype in ("add", "keyword") and suggested:
            flat = {s.lower() for group in groups for s in group.skills}
            for line in _split_edit(suggested):
                if line.lower() not in flat:
                    flat.add(line.lower())
                    if groups:
                        groups[0].skills.append(line)
                    else:
                        groups.append(SkillGroup(skills=[line]))
        return new

    if root == "custom_sections":
        items = new.custom_sections
        if index is not None and 0 <= index < len(items):
            item = items[index]
            if field == "title":
                if stype == "remove":
                    item.title = ""
                elif suggested or original:
                    item.title = suggested or original
            elif field == "bullets":
                if not _apply_list_edit(item.bullets, suggestion, sub) and sub is not None:
                    return None
        elif stype == "add" and field == "bullets" and suggested:
            items.append(CustomSection(bullets=_split_edit(suggested)))
        return new

    if root == "certifications":
        items = new.certifications
        if index is not None and 0 <= index < len(items):
            item = items[index]
            if field == "name" and stype == "remove":
                del items[index]
            elif field in ("name", "issuer", "year") and (suggested or original):
                setattr(item, field, suggested or original)
        elif stype == "add" and suggested and not _contains_named(items, suggested):
            items.append(CertificationItem(name=suggested))
        return new

    if root == "languages":
        items = new.languages
        if index is not None and 0 <= index < len(items):
            item = items[index]
            if field == "name" and stype == "remove":
                del items[index]
            elif field in ("name", "level") and (suggested or original):
                setattr(item, field, suggested or original)
        elif stype == "add" and suggested and not _contains_named(items, suggested):
            items.append(LanguageItem(name=suggested))
        return new

    return None


def apply_suggestion(cv: CVData, suggestion: Suggestion) -> CVData:
    new = cv.model_copy(deep=True)

    # Structural suggestions first: reorder/rename/layout never reach the content
    # branches (a stray field_path on a structural suggestion is LLM noise).
    # ``layout`` is accepted as a no-op for backwards compatibility (no longer
    # generated by the prompts).
    stype = (suggestion.type or "").strip().lower()
    if stype == "reorder":
        return _apply_reorder(new, suggestion)
    if stype == "rename":
        return _apply_rename(new, suggestion)
    if stype == "layout":
        return new

    # field_path first: when present, honor it (falls back to section/field/index
    # logic only when empty). A malformed path never crashes — the CV is returned
    # unchanged.
    if (suggestion.field_path or "").strip():
        _apply_field_path(new, suggestion)
        return new

    section, field = _normalize_suggestion(suggestion)
    index = suggestion.index
    stype = suggestion.type
    suggested = (suggestion.suggested or "").strip()
    original = (suggestion.original or "").strip()

    if section == "summary":
        if stype == "remove":
            new.summary = ""
        elif stype == "add" and suggested:
            new.summary = (new.summary + " " + suggested).strip()
        elif suggested:
            new.summary = suggested
        elif original:
            new.summary = original

    elif section == "experience":
        items = new.experience
        if field == "bullets":
            if stype == "add" and suggested:
                if isinstance(index, int) and 0 <= index < len(items):
                    if not _contains_line(items[index].bullets, suggested):
                        items[index].bullets.append(suggested)
                else:
                    new.experience.append(ExperienceItem(bullets=[suggested]))
            elif stype == "remove" and original:
                for item in items:
                    item.bullets = [b for b in item.bullets if b != original]
            elif stype in ("rewrite", "reword") and suggested and isinstance(index, int) and 0 <= index < len(items):
                items[index].bullets = [b.strip() for b in suggested.splitlines() if b.strip()]
        elif field in ("role", "company", "location") and isinstance(index, int) and 0 <= index < len(items):
            setattr(items[index], field, suggested or original)
        elif field == "dates" and isinstance(index, int) and 0 <= index < len(items):
            if suggested:
                parts = [p.strip() for p in suggested.split("-", 1)]
                items[index].dates.start = parts[0]
                if len(parts) > 1:
                    items[index].dates.end = parts[1]

    elif section == "education":
        items = new.education
        idx = index if isinstance(index, int) else (0 if len(items) == 1 else None)
        if field in ("degree", "institution", "field", "gpa") and idx is not None and 0 <= idx < len(items):
            setattr(items[idx], field, suggested or original)
        elif field == "dates" and idx is not None and 0 <= idx < len(items):
            if suggested:
                parts = [p.strip() for p in suggested.split("-", 1)]
                items[idx].dates.start = parts[0]
                if len(parts) > 1:
                    items[idx].dates.end = parts[1]

    elif section == "skills":
        if stype == "remove" and original:
            for group in new.skills:
                group.skills = [s for s in group.skills if s != original]
        elif suggested:
            flat = {s.lower() for group in new.skills for s in group.skills}
            if suggested.lower() not in flat:
                if new.skills:
                    new.skills[0].skills.append(suggested)
                else:
                    new.skills.append(SkillGroup(skills=[suggested]))

    elif section == "projects":
        items = new.projects
        if field == "description" and isinstance(index, int) and 0 <= index < len(items):
            items[index].description = suggested or original
        elif field == "name" and isinstance(index, int) and 0 <= index < len(items):
            items[index].name = suggested or original
        elif field == "link" and isinstance(index, int) and 0 <= index < len(items):
            items[index].link = suggested or original
        elif field == "bullets":
            if stype == "remove" and original:
                for item in items:
                    item.bullets = [b for b in item.bullets if b != original]
            elif suggested:
                if isinstance(index, int) and 0 <= index < len(items):
                    if not _contains_line(items[index].bullets, suggested):
                        items[index].bullets.append(suggested)
                else:
                    new.projects.append(ProjectItem(bullets=[suggested]))

    elif section == "certifications":
        items = new.certifications
        if isinstance(index, int) and 0 <= index < len(items):
            item = items[index]
            if field == "name" and stype == "remove":
                del items[index]
            elif field == "name" and suggested:
                item.name = suggested
            elif field == "issuer" and suggested:
                item.issuer = suggested
            elif field == "year" and suggested:
                item.year = suggested
        elif suggested and stype == "add" and not _contains_named(items, suggested):
            new.certifications.append(CertificationItem(name=suggested))

    elif section == "languages":
        items = new.languages
        if isinstance(index, int) and 0 <= index < len(items):
            item = items[index]
            if field == "name" and stype == "remove":
                del items[index]
            elif field == "name" and suggested:
                item.name = suggested
            elif field == "level" and suggested:
                item.level = suggested
        elif suggested and stype == "add" and not _contains_named(items, suggested):
            new.languages.append(LanguageItem(name=suggested))

    elif section in ("custom", "custom_sections"):
        items = new.custom_sections
        idx = index if isinstance(index, int) and 0 <= index < len(items) else None
        if field == "bullets":
            if stype == "add" and suggested:
                if idx is not None:
                    if not _contains_line(items[idx].bullets, suggested):
                        items[idx].bullets.append(suggested)
                else:
                    new.custom_sections.append(CustomSection(bullets=[suggested]))
            elif stype == "remove" and original:
                for item in items:
                    item.bullets = [b for b in item.bullets if b != original]
            elif stype in ("rewrite", "reword") and suggested and idx is not None:
                items[idx].bullets = [b.strip() for b in suggested.splitlines() if b.strip()]
        elif field == "title" and (suggested or original) and idx is not None:
            items[idx].title = suggested or original

    elif section == "personal":
        p = new.personal
        if field in ("name", "title", "email", "phone", "location", "website", "linkedin", "github"):
            setattr(p, field, suggested or original)

    return new


def apply_suggestions(cv: CVData, suggestions: list[Suggestion]) -> tuple[CVData, int]:
    new = cv
    applied = 0
    for suggestion in suggestions:
        before = new
        new = apply_suggestion(new, suggestion)
        if new != before:
            applied += 1
    return new, applied


def rewrite_segment(
    cv: CVData,
    text: str,
    job_description: str,
    segment: str,
    context: str,
    client: LLMClient,
) -> str:
    prompt = (
        f"Rewrite the requested CV {segment} to better match the target job description. "
        "Return only the rewritten text with no commentary or formatting markers.\n\n"
        f"Segment to rewrite: {segment}\n"
        f"Original content:\n{context}\n\n"
        f"Target Job Description:\n{job_description or '(none provided)'}\n\n"
        f"Full CV:\n{text or cv_to_text(cv)}"
    )
    return client.chat(
        [{"role": "system", "content": "You are a professional CV writer."}, {"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=4000,
    ).strip()


def rewrite_segment_vision(
    segment: str,
    context: str,
    job_description: str,
    images: list[bytes],
    client: LLMClient,
) -> str:
    """Vision variant of ``rewrite_segment`` for scanned (image) PDFs.

    The model sees the page images, so it first transcribes the relevant segment
    from the scanned pages and then rewrites it per the request. Text-only path is
    unchanged (see ``rewrite_segment``).
    """
    instructions = (
        "You are a professional CV writer. Below you will receive page images of a "
        "candidate's SCANNED CV (one image per page, in reading order) — there is no "
        "machine-readable text for this document. Transcribe the relevant segment "
        "faithfully from the scanned CV pages, then rewrite it to better match the "
        "target job description. Base the rewrite ONLY on what is visible in the "
        "images; do not invent details that are not visible.\n\n"
        f"Segment to rewrite: {segment}\n"
        f"Original content:\n{context}\n\n"
        f"Target Job Description:\n{job_description or '(none provided)'}\n\n"
        "Return only the rewritten text with no commentary or formatting markers."
    )
    content: list[dict] = [text_part(instructions)]
    for img in cap_vision_images(images, "rewrite segment vision"):
        content.append(image_part(base64.b64encode(img).decode("ascii")))
    return client.chat(
        [
            {"role": "system", "content": "You are a professional CV writer."},
            {"role": "user", "content": content},
        ],
        temperature=0.4,
        max_tokens=4000,
    ).strip()


# ---------------------------------------------------------------------------
# Chat 2.0 — history-aware assistant that proposes applicable edits
# ---------------------------------------------------------------------------

_CHAT_HISTORY_CAP = 20

_CHAT_SYSTEM = (
    "You are a professional CV assistant embedded in a CV editor. You help the "
    "user improve their CV through conversation and, when asked, concrete "
    "applicable edits.\n\n"
    "You receive the candidate's current CV (compact JSON), the target job "
    "description (when provided), the current edit target (the CV field the user "
    "is focused on, when any), and the conversation history.\n\n"
    "CURRENT CV (compact JSON):\n{{CV_JSON}}\n\n"
    "TARGET JOB DESCRIPTION:\n{{JD}}\n\n"
    "CURRENT EDIT TARGET (JSON; empty object means no specific field):\n{{TARGET}}\n\n"
    "Respond with STRICT JSON only — no prose, markdown fences, or commentary "
    "outside the JSON — of the exact shape:\n"
    '{"reply": string, "proposed_edits": [Suggestion, ...]}\n'
    "Each Suggestion has the exact shape:\n"
    '{"id": str, "section": str, "field": str, "index": int|null, '
    '"type": "rewrite|add|remove|reword|keyword|reorder|rename", '
    '"title": str, "original": str, "suggested": str, "reason": str, '
    '"priority": "high|medium|low", "impact": str, "move_from": str, '
    '"move_to": str, "target_section": str, "field_path": str, "rationale": str}\n'
    "Rules:\n"
    '- "reply" is concise markdown addressed to the user.\n'
    "- Only propose edits when the user asked for a change to the CV; otherwise "
    '"proposed_edits" must be [].\n'
    '- Give each proposed edit a stable unique slug id (e.g. "summary-stronger-1").\n'
    '- Prefer "field_path" (JSON-ish, e.g. "experience[0].bullets[1]", "summary", '
    '"skills[0].skills") so edits can be applied automatically.\n'
    "- Never invent facts, employers, dates, or metrics; only rephrase or add "
    "keywords grounded in the CV and the job description."
)


def _chat_message_parts(message) -> tuple[str, str]:
    """Accept ChatMessage models or plain dicts; return ``(role, content)``."""
    role = getattr(message, "role", None)
    content = getattr(message, "content", None)
    if role is None and isinstance(message, dict):
        role = message.get("role")
        content = message.get("content")
    return str(role or ""), str(content or "")


def chat_assist(
    cv: CVData,
    job_description: str,
    messages: list,
    target: dict,
    client: LLMClient,
    images: list[bytes] | None = None,
    structure: dict | None = None,
) -> tuple[str, list[Suggestion]]:
    """History-aware chat assistant that may propose applicable CV edits.

    Sends the full CV, the job description, the current field ``target`` and the
    conversation history (capped to the last ~20 messages). The model answers
    with strict JSON ``{"reply": str, "proposed_edits": [Suggestion, ...]}``;
    unparseable output degrades to ``(raw_text, [])`` and malformed edits are
    dropped. When page ``images`` are supplied (scanned PDF session) they are
    attached, capped, to the first user message as vision content blocks.
    """
    history = list(messages or [])[-_CHAT_HISTORY_CAP:]
    system = (
        _CHAT_SYSTEM.replace("{{CV_JSON}}", cv.model_dump_json())
        .replace("{{JD}}", (job_description or "").strip() or "(none provided)")
        .replace("{{TARGET}}", json.dumps(target or {}, ensure_ascii=False))
    )
    if structure is not None:
        from .analyzer import structure_context

        layout = structure_context(structure)
        if layout:
            system += "\n\nCV LAYOUT CONTEXT (positions of extracted text):\n" + layout

    llm_messages: list[dict] = [{"role": "system", "content": system}]
    first_user_done = False
    for message in history:
        role, content = _chat_message_parts(message)
        if role not in ("user", "assistant") or not content.strip():
            continue
        if role == "user" and not first_user_done and images:
            blocks: list[dict] = [text_part(content)]
            for img in cap_vision_images(images, "chat"):
                blocks.append(image_part(base64.b64encode(img).decode("ascii")))
            llm_messages.append({"role": "user", "content": blocks})
        else:
            llm_messages.append({"role": role, "content": content})
        if role == "user":
            first_user_done = True

    raw = client.chat(llm_messages, temperature=0.4, max_tokens=8000)
    try:
        data = extract_json(raw)
    except Exception:
        logger.warning("Chat reply was not valid JSON; returning raw text without edits")
        return raw.strip(), []
    reply = str(data.get("reply") or "").strip() or raw.strip()
    edits = parse_suggestions({"suggestions": data.get("proposed_edits")})
    return reply, edits
