from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from ..errors import (
    ConflictError,
    EngineError,
    PreconditionError,
    RecoveryRequiredError,
    TransactionError,
)
from ..models import JsonValue
from ..vault import resolve_vault_target
from .bundle import FileIntent, OperationBundle, load_bundle, validate_transaction_id
from .lock import VaultLock

TRANSACTION_ROOT = Path(".copilot-obsidian") / "transactions"
JOURNAL_NAME = "journal.json"
ApplyHook = Callable[[int, FileIntent], None]
RollbackHook = Callable[[int, dict[str, JsonValue]], None]
LockedPrecondition = Callable[[], None]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _target_path(vault: Path, target: str) -> Path:
    return resolve_vault_target(vault, target)


def _validate_precondition(vault: Path, operation: FileIntent) -> dict[str, JsonValue]:
    path = _target_path(vault, operation.target)
    if path.exists() and not path.is_file():
        raise PreconditionError(
            "Operation target exists but is not a file.",
            details={"target": operation.target},
        )
    if not path.exists():
        parent = path.parent
        while parent != vault and not parent.exists():
            parent = parent.parent
        if not parent.is_dir():
            raise PreconditionError(
                "Operation target parent is not a directory.",
                details={"target": operation.target, "parent": str(parent)},
            )

    if operation.intent == "create":
        if path.exists():
            raise ConflictError(
                "Create target already exists.",
                details={"target": operation.target},
            )
        return {"target": operation.target, "exists": False, "sha256": None}

    if not path.exists():
        raise PreconditionError(
            "Replace target does not exist.",
            details={"target": operation.target},
        )
    actual_sha = sha256_file(path)
    if actual_sha != operation.expected_sha256:
        raise ConflictError(
            "Target content does not match expected SHA-256.",
            details={
                "target": operation.target,
                "expected_sha256": operation.expected_sha256,
                "actual_sha256": actual_sha,
            },
        )
    return {"target": operation.target, "exists": True, "sha256": actual_sha}


def _inspect(vault: Path, bundle: OperationBundle) -> dict[str, JsonValue]:
    targets = [
        _validate_precondition(vault, operation)
        for operation in bundle.operations
    ]
    return {
        "transaction_id": bundle.transaction_id,
        "operation_count": len(bundle.operations),
        "metadata": bundle.metadata,
        "targets": targets,
    }


def inspect_bundle(vault: Path, bundle_path: str | Path) -> dict[str, JsonValue]:
    bundle = load_bundle(bundle_path)
    return inspect_operation_bundle(vault, bundle)


def inspect_operation_bundle(
    vault: Path,
    bundle: OperationBundle,
) -> dict[str, JsonValue]:
    vault = vault.resolve()
    return _inspect(vault, bundle)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _publish_bytes(path: Path, content: bytes) -> None:
    """Publish complete content only if the target path is still absent."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise ConflictError(
                "Target appeared while the transaction was applying.",
                details={"target": str(path)},
            ) from error
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: dict[str, JsonValue]) -> None:
    content = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, content)


def _missing_parents(vault: Path, target: Path) -> list[str]:
    missing: list[Path] = []
    current = target.parent
    while current != vault and not current.exists():
        missing.append(current)
        current = current.parent
    return [path.relative_to(vault).as_posix() for path in reversed(missing)]


def _transaction_directory(vault: Path, transaction_id: str) -> Path:
    validate_transaction_id(transaction_id)
    engine_root = vault / TRANSACTION_ROOT.parts[0]
    transaction_root = vault / TRANSACTION_ROOT
    for path in (engine_root, transaction_root):
        if path.is_symlink():
            raise PreconditionError(
                "Engine transaction state may not use symbolic links.",
                details={"path": str(path)},
            )
        if path.exists() and not path.is_dir():
            raise PreconditionError(
                "Engine transaction state path is not a directory.",
                details={"path": str(path)},
            )
    transaction_directory = transaction_root / transaction_id
    if transaction_directory.is_symlink():
        raise PreconditionError(
            "Transaction journal directory may not be a symbolic link.",
            details={"path": str(transaction_directory)},
        )
    return transaction_directory


def _prepare_journal(
    vault: Path,
    bundle: OperationBundle,
    bundle_source: str,
    transaction_directory: Path,
) -> dict[str, JsonValue]:
    entries: list[JsonValue] = []
    for index, operation in enumerate(bundle.operations):
        target = _target_path(vault, operation.target)
        existed = target.exists()
        entries.append(
            {
                "index": index,
                "target": operation.target,
                "intent": operation.intent,
                "existed": existed,
                "backup": f"backups/{index:04d}.bin" if existed else None,
                "created_parents": _missing_parents(vault, target),
                "post_sha256": sha256_bytes(operation.content.encode("utf-8")),
                "state": "pending",
            }
        )
    return {
        "version": 1,
        "transaction_id": bundle.transaction_id,
        "bundle_source": bundle_source,
        "status": "applying",
        "operations": entries,
    }


def _journal_entries(journal: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    operations = journal.get("operations")
    if not isinstance(operations, list):
        raise PreconditionError("Transaction journal operations are invalid.")
    entries: list[dict[str, JsonValue]] = []
    for index, entry in enumerate(operations):
        if not isinstance(entry, dict):
            raise PreconditionError(
                "Transaction journal entry is invalid.",
                details={"operation_index": index},
            )
        entry_index = entry.get("index")
        target = entry.get("target")
        state = entry.get("state")
        existed = entry.get("existed")
        backup = entry.get("backup")
        parents = entry.get("created_parents")
        post_sha = entry.get("post_sha256")
        if (
            not isinstance(entry_index, int)
            or not isinstance(target, str)
            or not isinstance(existed, bool)
            or (backup is not None and not isinstance(backup, str))
            or not isinstance(parents, list)
            or not all(isinstance(parent, str) for parent in parents)
            or not isinstance(post_sha, str)
            or state
            not in {
                "pending",
                "claiming",
                "publishing",
                "applied",
                "rolled_back",
            }
        ):
            raise PreconditionError(
                "Transaction journal entry fields are invalid.",
                details={"operation_index": index},
            )
        entries.append(entry)
    return entries


def _backup_path(
    transaction_directory: Path,
    entry: dict[str, JsonValue],
) -> Path:
    backup_value = entry.get("backup")
    if not isinstance(backup_value, str):
        raise PreconditionError("Transaction backup path is invalid.")
    backup = (transaction_directory / backup_value).resolve()
    if not backup.is_relative_to(transaction_directory.resolve()):
        raise PreconditionError("Transaction backup escapes its journal.")
    return backup


def _restore_without_overwrite(target: Path, content: bytes) -> None:
    try:
        _publish_bytes(target, content)
    except ConflictError as error:
        raise RecoveryRequiredError(
            "Target changed while rollback was restoring content.",
            details={"target": str(target)},
        ) from error


def _rollback_entry(
    vault: Path,
    transaction_directory: Path,
    entry: dict[str, JsonValue],
) -> None:
    target_value = entry["target"]
    if not isinstance(target_value, str):
        raise PreconditionError("Transaction journal target is invalid.")
    target = _target_path(vault, target_value)
    state = entry["state"]
    existed = entry["existed"] is True

    if state == "pending":
        entry["state"] = "rolled_back"
        return

    if state == "claiming" and existed:
        backup = _backup_path(transaction_directory, entry)
        if not backup.exists():
            if not target.is_file():
                raise RecoveryRequiredError(
                    "Claimed target and backup are both missing.",
                    details={"target": target_value},
                )
            entry["state"] = "rolled_back"
            return
        if target.exists():
            raise ConflictError(
                "Target changed after it was claimed; recovery will not overwrite it.",
                details={"target": target_value},
            )
        _restore_without_overwrite(target, backup.read_bytes())
        entry["state"] = "rolled_back"
        return

    if state in {"publishing", "applied"}:
        rollback_directory = transaction_directory / "rollback"
        if rollback_directory.is_symlink():
            raise PreconditionError(
                "Transaction rollback state may not use symbolic links.",
                details={"path": str(rollback_directory)},
            )
        rollback_directory.mkdir(exist_ok=True)
        if not rollback_directory.is_dir():
            raise PreconditionError(
                "Transaction rollback state path is not a directory.",
                details={"path": str(rollback_directory)},
            )
        capture = rollback_directory / f"{entry['index']:04d}.bin"
        if capture.exists():
            captured = capture.read_bytes()
            if sha256_bytes(captured) != entry["post_sha256"]:
                if not target.exists():
                    _restore_without_overwrite(target, captured)
                raise ConflictError(
                    "Rollback captured content changed; recovery preserved it.",
                    details={"target": target_value},
                )
            if existed and target.is_file():
                backup = _backup_path(transaction_directory, entry)
                if backup.is_file() and sha256_file(target) == sha256_file(backup):
                    entry["state"] = "rolled_back"
                    return
            if not existed and not target.exists():
                entry["state"] = "rolled_back"
                return
            if target.exists():
                raise ConflictError(
                    "Target changed while rollback was incomplete.",
                    details={"target": target_value},
                )
        else:
            if not target.exists():
                if state == "publishing":
                    if existed:
                        backup = _backup_path(transaction_directory, entry)
                        _restore_without_overwrite(target, backup.read_bytes())
                    entry["state"] = "rolled_back"
                    return
                raise ConflictError(
                    "Applied target is missing; recovery will not recreate it blindly.",
                    details={"target": target_value},
                )
            if not target.is_file():
                raise ConflictError(
                    "Applied target is no longer a file.",
                    details={"target": target_value},
                )
            os.replace(target, capture)

        captured = capture.read_bytes()
        if sha256_bytes(captured) != entry["post_sha256"]:
            _restore_without_overwrite(target, captured)
            raise ConflictError(
                "Applied target changed after the transaction; recovery preserved it.",
                details={"target": target_value},
            )
        if existed:
            backup = _backup_path(transaction_directory, entry)
            if not backup.is_file():
                _restore_without_overwrite(target, captured)
                raise RecoveryRequiredError(
                    "Required transaction backup is missing.",
                    details={"target": target_value, "backup": str(backup)},
                )
            _restore_without_overwrite(target, backup.read_bytes())
        entry["state"] = "rolled_back"
        return

    if state != "rolled_back":
        raise PreconditionError(
            "Transaction journal state cannot be rolled back.",
            details={"target": target_value, "state": state},
        )


def _rollback(
    vault: Path,
    transaction_directory: Path,
    journal: dict[str, JsonValue],
    *,
    before_rollback: RollbackHook | None = None,
) -> None:
    entries = _journal_entries(journal)
    for entry in reversed(entries):
        if entry["state"] == "rolled_back":
            continue
        index = entry["index"]
        if not isinstance(index, int):
            raise PreconditionError("Transaction journal index is invalid.")
        if before_rollback is not None:
            before_rollback(index, entry)
        _rollback_entry(vault, transaction_directory, entry)
        _write_json(transaction_directory / JOURNAL_NAME, journal)

    for entry in reversed(entries):
        parents = entry.get("created_parents", [])
        if not isinstance(parents, list):
            raise PreconditionError("Transaction created_parents is invalid.")
        for parent_value in reversed(parents):
            if not isinstance(parent_value, str):
                raise PreconditionError("Transaction parent path is invalid.")
            parent = _target_path(vault, parent_value)
            try:
                parent.rmdir()
            except FileNotFoundError:
                continue
            except OSError:
                if parent.exists() and any(parent.iterdir()):
                    continue
                raise


def apply_operation_bundle(
    vault: Path,
    bundle: OperationBundle,
    *,
    bundle_source: str = "generated",
    locked_precondition: LockedPrecondition | None = None,
    before_apply: ApplyHook | None = None,
    before_rollback: RollbackHook | None = None,
) -> dict[str, JsonValue]:
    vault = vault.resolve()
    with VaultLock(vault):
        if locked_precondition is not None:
            locked_precondition()
        inspection = _inspect(vault, bundle)
        transaction_directory = _transaction_directory(
            vault, bundle.transaction_id
        )
        engine_root = vault / TRANSACTION_ROOT.parts[0]
        transaction_root = vault / TRANSACTION_ROOT
        engine_root.mkdir(exist_ok=True)
        if engine_root.is_symlink():
            raise PreconditionError(
                "Engine transaction state may not use symbolic links.",
                details={"path": str(engine_root)},
            )
        transaction_root.mkdir(exist_ok=True)
        if transaction_root.is_symlink():
            raise PreconditionError(
                "Engine transaction state may not use symbolic links.",
                details={"path": str(transaction_root)},
            )
        try:
            transaction_directory.mkdir()
        except FileExistsError as error:
            raise PreconditionError(
                "Transaction id already has a journal.",
                details={"transaction_id": bundle.transaction_id},
            ) from error
        (transaction_directory / "backups").mkdir()

        journal = _prepare_journal(
            vault,
            bundle,
            bundle_source,
            transaction_directory,
        )
        journal_path = transaction_directory / JOURNAL_NAME
        _write_json(journal_path, journal)
        entries = _journal_entries(journal)

        try:
            for index, operation in enumerate(bundle.operations):
                _validate_precondition(vault, operation)
                target = _target_path(vault, operation.target)
                entry = entries[index]
                for parent_value in entry["created_parents"]:
                    if not isinstance(parent_value, str):
                        raise PreconditionError(
                            "Transaction parent path is invalid."
                        )
                    _target_path(vault, parent_value).mkdir(exist_ok=True)

                if operation.intent == "replace":
                    backup = _backup_path(transaction_directory, entry)
                    entry["state"] = "claiming"
                    _write_json(journal_path, journal)
                    try:
                        os.replace(target, backup)
                    except FileNotFoundError as error:
                        raise ConflictError(
                            "Replace target disappeared while applying.",
                            details={"target": operation.target},
                        ) from error
                    original_content = backup.read_bytes()
                    _atomic_write_bytes(backup, original_content)
                    actual_sha = sha256_bytes(original_content)
                    if actual_sha != operation.expected_sha256:
                        raise ConflictError(
                            "Target changed while the transaction was claiming it.",
                            details={
                                "target": operation.target,
                                "expected_sha256": operation.expected_sha256,
                                "actual_sha256": actual_sha,
                            },
                        )

                entry["state"] = "publishing"
                _write_json(journal_path, journal)
                if before_apply is not None:
                    before_apply(index, operation)
                _publish_bytes(target, operation.content.encode("utf-8"))
                entry["state"] = "applied"
                _write_json(journal_path, journal)
        except Exception as error:
            journal["failure"] = f"{type(error).__name__}: {error}"
            try:
                _rollback(
                    vault,
                    transaction_directory,
                    journal,
                    before_rollback=before_rollback,
                )
                journal["status"] = "rolled_back"
                _write_json(journal_path, journal)
            except Exception as rollback_error:
                journal["status"] = "needs_recovery"
                journal["rollback_failure"] = (
                    f"{type(rollback_error).__name__}: {rollback_error}"
                )
                try:
                    _write_json(journal_path, journal)
                except OSError:
                    pass
                raise RecoveryRequiredError(
                    "Transaction failed and automatic rollback was incomplete.",
                    details={
                        "transaction_id": bundle.transaction_id,
                        "journal": str(journal_path.relative_to(vault)),
                        "cause": str(error),
                        "rollback_cause": str(rollback_error),
                    },
                ) from rollback_error
            if isinstance(error, EngineError):
                raise
            raise TransactionError(
                "Transaction failed and was rolled back.",
                details={
                    "transaction_id": bundle.transaction_id,
                    "journal": str(journal_path.relative_to(vault)),
                    "cause": str(error),
                },
            ) from error

        journal["status"] = "committed"
        try:
            _write_json(journal_path, journal)
        except OSError as error:
            raise RecoveryRequiredError(
                "Files were applied but commit status could not be recorded.",
                details={
                    "transaction_id": bundle.transaction_id,
                    "journal": str(journal_path.relative_to(vault)),
                    "cause": str(error),
                },
            ) from error
        return {
            **inspection,
            "status": "committed",
            "journal": journal_path.relative_to(vault).as_posix(),
        }


def apply_bundle(
    vault: Path,
    bundle_path: str | Path,
    *,
    before_apply: ApplyHook | None = None,
    before_rollback: RollbackHook | None = None,
) -> dict[str, JsonValue]:
    bundle = load_bundle(bundle_path)
    return apply_operation_bundle(
        vault,
        bundle,
        bundle_source=str(Path(bundle_path).expanduser().resolve()),
        before_apply=before_apply,
        before_rollback=before_rollback,
    )


def _load_journal(path: Path, transaction_id: str) -> dict[str, JsonValue]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PreconditionError(
            "Transaction journal does not exist.",
            details={"transaction_id": transaction_id},
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PreconditionError(
            "Transaction journal could not be parsed.",
            details={"path": str(path), "reason": str(error)},
        ) from error
    if not isinstance(value, dict):
        raise PreconditionError("Transaction journal must be a JSON object.")
    if value.get("version") != 1 or value.get("transaction_id") != transaction_id:
        raise PreconditionError("Transaction journal identity is invalid.")
    _journal_entries(value)
    return value


def recover_transaction(
    vault: Path,
    transaction_id: str,
) -> dict[str, JsonValue]:
    vault = vault.resolve()
    transaction_directory = _transaction_directory(vault, transaction_id)
    journal_path = transaction_directory / JOURNAL_NAME
    with VaultLock(vault):
        journal = _load_journal(journal_path, transaction_id)
        status = journal.get("status")
        if status == "committed":
            raise PreconditionError(
                "Committed transactions cannot be recovered.",
                details={"transaction_id": transaction_id},
            )
        if status == "rolled_back":
            return {
                "transaction_id": transaction_id,
                "status": "rolled_back",
                "journal": journal_path.relative_to(vault).as_posix(),
            }
        if status not in {"applying", "needs_recovery"}:
            raise PreconditionError(
                "Transaction journal status is not recoverable.",
                details={"transaction_id": transaction_id, "status": status},
            )

        try:
            _rollback(vault, transaction_directory, journal)
        except EngineError:
            raise
        except Exception as error:
            raise RecoveryRequiredError(
                "Transaction recovery failed.",
                details={
                    "transaction_id": transaction_id,
                    "journal": str(journal_path.relative_to(vault)),
                    "cause": str(error),
                },
            ) from error
        journal["status"] = "rolled_back"
        _write_json(journal_path, journal)
        return {
            "transaction_id": transaction_id,
            "status": "rolled_back",
            "journal": journal_path.relative_to(vault).as_posix(),
        }
