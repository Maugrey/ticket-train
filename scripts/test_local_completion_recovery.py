import json
from pathlib import Path
import tempfile
import unittest

from train_supervisor import recover_completed_message


class LocalCompletionRecoveryTests(unittest.TestCase):
    def read(self, session, messages, requested="current"):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout.jsonl"
            rows = [{"type": "session_meta", "payload": {"id": session}}]
            rows += [{"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn,
                     "last_agent_message": text}} for turn, text in messages]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            return recover_completed_message({"result": {"thread": {"id": "worker", "path": str(path)}}}, "worker", requested)

    def test_exact_turn_not_previous_result(self):
        text, evidence = self.read("worker", [("old", "wrong"), ("current", '{"envelope":{}}')])
        self.assertEqual(text, '{"envelope":{}}')
        self.assertEqual(evidence["turn_id"], "current")
        self.assertEqual(len(evidence["message_sha256"]), 64)

    def test_mismatched_session_rejected(self):
        with self.assertRaises(ValueError):
            self.read("other", [("current", "result")])

    def test_missing_turn_rejected(self):
        with self.assertRaises(ValueError):
            self.read("worker", [("old", "result")])

    def test_duplicate_completion_rejected(self):
        with self.assertRaises(ValueError):
            self.read("worker", [("current", "one"), ("current", "two")])

    def test_empty_completion_rejected(self):
        with self.assertRaises(ValueError):
            self.read("worker", [("current", "")])

    def test_creation_identity_rejected(self):
        with self.assertRaises(ValueError):
            recover_completed_message({"result": {"thread": {"id": "other", "path": "unused"}}}, "worker", "current")


if __name__ == "__main__":
    unittest.main()
