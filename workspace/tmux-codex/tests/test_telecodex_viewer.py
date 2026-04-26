import sqlite3
import unittest

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.telecodex_viewer import _compact_events


class TelecodexViewerTests(unittest.TestCase):
    def test_compacts_consecutive_assistant_snapshots(self):
        rows = [
            {"id": 1, "event_kind": "status", "text": "Starting", "created_at": "2026-04-26T19:40:00Z"},
            {"id": 2, "event_kind": "assistant", "text": "I", "created_at": "2026-04-26T19:40:01Z"},
            {"id": 3, "event_kind": "assistant", "text": "I am working", "created_at": "2026-04-26T19:40:02Z"},
            {"id": 4, "event_kind": "progress", "text": "Running test", "created_at": "2026-04-26T19:40:03Z"},
            {"id": 5, "event_kind": "assistant", "text": "Done", "created_at": "2026-04-26T19:40:04Z"},
        ]

        compacted = _compact_events(rows)

        self.assertEqual([row["id"] for row in compacted], [1, 3, 4, 5])
        self.assertEqual(compacted[1]["text"], "I am working")


if __name__ == "__main__":
    unittest.main()
