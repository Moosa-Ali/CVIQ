from typing import Optional

from pydantic import BaseModel, Field


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
    start: str = ""
    end: str = ""


class ExperienceItem(BaseModel):
    company: str = ""
    role: str = ""
    location: str = ""
    dates: DateRange = Field(default_factory=DateRange)
    bullets: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    institution: str = ""
    degree: str = ""
    field: str = ""
    dates: DateRange = Field(default_factory=DateRange)
    gpa: str = ""


class ProjectItem(BaseModel):
    name: str = ""
    link: str = ""
    description: str = ""
    bullets: list[str] = Field(default_factory=list)


class CertificationItem(BaseModel):
    name: str = ""
    issuer: str = ""
    year: str = ""


class LanguageItem(BaseModel):
    name: str = ""
    level: str = ""


class SkillGroup(BaseModel):
    category: str = ""
    skills: list[str] = Field(default_factory=list)


class CustomSection(BaseModel):
    """An arbitrary user-defined section ("extensible array" requirement).

    Rendered as a ``<title>`` heading followed by bullet lines wherever the
    rendering/export pipeline emits sections. The title may be empty (section
    of bare bullets); bullets may be empty (heading-only section).
    """

    title: str = ""
    bullets: list[str] = Field(default_factory=list)


# Canonical section ids, in the default render order. ``section_order`` on
# CVData stores a permutation of these (empty = this default); renderers emit
# any sections missing from a custom order afterwards, in this order, so no
# content is ever hidden.
DEFAULT_SECTION_ORDER = [
    "summary",
    "experience",
    "education",
    "skills",
    "projects",
    "certifications",
    "languages",
    "custom_sections",
]

# Section ids that hold a list of items (valid targets for item-level reorder).
ARRAY_SECTIONS = (
    "experience",
    "education",
    "projects",
    "certifications",
    "languages",
    "skills",
    "custom_sections",
)

# Section ids that have a single overridable heading via ``section_titles``.
TITLED_SECTIONS = (
    "summary",
    "experience",
    "education",
    "skills",
    "projects",
    "certifications",
    "languages",
)


class TemplateConfig(BaseModel):
    """Visual toggles for the universal CV template (the single design all
    legacy template ids now resolve to as named presets).

    - ``font``: ``"sans"`` | ``"serif"`` (rendered as Helvetica/Arial vs
      Georgia/Times).
    - ``header_alignment``: ``"left"`` | ``"center"``.
    - ``header_divider``: line under the name/title header.
    - ``section_divider``: line under each section heading.
    - ``heading_case``: ``"upper"`` | ``"title"`` (UPPERCASE vs Title Case
      section headings).

    When ``None`` on a CV, the renderer falls back to the preset mapped from the
    CV's ``template`` id (so legacy saved CVs render as before).
    """

    font: str = "sans"
    header_alignment: str = "center"
    header_divider: bool = True
    section_divider: bool = True
    heading_case: str = "upper"


class CVData(BaseModel):
    template: str = "modern"
    accent: str = "#2563eb"
    template_config: Optional[TemplateConfig] = None
    personal: PersonalInfo = Field(default_factory=PersonalInfo)
    summary: str = ""
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
    custom_sections: list[CustomSection] = Field(default_factory=list)
    # Ordered list of section ids; empty = DEFAULT_SECTION_ORDER. Renderers
    # honor this for the main document flow (fixed sidebars keep their split).
    section_order: list[str] = Field(default_factory=list)
    # Section id -> custom heading override (empty = template default heading).
    section_titles: dict[str, str] = Field(default_factory=dict)


class KeywordMatch(BaseModel):
    keyword: str
    present: bool
    count: int = 0


class SectionAssessment(BaseModel):
    section: str
    score: int = 0
    comment: str = ""


class Suggestion(BaseModel):
    """A single suggested edit to the CV.

    ``type`` may be one of: ``rewrite|add|remove|reword|keyword|reorder|layout|rename``.
    ``reorder`` / ``layout`` suggestions carry positioning metadata in
    ``move_from`` / ``move_to`` / ``target_section`` (e.g. move a section or
    reflow a layout element). A ``rename`` suggestion is a heading-naming
    improvement: ``original`` = the current heading, ``suggested`` = the better
    heading. These fields are empty for content edits.
    """

    id: str
    section: str
    field: str
    index: Optional[int] = None
    type: str
    title: str
    original: str = ""
    suggested: str = ""
    reason: str
    priority: str = "medium"
    impact: str = ""
    move_from: str = ""
    move_to: str = ""
    target_section: str = ""
    field_path: str = ""
    rationale: str = ""


class ConfidenceFlag(BaseModel):
    """A low-confidence / needs-review marker on one canonical field.

    ``field_path`` is the same JSON-ish path convention as
    ``Suggestion.field_path`` (e.g. ``experience[0].bullets[1]``,
    ``personal.email``). Carried through routes now; produced by the parsing
    packet later.
    """

    field_path: str = ""
    level: str = "low"
    reason: str = ""


class JDProfile(BaseModel):
    """Parsed structure of a job description (LLM-extracted or heuristic)."""

    role_title: str = ""
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    must_have_keywords: list[str] = Field(default_factory=list)
    seniority_signals: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)


class GapDiff(BaseModel):
    """A single gap between the CV and a target job description.

    ``kind`` is ``"deterministic"`` (rule-based, no AI) or ``"semantic"``
    (LLM-derived). ``field_path`` uses the same JSON-ish path convention as
    ``Suggestion.field_path`` (e.g. ``experience[0].bullets[1]``). ``severity``
    is one of ``high|medium|low``.
    """

    field_path: str = ""
    issue: str = ""
    suggested_value: str = ""
    rationale: str = ""
    kind: str = "deterministic"  # "deterministic" | "semantic"
    severity: str = "medium"  # "high" | "medium" | "low"


class AnalysisReport(BaseModel):
    ats_score: int = 0
    matched_keywords: list[KeywordMatch] = Field(default_factory=list)
    missing_keywords: list[KeywordMatch] = Field(default_factory=list)
    sections: list[SectionAssessment] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    # Set by the route when a supplied upload session was missing/expired.
    session_warning: str = ""
