"""Gap analysis engine: deterministic (rules, NO AI) + semantic (LLM) diffs.

Returns structured :class:`GapDiff` objects — never text edits and never file
writes. ``deterministic_checks`` is pure rule logic over ``CVData`` + the parser
layout ``structure``; ``semantic_checks`` asks the LLM for gaps against a parsed
JD profile (or generic quality gaps when no JD) and returns ``[]`` on any
failure.
"""

import logging
import re
from collections import Counter

from ..llm import LLMClient, LLMConfigError, LLMError
from .jd_parser import parse_jd
from .json_util import extract_json
from .models import CVData, GapDiff, JDProfile
from .parser import _SECTION_ALIASES, _classify_section

logger = logging.getLogger("cviq")

COLUMN_GAP_THRESHOLD = 40.0  # points between distinct x0 clusters on one page
UNQUANTIFIED_CAP = 5
HEADING_CAP = 3


def gap_analysis(
    cv: CVData,
    text: str,
    structure: dict | None,
    job_description: str,
    client: LLMClient | None = None,
) -> dict:
    """Combined gap analysis.

    ``deterministic`` ALWAYS runs (no LLM required). ``semantic`` runs only when
    a client is available and JD parsing / semantic checks silently degrade to
    empty on LLM errors. ``mode`` is ``"jd"`` when a job description is given,
    else ``"generic"``. Never raises on LLM failures.
    """
    text = text or ""
    job_description = (job_description or "").strip()
    mode = "jd" if job_description else "generic"

    deterministic = deterministic_checks(cv, structure, text)

    jd_profile: JDProfile | None = None
    if mode == "jd" and client is not None:
        try:
            jd_profile = parse_jd(job_description, client)
        except (LLMConfigError, LLMError):
            logger.exception("JD parse failed; proceeding without a JD profile")
            jd_profile = None

    semantic: list[GapDiff] = []
    if client is not None:
        try:
            from .markdown import document_markdown

            md = document_markdown(cv, text, structure)
            semantic = semantic_checks(cv, md, jd_profile, job_description, client)
        except (LLMConfigError, LLMError):
            logger.exception("Semantic gap analysis unavailable; returning deterministic only")
            semantic = []
        except Exception:
            logger.exception("Unexpected error in semantic gap analysis")
            semantic = []

    return {
        "jd_profile": jd_profile,
        "deterministic": deterministic,
        "semantic": semantic,
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# Deterministic checks (RULES ONLY — never touches the LLM)
# ---------------------------------------------------------------------------


def deterministic_checks(cv: CVData, structure: dict | None, text: str) -> list[GapDiff]:
    """Rule-based gap detection over the canonical CV + parser layout structure."""
    gaps: list[GapDiff] = []
    personal = cv.personal

    # -- Missing personal details -------------------------------------------------
    if not (personal.name or "").strip():
        gaps.append(
            GapDiff(
                field_path="personal.name",
                issue="Missing candidate name",
                suggested_value="",
                rationale="ATS parsers expect the CV to lead with the full candidate name.",
                kind="deterministic",
                severity="high",
            )
        )
    if not (personal.email or "").strip():
        gaps.append(
            GapDiff(
                field_path="personal.email",
                issue="Missing contact email",
                suggested_value="",
                rationale="Recruiters and ATS workflows need a contact email.",
                kind="deterministic",
                severity="high",
            )
        )
    if not (personal.phone or "").strip():
        gaps.append(
            GapDiff(
                field_path="personal.phone",
                issue="Missing phone number",
                suggested_value="",
                rationale="A phone number is a standard contact detail recruiters expect.",
                kind="deterministic",
                severity="medium",
            )
        )
    if not (personal.location or "").strip():
        gaps.append(
            GapDiff(
                field_path="personal.location",
                issue="Missing location",
                suggested_value="",
                rationale="Location (city/region) is commonly filtered on by ATS.",
                kind="deterministic",
                severity="medium",
            )
        )

    # -- Missing standard sections ------------------------------------------------
    for field, label in (
        ("summary", "summary/profile"),
        ("experience", "work experience"),
        ("education", "education"),
        ("skills", "skills"),
    ):
        if not getattr(cv, field):
            gaps.append(
                GapDiff(
                    field_path=("summary" if field == "summary" else field),
                    issue=f"Missing {label} section",
                    suggested_value="",
                    rationale=f"No {label} section detected in the CV.",
                    kind="deterministic",
                    severity="high",
                )
            )

    # -- ATS-breaking layout indicators (from structure) --------------------------
    gaps.extend(_structure_ats_checks(structure))

    # -- Non-standard section headings --------------------------------------------
    gaps.extend(_heading_gaps(structure))

    # -- Unquantified bullets ------------------------------------------------------
    gaps.extend(_unquantified_bullet_gaps(cv))

    return gaps


def _structure_ats_checks(structure: dict | None) -> list[GapDiff]:
    if structure is None:
        return []
    gaps: list[GapDiff] = []
    kind = structure.get("kind")

    if kind == "pdf":
        image_pages = _image_page_count(structure)
        if image_pages:
            gaps.append(
                GapDiff(
                    field_path="",
                    issue="Contains embedded image pages",
                    suggested_value="",
                    rationale=(
                        f"{image_pages} page(s) have no extractable text — ATS parsers "
                        "may miss content rendered as images."
                    ),
                    kind="deterministic",
                    severity="high",
                )
            )
        if _detect_multicolumn(structure):
            gaps.append(
                GapDiff(
                    field_path="",
                    issue="Multi-column layout detected",
                    suggested_value="",
                    rationale=(
                        "Multi-column layouts are read inconsistently by ATS parsers; "
                        "a single-column order is safest."
                    ),
                    kind="deterministic",
                    severity="medium",
                )
            )
    elif kind == "docx" and _has_tables(structure):
        gaps.append(
            GapDiff(
                field_path="",
                issue="Table-based content detected",
                suggested_value="",
                rationale=(
                    "Word tables are parsed unpredictably by ATS; row-by-row text "
                    "readout may be scrambled."
                ),
                kind="deterministic",
                severity="medium",
            )
        )
    return gaps


def _image_page_count(structure: dict) -> int:
    """Pages of a PDF structure with zero text blocks — a proxy for image content.

    The CVIQ parser only retains text blocks in ``structure["blocks"]`` (image
    blocks are skipped by PyMuPDF extraction), so a page with no text blocks is
    treated as image-rendered content.
    """
    blocks = structure.get("blocks") or []
    page_count = structure.get("page_count", 0)
    if not page_count:
        return 0
    per_page = Counter(b.get("page", 0) for b in blocks)
    return sum(1 for page in range(page_count) if per_page.get(page, 0) == 0)


def _detect_multicolumn(structure: dict) -> bool:
    """Cluster block ``x0`` positions per page; >=2 clusters of >=2 blocks each
    is suspicious of a multi-column layout (columns create distinct x0 bands)."""
    blocks = structure.get("blocks") or []
    if not blocks:
        return False
    by_page: dict[int, list[float]] = {}
    for block in blocks:
        by_page.setdefault(block.get("page", 0), []).append(float(block.get("x0", 0.0) or 0.0))
    for xs in by_page.values():
        xs = sorted(set(xs))
        if len(xs) < 4:
            continue
        clusters: list[list[float]] = []
        current = [xs[0]]
        for x in xs[1:]:
            if x - current[-1] > COLUMN_GAP_THRESHOLD:
                clusters.append(current)
                current = [x]
            else:
                current.append(x)
        clusters.append(current)
        if sum(1 for cluster in clusters if len(cluster) >= 2) >= 2:
            return True
    return False


def _has_tables(structure: dict) -> bool:
    tables = structure.get("tables") or []
    return any(
        any((cell or "").strip() for cell in row)
        for table in tables
        for row in (table.get("rows") or [])
    )


def _detected_headings(structure: dict | None) -> list[str]:
    """Heading strings from the layout-aware markdown renderer (body only)."""
    if structure is None:
        return []
    from .markdown import structure_to_markdown

    md = structure_to_markdown(structure) or ""
    headings: list[str] = []
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("## OBSERVED LAYOUT"):
            break
        if line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


def _suggest_canonical_heading(heading: str) -> str:
    """Suggest the canonical section title for a non-standard heading string.

    Starts with the parser's canonical names, then falls back to keyword
    matching for headings the alias table doesn't cover verbatim
    (e.g. "Professional Experience" -> "Experience").
    """
    low = heading.lower()
    for canonical, aliases in _SECTION_ALIASES.items():
        if canonical == "personal":
            continue
        if low in aliases or canonical in low:
            return canonical.capitalize()
    if any(word in low for word in ("employment", "work history", "career")):
        return "Experience"
    if any(word in low for word in ("technologies", "competencies")):
        return "Skills"
    if any(word in low for word in ("objective", "profile", "about")):
        return "Summary"
    return ""


def _heading_gaps(structure: dict | None) -> list[GapDiff]:
    gaps: list[GapDiff] = []
    for heading in _detected_headings(structure):
        if not heading:
            continue
        if _classify_section(heading) is not None:
            continue  # standard (or alias) heading — fine
        suggestion = _suggest_canonical_heading(heading)
        rationale = f"Non-standard section heading {heading!r} may not be recognized by ATS parsers."
        if suggestion:
            rationale += f" Consider using the standard {suggestion!r} heading."
        # Heading gaps carry NO suggested_value: renaming a heading has no in-place
        # JSON apply semantic (field_path is empty), so it must not produce a false
        # "Apply as suggestion" affordance. The canonical heading stays informational
        # in rationale/issue.
        gaps.append(
            GapDiff(
                field_path="",
                issue=f"Non-standard section heading: {heading}",
                suggested_value="",
                rationale=rationale,
                kind="deterministic",
                severity="medium",
            )
        )
        if len(gaps) >= HEADING_CAP:
            break
    return gaps


def _unquantified_bullet_gaps(cv: CVData) -> list[GapDiff]:
    gaps: list[GapDiff] = []
    for i, item in enumerate(cv.experience):
        for j, bullet in enumerate(item.bullets):
            if bullet and not re.search(r"\d", bullet):
                gaps.append(
                    GapDiff(
                        field_path=f"experience[{i}].bullets[{j}]",
                        issue="No quantified outcome",
                        suggested_value="",
                        rationale="Bullets with concrete numbers (users, %, revenue, time saved) score higher with ATS and recruiters.",
                        kind="deterministic",
                        severity="low",
                    )
                )
            if len(gaps) >= UNQUANTIFIED_CAP:
                return gaps
    for i, project in enumerate(cv.projects):
        for j, bullet in enumerate(project.bullets):
            if bullet and not re.search(r"\d", bullet):
                gaps.append(
                    GapDiff(
                        field_path=f"projects[{i}].bullets[{j}]",
                        issue="No quantified outcome",
                        suggested_value="",
                        rationale="Bullets with concrete numbers (users, %, revenue, time saved) score higher with ATS and recruiters.",
                        kind="deterministic",
                        severity="low",
                    )
                )
            if len(gaps) >= UNQUANTIFIED_CAP:
                return gaps
    return gaps


# ---------------------------------------------------------------------------
# Semantic checks (LLM) — always degrade to [] on error
# ---------------------------------------------------------------------------

_SEMANTIC_SYSTEM = (
    "You are an expert ATS recruiter and CV content reviewer. You perform gap analysis "
    "between a candidate's CV and a target job description (or a generic quality "
    "review) and return a strict JSON object. Do not output anything outside the JSON."
)

_SEMANTIC_SCHEMA = (
    '{"gaps": [{"field_path": str, "issue": str, "suggested_value": str, '
    '"rationale": str, "severity": "high|medium|low"}]}'
)

_FIELD_PATH_NOTES = (
    'Use JSON-ish "field_path" syntax such as "personal.email", "summary", '
    '"experience[0].bullets[1]", "skills[0].skills", "custom_sections[0].bullets[0]" '
    "so edits can be applied automatically. Never invent facts — only report gaps "
    "grounded in the CV content."
)


def semantic_checks(
    cv: CVData,
    markdown: str,
    jd_profile: JDProfile | None,
    jd_text: str,
    client: LLMClient,
) -> list[GapDiff]:
    """LLM gap analysis. Returns up to 8 :class:`GapDiff` objects; [] on error."""
    if client is None:
        return []
    try:
        if (jd_text or "").strip():
            profile_json = jd_profile.model_dump_json() if jd_profile is not None else "null"
            prompt = _semantic_jd_prompt(markdown, jd_text, profile_json)
        else:
            prompt = _semantic_generic_prompt(markdown)
        raw = client.chat(
            [
                {"role": "system", "content": _SEMANTIC_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=8000,
        )
        gaps = _parse_semantic(extract_json(raw))
        return gaps[:8]
    except Exception:
        logger.exception("Semantic gap analysis failed; returning no semantic gaps")
        return []


def _semantic_jd_prompt(md: str, jd_text: str, profile_json: str) -> str:
    return (
        "You are performing a gap analysis between a candidate's CV and a target job "
        "description. Return ONLY a JSON object with the exact shape: "
        + _SEMANTIC_SCHEMA
        + "\n\n"
        + _FIELD_PATH_NOTES
        + "\nCover: missing required skills (use \"skills[...]\" field paths), weak "
        "phrasing (bullets), unquantified achievements, and seniority mismatches. "
        "Cap the list at 8 gaps.\n\n"
        "Candidate CV (markdown):\n"
        + md
        + "\n\nTarget Job Description:\n"
        + jd_text
        + "\n\nParsed JD profile (JSON):\n"
        + profile_json
    )


def _semantic_generic_prompt(md: str) -> str:
    return (
        "You are performing a generic quality-gap analysis of a candidate's CV (no job "
        "description was provided). Return ONLY a JSON object with the exact shape: "
        + _SEMANTIC_SCHEMA
        + "\n\n"
        + _FIELD_PATH_NOTES
        + "\nCover: missing standard sections, missing or weak contact details, weak "
        "bullet phrasing, unquantified achievements, and the absence of action verbs. "
        "Cap the list at 8 gaps.\n\n"
        "Candidate CV (markdown):\n"
        + md
    )


def _text(value) -> str:
    """Coerce an LLM JSON scalar to ``str`` — never the literal string "None".

    ``None`` (and other non-string values) become ``""`` so semantic gap fields
    never leak ``"None"`` into the UI or downstream consumers.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _severity(value) -> str:
    """Coerce an LLM ``severity`` to one of high|medium|low (default medium)."""
    severity = _text(value) or "medium"
    return severity if severity in ("high", "medium", "low") else "medium"


def _parse_semantic(data: dict) -> list[GapDiff]:
    gaps: list[GapDiff] = []
    for entry in data.get("gaps", []) or []:
        if not isinstance(entry, dict):
            continue
        field_path = entry.get("field_path")
        issue = entry.get("issue")
        if not field_path and not issue:
            continue
        gaps.append(
            GapDiff(
                field_path=_text(field_path),
                issue=_text(issue),
                suggested_value=_text(entry.get("suggested_value")),
                rationale=_text(entry.get("rationale")),
                kind="semantic",
                severity=_severity(entry.get("severity")),
            )
        )
    return gaps