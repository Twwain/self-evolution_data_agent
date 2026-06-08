"""Unified LLM JSON parser — single canonical entry point for all LLM JSON parsing.

Pipeline (ordered by cost, cheapest first):
1. json.loads(raw.strip())
2. Strip markdown fence → json.loads
3. Extract first JSON object/array from mixed prose → json.loads
4. Truncation repair (close unclosed braces/brackets) → json.loads
5. json_repair.loads as final fallback
"""

from __future__ import annotations

import json
import logging
from typing import Literal, overload

import json_repair as _json_repair_lib

logger = logging.getLogger(__name__)


@overload
def parse_llm_json(raw: str, *, expect: Literal["dict"]) -> dict | None: ...


@overload
def parse_llm_json(raw: str, *, expect: Literal["list"]) -> list | None: ...


@overload
def parse_llm_json(raw: str, *, expect: None = None) -> dict | list | None: ...


def parse_llm_json(
    raw: str,
    *,
    expect: Literal["dict", "list"] | None = None,
) -> dict | list | None:
    """Parse JSON from raw LLM text output.

    Args:
        raw: Raw LLM response text.
        expect: Optional type constraint. "dict" returns None if result is not dict,
                "list" returns None if result is not list. None accepts both.

    Returns:
        Parsed dict or list, or None if all strategies fail.
    """
    # Edge case: empty/whitespace input (TypeError propagates for non-str)
    if not raw.strip():
        return None

    text = raw.strip()

    # Stage 1: Direct json.loads
    result = _try_parse(text)
    if result is not None:
        return _check_expect(result, expect)

    # Stage 2: Strip markdown fence
    stripped = _strip_fence(text)
    if stripped != text:
        result = _try_parse(stripped)
        if result is not None:
            logger.debug("JSON parsed after stripping markdown fence")
            return _check_expect(result, expect)

    # Stage 3: Extract JSON from prose
    extracted = _extract_json_from_prose(text)
    if extracted is not None:
        result = _try_parse(extracted)
        if result is not None:
            logger.debug("JSON extracted from surrounding prose")
            return _check_expect(result, expect)

    # Stage 4: Truncation repair
    repaired = _repair_truncated(text)
    if repaired is not None:
        result = _try_parse(repaired)
        if result is not None:
            logger.info("JSON recovered via truncation repair (raw_chars=%d)", len(text))
            return _check_expect(result, expect)

    # Stage 5: json_repair fallback
    try:
        repaired_result = _json_repair_lib.loads(text)
        if isinstance(repaired_result, (dict, list)) and repaired_result:
            logger.info("JSON recovered via json_repair (raw_chars=%d)", len(text))
            return _check_expect(repaired_result, expect)
    except Exception:
        logger.warning("json_repair raised an unexpected exception", exc_info=True)

    # All strategies failed
    logger.warning("All JSON parsing strategies failed: %s...", text[:200])
    return None


def _try_parse(text: str) -> dict | list | None:
    """Attempt json.loads, return None on failure."""
    try:
        result = json.loads(text)
        if isinstance(result, (dict, list)):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _strip_fence(text: str) -> str:
    """Remove markdown code fence markers (```json / ```)."""
    lines = text.split("\n")
    if not lines:
        return text

    # Check if first line is a fence opener
    first = lines[0].strip()
    if first.startswith("```"):
        lines = lines[1:]  # Remove opening fence line (```json, ```JSON, ```, etc.)
        # Remove closing fence if present
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)

    return text


def _extract_json_from_prose(text: str) -> str | None:
    """Find first complete JSON object/array via brace/bracket depth tracking."""
    start_idx = None
    open_char = None
    close_char = None
    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue

        if ch == "\\":
            if in_string:
                escape_next = True
            continue

        if ch == '"':
            if start_idx is not None:
                in_string = not in_string
            continue

        if in_string:
            continue

        if start_idx is None:
            if ch == "{":
                start_idx = i
                open_char = "{"
                close_char = "}"
                depth = 1
            elif ch == "[":
                start_idx = i
                open_char = "["
                close_char = "]"
                depth = 1
        else:
            if ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    return text[start_idx : i + 1]

    return None


def _repair_truncated(text: str) -> str | None:
    """Close unclosed delimiters in reverse nesting order, strip trailing commas."""
    # Track unclosed delimiters
    stack: list[str] = []
    in_string = False
    escape_next = False

    for ch in text:
        if escape_next:
            escape_next = False
            continue

        if ch == "\\":
            if in_string:
                escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch in ("{", "["):
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()

    if not stack:
        return None  # Nothing to repair — all delimiters balanced

    # Build repaired text: strip trailing comma, close in reverse order
    repaired = text.rstrip()

    # If we're inside an unclosed string, close it first
    if in_string:
        repaired += '"'

    # Strip trailing comma (common in truncated JSON)
    repaired = repaired.rstrip(",")

    # Append closing delimiters in reverse order
    for opener in reversed(stack):
        if opener == "{":
            repaired += "}"
        else:
            repaired += "]"

    return repaired


def _check_expect(
    result: dict | list, expect: Literal["dict", "list"] | None
) -> dict | list | None:
    """Enforce type constraint, return None on mismatch."""
    if expect is None:
        return result
    if expect == "dict" and isinstance(result, dict):
        return result
    if expect == "list" and isinstance(result, list):
        return result
    return None
