from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..errors import PreconditionError
from ..models import JsonValue

BUNDLE_VERSION = 1
TRANSACTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
ENGINE_TARGET = ".copilot-obsidian"
ALWAYS_RESERVED_TARGETS = {".copilot-obsidian-lock"}


@dataclass(frozen=True)
class FileIntent:
    target: str
    intent: str
    content: str
    expected_sha256: str | None


@dataclass(frozen=True)
class OperationBundle:
    transaction_id: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    operations: tuple[FileIntent, ...] = ()
    version: int = BUNDLE_VERSION


def validate_transaction_id(value: object) -> str:
    if not isinstance(value, str) or not TRANSACTION_ID_PATTERN.fullmatch(value):
        raise PreconditionError(
            "Transaction id must use 1-128 letters, numbers, dots, underscores, or hyphens.",
            details={"transaction_id": value},
        )
    return value


def _normalize_target(
    value: object,
    *,
    allow_engine_targets: bool,
) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PreconditionError(
            "Operation target must be a non-empty vault-relative POSIX path.",
            details={"target": value},
        )
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise PreconditionError(
            "Operation target must stay within the vault.",
            details={"target": value},
        )
    normalized = path.as_posix()
    targets_transaction_state = (
        len(path.parts) > 1
        and path.parts[0] == ENGINE_TARGET
        and path.parts[1] == "transactions"
    )
    if (
        path.parts[0] in ALWAYS_RESERVED_TARGETS
        or targets_transaction_state
        or (path.parts[0] == ENGINE_TARGET and not allow_engine_targets)
    ):
        raise PreconditionError(
            "Operation target uses a reserved engine path.",
            details={"target": normalized},
        )
    return normalized


def _parse_intent(
    value: object,
    index: int,
    *,
    allow_engine_targets: bool,
) -> FileIntent:
    if not isinstance(value, dict):
        raise PreconditionError(
            "Each operation must be a JSON object.",
            details={"operation_index": index},
        )

    target = _normalize_target(
        value.get("target"),
        allow_engine_targets=allow_engine_targets,
    )
    intent = value.get("intent")
    if intent not in {"create", "replace"}:
        raise PreconditionError(
            "Operation intent must be 'create' or 'replace'.",
            details={"operation_index": index, "intent": intent},
        )
    content = value.get("content")
    if not isinstance(content, str):
        raise PreconditionError(
            "Operation content must be a string.",
            details={"operation_index": index, "target": target},
        )

    expected = value.get("expected_sha256")
    if intent == "create":
        if expected is not None:
            raise PreconditionError(
                "Create operations must use a null expected_sha256.",
                details={"operation_index": index, "target": target},
            )
    elif not isinstance(expected, str) or not SHA256_PATTERN.fullmatch(expected):
        raise PreconditionError(
            "Replace operations require a 64-character expected_sha256.",
            details={"operation_index": index, "target": target},
        )

    return FileIntent(
        target=target,
        intent=intent,
        content=content,
        expected_sha256=expected.lower() if isinstance(expected, str) else None,
    )


def parse_bundle(
    value: object,
    *,
    allow_engine_targets: bool = False,
) -> OperationBundle:
    if not isinstance(value, dict):
        raise PreconditionError("Operation bundle must be a JSON object.")
    if value.get("version") != BUNDLE_VERSION:
        raise PreconditionError(
            "Unsupported operation bundle version.",
            details={"version": value.get("version"), "supported": BUNDLE_VERSION},
        )

    transaction_id = validate_transaction_id(value.get("transaction_id"))
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        raise PreconditionError("Operation bundle metadata must be a JSON object.")
    operations_value = value.get("operations")
    if not isinstance(operations_value, list) or not operations_value:
        raise PreconditionError(
            "Operation bundle must contain at least one operation."
        )

    operations = tuple(
        _parse_intent(
            operation,
            index,
            allow_engine_targets=allow_engine_targets,
        )
        for index, operation in enumerate(operations_value)
    )
    targets = [operation.target for operation in operations]
    if len(targets) != len(set(targets)):
        raise PreconditionError("Operation bundle contains duplicate targets.")

    return OperationBundle(
        transaction_id=transaction_id,
        metadata=metadata,
        operations=operations,
    )


def load_bundle(path: str | Path) -> OperationBundle:
    bundle_path = Path(path).expanduser().resolve()
    try:
        value = json.loads(bundle_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PreconditionError(
            "Operation bundle does not exist.",
            details={"path": str(bundle_path)},
        ) from error
    except (OSError, UnicodeError) as error:
        raise PreconditionError(
            "Operation bundle could not be read.",
            details={"path": str(bundle_path), "reason": str(error)},
        ) from error
    except json.JSONDecodeError as error:
        raise PreconditionError(
            "Operation bundle is not valid JSON.",
            details={
                "path": str(bundle_path),
                "line": error.lineno,
                "column": error.colno,
            },
        ) from error
    return parse_bundle(value)


def build_bundle(
    transaction_id: str,
    operations: list[FileIntent] | tuple[FileIntent, ...],
    *,
    metadata: dict[str, JsonValue] | None = None,
    allow_engine_targets: bool = False,
) -> OperationBundle:
    """Build and validate a bundle for future wiki and save planners."""

    return parse_bundle(
        {
            "version": BUNDLE_VERSION,
            "transaction_id": transaction_id,
            "metadata": metadata or {},
            "operations": [
                {
                    "target": operation.target,
                    "intent": operation.intent,
                    "content": operation.content,
                    "expected_sha256": operation.expected_sha256,
                }
                for operation in operations
            ],
        },
        allow_engine_targets=allow_engine_targets,
    )


def bundle_to_dict(bundle: OperationBundle) -> dict[str, JsonValue]:
    return {
        "version": bundle.version,
        "transaction_id": bundle.transaction_id,
        "metadata": bundle.metadata,
        "operations": [
            {
                "target": operation.target,
                "intent": operation.intent,
                "content": operation.content,
                "expected_sha256": operation.expected_sha256,
            }
            for operation in bundle.operations
        ],
    }
