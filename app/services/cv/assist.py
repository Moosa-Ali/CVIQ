from ..llm import LLMClient
from .models import CVData
from .writer import cv_to_text

_ASSIST_KINDS = (
    "summary",
    "bullets",
    "optimize",
    "optimize_summary",
    "optimize_bullets",
    "optimize_skills",
    "optimize_project",
)

_OPTIMIZE_INSTRUCTIONS = {
    "optimize": (
        "Rewrite the candidate's draft text below to sound more professional and "
        "impactful for an ATS and recruiter. Preserve meaning and facts; do not invent "
        "details. Return only the improved text."
    ),
    "optimize_summary": (
        "Rewrite the candidate's draft text below as a polished, professional CV summary "
        "that sounds impactful for an ATS and recruiter. Preserve meaning and facts; do "
        "not invent details. Return only the improved summary."
    ),
    "optimize_bullets": (
        "Rewrite the candidate's draft text below as punchy, achievement-oriented bullet "
        "points that sound professional and impactful for an ATS and recruiter. Preserve "
        "meaning and facts; do not invent details. Return only the improved bullets, one "
        "per line, no numbering or bullet characters."
    ),
    "optimize_skills": (
        "Rewrite the candidate's skills below as grouped skill lines for an ATS-friendly "
        "CV. Return ONLY lines of the form 'Category: skill, skill, skill' — one category "
        "per line, no bullets, no dashes, no numbering, no commentary. Preserve the "
        "candidate's skills; do not invent new ones."
    ),
    "optimize_project": (
        "Rewrite the candidate's project draft below for an ATS-friendly CV. Return ONLY "
        "a line 'Description: <one or two sentences>' followed by a line 'Bullets:' and "
        "then one '- ' bullet per line. No other commentary. Preserve meaning and facts; "
        "do not invent details."
    ),
}


def _optimize_prompt(kind: str, draft: str, jd: str) -> str:
    instruction = _OPTIMIZE_INSTRUCTIONS[kind]
    parts = [instruction, "", f"Candidate draft:\n{draft}"]
    if jd and jd.strip():
        parts.append(f"\nJob Description:\n{jd}")
    parts.append("\nImproved text:")
    return "\n".join(parts)


def assist(
    kind: str,
    cv: CVData,
    job_description: str | None,
    client: LLMClient,
    text: str = "",
) -> str:
    """Generate an assist draft for the given ``kind``.

    ``kind`` is one of ``summary``, ``bullets`` (classic kinds that rewrite the
    whole CV) or ``optimize``, ``optimize_summary``, ``optimize_bullets``,
    ``optimize_skills``, ``optimize_project`` (which improve the candidate's own
    draft ``text``). When a job description is provided the output is tailored
    to it; without one the output is generic content-improvement guidance with
    no JD-specific claims.
    """
    if kind not in _ASSIST_KINDS:
        raise ValueError("kind must be one of: " + ", ".join(_ASSIST_KINDS))
    cv_text = cv_to_text(cv)
    jd = (job_description or "").strip()
    has_jd = bool(jd)

    if kind in ("optimize", "optimize_summary", "optimize_bullets", "optimize_skills", "optimize_project"):
        draft = (text or "").strip()
        if not draft:
            draft = cv_text
        prompt = _optimize_prompt(kind, draft, jd)
    elif kind == "summary":
        if has_jd:
            prompt = (
                "Write a concise, professional CV summary (3-5 sentences) tailored to the job "
                "description. Use concrete, verifiable details only from the candidate's CV; "
                "never invent facts. Return only the summary text.\n\n"
                f"Job Description:\n{jd}\n\nCandidate CV:\n{cv_text}"
            )
        else:
            prompt = (
                "Write a concise, professional CV summary (3-5 sentences) that highlights the "
                "candidate's strengths for an ATS and recruiter. Use concrete, verifiable "
                "details only from the candidate's CV; never invent facts. Return only the "
                "summary text.\n\nCandidate CV:\n{cv_text}"
            )
    else:  # bullets
        if has_jd:
            prompt = (
                "Rewrite the work-experience bullet points of the candidate's CV to be more "
                "impactful, quantified, and keyword-aligned with the job description. Return "
                "only rewritten bullets, one per line, no numbering or bullet characters.\n\n"
                f"Job Description:\n{jd}\n\nCandidate CV:\n{cv_text}"
            )
        else:
            prompt = (
                "Rewrite the work-experience bullet points of the candidate's CV to be more "
                "impactful, quantified, and achievement-oriented for an ATS and recruiter. "
                "Return only rewritten bullets, one per line, no numbering or bullet "
                "characters. Base each rewrite only on facts present in the CV; never invent "
                "experience, metrics, employers, or dates.\n\nCandidate CV:\n{cv_text}"
            )
    return client.chat(
        [{"role": "system", "content": "You are a professional CV writer."}, {"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=4000,
    ).strip()