import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from copilot_obsidian_engine import cli
from copilot_obsidian_engine.ingest_workflows import apply_ingest, plan_ingest
from copilot_obsidian_engine.wiki_workflows import (
    MARKER_CONTENT,
    apply_wiki,
    plan_wiki,
)


class QueryWorkflowTests(unittest.TestCase):
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

    def ingest(self, name, content):
        source = self.sources / name
        source.write_text(content, encoding="utf-8")
        plan = plan_ingest(self.vault, [str(source)])
        apply_ingest(self.vault, [str(source)], plan.approval_hash)
        return plan

    def run_cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(arguments)
        return exit_code, json.loads(output.getvalue())

    def query(self, text, limit=10):
        return self.run_cli(
            [
                "wiki-query",
                "--vault",
                str(self.vault),
                "--query",
                text,
                "--limit",
                str(limit),
                "--format",
                "json",
            ]
        )

    def test_retrieval_finds_expected_ingested_note(self) -> None:
        plan = self.ingest(
            "Retrieval.md",
            "# Retrieval Strategy\n\nVector retrieval uses grounded evidence.\n",
        )

        exit_code, payload = self.query("vector retrieval")

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["data"]["supported"])
        self.assertEqual(
            payload["data"]["evidence"][0]["path"],
            plan.candidates[0].path,
        )
        self.assertEqual(payload["data"]["evidence"][0]["kind"], "note")
        self.assertIn("[1]", payload["data"]["answer"])

    def test_provenance_records_are_queryable(self) -> None:
        self.ingest("FalconSource.md", "# General\n\nRecorded locally.\n")

        _, payload = self.query("FalconSource")

        self.assertTrue(payload["data"]["supported"])
        self.assertEqual(
            payload["data"]["evidence"][0]["kind"],
            "provenance",
        )

    def test_ranking_and_tie_breaking_are_deterministic(self) -> None:
        notes = self.vault / "Notes" / "Ingested"
        notes.mkdir(exist_ok=True)
        (notes / "beta.md").write_text("shared token", encoding="utf-8")
        (notes / "alpha.md").write_text("shared token", encoding="utf-8")

        _, first = self.query("shared token")
        _, second = self.query("shared token")
        first_paths = [
            evidence["path"] for evidence in first["data"]["evidence"]
        ]
        second_paths = [
            evidence["path"] for evidence in second["data"]["evidence"]
        ]

        self.assertEqual(first_paths, second_paths)
        self.assertEqual(
            first_paths[:2],
            [
                "Notes/Ingested/alpha.md",
                "Notes/Ingested/beta.md",
            ],
        )

    def test_evidence_shape_offsets_and_citation_order(self) -> None:
        self.ingest(
            "Evidence.md",
            "# Evidence\n\nGrounded citations preserve offsets.\n",
        )

        _, payload = self.query("grounded citations")
        evidence = payload["data"]["evidence"]

        self.assertEqual(
            [item["citation_order"] for item in evidence],
            list(range(1, len(evidence) + 1)),
        )
        first = evidence[0]
        for field in (
            "citation",
            "path",
            "kind",
            "snippet",
            "start_offset",
            "end_offset",
            "offset_basis",
            "score",
            "coverage",
            "matched_terms",
        ):
            self.assertIn(field, first)
        self.assertLess(first["start_offset"], first["end_offset"])
        self.assertEqual(
            payload["data"]["reasoning"]["method"],
            "lexical-overlap-v1",
        )

    def test_insufficient_evidence_returns_grounded_refusal(self) -> None:
        exit_code, payload = self.query("nonexistent quantum zebras")

        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["data"]["supported"])
        self.assertIsNone(payload["data"]["answer"])
        self.assertEqual(
            payload["data"]["refusal"]["code"],
            "insufficient_evidence",
        )
        self.assertEqual(payload["data"]["confidence"]["level"], "none")

    def test_limit_caps_evidence(self) -> None:
        notes = self.vault / "Notes" / "Ingested"
        notes.mkdir(exist_ok=True)
        for name in ("a.md", "b.md", "c.md"):
            (notes / name).write_text("bounded evidence", encoding="utf-8")

        _, payload = self.query("bounded evidence", limit=2)

        self.assertEqual(len(payload["data"]["evidence"]), 2)
        self.assertEqual(
            [item["citation_order"] for item in payload["data"]["evidence"]],
            [1, 2],
        )
        exit_code, invalid = self.query("bounded evidence", limit=0)
        self.assertEqual(exit_code, 6)
        self.assertEqual(invalid["error"]["code"], "precondition_failed")

    def test_query_is_read_only(self) -> None:
        self.ingest("ReadOnly.md", "# Read Only\n\nImmutable evidence.\n")
        before = {
            path.relative_to(self.vault).as_posix(): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }

        exit_code, _ = self.query("immutable evidence")

        after = {
            path.relative_to(self.vault).as_posix(): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }
        self.assertEqual(exit_code, 0)
        self.assertEqual(before, after)

    def test_notes_rank_before_denser_provenance(self) -> None:
        notes = self.vault / "Notes" / "Ingested"
        notes.mkdir(exist_ok=True)
        (notes / "grounded.md").write_text(
            "alpha beta plus several neutral filler words",
            encoding="utf-8",
        )
        self.ingest(
            "alpha-beta-alpha-beta-alpha-beta.md",
            "# Unrelated\n\nLocal record.\n",
        )

        _, payload = self.query("alpha beta")

        self.assertEqual(payload["data"]["evidence"][0]["kind"], "note")
        self.assertEqual(
            payload["data"]["evidence"][0]["path"],
            "Notes/Ingested/grounded.md",
        )

    def test_malformed_marker_and_provenance_are_refused(self) -> None:
        (self.vault / ".copilot-obsidian.json").write_text(
            "[]",
            encoding="utf-8",
        )
        exit_code, marker_payload = self.query("anything")
        self.assertEqual(exit_code, 1)
        self.assertEqual(marker_payload["error"]["code"], "invalid_config")

        (self.vault / ".copilot-obsidian.json").write_text(
            MARKER_CONTENT,
            encoding="utf-8",
        )
        provenance = self.vault / ".copilot-obsidian" / "provenance"
        provenance.mkdir()
        (provenance / "bad.json").write_text(
            json.dumps(
                {
                    "source_id": "0" * 64,
                    "sha256": "0" * 64,
                    "note_path": "Notes/example.md",
                }
            ),
            encoding="utf-8",
        )
        exit_code, provenance_payload = self.query("the")
        self.assertEqual(exit_code, 6)
        self.assertEqual(
            provenance_payload["error"]["code"],
            "precondition_failed",
        )

    def test_special_corpus_file_is_refused_without_reading(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs are unavailable")
        notes = self.vault / "Notes" / "Ingested"
        notes.mkdir(exist_ok=True)
        os.mkfifo(notes / "blocked.md")

        exit_code, payload = self.query("blocked")

        self.assertEqual(exit_code, 6)
        self.assertEqual(payload["error"]["code"], "precondition_failed")

    def test_unsafe_corpus_symlink_is_refused(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        vault = self.root / "unsafe-vault"
        external = self.root / "external-notes"
        vault.mkdir()
        external.mkdir()
        (vault / ".copilot-obsidian.json").write_text(
            MARKER_CONTENT,
            encoding="utf-8",
        )
        (vault / "Notes").symlink_to(external, target_is_directory=True)

        exit_code, payload = self.run_cli(
            [
                "wiki-query",
                "--vault",
                str(vault),
                "--query",
                "anything",
            ]
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["error"]["code"], "invalid_vault_path")


if __name__ == "__main__":
    unittest.main()
