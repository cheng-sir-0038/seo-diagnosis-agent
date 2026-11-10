from __future__ import annotations

import re
from dataclasses import dataclass

_PIPE_PAIR = r"(?:[|｜]\s*){1,2}"
_DSML_PREFIX = rf"{_PIPE_PAIR}\s*DSML\s*{_PIPE_PAIR}"
_DSML_BLOCK = re.compile(
    rf"<\s*{_DSML_PREFIX}\s*tool_calls\s*>.*?"
    rf"<\s*/\s*{_DSML_PREFIX}\s*tool_calls\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_TAG = re.compile(
    rf"<\s*/?\s*{_DSML_PREFIX}[^>]*>",
    re.IGNORECASE,
)
_BLANK_LINES = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")


@dataclass(frozen=True, slots=True)
class SanitizedAssistantContent:
    content: str
    had_control_markup: bool


def sanitize_assistant_content(
    content: str,
    *,
    fallback: str | None = None,
    discard_body_on_control_markup: bool = False,
) -> SanitizedAssistantContent:
    """Remove model-internal DSML tool-control markup from user-visible text.

    DSML text is never converted into a real tool call. Canonical tool calls must
    arrive through the provider's structured ``tool_calls`` field.
    """

    text = str(content or "")
    had_control_markup = bool(_DSML_BLOCK.search(text) or _DSML_TAG.search(text))
    if not had_control_markup:
        return SanitizedAssistantContent(content=text.strip(), had_control_markup=False)

    cleaned = _DSML_BLOCK.sub("", text)
    cleaned = _DSML_TAG.sub("", cleaned)
    cleaned = _BLANK_LINES.sub("\n\n", cleaned).strip()
    if discard_body_on_control_markup:
        cleaned = str(fallback or "").strip()
    elif not cleaned and fallback:
        cleaned = fallback.strip()
    return SanitizedAssistantContent(content=cleaned, had_control_markup=True)
