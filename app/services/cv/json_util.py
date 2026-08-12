"""Shared helpers for LLM JSON extraction and vision-page capping.

These were historically private members of ``analyzer.py`` (``_extract_json``,
``_cap_vision_images``, ``_suggestions``) imported across module boundaries.
They live here as public API; ``analyzer`` re-exports the old private names for
backwards compatibility.
"""

import json
import logging
import re

from .models import Suggestion

logger = logging.getLogger("cviq")

# Cap on page images sent to any LLM (vision prompts). Session storage keeps the
# FULL image list — only what is sent to the model is sliced.
MAX_VISION_PAGES = 8


def cap_vision_images(images: list[bytes], what: str) -> list[bytes]:
    """Slice page images to the first ``MAX_VISION_PAGES`` for an LLM call.

    Logs a warning when truncation happens. ``what`` names the caller so logs
    read clearly (e.g. ``"analyze"``, ``"classify"``, ``"tailor suggest"``).
    """
    if not images or len(images) <= MAX_VISION_PAGES:
        return images
    logger.warning(
        "Truncating vision pages for %s: %d -> %d (cap %d)",
        what,
        len(images),
        MAX_VISION_PAGES,
        MAX_VISION_PAGES,
    )
    return images[:MAX_VISION_PAGES]


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*", re.IGNORECASE)


def extract_json(raw: str) -> dict:
    """Best-effort extraction of a JSON object from raw LLM output.

    Strips markdown fences, slices to the outermost ``{...}`` span, and retries
    with trailing commas removed (real LLMs often emit ``,}`` / ``,]``).
    Raises ``json.JSONDecodeError`` when no object can be recovered.
    """
    text = raw.strip()
    text = _JSON_FENCE_RE.sub("", text)
    text = text.replace("```", "")
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Lenient recovery: real LLMs often emit trailing commas before } and ].
        # Strip them (repeatedly, in case both ,} and ,] variants coexist) and retry.
        recovered = text
        for _ in range(3):
            recovered = re.sub(r",\s*([}\]])", r"\1", recovered)
        return json.loads(recovered)


def parse_suggestions(data: dict, key: str = "suggestions") -> list[Suggestion]:
    """Validate a list of raw suggestion dicts into :class:`Suggestion` models.

    Malformed entries (non-dicts) are dropped; scalar fields are coerced to
    ``str`` so a slightly-off model response never crashes the caller.
    """
    out: list[Suggestion] = []
    for idx, entry in enumerate(data.get(key, []) or []):
        if not isinstance(entry, dict):
            continue
        try:
            out.append(
                Suggestion(
                    id=str(entry.get("id") or f"sugg-{idx}"),
                    section=str(entry.get("section", "")),
                    field=str(entry.get("field", "")),
                    index=entry.get("index") if isinstance(entry.get("index"), int) else None,
                    type=str(entry.get("type", "reword")),
                    title=str(entry.get("title", "")),
                    original=str(entry.get("original", "")),
                    suggested=str(entry.get("suggested", "")),
                    reason=str(entry.get("reason", "")),
                    priority=str(entry.get("priority", "medium")),
                    impact=str(entry.get("impact", "")),
                    move_from=str(entry.get("move_from", "")),
                    move_to=str(entry.get("move_to", "")),
                    target_section=str(entry.get("target_section", "")),
                    field_path=str(entry.get("field_path", "")),
                    rationale=str(entry.get("rationale", "")),
                )
            )
        except Exception:
            logger.warning("Dropping malformed suggestion entry %d: %r", idx, entry)
            continue
    return out
