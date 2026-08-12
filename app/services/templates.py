"""CV template gallery (B3): index and previews + HTML-render mapping.

Templates are real-world example CVs shipped as PDFs in ``settings.templates_dir``
(default ``<repo>/Templates``). The user picks one as the design source to build
on; the gallery PDFs are used ONLY for ``preview_url`` (design inspiration) and
metadata. Final CV files are rendered from the canonical JSON via the HTML/CSS
templates in ``app/templates_html`` — gallery PDFs are never edited, overlaid or
filled (see ``GALLERY_RENDER_MAP`` -> ``render_template``).

Everything here is defensive: a missing file / unreadable PDF yields ``None``
or an empty structure rather than raising.
"""

import hashlib
import os
import re
import threading
from collections import OrderedDict

import fitz

from ..config import settings

# ---------------------------------------------------------------------------
# HTML-render mapping
# ---------------------------------------------------------------------------

# HTML template ids that exist (or are planned) in ``app/templates_html/``.
RENDER_TEMPLATES = {
    "modern",
    "classic",
    "minimal",
    "awesome-cv",
    "deedy-resume",
    "cvresume",
    "universal-resume",
    "newfuture-cv",
}

# Gallery PDF id -> HTML template id (content mapping; safe to adjust).
# Dedicated designs map 1:1; the rest fall back to a generic look.
GALLERY_RENDER_MAP: dict[str, str] = {
    "awesome-cv": "awesome-cv",
    "deedy-resume": "deedy-resume",
    "cvresume": "cvresume",
    "universal-resume": "universal-resume",
    "newfuture-cv": "newfuture-cv",
    "altacv": "modern",
    "moderncv": "modern",
    "twentyseconds": "minimal",
    "resume-ng": "classic",
    "simple-resume-cv": "minimal",
}

_DEFAULT_RENDER_TEMPLATE = "modern"


def render_template_for(tpl_id: str) -> str:
    """HTML template id used to render-final a gallery template's CV.

    Gallery designs that ARE render templates (their id matches a builtin
    render-template id, e.g. ``modern``/``classic``/``minimal`` or a dedicated
    design like ``awesome-cv``) map to themselves; anything else resolves via
    ``GALLERY_RENDER_MAP`` and falls back to the builtin default.
    """
    tid = (tpl_id or "").strip()
    if tid in RENDER_TEMPLATES:
        return tid
    mapped = GALLERY_RENDER_MAP.get(tid, _DEFAULT_RENDER_TEMPLATE)
    return mapped if mapped in RENDER_TEMPLATES else _DEFAULT_RENDER_TEMPLATE


def resolve_render_template(template_id: str, default: str = "") -> str:
    """Resolve ANY catalog id (builtin render template OR gallery id) to a
    render-template id.

    Builtin render-template ids (modern/classic/minimal/awesome-cv/...) are used
    as-is; gallery ids resolve via ``render_template_for``; unknown ids fall back
    to ``default`` (or the builtin default) so ``render_html`` never raises.
    """
    tid = (template_id or "").strip() or (default or "").strip() or _DEFAULT_RENDER_TEMPLATE
    if tid in RENDER_TEMPLATES:
        return tid
    if get_template(tid) is not None:
        return render_template_for(tid)
    return tid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_templates() -> list[dict]:
    """Return metadata for every ``*.pdf`` in the templates dir (cached).

    Entries gain ``render_template`` (the HTML template used to render-final
    files for this design) and ``converted`` (True when the gallery design has
    a dedicated HTML template, otherwise the generic fallback is used).

    The result is cached at module level and re-scanned whenever the directory
    contents/``mtime`` change or the ``_RESCAN`` test hook is set.
    """
    global _RESCAN
    template_dir = settings.templates_dir
    fingerprint = _dir_fingerprint(template_dir)
    with _scan_lock:
        if not _RESCAN and _scan_cache["fingerprint"] == fingerprint and _scan_cache["templates"]:
            return list(_scan_cache["templates"])
        _RESCAN = False
        templates: list[dict] = []
        if template_dir.exists():
            for path in sorted(template_dir.iterdir()):
                try:
                    if not path.is_file() or path.suffix.lower() != ".pdf":
                        continue
                    tpl_id = _slugify(path.stem)
                    templates.append(
                        {
                            "id": tpl_id,
                            "name": _pretty_name(path.stem),
                            "file": path.stem,  # original stem; path resolution must be lossless
                            "pages": _pdf_page_count(path),
                            "size_bytes": path.stat().st_size,
                            "preview_url": f"/api/templates/{tpl_id}/preview/0",
                            "render_template": render_template_for(tpl_id),
                            "converted": tpl_id in RENDER_TEMPLATES,
                        }
                    )
                except Exception:
                    continue
        templates.sort(key=lambda t: t["name"].lower())
        _scan_cache["fingerprint"] = fingerprint
        _scan_cache["templates"] = templates
        return list(templates)


def get_template(tpl_id: str) -> dict | None:
    """Metadata dict for a single template id, or ``None``."""
    for tpl in scan_templates():
        if tpl["id"] == tpl_id:
            return dict(tpl)
    return None


def template_bytes(tpl_id: str) -> bytes | None:
    """Raw PDF bytes for ``tpl_id`` (bounded LRU cache), or ``None``."""
    meta = get_template(tpl_id)
    if meta is None:
        return None
    path = settings.templates_dir / f"{meta.get('file') or meta['name']}.pdf"
    try:
        stat = path.stat()
        key = (tpl_id, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
    with _bytes_lock:
        if key in _bytes_cache:
            _bytes_cache.move_to_end(key)
            return _bytes_cache[key]
    try:
        data = path.read_bytes()
    except OSError:
        return None
    with _bytes_lock:
        _bytes_cache[key] = data
        _bytes_cache.move_to_end(key)
        while (
            len(_bytes_cache) > 1
            and sum(len(v) for v in _bytes_cache.values()) > _BYTES_BUDGET
        ):
            _bytes_cache.popitem(last=False)
    return data


def template_preview(tpl_id: str, page: int = 0, dpi: int = 72) -> bytes | None:
    """Render ``page`` (0-based) of the template to PNG bytes, or ``None``.

    Unknown template ids and out-of-range pages return ``None``. Renderings are
    cached in memory keyed by ``(tpl_id, page, dpi)``.
    """
    meta = get_template(tpl_id)
    if meta is None or page < 0 or page >= meta["pages"]:
        return None
    dpi = min(max(int(dpi or 72), 36), 300)
    key = (tpl_id, page, dpi)
    with _preview_lock:
        if key in _preview_cache:
            _preview_cache.move_to_end(key)
            return _preview_cache[key]
    data = template_bytes(tpl_id)
    if data is None:
        return None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            pix = doc[page].get_pixmap(dpi=dpi, colorspace=fitz.csRGB)
            png = pix.tobytes("png")
        finally:
            doc.close()
    except Exception:
        return None
    with _preview_lock:
        _preview_cache[key] = png
        _preview_cache.move_to_end(key)
        while len(_preview_cache) > _PREVIEW_MAX_ITEMS or (
            len(_preview_cache) > 1 and sum(len(v) for v in _preview_cache.values()) > _PREVIEW_BUDGET
        ):
            _preview_cache.popitem(last=False)
    return png


# ---------------------------------------------------------------------------
# Scan helpers
# ---------------------------------------------------------------------------

_SLUG_CLEAN = re.compile(r"[^a-z0-9-]")


def _slugify(stem: str) -> str:
    return _SLUG_CLEAN.sub("", (stem or "").lower().replace(" ", "-"))


def _pretty_name(stem: str) -> str:
    return " ".join((stem or "").replace("_", " ").split())


def _dir_fingerprint(directory) -> str:
    h = hashlib.md5()
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        entries = []
    for entry in entries:
        h.update(entry.encode("utf-8", "replace"))
        try:
            st = os.stat(os.path.join(directory, entry))
            h.update(f"{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            pass
    return h.hexdigest()


def _pdf_page_count(path) -> int:
    try:
        doc = fitz.open(path)
        try:
            return doc.page_count
        finally:
            doc.close()
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Caches + test hooks
# ---------------------------------------------------------------------------

_RESCAN = False  # test hook: set True to force the next scan_templates() to rescan

_scan_lock = threading.Lock()
_scan_cache: dict = {"fingerprint": None, "templates": []}

_bytes_lock = threading.Lock()
_bytes_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_BYTES_BUDGET = 5 * 1024 * 1024  # ~5MB LRU for raw template bytes

_preview_lock = threading.Lock()
_preview_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_PREVIEW_BUDGET = 20 * 1024 * 1024
_PREVIEW_MAX_ITEMS = 128


def _force_rescan() -> None:
    """Test hook: clear the scan cache so the next call re-lists the directory."""
    global _RESCAN
    _RESCAN = True
    with _scan_lock:
        _scan_cache["fingerprint"] = None
        _scan_cache["templates"] = []