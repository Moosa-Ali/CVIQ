"""Template configuration presets for the universal CV renderer.

All legacy template ids (modern/classic/minimal/awesome-cv/deedy-resume/
cvresume/universal-resume/newfuture-cv plus the gallery ids) now resolve to a
single universal HTML template whose appearance is driven by a
``TemplateConfig`` (font, header alignment, header divider, section divider).

Each legacy id maps to a named preset (a fixed ``TemplateConfig``) so existing
saved CVs that only carry a ``template`` id keep rendering with the same look.
New CVs carry an explicit ``template_config`` chosen via the UI toggles.
"""

from .models import CVData, TemplateConfig

PRESETS: dict[str, TemplateConfig] = {
    "modern": TemplateConfig(font="sans", header_alignment="center", header_divider=True, section_divider=True, heading_case="upper"),
    "classic": TemplateConfig(font="serif", header_alignment="center", header_divider=True, section_divider=True, heading_case="title"),
    "minimal": TemplateConfig(font="sans", header_alignment="left", header_divider=False, section_divider=False, heading_case="upper"),
    "awesome-cv": TemplateConfig(font="sans", header_alignment="left", header_divider=True, section_divider=True, heading_case="upper"),
    "deedy-resume": TemplateConfig(font="sans", header_alignment="center", header_divider=True, section_divider=True, heading_case="upper"),
    "cvresume": TemplateConfig(font="serif", header_alignment="left", header_divider=True, section_divider=True, heading_case="upper"),
    "universal-resume": TemplateConfig(font="sans", header_alignment="center", header_divider=True, section_divider=True, heading_case="upper"),
    "newfuture-cv": TemplateConfig(font="sans", header_alignment="center", header_divider=False, section_divider=False, heading_case="upper"),
}

DEFAULT_PRESET = "modern"
PRESET_IDS = frozenset(PRESETS.keys())


def preset_config(template_id: str) -> TemplateConfig:
    tid = (template_id or "").strip() or DEFAULT_PRESET
    return PRESETS.get(tid, PRESETS[DEFAULT_PRESET])


def resolve_template_config(cv: CVData, template_id: str) -> TemplateConfig:
    if cv.template_config is not None:
        return cv.template_config
    return preset_config(template_id or cv.template)
