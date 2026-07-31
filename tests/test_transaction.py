import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from copilot_obsidian_engine.errors import (
    ConflictError,
    LockError,
    PreconditionError,
    RecoveryRequiredError,
    TransactionError,
)
from copilot_obsidian_engine.transaction import (
    VaultLock,
    apply_bundle,
    inspect_bundle,
    recover_transaction,
)


def content_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_bundle(self, transaction_id, operations):
        path = self.vault.parent / f"{transaction_id}.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "transaction_id": transaction_id,
                    "metadata": {"source": "test"},
                    "operations": operations,
                }
            ),
            encoding="utf-8",
        )
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def journal(self, transaction_id):
        path = (
            self.vault
            / ".copilot-obsidian"
            / "transactions"
            / transaction_id
            / "journal.json"
        )
        return json.loads(path.read_text(encoding="utf-8"))

    def test_lock_contention(self) -> None:
        with VaultLock(self.vault):
            with self.assertRaises(LockError):
                with VaultLock(self.vault):
                    pass
        with VaultLock(self.vault):
            pass

    def test_stale_lock_is_reclaimed(self) -> None:
        lock = self.vault / ".copilot-obsidian-lock"
        lock.write_text(
            json.dumps({"pid": 999_999_999, "token": "stale"}),
            encoding="utf-8",
        )

        with VaultLock(self.vault):
            self.assertTrue(lock.exists())

        self.assertFalse(lock.exists())

    def test_inspect_rejects_malformed_bundle(self) -> None:
        path = self.vault.parent / "malformed-bundle.json"
        path.write_text('{"version": 1}', encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)

        with self.assertRaises(PreconditionError):
            inspect_bundle(self.vault, path)

        self.assertEqual(list(self.vault.iterdir()), [])

    def test_apply_replaces_file_atomically(self) -> None:
        target = self.vault / "Notes" / "example.md"
        target.parent.mkdir()
        target.write_text("before", encoding="utf-8")
        bundle = self.write_bundle(
            "happy-path",
            [
                {
                    "target": "Notes/example.md",
                    "intent": "replace",
                    "content": "after",
                    "expected_sha256": content_sha("before"),
                }
            ],
        )

        result = apply_bundle(self.vault, bundle)

        self.assertEqual(target.read_text(encoding="utf-8"), "after")
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.journal("happy-path")["status"], "committed")

    def test_apply_detects_sha_conflict_without_mutation(self) -> None:
        target = self.vault / "example.md"
        target.write_text("current", encoding="utf-8")
        bundle = self.write_bundle(
            "conflict",
            [
                {
                    "target": "example.md",
                    "intent": "replace",
                    "content": "new",
                    "expected_sha256": "0" * 64,
                }
            ],
        )

        with self.assertRaises(ConflictError):
            apply_bundle(self.vault, bundle)

        self.assertEqual(target.read_text(encoding="utf-8"), "current")
        self.assertFalse((self.vault / ".copilot-obsidian").exists())

    def test_apply_rejects_symlinked_transaction_state(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        (self.vault / ".copilot-obsidian").symlink_to(
            Path(external.name),
            target_is_directory=True,
        )
        bundle = self.write_bundle(
            "symlink-state",
            [
                {
                    "target": "example.md",
                    "intent": "create",
                    "content": "content",
                    "expected_sha256": None,
                }
            ],
        )

        with self.assertRaises(PreconditionError):
            apply_bundle(self.vault, bundle)

        self.assertEqual(list(Path(external.name).iterdir()), [])

    def test_apply_failure_rolls_back_completed_operations(self) -> None:
        first = self.vault / "first.md"
        first.write_text("first-before", encoding="utf-8")
        bundle = self.write_bundle(
            "rollback",
            [
                {
                    "target": "first.md",
                    "intent": "replace",
                    "content": "first-after",
                    "expected_sha256": content_sha("first-before"),
                },
                {
                    "target": "nested/second.md",
                    "intent": "create",
                    "content": "second",
                    "expected_sha256": None,
                },
            ],
        )

        def fail_second(index, operation):
            if index == 1:
                raise OSError("injected apply failure")

        with self.assertRaises(TransactionError):
            apply_bundle(self.vault, bundle, before_apply=fail_second)

        self.assertEqual(first.read_text(encoding="utf-8"), "first-before")
        self.assertFalse((self.vault / "nested").exists())
        self.assertEqual(self.journal("rollback")["status"], "rolled_back")

    def test_recover_finishes_incomplete_rollback(self) -> None:
        first = self.vault / "first.md"
        first.write_text("before", encoding="utf-8")
        bundle = self.write_bundle(
            "recoverable",
            [
                {
                    "target": "first.md",
                    "intent": "replace",
                    "content": "after",
                    "expected_sha256": content_sha("before"),
                },
                {
                    "target": "second.md",
                    "intent": "create",
                    "content": "second",
                    "expected_sha256": None,
                },
            ],
        )

        def fail_second(index, operation):
            if index == 1:
                raise OSError("injected apply failure")

        def fail_rollback(index, entry):
            raise OSError("injected rollback failure")

        with self.assertRaises(RecoveryRequiredError):
            apply_bundle(
                self.vault,
                bundle,
                before_apply=fail_second,
                before_rollback=fail_rollback,
            )

        self.assertEqual(self.journal("recoverable")["status"], "needs_recovery")
        result = recover_transaction(self.vault, "recoverable")

        self.assertEqual(first.read_text(encoding="utf-8"), "before")
        self.assertFalse((self.vault / "second.md").exists())
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(self.journal("recoverable")["status"], "rolled_back")

    def test_recover_preserves_post_failure_user_edit(self) -> None:
        target = self.vault / "example.md"
        target.write_text("before", encoding="utf-8")
        bundle = self.write_bundle(
            "preserve-edit",
            [
                {
                    "target": "example.md",
                    "intent": "replace",
                    "content": "after",
                    "expected_sha256": content_sha("before"),
                },
                {
                    "target": "second.md",
                    "intent": "create",
                    "content": "second",
                    "expected_sha256": None,
                },
            ],
        )

        def fail_second(index, operation):
            if index == 1:
                raise OSError("injected apply failure")

        def fail_rollback(index, entry):
            raise OSError("injected rollback failure")

        with self.assertRaises(RecoveryRequiredError):
            apply_bundle(
                self.vault,
                bundle,
                before_apply=fail_second,
                before_rollback=fail_rollback,
            )
        target.write_text("user edit", encoding="utf-8")

        with self.assertRaises(ConflictError):
            recover_transaction(self.vault, "preserve-edit")

        self.assertEqual(target.read_text(encoding="utf-8"), "user edit")
        self.assertEqual(self.journal("preserve-edit")["status"], "needs_recovery")

    def test_recover_resumes_after_rollback_capture(self) -> None:
        target = self.vault / "example.md"
        target.write_text("before", encoding="utf-8")
        bundle = self.write_bundle(
            "capture-recovery",
            [
                {
                    "target": "example.md",
                    "intent": "replace",
                    "content": "after",
                    "expected_sha256": content_sha("before"),
                },
                {
                    "target": "second.md",
                    "intent": "create",
                    "content": "second",
                    "expected_sha256": None,
                },
            ],
        )

        def fail_second(index, operation):
            if index == 1:
                raise OSError("injected apply failure")

        def fail_rollback(index, entry):
            raise OSError("injected rollback failure")

        with self.assertRaises(RecoveryRequiredError):
            apply_bundle(
                self.vault,
                bundle,
                before_apply=fail_second,
                before_rollback=fail_rollback,
            )

        transaction_directory = (
            self.vault
            / ".copilot-obsidian"
            / "transactions"
            / "capture-recovery"
        )
        rollback_directory = transaction_directory / "rollback"
        rollback_directory.mkdir()
        os.replace(target, rollback_directory / "0000.bin")

        result = recover_transaction(self.vault, "capture-recovery")

        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(target.read_text(encoding="utf-8"), "before")


if __name__ == "__main__":
    unittest.main()
