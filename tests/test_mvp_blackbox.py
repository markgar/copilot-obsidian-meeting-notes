import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MvpBlackBoxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_cli(self, *arguments):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "copilot_obsidian_engine",
                *map(str, arguments),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(
                f"CLI returned non-JSON output ({result.returncode}): "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
            raise error
        return result.returncode, payload

    def initialize(self) -> None:
        preview_exit, preview = self.run_cli(
            "wiki",
            "init",
            "--vault",
            self.vault,
        )
        self.assertEqual(preview_exit, 0)
        apply_exit, _ = self.run_cli(
            "wiki",
            "init",
            "--vault",
            self.vault,
            "--apply",
            "--approved-hash",
            preview["data"]["approval_hash"],
        )
        self.assertEqual(apply_exit, 0)

    def save_request(self, body=None) -> Path:
        request = self.root / "save-request.json"
        request.write_text(
            json.dumps(
                {
                    "version": 1,
                    "title": "Quasar telemetry insight",
                    "summary": "Quasar telemetry remains grounded in local evidence.",
                    "body": body
                    or (
                        "Quasar telemetry should use deterministic citations "
                        "so every saved conclusion remains locally verifiable."
                    ),
                    "tags": ["quasar", "evidence"],
                    "links": [],
                    "source_refs": [],
                }
            ),
            encoding="utf-8",
        )
        return request

    def test_init_preview_apply_then_doctor_ready(self) -> None:
        doctor_exit, before = self.run_cli(
            "wiki",
            "doctor",
            "--vault",
            self.vault,
        )
        self.assertEqual(doctor_exit, 0)
        self.assertFalse(before["data"]["ready"])

        self.initialize()

        doctor_exit, after = self.run_cli(
            "wiki",
            "doctor",
            "--vault",
            self.vault,
        )
        self.assertEqual(doctor_exit, 0)
        self.assertTrue(after["data"]["ready"])
        self.assertEqual(after["data"]["config"]["status"], "ready")

    def test_ingest_preview_apply_then_query_evidence(self) -> None:
        self.initialize()
        source = self.root / "retrieval.md"
        source.write_text(
            "# Black Box Retrieval\n\n"
            "Nebula retrieval uses deterministic grounded citations.\n",
            encoding="utf-8",
        )

        preview_exit, preview = self.run_cli(
            "wiki-ingest",
            "--vault",
            self.vault,
            "--source",
            source,
        )
        self.assertEqual(preview_exit, 0)
        apply_exit, _ = self.run_cli(
            "wiki-ingest",
            "--vault",
            self.vault,
            "--source",
            source,
            "--apply",
            "--approved-hash",
            preview["data"]["approval_hash"],
        )
        self.assertEqual(apply_exit, 0)

        query_exit, query = self.run_cli(
            "wiki-query",
            "--vault",
            self.vault,
            "--query",
            "nebula retrieval",
            "--limit",
            "3",
        )
        self.assertEqual(query_exit, 0)
        self.assertTrue(query["data"]["supported"])
        self.assertEqual(query["data"]["evidence"][0]["kind"], "note")
        self.assertEqual(
            query["data"]["evidence"][0]["path"],
            preview["data"]["candidates"][0]["path"],
        )

    def test_save_dry_run_apply_then_query_saved_note(self) -> None:
        self.initialize()
        request = self.save_request()

        preview_exit, preview = self.run_cli(
            "save",
            "--vault",
            self.vault,
            "--changes",
            request,
            "--dry-run",
        )
        self.assertEqual(preview_exit, 0)
        apply_exit, _ = self.run_cli(
            "save",
            "--vault",
            self.vault,
            "--changes",
            request,
            "--apply",
            "--approved-hash",
            preview["data"]["approval_hash"],
        )
        self.assertEqual(apply_exit, 0)

        query_exit, query = self.run_cli(
            "wiki-query",
            "--vault",
            self.vault,
            "--query",
            "quasar telemetry",
        )
        self.assertEqual(query_exit, 0)
        self.assertTrue(query["data"]["supported"])
        self.assertEqual(
            query["data"]["evidence"][0]["path"],
            preview["data"]["target_path"],
        )

    def test_stale_adopt_approval_is_refused(self) -> None:
        (self.vault / "Existing.md").write_text("existing", encoding="utf-8")
        preview_exit, preview = self.run_cli(
            "wiki",
            "adopt",
            "--vault",
            self.vault,
        )
        self.assertEqual(preview_exit, 0)
        inbox = self.vault / "Inbox"
        inbox.mkdir()
        (inbox / "README.md").write_text("user file", encoding="utf-8")

        apply_exit, payload = self.run_cli(
            "wiki",
            "adopt",
            "--vault",
            self.vault,
            "--apply",
            "--approved-hash",
            preview["data"]["approval_hash"],
        )

        self.assertEqual(apply_exit, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")
        self.assertFalse((self.vault / ".copilot-obsidian.json").exists())

    def test_stale_ingest_and_save_approvals_are_refused(self) -> None:
        self.initialize()
        source = self.root / "source.md"
        source.write_text("# Source\n\nOriginal grounded content.\n", encoding="utf-8")
        _, ingest_preview = self.run_cli(
            "wiki-ingest",
            "--vault",
            self.vault,
            "--source",
            source,
        )
        source.write_text("# Source\n\nChanged grounded content.\n", encoding="utf-8")
        ingest_exit, ingest_error = self.run_cli(
            "wiki-ingest",
            "--vault",
            self.vault,
            "--source",
            source,
            "--apply",
            "--approved-hash",
            ingest_preview["data"]["approval_hash"],
        )
        self.assertEqual(ingest_exit, 6)
        self.assertEqual(ingest_error["error"]["code"], "precondition_failed")

        request = self.save_request()
        _, save_preview = self.run_cli(
            "save",
            "--vault",
            self.vault,
            "--changes",
            request,
            "--dry-run",
        )
        self.save_request(
            body=(
                "Changed quasar telemetry requires a new deterministic "
                "approval before any scoped local persistence can occur."
            )
        )
        save_exit, save_error = self.run_cli(
            "save",
            "--vault",
            self.vault,
            "--changes",
            request,
            "--apply",
            "--approved-hash",
            save_preview["data"]["approval_hash"],
        )
        self.assertEqual(save_exit, 6)
        self.assertEqual(save_error["error"]["code"], "precondition_failed")

    def test_existing_save_target_returns_conflict(self) -> None:
        self.initialize()
        request = self.save_request()
        _, preview = self.run_cli(
            "save",
            "--vault",
            self.vault,
            "--changes",
            request,
            "--dry-run",
        )
        target = self.vault / preview["data"]["target_path"]
        target.parent.mkdir()
        target.write_text("existing user content", encoding="utf-8")

        apply_exit, payload = self.run_cli(
            "save",
            "--vault",
            self.vault,
            "--changes",
            request,
            "--apply",
            "--approved-hash",
            preview["data"]["approval_hash"],
        )

        self.assertEqual(apply_exit, 4)
        self.assertEqual(payload["error"]["code"], "transaction_conflict")
        self.assertEqual(target.read_text(encoding="utf-8"), "existing user content")

    def test_malformed_marker_blocks_ingest_query_and_save(self) -> None:
        self.initialize()
        (self.vault / ".copilot-obsidian.json").write_text(
            "[]",
            encoding="utf-8",
        )
        source = self.root / "source.md"
        source.write_text("# Source\n\nLocal evidence content.\n", encoding="utf-8")
        request = self.save_request()
        commands = (
            (
                "wiki-ingest",
                "--vault",
                self.vault,
                "--source",
                source,
            ),
            (
                "wiki-query",
                "--vault",
                self.vault,
                "--query",
                "local evidence",
            ),
            (
                "save",
                "--vault",
                self.vault,
                "--changes",
                request,
                "--dry-run",
            ),
        )

        for command in commands:
            with self.subTest(command=command[0]):
                exit_code, payload = self.run_cli(*command)
                self.assertEqual(exit_code, 1)
                self.assertEqual(payload["error"]["code"], "invalid_config")

    def test_malformed_provenance_blocks_query(self) -> None:
        self.initialize()
        provenance = self.vault / ".copilot-obsidian" / "provenance"
        provenance.mkdir()
        (provenance / "bad.json").write_text("{}", encoding="utf-8")

        exit_code, payload = self.run_cli(
            "wiki-query",
            "--vault",
            self.vault,
            "--query",
            "anything",
        )

        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")

    def test_legacy_wiki_request_returns_exit_three_without_mutation(self) -> None:
        request = self.root / "legacy.json"
        request.write_text("{}", encoding="utf-8")
        before = list(self.vault.iterdir())

        exit_code, payload = self.run_cli(
            "wiki",
            "--vault",
            self.vault,
            "--request",
            request,
        )

        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["error"]["code"], "not_implemented")
        self.assertEqual(list(self.vault.iterdir()), before)

    def test_ingest_output_compatibility_flag_does_not_write_file(self) -> None:
        self.initialize()
        source = self.root / "source.md"
        output = self.root / "candidates.json"
        source.write_text("# Source\n\nCompatibility evidence.\n", encoding="utf-8")

        exit_code, payload = self.run_cli(
            "wiki-ingest",
            "--vault",
            self.vault,
            "--source",
            source,
            "--output",
            output,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["data"]["requested_output"], str(output))
        self.assertFalse(payload["data"]["output_written"])
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
