import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from copilot_obsidian_engine import cli
from copilot_obsidian_engine.errors import PreconditionError
from copilot_obsidian_engine.wiki_workflows import apply_wiki, plan_wiki


class WikiWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(arguments)
        return exit_code, json.loads(output.getvalue())

    def test_doctor_payload_shape(self) -> None:
        exit_code, payload = self.run_cli(
            ["wiki", "doctor", "--vault", str(self.vault)]
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["command"], "wiki.doctor")
        data = payload["data"]
        self.assertFalse(data["ready"])
        self.assertEqual(data["config"]["status"], "missing")
        self.assertTrue(data["transaction"]["ready"])
        self.assertTrue(data["capabilities"]["sha256_preconditions"])
        self.assertEqual(data["recommended_action"], "init")

    def test_doctor_reports_invalid_marker(self) -> None:
        (self.vault / ".copilot-obsidian.json").write_text(
            "[]",
            encoding="utf-8",
        )

        exit_code, payload = self.run_cli(
            ["wiki", "doctor", "--vault", str(self.vault)]
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["data"]["ready"])
        self.assertEqual(payload["data"]["config"]["status"], "invalid")
        self.assertEqual(
            payload["data"]["recommended_action"],
            "repair-config",
        )

    def test_init_plan_then_apply(self) -> None:
        plan_exit, plan_payload = self.run_cli(
            ["wiki", "init", "--vault", str(self.vault)]
        )

        self.assertEqual(plan_exit, 0)
        plan = plan_payload["data"]
        self.assertTrue(plan["apply_required"])
        self.assertEqual(plan["workflow"], "init")
        self.assertEqual(list(self.vault.iterdir()), [])

        apply_exit, apply_payload = self.run_cli(
            [
                "wiki",
                "init",
                "--vault",
                str(self.vault),
                "--apply",
                "--approved-hash",
                plan["approval_hash"],
            ]
        )

        self.assertEqual(apply_exit, 0)
        self.assertEqual(
            apply_payload["data"]["transaction"]["status"],
            "committed",
        )
        self.assertTrue((self.vault / ".copilot-obsidian.json").is_file())
        self.assertTrue((self.vault / "Home.md").is_file())
        self.assertTrue((self.vault / "Inbox" / "README.md").is_file())
        self.assertTrue((self.vault / "Notes" / "README.md").is_file())
        doctor_exit, doctor_payload = self.run_cli(
            ["wiki", "doctor", "--vault", str(self.vault)]
        )
        self.assertEqual(doctor_exit, 0)
        self.assertTrue(doctor_payload["data"]["ready"])

    def test_adopt_plans_additions_without_touching_existing_content(self) -> None:
        existing = self.vault / "Existing.md"
        existing.write_text("# Existing\n", encoding="utf-8")

        exit_code, payload = self.run_cli(
            ["wiki", "adopt", "--vault", str(self.vault)]
        )

        self.assertEqual(exit_code, 0)
        targets = {
            operation["target"]
            for operation in payload["data"]["bundle"]["operations"]
        }
        self.assertIn(".copilot-obsidian.json", targets)
        self.assertEqual(existing.read_text(encoding="utf-8"), "# Existing\n")
        self.assertFalse((self.vault / ".copilot-obsidian.json").exists())

    def test_apply_requires_both_gate_and_approval_hash(self) -> None:
        exit_code, payload = self.run_cli(
            ["wiki", "init", "--vault", str(self.vault), "--apply"]
        )

        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_init_rechecks_empty_vault_under_transaction_lock(self) -> None:
        plan = plan_wiki(self.vault, "init")
        with patch(
            "copilot_obsidian_engine.wiki_workflows._meaningful_entries",
            side_effect=[[], ["raced.md"]],
        ):
            with self.assertRaises(PreconditionError):
                apply_wiki(self.vault, "init", plan.approval_hash)

        self.assertFalse((self.vault / ".copilot-obsidian.json").exists())
        self.assertFalse((self.vault / ".copilot-obsidian").exists())

    def test_apply_rejects_stale_approval_without_mutation(self) -> None:
        (self.vault / "Existing.md").write_text("existing", encoding="utf-8")
        _, plan_payload = self.run_cli(
            ["wiki", "adopt", "--vault", str(self.vault)]
        )
        approved_hash = plan_payload["data"]["approval_hash"]
        inbox = self.vault / "Inbox"
        inbox.mkdir()
        (inbox / "README.md").write_text("user content", encoding="utf-8")

        exit_code, payload = self.run_cli(
            [
                "wiki",
                "adopt",
                "--vault",
                str(self.vault),
                "--apply",
                "--approved-hash",
                approved_hash,
            ]
        )

        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")
        self.assertEqual(
            (inbox / "README.md").read_text(encoding="utf-8"),
            "user content",
        )
        self.assertFalse((self.vault / ".copilot-obsidian.json").exists())

    def test_adopt_rejects_symlinked_scaffold_path(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        (self.vault / "Existing.md").write_text("existing", encoding="utf-8")
        (self.vault / "Inbox").symlink_to(
            Path(external.name),
            target_is_directory=True,
        )

        exit_code, payload = self.run_cli(
            ["wiki", "adopt", "--vault", str(self.vault)]
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["error"]["code"], "invalid_vault_path")
        self.assertEqual(list(Path(external.name).iterdir()), [])

    def test_wiki_action_rejects_ambiguous_vault_arguments(self) -> None:
        exit_code, payload = self.run_cli(
            [
                "wiki",
                "--vault",
                str(self.vault),
                "doctor",
                "--vault",
                str(self.vault),
            ]
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["error"]["code"], "usage_error")


if __name__ == "__main__":
    unittest.main()
