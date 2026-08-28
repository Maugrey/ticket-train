#!/usr/bin/env python3
"""Regression tests for the persistent local Unity MCP slot pool."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_registry
import unity_slot_manager


class UnitySlotManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "unity-project"
        (self.repository / "Assets").mkdir(parents=True)
        (self.repository / "Packages").mkdir()
        (self.repository / "ProjectSettings").mkdir()
        (self.repository / ".codex").mkdir()
        (self.repository / "Packages" / "manifest.json").write_text(
            json.dumps({"dependencies": {"com.ivanmurzak.unity.mcp": "0.90.0"}}),
            encoding="utf-8",
        )
        (self.repository / "ProjectSettings" / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.0.50f1\n",
            encoding="utf-8",
        )
        (self.repository / ".codex" / "config.toml").write_text(
            "# tracked MCP template\n",
            encoding="utf-8",
        )
        self.git("init")
        self.git("config", "user.name", "Ticket Train Tests")
        self.git("config", "user.email", "ticket-train-tests@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "Initialize Unity fixture")
        self.git("branch", "codex/t-1")
        self.state_path = self.root / "state" / "slots.json"
        self.slot_root = self.root / "slots"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("git", "-C", str(self.repository), *args),
            check=True,
            capture_output=True,
            text=True,
        )

    def init_slots(self, count: int) -> int:
        args = argparse.Namespace(
            repository=self.repository,
            state=self.state_path,
            slot_root=self.slot_root,
            base_ref="HEAD",
            max_editors=count,
            skip_cli=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            return unity_slot_manager.init_slots(args)

    def test_initializes_three_persistent_worktrees_by_default_contract(self) -> None:
        self.assertEqual(self.init_slots(3), 0)
        state = run_registry.load_json(self.state_path)
        self.assertEqual(state["max_editors"], 3)
        self.assertEqual(state["mcp_mode"], "local")
        self.assertEqual(len(state["slots"]), 3)
        self.assertTrue(all(Path(slot["path"]).is_dir() for slot in state["slots"]))
        self.assertTrue(all(slot["status"] == "IDLE" for slot in state["slots"]))

    def test_acquire_is_exclusive_idempotent_and_releasable(self) -> None:
        self.assertEqual(self.init_slots(2), 0)
        expected_head = self.git("rev-parse", "HEAD").stdout.strip()
        acquire = argparse.Namespace(
            state=self.state_path,
            phase_key="run:T-1:implementation:1",
            requirement="editor-write",
            branch="codex/t-1",
            expected_head=expected_head,
            recovery_attempts=2,
            skip_cli=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(unity_slot_manager.acquire_slot(acquire), 0)
            self.assertEqual(unity_slot_manager.acquire_slot(acquire), 0)
        state = run_registry.load_json(self.state_path)
        leased = [slot for slot in state["slots"] if slot.get("lease")]
        self.assertEqual(len(leased), 1)
        self.assertEqual(leased[0]["lease"]["phase_key"], acquire.phase_key)

        release = argparse.Namespace(
            state=self.state_path,
            phase_key=acquire.phase_key,
            close_editor=False,
            skip_cli=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(unity_slot_manager.release_slot(release), 0)
            self.assertEqual(unity_slot_manager.release_slot(release), 0)
        state = run_registry.load_json(self.state_path)
        self.assertFalse(any(slot.get("lease") for slot in state["slots"]))
        self.assertEqual(state["slots"][0]["status"], "READY")

    def test_lowering_limit_disables_surplus_slots_without_deleting_worktrees(self) -> None:
        self.assertEqual(self.init_slots(3), 0)
        self.assertEqual(self.init_slots(1), 0)
        state = run_registry.load_json(self.state_path)
        self.assertEqual(state["max_editors"], 1)
        self.assertEqual([slot["status"] for slot in state["slots"]], ["IDLE", "DISABLED", "DISABLED"])
        self.assertTrue((self.slot_root / "unity-slot-3").is_dir())

    def test_stable_profile_hash_excludes_connection_credentials(self) -> None:
        config = self.root / "config.json"
        config.write_text(
            json.dumps({
                "keepServerRunning": True,
                "tools": ["tool-a"],
                "authToken": "must-not-enter-the-profile",
                "serverUrl": "http://127.0.0.1:9999",
            }),
            encoding="utf-8",
        )
        profile, digest = unity_slot_manager.stable_config_profile(config)
        self.assertEqual(profile, {"keepServerRunning": True, "tools": ["tool-a"]})
        self.assertEqual(len(digest or ""), 64)

    def test_acquire_preserves_managed_codex_config_across_detached_switch(self) -> None:
        self.assertEqual(self.init_slots(1), 0)
        slot_path = self.slot_root / "unity-slot-1"
        local_config = "# persistent slot endpoint\nport = 9123\n"
        (slot_path / ".codex" / "config.toml").write_text(local_config, encoding="utf-8")
        subprocess.run(
            ("git", "-C", str(slot_path), "update-index", "--skip-worktree", ".codex/config.toml"),
            check=True,
            capture_output=True,
            text=True,
        )
        (self.repository / ".codex" / "config.toml").write_text(
            "# changed tracked template\n",
            encoding="utf-8",
        )
        self.git("add", ".codex/config.toml")
        self.git("commit", "-m", "Change tracked MCP template")
        expected_head = self.git("rev-parse", "HEAD").stdout.strip()
        acquire = argparse.Namespace(
            state=self.state_path,
            phase_key="run:T-1:analysis:1",
            requirement="editor-read",
            branch=None,
            expected_head=expected_head,
            recovery_attempts=0,
            skip_cli=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(unity_slot_manager.acquire_slot(acquire), 0)
        self.assertEqual(
            (slot_path / ".codex" / "config.toml").read_text(encoding="utf-8"),
            local_config,
        )
        observed_head = subprocess.run(
            ("git", "-C", str(slot_path), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(observed_head, expected_head)
        status = subprocess.run(
            ("git", "-C", str(slot_path), "status", "--porcelain"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(status, "")
        index_flag = subprocess.run(
            ("git", "-C", str(slot_path), "ls-files", "-v", ".codex/config.toml"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertTrue(index_flag.startswith("S "))

    def test_acquire_quarantines_unusable_slot_and_tries_next_slot(self) -> None:
        self.assertEqual(self.init_slots(2), 0)
        (self.slot_root / "unity-slot-1" / "unexpected-local-change.txt").write_text(
            "do not overwrite\n",
            encoding="utf-8",
        )
        expected_head = self.git("rev-parse", "HEAD").stdout.strip()
        acquire = argparse.Namespace(
            state=self.state_path,
            phase_key="run:T-1:analysis:1",
            requirement="editor-read",
            branch=None,
            expected_head=expected_head,
            recovery_attempts=0,
            skip_cli=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(unity_slot_manager.acquire_slot(acquire), 0)
        state = run_registry.load_json(self.state_path)
        self.assertEqual(state["slots"][0]["status"], "BLOCKED_HUMAN")
        self.assertIn("uncommitted changes", state["slots"][0]["last_error"])
        self.assertEqual(state["slots"][1]["status"], "LEASED")
        self.assertEqual(state["slots"][1]["lease"]["phase_key"], acquire.phase_key)


if __name__ == "__main__":
    unittest.main()
