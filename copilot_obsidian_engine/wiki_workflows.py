from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .config import CONFIG_MARKER, load_config
from .errors import ConfigurationError, PreconditionError
from .models import JsonValue
from .transaction import (
    FileIntent,
    OperationBundle,
    apply_operation_bundle,
    build_bundle,
    bundle_to_dict,
    inspect_operation_bundle,
)
from .transaction.lock import LOCK_FILE

MARKER_VALUES: dict[str, JsonValue] = {
    "version": 1,
    "layout": {
        "home": "Home.md",
        "inbox": "Inbox",
        "notes": "Notes",
    },
}
MARKER_CONTENT = (
    json.dumps(MARKER_VALUES, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
)
SCAFFOLD_FILES = (
    (
        "Home.md",
        "# Home\n\nWelcome to your local-first wiki.\n",
    ),
    (
        "Inbox/README.md",
        "# Inbox\n\nCapture new material here before organizing it.\n",
    ),
    (
        "Notes/README.md",
        "# Notes\n\nStore durable wiki notes here.\n",
    ),
)


@dataclass(frozen=True)
class WikiPlan:
    workflow: str
    bundle: OperationBundle
    approval_hash: str
    skipped_targets: tuple[str, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "workflow": self.workflow,
            "approval_hash": self.approval_hash,
            "apply_required": True,
            "bundle": bundle_to_dict(self.bundle),
            "skipped_targets": list(self.skipped_targets),
        }


def _approval_hash(bundle: OperationBundle) -> str:
    canonical = json.dumps(
        bundle_to_dict(bundle),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _transaction_id(
    vault: Path,
    workflow: str,
    operations: list[FileIntent],
    metadata: dict[str, JsonValue],
) -> str:
    seed_bundle = build_bundle(
        f"wiki-{workflow}-plan",
        operations,
        metadata=metadata,
    )
    base = f"wiki-{workflow}-{_approval_hash(seed_bundle)[:12]}"
    transaction_root = vault / ".copilot-obsidian" / "transactions"
    candidate = base
    attempt = 1
    while (transaction_root / candidate).exists() or (
        transaction_root / candidate
    ).is_symlink():
        attempt += 1
        candidate = f"{base}-{attempt}"
    return candidate


def _marker_status(vault: Path) -> dict[str, JsonValue]:
    marker = vault / CONFIG_MARKER
    if not marker.exists() and not marker.is_symlink():
        return {
            "path": str(marker),
            "exists": False,
            "valid": None,
            "status": "missing",
        }
    try:
        config = load_config(vault)
    except ConfigurationError as error:
        return {
            "path": str(marker),
            "exists": True,
            "valid": False,
            "status": "invalid",
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
            },
        }
    return {
        "path": config.marker_path,
        "exists": True,
        "valid": True,
        "status": "ready",
        "values": config.values,
    }


def _meaningful_entries(vault: Path) -> list[str]:
    ignored = {
        ".obsidian",
        ".DS_Store",
        ".copilot-obsidian",
        LOCK_FILE,
    }
    return sorted(
        path.name
        for path in vault.iterdir()
        if path.name not in ignored
    )


def doctor(vault: Path) -> dict[str, JsonValue]:
    vault = vault.resolve()
    marker = _marker_status(vault)
    engine_root = vault / ".copilot-obsidian"
    transaction_root = engine_root / "transactions"
    unsafe_state_paths = [
        str(path)
        for path in (engine_root, transaction_root)
        if path.is_symlink() or (path.exists() and not path.is_dir())
    ]
    lock_path = vault / LOCK_FILE
    transaction_ready = not unsafe_state_paths and not lock_path.exists()
    marker_ready = marker["status"] == "ready"
    entries = _meaningful_entries(vault)

    return {
        "ready": transaction_ready and marker_ready,
        "vault": {
            "path": str(vault),
            "resolved": True,
            "exists": True,
            "is_directory": True,
        },
        "config": marker,
        "transaction": {
            "ready": transaction_ready,
            "lock_present": lock_path.exists(),
            "state_paths_safe": not unsafe_state_paths,
            "unsafe_state_paths": unsafe_state_paths,
        },
        "capabilities": {
            "local_only": True,
            "sha256_preconditions": True,
            "atomic_publication": True,
            "backup_journal": True,
            "rollback": True,
            "recovery": True,
        },
        "recommended_action": (
            "none"
            if marker["status"] == "ready"
            else "repair-config"
            if marker["status"] == "invalid"
            else "adopt"
            if entries
            else "init"
        ),
    }


def _ensure_unconfigured(vault: Path) -> None:
    marker = vault / CONFIG_MARKER
    if marker.exists() or marker.is_symlink():
        raise PreconditionError(
            "Vault already has a Copilot Obsidian marker.",
            details={"path": str(marker)},
        )


def _ensure_transaction_state_safe(vault: Path) -> None:
    for path in (
        vault / ".copilot-obsidian",
        vault / ".copilot-obsidian" / "transactions",
    ):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise PreconditionError(
                "Wiki planning refuses unsafe transaction state paths.",
                details={"path": str(path)},
            )


def _ensure_init_empty(vault: Path) -> None:
    entries = _meaningful_entries(vault)
    if entries:
        raise PreconditionError(
            "Wiki init requires an empty vault; use wiki adopt for existing content.",
            details={"entries": entries},
        )


def plan_wiki(vault: Path, workflow: str) -> WikiPlan:
    vault = vault.resolve()
    if workflow not in {"init", "adopt"}:
        raise PreconditionError(
            "Wiki workflow must be 'init' or 'adopt'.",
            details={"workflow": workflow},
        )
    _ensure_unconfigured(vault)
    _ensure_transaction_state_safe(vault)

    if workflow == "init":
        _ensure_init_empty(vault)

    operations = [
        FileIntent(
            target=CONFIG_MARKER,
            intent="create",
            content=MARKER_CONTENT,
            expected_sha256=None,
        )
    ]
    skipped: list[str] = []
    for target, content in SCAFFOLD_FILES:
        candidate = vault / target
        if candidate.is_symlink():
            raise PreconditionError(
                "Wiki scaffold target may not be a symbolic link.",
                details={"target": target},
            )
        if candidate.exists():
            skipped.append(target)
            continue
        operations.append(
            FileIntent(
                target=target,
                intent="create",
                content=content,
                expected_sha256=None,
            )
        )

    metadata: dict[str, JsonValue] = {
        "workflow": f"wiki-{workflow}",
        "schema_version": 1,
    }
    bundle = build_bundle(
        _transaction_id(vault, workflow, operations, metadata),
        operations,
        metadata=metadata,
    )
    inspect_operation_bundle(vault, bundle)
    return WikiPlan(
        workflow=workflow,
        bundle=bundle,
        approval_hash=_approval_hash(bundle),
        skipped_targets=tuple(skipped),
    )


def apply_wiki(
    vault: Path,
    workflow: str,
    approved_hash: str,
) -> dict[str, JsonValue]:
    plan = plan_wiki(vault, workflow)
    if approved_hash != plan.approval_hash:
        raise PreconditionError(
            "Approved hash does not match the current wiki plan.",
            details={
                "workflow": workflow,
                "approved_hash": approved_hash,
                "current_hash": plan.approval_hash,
            },
        )
    result = apply_operation_bundle(
        vault,
        plan.bundle,
        bundle_source=f"generated:wiki-{workflow}",
        locked_precondition=(
            (lambda: _ensure_init_empty(vault))
            if workflow == "init"
            else None
        ),
    )
    return {
        "workflow": workflow,
        "approval_hash": plan.approval_hash,
        "transaction": result,
    }
