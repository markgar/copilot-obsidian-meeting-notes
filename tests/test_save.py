import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from copilot_obsidian_engine import cli
from copilot_obsidian_engine.ingest_workflows import apply_ingest, plan_ingest
from copilot_obsidian_engine.save_workflows import plan_save
from copilot_obsidian_engine.wiki_workflows import apply_wiki, plan_wiki


class SaveWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        init = plan_wiki(self.vault, "init")
        apply_wiki(self.vault, "init", init.approval_hash)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def request(self, **overrides):
        payload = {
            "version": 1,
            "title": "Scoped Retrieval Insight",
            "summary": "Lexical evidence keeps local answers grounded.",
            "body": (
                "Use deterministic lexical ranking with explicit citations "
                "so every answer remains traceable to local vault evidence."
            ),
            "tags": ["Retrieval", "Local First"],
            "links": ["[[Retrieval Strategy]]"],
            "source_refs": [],
        }
        payload.update(overrides)
        path = self.root / "save-request.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def run_cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(arguments)
        return exit_code, json.loads(output.getvalue())

    def dry_run(self, request):
        return self.run_cli(
            [
                "save",
                "--vault",
                str(self.vault),
                "--changes",
                str(request),
                "--dry-run",
                "--format",
                "json",
            ]
        )

    def test_dry_run_shape_and_hash_are_deterministic(self) -> None:
        request = self.request()

        first_exit, first = self.dry_run(request)
        second_exit, second = self.dry_run(request)

        self.assertEqual(first_exit, 0)
        self.assertEqual(second_exit, 0)
        self.assertEqual(
            first["data"]["approval_hash"],
            second["data"]["approval_hash"],
        )
        self.assertEqual(len(first["data"]["approval_hash"]), 64)
        self.assertTrue(
            first["data"]["target_path"].startswith("Notes/Saved/")
        )
        self.assertEqual(len(first["data"]["content_sha256"]), 64)
        self.assertEqual(
            first["data"]["change_summary"],
            {"creates": 2, "updates": 0, "overwrites": False},
        )
        self.assertFalse((self.vault / first["data"]["target_path"]).exists())

    def test_apply_creates_note_and_provenance_metadata_atomically(self) -> None:
        source = self.root / "source.md"
        source.write_text("# Evidence\n\nGrounded local source.\n", encoding="utf-8")
        ingest = plan_ingest(self.vault, [str(source)])
        apply_ingest(self.vault, [str(source)], ingest.approval_hash)
        candidate = ingest.candidates[0]
        request = self.request(
            source_refs=[
                candidate.path,
                candidate.provenance_refs[1],
            ]
        )
        _, preview = self.dry_run(request)

        exit_code, payload = self.run_cli(
            [
                "save",
                "--vault",
                str(self.vault),
                "--changes",
                str(request),
                "--apply",
                "--approved-hash",
                preview["data"]["approval_hash"],
            ]
        )

        self.assertEqual(exit_code, 0)
        note = self.vault / preview["data"]["target_path"]
        metadata = self.vault / preview["data"]["metadata_path"]
        self.assertTrue(note.is_file())
        self.assertTrue(metadata.is_file())
        self.assertIn(candidate.path, note.read_text(encoding="utf-8"))
        metadata_value = json.loads(metadata.read_text(encoding="utf-8"))
        self.assertEqual(len(metadata_value["source_refs"]), 2)
        self.assertTrue(
            all(len(ref["sha256"]) == 64 for ref in metadata_value["source_refs"])
        )
        self.assertEqual(payload["data"]["transaction"]["status"], "committed")

    def test_apply_refuses_stale_request_hash(self) -> None:
        request = self.request()
        plan = plan_save(self.vault, request)
        self.request(summary="A changed summary invalidates prior approval.")

        exit_code, payload = self.run_cli(
            [
                "save",
                "--vault",
                str(self.vault),
                "--changes",
                str(request),
                "--apply",
                "--approved-hash",
                plan.approval_hash,
            ]
        )

        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")
        self.assertFalse((self.vault / plan.target_path).exists())

    def test_invalid_and_transcript_payloads_are_refused(self) -> None:
        low_signal = self.request(
            summary="Too short",
            body="Tiny body",
        )
        exit_code, payload = self.dry_run(low_signal)
        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")

        transcript = self.request(
            body=(
                "00:01 Alice: We started the meeting discussion.\n"
                "00:10 Bob: We reviewed the project status.\n"
                "00:20 Alice: We assigned several action items.\n"
                "00:30 Bob: We closed the meeting with next steps."
            )
        )
        exit_code, payload = self.dry_run(transcript)
        self.assertEqual(exit_code, 6)
        self.assertIn("Transcript-style", payload["error"]["message"])

        long_transcript = self.request(
            body=(
                "Alice: "
                + " ".join(["discussion"] * 45)
                + "\nBob: "
                + " ".join(["response"] * 45)
            )
        )
        exit_code, payload = self.dry_run(long_transcript)
        self.assertEqual(exit_code, 6)
        self.assertIn("Transcript-style", payload["error"]["message"])

        unsafe_ref = self.request(source_refs=["Notes/\u0000.md"])
        exit_code, payload = self.dry_run(unsafe_ref)
        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")

    def test_existing_target_conflict_preserves_content(self) -> None:
        request = self.request()
        plan = plan_save(self.vault, request)
        target = self.vault / plan.target_path
        target.parent.mkdir()
        target.write_text("user content", encoding="utf-8")

        exit_code, payload = self.run_cli(
            [
                "save",
                "--vault",
                str(self.vault),
                "--changes",
                str(request),
                "--apply",
                "--approved-hash",
                plan.approval_hash,
            ]
        )

        self.assertEqual(exit_code, 4)
        self.assertEqual(payload["error"]["code"], "transaction_conflict")
        self.assertEqual(target.read_text(encoding="utf-8"), "user content")
        self.assertFalse((self.vault / plan.metadata_path).exists())

    def test_unsafe_saved_metadata_symlink_is_refused(self) -> None:
        external = self.root / "external"
        external.mkdir()
        saved = self.vault / ".copilot-obsidian" / "saved"
        saved.symlink_to(external, target_is_directory=True)
        request = self.request()

        exit_code, payload = self.dry_run(request)

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["error"]["code"], "invalid_vault_path")
        self.assertEqual(list(external.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
