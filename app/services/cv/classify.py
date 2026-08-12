"""LLM-based upload classification into the canonical :class:`CVData` schema.

Phase-2a: the LLM reads the extracted text (plus a heuristic markdown rendering
of the same CV) — or the page images of a scanned PDF — and returns a STRICT JSON
object conforming to the canonical schema plus a ``low_confidence`` list. On ANY
failure (config/LLM/JSON/validation) the module falls back to ``(None, [])`` so
callers fall back to the heuristic parse with heuristic confidence flags.
Classification NEVER sees raw file bytes as an editing target and never writes
anything — it only produces canonical JSON.
"""

import base64
import logging

from ..llm import LLMClient, image_part, text_part
from . import parser
from .analyzer import _ANTI_FABRICATION
from .json_util import cap_vision_images, extract_json
from .markdown import document_markdown
from .models import ConfidenceFlag, CVData

logger = logging.getLogger("cviq")

# Cap on the RAW extracted text fed to the classification prompt (production
# robustness for pathological uploads). The heuristic markdown body is already
# capped inside markdown.py; this caps the verbatim text block.
_CLASSIFY_TEXT_MAX = 12000

_SYSTEM = (
    "You are a CV data-extraction engine. You convert a candidate's CV into a strict "
    "JSON object conforming to the CVIQ canonical schema. Do not output anything "
    "outside the JSON."
)

_VISION_SYSTEM = (
    "You are a CV data-extraction engine. You are viewing page images of a candidate's "
    "SCANNED CV — there is no machine-readable text, so the images are the only source "
    "of truth. You transcribe the CV faithfully and return a strict JSON object "
    "conforming to the CVIQ canonical schema. Do not output anything outside the JSON."
)

_CLASSIFY_SCHEMA = (
    '{"cv": {"personal": {"name": str, "title": str, "email": str, "phone": str, '
    '"location": str, "website": str, "linkedin": str, "github": str}, '
    '"summary": str, '
    '"experience": [{"company": str, "role": str, "location": str, '
    '"dates": {"start": str, "end": str}, "bullets": [str]}], '
    '"education": [{"institution": str, "degree": str, "field": str, '
    '"dates": {"start": str, "end": str}, "gpa": str}], '
    '"skills": [{"category": str, "skills": [str]}], '
    '"projects": [{"name": str, "link": str, "description": str, "bullets": [str]}], '
    '"certifications": [{"name": str, "issuer": str, "year": str}], '
    '"languages": [{"name": str, "level": str}], '
    '"custom_sections": [{"title": str, "bullets": [str]}]}, '
    '"low_confidence": [{"field_path": str, "reason": str}]}'
)


def classify_to_schema(
    text: str,
    structure: dict | None,
    client: LLMClient,
    *,
    images: list[bytes] | None = None,
) -> tuple[CVData | None, list[ConfidenceFlag]]:
    """LLM classification of a CV into the canonical schema.

    ``text`` is the extracted text, ``structure`` the parser layout dict, and
    ``client`` the LLM client. When ``images`` (scanned-PDF page images) is
    given, the vision path is used (text part + one image part per page).

    Returns ``(cv, low_confidence_flags)`` on success; on ANY exception
    ``(None, [])`` so the caller falls back to the heuristic parse. The returned
    :class:`CVData` is validated/stripped against the canonical model and is the
    ONLY way this module hands data back — it never writes to disk or mutates
    the caller's state.
    """
    try:
        if images:
            content: list[dict] = [text_part(_vision_classify_prompt())]
            for img in cap_vision_images(images, "classify"):
                content.append(image_part(base64.b64encode(img).decode("ascii")))
            messages = [
                {"role": "system", "content": _VISION_SYSTEM},
                {"role": "user", "content": content},
            ]
        else:
            heuristic_cv = parser._parse_text(text or "")
            md = document_markdown(heuristic_cv, text or "", structure)
            messages = [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _classify_prompt(_cap_classify_text(text), md)},
            ]
        raw = client.chat(messages, temperature=0.1, max_tokens=16000)
        data = extract_json(raw)
        cv, flags = _parse_classification(data)
        return cv, flags
    except Exception:
        logger.exception("LLM CV classification failed; using heuristic parse")
        return None, []


def _cap_classify_text(text: str) -> str:
    """Truncate the raw extracted text fed to the classification prompt."""
    text = text or ""
    if len(text) <= _CLASSIFY_TEXT_MAX:
        return text
    logger.warning(
        "Truncating classify raw text: %d -> %d chars (cap %d)",
        len(text),
        _CLASSIFY_TEXT_MAX,
        _CLASSIFY_TEXT_MAX,
    )
    return text[:_CLASSIFY_TEXT_MAX] + "\n… [truncated]"


def _classify_prompt(text: str, md: str) -> str:
    return (
        "You are converting the candidate's CV below into the CVIQ canonical schema. "
        "Return ONLY a strict JSON object with the exact shape:\n"
        + _CLASSIFY_SCHEMA
        + "\n\nRules:\n"
        "- ONLY put content that is present in the extracted text; if a section is "
        'absent leave it empty ([] or "").\n'
        "- Transcribe dates verbatim.\n"
        '- "low_confidence" lists every field you had to guess or that was ambiguous, '
        'each with a JSON-ish "field_path" such as "personal.name", "summary", '
        '"experience[2].company", "experience[0].bullets[1]", "skills[0].skills", '
        '"custom_sections[0].bullets[0]" and a short "reason".\n'
        "- Do NOT invent facts.\n"
        + _ANTI_FABRICATION
        + "\n\nExtracted text:\n"
        + (text or "(no extractable text)")
        + "\n\nHeuristic markdown rendering of the same CV:\n"
        + (md or "(none)")
        + "\n\nReturn ONLY the JSON object."
    )


def _vision_classify_prompt() -> str:
    return (
        "You are classifying a candidate's SCANNED CV. Below you will receive one page "
        "image per page, in reading order. There is NO machine-readable text for this "
        "document — transcribe the CV faithfully from the images (name, contact "
        "details, and every section with all of its content), then return a strict "
        "JSON object with the exact shape:\n"
        + _CLASSIFY_SCHEMA
        + "\n\nRules:\n"
        "- ONLY put content that is visible in the images; if a section is absent "
        'leave it empty ([] or "").\n'
        "- Transcribe names, titles, dates, numbers, and skills verbatim.\n"
        '- "low_confidence" lists any field that was illegible, blurred, cut off, or '
        'otherwise ambiguous, each with a JSON-ish "field_path" and a short "reason".\n'
        "- Do NOT invent facts.\n\n"
        "Return ONLY the JSON object."
    )


def _parse_classification(data: dict) -> tuple[CVData, list[ConfidenceFlag]]:
    """Validate the model's response into a CVData + ConfidenceFlag list.

    ``CVData.model_validate`` strips unknown keys (pydantic default is to ignore
    extras), so the canonical schema is the only thing that survives. Raises on
    any malformed payload — the caller converts that into ``(None, [])``.
    """
    cv_data = data.get("cv")
    if not isinstance(cv_data, dict):
        raise ValueError("Model classification response missing 'cv' object")
    cv = CVData.model_validate(cv_data)
    flags: list[ConfidenceFlag] = []
    for entry in data.get("low_confidence", []) or []:
        if not isinstance(entry, dict):
            continue
        field_path = entry.get("field_path")
        if not field_path:
            continue
        level = str(entry.get("level") or "low")
        if level not in ("low", "medium", "high"):
            level = "low"
        flags.append(
            ConfidenceFlag(
                field_path=str(field_path),
                level=level,
                reason=str(entry.get("reason", "")),
            )
        )
    return cv, flags


def heuristic_flags(cv: CVData, confidence: float | None = None) -> list[ConfidenceFlag]:
    """Rule-based confidence flags for the heuristic parse.

    Flags missing personal name / email, missing summary, missing standard
    sections, and (when ``confidence`` is provided) a low heuristic parse score
    as a general ``parse.confidence`` flag.
    """
    flags: list[ConfidenceFlag] = []
    if not (cv.personal.name or "").strip():
        flags.append(
            ConfidenceFlag(
                field_path="personal.name",
                level="low",
                reason="Candidate name not detected — verify it is present and spelled correctly.",
            )
        )
    if not (cv.personal.email or "").strip():
        flags.append(
            ConfidenceFlag(
                field_path="personal.email",
                level="low",
                reason="Contact email not detected in the document.",
            )
        )
    if not (cv.summary or "").strip():
        flags.append(
            ConfidenceFlag(
                field_path="summary",
                level="low",
                reason="No summary/profile section detected.",
            )
        )
    for field, label in (
        ("experience", "work experience"),
        ("education", "education"),
        ("skills", "skills"),
    ):
        if not getattr(cv, field):
            flags.append(
                ConfidenceFlag(
                    field_path=field,
                    level="low",
                    reason=f"No {label} section detected.",
                )
            )
    if confidence is not None and confidence < 0.6:
        flags.append(
            ConfidenceFlag(
                field_path="parse.confidence",
                level="low",
                reason=f"Heuristic parse confidence is low ({confidence:.2f}) — verify the extracted content.",
            )
        )
    return flags


def merge_flags(
    heuristic: list[ConfidenceFlag], llm: list[ConfidenceFlag]
) -> list[ConfidenceFlag]:
    """Merge heuristic + LLM confidence flags; dedupe by field_path with the LLM winning."""
    merged = {flag.field_path: flag for flag in heuristic}
    for flag in llm:
        merged[flag.field_path] = flag
    return list(merged.values())