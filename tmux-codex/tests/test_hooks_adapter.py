import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.hooks import HookAdapter, LocalHooks


class _FailingHandler:
    def on_start(self, event):
        raise RuntimeError("boom")


class HookAdapterTests(unittest.TestCase):
    def test_local_on_finish_logs_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp)
            hooks_log = memory / "RUNNER_HOOKS.alpha.ndjson"
            adapter = HookAdapter([LocalHooks(memory_dir=memory, hooks_log=hooks_log)])

            adapter.emit(
                "on_finish",
                ts="2026-03-03T00:00:00Z",
                project="blog",
                runner_id="alpha",
                iteration=1,
                payload={
                    "final_message": "Implemented feature",
                    "hil_mode": "strict",
                    "runner_update": {
                        "summary": "Implemented feature",
                        "completed": ["a"],
                        "next_task": "b",
                        "next_task_reason": "c",
                        "blockers": [],
                            "done_candidate": False,
                        },
                },
            )

            self.assertTrue(hooks_log.exists())
            events = hooks_log.read_text().strip().splitlines()
            self.assertEqual(len(events), 1)
            self.assertIn('"event": "on_finish"', events[0])

    def test_handler_errors_are_non_fatal(self):
        adapter = HookAdapter([_FailingHandler()])
        event = adapter.emit(
            "on_start",
            ts="2026-03-03T00:00:00Z",
            project="blog",
            runner_id="alpha",
            iteration=0,
            payload={},
        )
        self.assertEqual(event.name, "on_start")

    def test_local_on_finalize_logs_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp)
            hooks_log = memory / "RUNNER_HOOKS.alpha.ndjson"
            adapter = HookAdapter([LocalHooks(memory_dir=memory, hooks_log=hooks_log)])

            adapter.emit(
                "on_finalize",
                ts="2026-03-03T00:00:00Z",
                project="blog",
                runner_id="alpha",
                iteration=2,
                payload={
                    "finalize_mode": "probe",
                    "completion_action": "done_lock_created",
                    "done_candidate": True,
                },
            )

            self.assertTrue(hooks_log.exists())
            events = hooks_log.read_text().strip().splitlines()
            self.assertEqual(len(events), 1)
            self.assertIn('"event": "on_finalize"', events[0])


if __name__ == "__main__":
    unittest.main()
