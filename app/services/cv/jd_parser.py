"""Job-description parser.

Extracts a structured :class:`JDProfile` from raw job-description text. The LLM
path is tried first; on ANY failure (config/LLM/JSON/validation) a deterministic
keyword-extraction fallback builds a :class:`JDProfile` that is guaranteed to
carry at least ``must_have_keywords``. Never raises.
"""

import logging
import re

from ..llm import LLMClient
from .analyzer import _STOPWORDS
from .json_util import extract_json
from .models import JDProfile

logger = logging.getLogger("cviq")

_SYSTEM = (
    "You are a job-description parser. You convert a job description into a strict "
    "JSON object. Do not output anything outside the JSON."
)

_JD_SCHEMA = (
    '{"role_title": str, '
    '"required_skills": [str], '
    '"nice_to_have_skills": [str], '
    '"must_have_keywords": [str], '
    '"seniority_signals": [str], '
    '"requirements": [str]}'
)

_SENIORITY_RE = re.compile(
    r"\b(?:senior|mid(?:[- ]level)?|midlevel|lead|junior|principal|staff|entry[- ]level|executive|intern)\b",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9+#.]+")


def _jd_text(value) -> str:
    """Coerce a JD-profile scalar to ``str`` — never the literal string "None"."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


_JD_LIST_FIELDS = (
    "required_skills",
    "nice_to_have_skills",
    "must_have_keywords",
    "seniority_signals",
    "requirements",
)


def _sanitize_profile(data: dict) -> dict:
    """Guard LLM JD JSON: coerce scalar fields and filter non-str list items.

    Prevents ``str(None) == "None"`` pollution and makes ``JDProfile``
    validation tolerant of null entries inside lists (which would otherwise
    raise and force the deterministic fallback).
    """
    sanitized: dict = {}
    for key, value in data.items():
        if key in _JD_LIST_FIELDS:
            if value is None:
                sanitized[key] = []
            elif isinstance(value, list):
                sanitized[key] = [_jd_text(v) for v in value]
            else:
                sanitized[key] = [_jd_text(value)]
        elif key == "role_title":
            sanitized[key] = _jd_text(value)
        else:
            sanitized[key] = value
    return sanitized


def parse_jd(job_description: str, client: LLMClient) -> JDProfile | None:
    """Parse a job description into a :class:`JDProfile`.

    Returns ``None`` when ``job_description`` is empty. On any LLM/JSON/validation
    failure, falls back to a deterministic keyword-extraction profile (never
    raises).
    """
    jd = (job_description or "").strip()
    if not jd:
        return None
    try:
        prompt = (
            "You are converting the job description below into a structured profile. "
            "Return ONLY a strict JSON object with the exact shape:\n"
            + _JD_SCHEMA
            + "\n\nRules:\n"
            '- "required_skills" lists skills the candidate must have (e.g. "Python", "Kubernetes").\n'
            '- "nice_to_have_skills" lists preferred/plus skills.\n'
            '- "must_have_keywords" lists the key ATS keywords (skills, tools, technologies) recruiters will search for.\n'
            '- "seniority_signals" lists level cues explicitly stated (e.g. "senior", "lead", "junior"); empty when none.\n'
            '- "requirements" lists the human-readable requirement sentences.\n'
            "- Only use content present in the job description; do not invent requirements.\n\n"
            "Job Description:\n"
            + jd
            + "\n\nReturn ONLY the JSON object."
        )
        raw = client.chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4000,
        )
        data = extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("JD parse response is not a JSON object")
        profile = JDProfile.model_validate(_sanitize_profile(data))
        # A profile with nothing at all on every field is a failed parse — fall back.
        if not (profile.role_title or profile.required_skills or profile.must_have_keywords or profile.requirements):
            raise ValueError("JD parse response was empty")
        return profile
    except Exception:
        logger.exception("JD parse failed; using deterministic keyword fallback")
        return _fallback_profile(jd)


def _fallback_profile(jd: str) -> JDProfile:
    """Deterministic JD profile from keyword extraction (no LLM)."""
    # Separate into requirement-ish sentences.
    sentences = re.split(r"(?<=[.!?])\s+|\n+", jd)
    requirements = [
        s.strip() for s in sentences if len((s or "").strip().split()) >= 5
    ][:10]

    def interesting(word: str) -> bool:
        w = word.strip().lower()
        return len(w) >= 2 and w not in _STOPWORDS and any(c.isalnum() for c in w)

    tokens = [t for t in _TOKEN_RE.findall(jd.lower()) if interesting(t)]
    seen: set[str] = set()
    keywords: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            keywords.append(token)

    # Capitalized phrases can hint at the role title and named technologies.
    capitalized = [
        phrase.strip()
        for phrase in re.findall(r"[A-Z][A-Za-z0-9+#.\-/]{1,}", jd)
        if len(phrase.strip()) >= 3
    ]
    role_title = ""
    for phrase in capitalized:
        words = phrase.split()
        if 1 <= len(words) <= 4 and all(w[:1].isupper() for w in words if w):
            role_title = phrase
            break

    return JDProfile(
        role_title=role_title,
        required_skills=keywords[:12],
        nice_to_have_skills=[],
        must_have_keywords=keywords[:20],
        seniority_signals=_extract_seniority(jd),
        requirements=requirements,
    )


def _extract_seniority(jd: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _SENIORITY_RE.findall(jd.lower()):
        signal = match.strip()
        if signal and signal not in seen:
            seen.add(signal)
            out.append(signal)
    return out