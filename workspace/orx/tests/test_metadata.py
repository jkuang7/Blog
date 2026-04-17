from __future__ import annotations

import unittest

from orx.metadata import MetadataParseError, parse_orx_metadata
from orx.mirror import LinearMirrorRepository
from orx.storage import Storage
from orx.config import resolve_runtime_paths
import tempfile


class MetadataParserTests(unittest.TestCase):
    def test_parse_valid_metadata_block(self) -> None:
        description = """
        Some issue body text.

        <!-- orx:metadata:start -->
        {
          "priority_hint": "high",
          "acceptance": ["tests", "tmux"],
          "selection": {"lane": "linear"}
        }
        <!-- orx:metadata:end -->
        """

        parsed = parse_orx_metadata(description)

        self.assertTrue(parsed.found)
        self.assertEqual(parsed.metadata["priority_hint"], "high")
        self.assertEqual(parsed.metadata["selection"]["lane"], "linear")

    def test_missing_metadata_block_is_empty_result(self) -> None:
        parsed = parse_orx_metadata("Issue body without structured metadata.")
        self.assertFalse(parsed.found)
        self.assertEqual(parsed.metadata, {})

    def test_duplicate_metadata_blocks_fail(self) -> None:
        description = """
        <!-- orx:metadata:start -->{"one": 1}<!-- orx:metadata:end -->
        <!-- orx:metadata:start -->{"two": 2}<!-- orx:metadata:end -->
        """
        with self.assertRaises(MetadataParseError):
            parse_orx_metadata(description)

    def test_malformed_metadata_fails(self) -> None:
        description = """
        <!-- orx:metadata:start -->
        {"bad-key": true}
        <!-- orx:metadata:end -->
        """
        with self.assertRaises(MetadataParseError):
            parse_orx_metadata(description)

    def test_parse_metadata_with_escaped_list_delimiters(self) -> None:
        description = r"""
        <!-- orx:metadata:start -->
        {
          "priority_hint": "high",
          "acceptance": \["tests", "tmux"\],
          "selection": {"lane": "linear"}
        }
        <!-- orx:metadata:end -->
        """

        parsed = parse_orx_metadata(description)

        self.assertTrue(parsed.found)
        self.assertEqual(parsed.metadata["acceptance"], ["tests", "tmux"])

    def test_parsed_metadata_can_be_stored_in_mirror_repository(self) -> None:
        description = """
        <!-- orx:metadata:start -->
        {"priority_hint": "high", "acceptance": ["tests"]}
        <!-- orx:metadata:end -->
        """
        parsed = parse_orx_metadata(description)

        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = LinearMirrorRepository(storage)
            record = repository.upsert_issue(
                linear_id="linear-18",
                identifier="PRO-18",
                title="Metadata parser",
                description=description,
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                source_updated_at="2026-04-15T19:27:00+00:00",
                metadata=parsed.metadata,
            )

        self.assertEqual(record.metadata["priority_hint"], "high")
        self.assertEqual(record.metadata["acceptance"], ["tests"])


if __name__ == "__main__":
    unittest.main()
