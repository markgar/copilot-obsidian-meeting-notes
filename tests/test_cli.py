import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import copilot_obsidian_engine
from copilot_obsidian_engine import cli
from copilot_obsidian_engine.models import CommandResponse
from copilot_obsidian_engine.transaction import VaultLock


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_package_import(self) -> None:
        self.assertEqual(copilot_obsidian_engine.__version__, "0.1.0")

    def test_argument_contracts_parse(self) -> None:
        cases = (
            ["wiki", "--vault", str(self.vault), "--request", "request.json"],
            [
                "wiki-ingest",
                "--vault",
                str(self.vault),
                "--source",
                "source.md",
                "--output",
                "candidate.json",
            ],
            [
                "wiki-query",
                "--vault",
                str(self.vault),
                "--query",
                "local-first",
                "--limit",
                "5",
            ],
            [
                "save",
                "--vault",
                str(self.vault),
                "--changes",
                "changes.json",
                "--dry-run",
            ],
            [
                "transaction",
                "inspect",
                "--vault",
                str(self.vault),
                "--bundle",
                "bundle.json",
            ],
            [
                "transaction",
                "apply",
                "--vault",
                str(self.vault),
                "--bundle",
                "bundle.json",
            ],
            [
                "transaction",
                "recover",
                "--vault",
                str(self.vault),
                "--transaction-id",
                "tx-1",
            ],
        )
        parser = cli.build_parser()
        for arguments in cases:
            with self.subTest(command=arguments[0]):
                parsed = parser.parse_args(arguments)
                self.assertEqual(parsed.command, arguments[0])
                self.assertEqual(parsed.output_format, "json")

    def test_dispatch_routes_each_command_to_its_handler(self) -> None:
        cases = (
            ["wiki", "--vault", str(self.vault), "--request", "request.json"],
            [
                "wiki-ingest",
                "--vault",
                str(self.vault),
                "--source",
                "source.md",
                "--output",
                "candidate.json",
            ],
            ["wiki-query", "--vault", str(self.vault), "--query", "topic"],
            [
                "save",
                "--vault",
                str(self.vault),
                "--changes",
                "changes.json",
                "--apply",
            ],
            [
                "transaction",
                "inspect",
                "--vault",
                str(self.vault),
                "--bundle",
                "bundle.json",
            ],
        )
        parser = cli.build_parser()

        for arguments in cases:
            with self.subTest(command=arguments[0]):
                captured = []

                def handler(request):
                    captured.append(request)
                    return CommandResponse.success(request.command)

                args = parser.parse_args(arguments)
                with patch.dict(
                    cli.HANDLERS,
                    {arguments[0]: handler},
                    clear=True,
                ):
                    response = cli.dispatch(args)

                self.assertTrue(response.ok)
                self.assertEqual(captured[0].command, arguments[0])
                self.assertEqual(captured[0].vault, str(self.vault.resolve()))

    def test_not_implemented_is_structured_and_nonzero(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(
                [
                    "wiki",
                    "--vault",
                    str(self.vault),
                    "--request",
                    "request.json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "wiki")
        self.assertEqual(payload["error"]["code"], "not_implemented")

    def test_invalid_arguments_are_structured_and_nonzero(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(["wiki"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["error"]["code"], "usage_error")

    def test_transaction_precondition_exit_is_structured(self) -> None:
        bundle = self.vault / "malformed.json"
        bundle.write_text("{}", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(
                [
                    "transaction",
                    "inspect",
                    "--vault",
                    str(self.vault),
                    "--bundle",
                    str(bundle),
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["command"], "transaction.inspect")
        self.assertEqual(payload["error"]["code"], "precondition_failed")

    def test_transaction_conflict_exit_is_structured(self) -> None:
        target = self.vault / "note.md"
        target.write_text("current", encoding="utf-8")
        bundle = self.vault / "conflict.json"
        bundle.write_text(
            json.dumps(
                {
                    "version": 1,
                    "transaction_id": "cli-conflict",
                    "metadata": {},
                    "operations": [
                        {
                            "target": "note.md",
                            "intent": "replace",
                            "content": "new",
                            "expected_sha256": "0" * 64,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(
                [
                    "transaction",
                    "apply",
                    "--vault",
                    str(self.vault),
                    "--bundle",
                    str(bundle),
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 4)
        self.assertEqual(payload["error"]["code"], "transaction_conflict")
        self.assertEqual(target.read_text(encoding="utf-8"), "current")

    def test_transaction_lock_exit_is_structured(self) -> None:
        bundle = self.vault / "locked.json"
        bundle.write_text(
            json.dumps(
                {
                    "version": 1,
                    "transaction_id": "cli-locked",
                    "metadata": {},
                    "operations": [
                        {
                            "target": "note.md",
                            "intent": "create",
                            "content": "new",
                            "expected_sha256": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        output = io.StringIO()
        with VaultLock(self.vault), redirect_stdout(output):
            exit_code = cli.main(
                [
                    "transaction",
                    "apply",
                    "--vault",
                    str(self.vault),
                    "--bundle",
                    str(bundle),
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 5)
        self.assertEqual(payload["error"]["code"], "vault_locked")


if __name__ == "__main__":
    unittest.main()
