from __future__ import annotations

import json
import re
from typing import Any


def decode_json_object(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    candidates = [text]
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.I)
    if len(fenced) == 1:
        candidates.insert(0, fenced[0].strip())

    for candidate in candidates:
        for normalized in (candidate, _escape_inner_string_quotes(candidate)):
            try:
                payload = json.loads(normalized)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                return payload
    raise ValueError("Agent output must contain exactly one JSON object")


def _escape_inner_string_quotes(value: str) -> str:
    """Repair model-produced prose quotes without accepting other invalid JSON."""
    result: list[str] = []
    inside_string = False
    escaped = False
    length = len(value)

    for position, character in enumerate(value):
        if character != '"' or escaped:
            result.append(character)
        elif not inside_string:
            inside_string = True
            result.append(character)
        else:
            next_position = position + 1
            while next_position < length and value[next_position].isspace():
                next_position += 1
            next_character = value[next_position] if next_position < length else ""
            if not next_character or next_character in ",:}]":
                inside_string = False
                result.append(character)
            else:
                result.append('\\"')

        if character == "\\" and not escaped:
            escaped = True
        else:
            escaped = False

    return "".join(result)
