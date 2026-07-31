import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from copilot_obsidian_engine import cli
from copilot_obsidian_engine.errors import (
    PreconditionError,
    TransactionError,
    VaultPathError,
)
from copilot_obsidian_engine.ingest_workflows import apply_ingest, plan_ingest
from copilot_obsidian_engine.wiki_workflows import apply_wiki, plan_wiki


class IngestWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.vault = self.root / "vault"
        self.sources = self.root / "sources"
        self.vault.mkdir()
        self.sources.mkdir()
        init = plan_wiki(self.vault, "init")
        apply_wiki(self.vault, "init", init.approval_hash)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def source(self, name="Research.md", content="# Research\n\nSee [[Topic]].\n"):
        path = self.sources / name
        path.write_text(content, encoding="utf-8")
        return path

    def run_cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(arguments)
        return exit_code, json.loads(output.getvalue())

    def test_source_capture_paths_and_plan_are_deterministic(self) -> None:
        source = self.source()
        first = plan_ingest(self.vault, [str(source)])
        second = plan_ingest(self.vault, [str(source)])
        expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()

        self.assertEqual(first.approval_hash, second.approval_hash)
        self.assertEqual(first.sources[0].sha256, expected_hash)
        self.assertEqual(
            first.sources[0].raw_path,
            f".copilot-obsidian/raw/{expected_hash}.md",
        )
        self.assertEqual(
            first.candidates[0].path,
            second.candidates[0].path,
        )
        self.assertEqual(first.candidates[0].links, ("Topic",))

    def test_multiple_source_order_is_deterministic(self) -> None:
        first_source = self.source("A.md", "# A\n")
        second_source = self.source("B.md", "# B\n")

        forward = plan_ingest(
            self.vault,
            [str(first_source), str(second_source)],
        )
        reverse = plan_ingest(
            self.vault,
            [str(second_source), str(first_source)],
        )

        self.assertEqual(forward.approval_hash, reverse.approval_hash)
        self.assertEqual(
            [source.path for source in forward.sources],
            [source.path for source in reverse.sources],
        )

    def test_preview_payload_contains_sources_candidates_and_hash(self) -> None:
        source = self.source()
        legacy_output = self.root / "candidates.json"

        exit_code, payload = self.run_cli(
            [
                "wiki-ingest",
                "--vault",
                str(self.vault),
                "--source",
                str(source),
                "--output",
                str(legacy_output),
                "--format",
                "json",
            ]
        )

        self.assertEqual(exit_code, 0)
        data = payload["data"]
        self.assertEqual(len(data["approval_hash"]), 64)
        self.assertEqual(len(data["sources"]), 1)
        self.assertEqual(len(data["candidates"]), 1)
        self.assertEqual(data["bundle_summary"]["operation_count"], 3)
        self.assertEqual(data["requested_output"], str(legacy_output))
        self.assertFalse(data["output_written"])
        self.assertFalse(legacy_output.exists())

    def test_apply_atomically_creates_raw_provenance_and_note(self) -> None:
        source = self.source()
        plan = plan_ingest(self.vault, [str(source)])

        exit_code, payload = self.run_cli(
            [
                "wiki-ingest",
                "--vault",
                str(self.vault),
                "--source",
                str(source),
                "--apply",
                "--approved-hash",
                plan.approval_hash,
            ]
        )

        self.assertEqual(exit_code, 0)
        candidate = plan.candidates[0]
        raw = self.vault / plan.sources[0].raw_path
        ledger = self.vault / candidate.provenance_refs[1]
        note = self.vault / candidate.path
        self.assertEqual(raw.read_text(encoding="utf-8"), source.read_text())
        self.assertEqual(
            json.loads(ledger.read_text())["sha256"],
            plan.sources[0].sha256,
        )
        self.assertEqual(note.read_text(encoding="utf-8"), candidate.content)
        self.assertEqual(payload["data"]["transaction"]["status"], "committed")

    def test_apply_refuses_stale_source_hash(self) -> None:
        source = self.source()
        plan = plan_ingest(self.vault, [str(source)])
        source.write_text("# Changed\n", encoding="utf-8")

        exit_code, payload = self.run_cli(
            [
                "wiki-ingest",
                "--vault",
                str(self.vault),
                "--source",
                str(source),
                "--apply",
                "--approved-hash",
                plan.approval_hash,
            ]
        )

        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")
        self.assertFalse((self.vault / plan.sources[0].raw_path).exists())
        self.assertFalse((self.vault / plan.candidates[0].path).exists())

    def test_apply_requires_approval_gate(self) -> None:
        source = self.source()

        exit_code, payload = self.run_cli(
            [
                "wiki-ingest",
                "--vault",
                str(self.vault),
                "--source",
                str(source),
                "--apply",
            ]
        )

        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")

    def test_injected_failure_rolls_back_all_ingest_targets(self) -> None:
        source = self.source()
        plan = plan_ingest(self.vault, [str(source)])

        def fail_second(index, operation):
            if index == 1:
                raise OSError("injected ingest failure")

        with self.assertRaises(TransactionError):
            apply_ingest(
                self.vault,
                [str(source)],
                plan.approval_hash,
                before_apply=fail_second,
            )

        for operation in plan.bundle.operations:
            self.assertFalse((self.vault / operation.target).exists())
        journal = (
            self.vault
            / ".copilot-obsidian"
            / "transactions"
            / plan.bundle.transaction_id
            / "journal.json"
        )
        self.assertEqual(json.loads(journal.read_text())["status"], "rolled_back")

    def test_rejects_unsafe_engine_storage_symlink(self) -> None:
        source = self.source()
        external = self.root / "external"
        external.mkdir()
        raw = self.vault / ".copilot-obsidian" / "raw"
        raw.symlink_to(external, target_is_directory=True)

        with self.assertRaises(VaultPathError):
            plan_ingest(self.vault, [str(source)])

        self.assertEqual(list(external.iterdir()), [])

    def test_rejects_engine_state_as_source(self) -> None:
        source = self.vault / ".copilot-obsidian" / "state.md"
        source.write_text("internal", encoding="utf-8")

        with self.assertRaises(PreconditionError):
            plan_ingest(self.vault, [str(source)])

    def test_rejects_source_symlink(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        source = self.source()
        link = self.sources / "linked.md"
        link.symlink_to(source)

        with self.assertRaises(PreconditionError):
            plan_ingest(self.vault, [str(link)])

    def test_rejects_source_parent_symlink(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        source = self.source()
        linked_directory = self.root / "linked-sources"
        linked_directory.symlink_to(self.sources, target_is_directory=True)

        with self.assertRaises(PreconditionError):
            plan_ingest(self.vault, [str(linked_directory / source.name)])

    def test_rejects_missing_and_duplicate_sources(self) -> None:
        missing = self.sources / "missing.md"
        with self.assertRaises(PreconditionError):
            plan_ingest(self.vault, [str(missing)])

        source = self.source()
        with self.assertRaises(PreconditionError):
            plan_ingest(self.vault, [str(source), str(source)])

    def test_rejects_unreadable_source(self) -> None:
        source = self.source()
        with patch.object(
            Path,
            "read_bytes",
            side_effect=PermissionError("denied"),
        ):
            with self.assertRaises(PreconditionError):
                plan_ingest(self.vault, [str(source)])


if __name__ == "__main__":
    unittest.main()
