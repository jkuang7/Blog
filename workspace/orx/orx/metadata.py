"""Structured ORX metadata block parsing for Linear issue descriptions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


METADATA_START = "<!-- orx:metadata:start -->"
METADATA_END = "<!-- orx:metadata:end -->"
_BLOCK_RE = re.compile(
    re.escape(METADATA_START) + r"\s*(.*?)\s*" + re.escape(METADATA_END),
    re.DOTALL,
)
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class MetadataParseError(ValueError):
    """Raised when a structured ORX metadata block is malformed."""


@dataclass(frozen=True)
class ParsedMetadataBlock:
    metadata: dict[str, Any]
    raw_block: str | None

    @property
    def found(self) -> bool:
        return self.raw_block is not None


def parse_orx_metadata(description: str) -> ParsedMetadataBlock:
    matches = _BLOCK_RE.findall(description)
    if not matches:
        return ParsedMetadataBlock(metadata={}, raw_block=None)
    if len(matches) > 1:
        raise MetadataParseError("Multiple ORX metadata blocks found in description.")

    raw_block = matches[0].strip()
    if not raw_block:
        raise MetadataParseError("ORX metadata block cannot be empty.")

    try:
        payload = json.loads(raw_block)
    except json.JSONDecodeError as error:
        normalized = _normalize_escaped_metadata_block(raw_block)
        if normalized == raw_block:
            raise MetadataParseError(f"Invalid ORX metadata JSON: {error.msg}") from error
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError as normalized_error:
            raise MetadataParseError(
                f"Invalid ORX metadata JSON: {normalized_error.msg}"
            ) from normalized_error

    if not isinstance(payload, dict):
        raise MetadataParseError("ORX metadata block must decode to a JSON object.")

    _validate_metadata_object(payload)
    return ParsedMetadataBlock(metadata=payload, raw_block=raw_block)


def _validate_metadata_object(value: dict[str, Any]) -> None:
    for key, item in value.items():
        if not _KEY_RE.match(key):
            raise MetadataParseError(
                f"Invalid ORX metadata key {key!r}; use lowercase snake_case keys."
            )
        _validate_metadata_value(item)


def _validate_metadata_value(value: Any) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for entry in value:
            _validate_metadata_value(entry)
        return
    if isinstance(value, dict):
        _validate_metadata_object(value)
        return
    raise MetadataParseError(
        f"Unsupported ORX metadata value type: {type(value).__name__}"
    )


def _normalize_escaped_metadata_block(raw_block: str) -> str:
    """Tolerate legacy escaped list delimiters from previously rendered issue bodies."""
    return raw_block.replace("\\[", "[").replace("\\]", "]")
