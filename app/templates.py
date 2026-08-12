_SECTIONS_ORDER = ["summary", "experience", "education", "skills", "projects", "certifications", "languages"]

_ACCENTS = [
    {"id": "blue", "hex": "#2563eb", "name": "Blue"},
    {"id": "slate", "hex": "#334155", "name": "Slate"},
    {"id": "emerald", "hex": "#059669", "name": "Emerald"},
    {"id": "rose", "hex": "#e11d48", "name": "Rose"},
    {"id": "violet", "hex": "#7c3aed", "name": "Violet"},
    {"id": "amber", "hex": "#d97706", "name": "Amber"},
]

MODERN = {
    "id": "modern",
    "name": "Modern",
    "heading_style": "uppercase",
    "layout": "single",
    "default_accent": "#2563eb",
    "font": "system",
    "sections_order": _SECTIONS_ORDER,
}

CLASSIC = {
    "id": "classic",
    "name": "Classic",
    "heading_style": "title",
    "layout": "single",
    "default_accent": "#111827",
    "font": "serif",
    "sections_order": _SECTIONS_ORDER,
}

MINIMAL = {
    "id": "minimal",
    "name": "Minimal",
    "heading_style": "uppercase",
    "layout": "single",
    "default_accent": "#6b7280",
    "font": "system",
    "sections_order": _SECTIONS_ORDER,
}

TEMPLATES = [MODERN, CLASSIC, MINIMAL]

DEFAULTS = {
    "template": "modern",
    "accent": "#2563eb",
    "sections_order": _SECTIONS_ORDER,
}
