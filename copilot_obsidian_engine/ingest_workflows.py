from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .errors import ConflictError, PreconditionError
from .models import JsonValue
from .transaction import (
    FileIntent,
    OperationBundle,
    apply_operation_bundle,
    build_bundle,
    bundle_to_dict,
    inspect_operation_bundle,
)
from .vault import resolve_vault_target

WIKILINK_PATTERN = re.compile(r"\[\[([^\]\n|#]+)")
EXTENSION_PATTERN = re.compile(r"^\.[a-z0-9]{1,10}$")
MIME_BY_EXTENSION = {
    ".csv": "text/csv",
    ".html": "text/html",
    ".json": "application/json",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}
ApplyHook = Callable[[int, FileIntent], None]


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    path: str
    sha256: str
    size: int
    mime: str
    extension: str
    captured_at: str
    modified_ns: int
    raw_path: str
    text: str

    def metadata(self) -> dict[str, JsonValue]:
        return {
            "source_id": self.source_id,
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "mime": self.mime,
            "extension": self.extension,
            "captured_at": self.captured_at,
            "modified_ns": self.modified_ns,
            "raw_path": self.raw_path,
        }


@dataclass(frozen=True)
class CandidateNote:
    title: str
    path: str
    content: str
    links: tuple[str, ...]
    tags: tuple[str, ...]
    provenance_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "title": self.title,
            "path": self.path,
            "content": self.content,
            "links": list(self.links),
            "tags": list(self.tags),
            "provenance_refs": list(self.provenance_refs),
        }


@dataclass(frozen=True)
class IngestPlan:
    bundle: OperationBundle
    approval_hash: str
    sources: tuple[SourceRecord, ...]
    candidates: tuple[CandidateNote, ...]
    skipped_targets: tuple[str, ...]

    def to_dict(self, *, requested_output: str | None = None) -> dict[str, JsonValue]:
        operations = bundle_to_dict(self.bundle)["operations"]
        return {
            "workflow": "wiki-ingest",
            "approval_hash": self.approval_hash,
            "apply_required": True,
            "sources": [source.metadata() for source in self.sources],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "bundle_summary": {
                "transaction_id": self.bundle.transaction_id,
                "operation_count": len(self.bundle.operations),
                "operations": [
                    {
                        "target": operation["target"],
                        "intent": operation["intent"],
                        "expected_sha256": operation["expected_sha256"],
                    }
                    for operation in operations
                ],
            },
            "skipped_targets": list(self.skipped_targets),
            "requested_output": requested_output,
            "output_written": False,
        }


def _source_timestamp(modified_ns: int) -> str:
    value = datetime.fromtimestamp(
        modified_ns / 1_000_000_000,
        tz=timezone.utc,
    )
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_extension(path: Path) -> str:
    extension = path.suffix.lower()
    return extension if EXTENSION_PATTERN.fullmatch(extension) else ""


def _source_symlink(value: Path) -> Path | None:
    if value.is_absolute():
        current = Path(value.anchor)
        parts = value.parts[1:]
    else:
        current = Path.cwd()
        parts = value.parts
    for part in parts:
        current /= part
        if current.is_symlink():
            return current
    return None


def _snapshot_source(value: str | Path) -> SourceRecord:
    source = Path(value).expanduser()
    symlink = _source_symlink(source)
    if symlink is not None:
        raise PreconditionError(
            "Ingest source may not traverse symbolic links.",
            details={"source": str(source), "component": str(symlink)},
        )
    try:
        resolved = source.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise PreconditionError(
            "Ingest source does not exist or cannot be resolved.",
            details={"source": str(source), "reason": str(error)},
        ) from error
    if not resolved.is_file():
        raise PreconditionError(
            "Ingest source must be a regular file.",
            details={"source": str(resolved)},
        )

    try:
        before = resolved.stat()
        content = resolved.read_bytes()
        after = resolved.stat()
    except (OSError, PermissionError) as error:
        raise PreconditionError(
            "Ingest source is unreadable.",
            details={"source": str(resolved), "reason": str(error)},
        ) from error
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise PreconditionError(
            "Ingest source changed while it was being read.",
            details={"source": str(resolved)},
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreconditionError(
            "Ingest source must be UTF-8 text.",
            details={"source": str(resolved)},
        ) from error
    if "\x00" in text:
        raise PreconditionError(
            "Ingest source contains unsupported NUL bytes.",
            details={"source": str(resolved)},
        )

    content_hash = hashlib.sha256(content).hexdigest()
    source_id = hashlib.sha256(
        f"{resolved}\0{content_hash}".encode("utf-8")
    ).hexdigest()
    extension = _safe_extension(resolved)
    mime = MIME_BY_EXTENSION.get(extension, "text/plain")
    return SourceRecord(
        source_id=source_id,
        path=str(resolved),
        sha256=content_hash,
        size=len(content),
        mime=mime,
        extension=extension,
        captured_at=_source_timestamp(after.st_mtime_ns),
        modified_ns=after.st_mtime_ns,
        raw_path=f".copilot-obsidian/raw/{content_hash}{extension}",
        text=text,
    )


def _title(record: SourceRecord) -> str:
    for line in record.text.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return Path(record.path).stem.replace("_", " ").replace("-", " ").strip().title()


def _slug(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode(
        "ascii",
        "ignore",
    ).decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    return slug or "untitled"


def _candidate(record: SourceRecord) -> tuple[CandidateNote, str]:
    title = _title(record)
    source_tag = record.extension.removeprefix(".") or "text"
    tags = ("ingested", source_tag)
    links = tuple(
        sorted(
            {
                link.strip()
                for link in WIKILINK_PATTERN.findall(record.text)
                if link.strip()
            }
        )
    )
    ledger_path = (
        f".copilot-obsidian/provenance/{record.source_id}.json"
    )
    note_path = (
        f"Notes/Ingested/{_slug(title)}-"
        f"{record.sha256[:12]}-{record.source_id[:8]}.md"
    )
    frontmatter = [
        "---",
        "tags:",
        *(f"  - {tag}" for tag in tags),
        "provenance:",
        f'  - "{ledger_path}"',
        "---",
        "",
    ]
    body = record.text.rstrip()
    if not any(line.startswith("# ") for line in record.text.splitlines()):
        body = f"# {title}\n\n{body}"
    content = "\n".join(frontmatter) + body + "\n"
    return (
        CandidateNote(
            title=title,
            path=note_path,
            content=content,
            links=links,
            tags=tags,
            provenance_refs=(record.raw_path, ledger_path),
        ),
        ledger_path,
    )


def _ledger_content(
    record: SourceRecord,
    candidate: CandidateNote,
) -> str:
    payload = {
        "version": 1,
        **record.metadata(),
        "note_path": candidate.path,
    }
    return json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"


def _approval_hash(bundle: OperationBundle) -> str:
    canonical = json.dumps(
        bundle_to_dict(bundle),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _transaction_id(vault: Path, operations: list[FileIntent]) -> str:
    seed = build_bundle(
        "wiki-ingest-plan",
        operations,
        metadata={"workflow": "wiki-ingest", "schema_version": 1},
        allow_engine_targets=True,
    )
    base = f"wiki-ingest-{_approval_hash(seed)[:12]}"
    root = vault / ".copilot-obsidian" / "transactions"
    candidate = base
    attempt = 1
    while (root / candidate).exists() or (root / candidate).is_symlink():
        attempt += 1
        candidate = f"{base}-{attempt}"
    return candidate


def _add_create(
    vault: Path,
    operations: dict[str, FileIntent],
    skipped: list[str],
    *,
    target: str,
    content: str,
    allow_identical: bool,
) -> None:
    existing_operation = operations.get(target)
    if existing_operation is not None:
        if existing_operation.content == content:
            return
        raise PreconditionError(
            "Ingest sources produced conflicting duplicate targets.",
            details={"target": target},
        )

    path = resolve_vault_target(vault, target)
    if path.exists():
        if not path.is_file():
            raise PreconditionError(
                "Ingest target exists but is not a file.",
                details={"target": target},
            )
        if allow_identical:
            try:
                is_identical = path.read_bytes() == content.encode("utf-8")
            except OSError as error:
                raise PreconditionError(
                    "Existing ingest target is unreadable.",
                    details={"target": target, "reason": str(error)},
                ) from error
            if is_identical:
                skipped.append(target)
                return
        raise ConflictError(
            "Ingest target already exists with different content.",
            details={"target": target},
        )
    operations[target] = FileIntent(
        target=target,
        intent="create",
        content=content,
        expected_sha256=None,
    )


def plan_ingest(
    vault: Path,
    source_values: list[str] | tuple[str, ...],
) -> IngestPlan:
    vault = vault.resolve()
    if load_config(vault).marker_path is None:
        raise PreconditionError(
            "Vault is not initialized; run wiki init or wiki adopt first."
        )
    if not source_values:
        raise PreconditionError("At least one ingest source is required.")

    records = tuple(
        sorted(
            (_snapshot_source(value) for value in source_values),
            key=lambda record: record.path,
        )
    )
    paths = [record.path for record in records]
    if len(paths) != len(set(paths)):
        raise PreconditionError(
            "The same ingest source was provided more than once."
        )
    engine_state = vault / ".copilot-obsidian"
    for record in records:
        if Path(record.path).is_relative_to(engine_state):
            raise PreconditionError(
                "Ingest sources may not come from engine state storage.",
                details={"source": record.path},
            )

    operations: dict[str, FileIntent] = {}
    skipped: list[str] = []
    candidates: list[CandidateNote] = []
    for record in records:
        candidate, ledger_path = _candidate(record)
        candidates.append(candidate)
        _add_create(
            vault,
            operations,
            skipped,
            target=record.raw_path,
            content=record.text,
            allow_identical=True,
        )
        _add_create(
            vault,
            operations,
            skipped,
            target=ledger_path,
            content=_ledger_content(record, candidate),
            allow_identical=True,
        )
        _add_create(
            vault,
            operations,
            skipped,
            target=candidate.path,
            content=candidate.content,
            allow_identical=False,
        )

    operation_values = list(operations.values())
    bundle = build_bundle(
        _transaction_id(vault, operation_values),
        operation_values,
        metadata={
            "workflow": "wiki-ingest",
            "schema_version": 1,
            "source_sha256": [record.sha256 for record in records],
        },
        allow_engine_targets=True,
    )
    inspect_operation_bundle(vault, bundle)
    return IngestPlan(
        bundle=bundle,
        approval_hash=_approval_hash(bundle),
        sources=records,
        candidates=tuple(candidates),
        skipped_targets=tuple(sorted(skipped)),
    )


def _verify_sources(records: tuple[SourceRecord, ...]) -> None:
    for expected in records:
        current = _snapshot_source(expected.path)
        if current != expected:
            raise PreconditionError(
                "Ingest source changed after preview.",
                details={"source": expected.path},
            )


def apply_ingest(
    vault: Path,
    source_values: list[str] | tuple[str, ...],
    approved_hash: str,
    *,
    before_apply: ApplyHook | None = None,
) -> dict[str, JsonValue]:
    plan = plan_ingest(vault, source_values)
    if approved_hash != plan.approval_hash:
        raise PreconditionError(
            "Approved hash does not match the current ingest plan.",
            details={
                "approved_hash": approved_hash,
                "current_hash": plan.approval_hash,
            },
        )
    result = apply_operation_bundle(
        vault,
        plan.bundle,
        bundle_source="generated:wiki-ingest",
        locked_precondition=lambda: _verify_sources(plan.sources),
        before_apply=before_apply,
    )
    return {
        "workflow": "wiki-ingest",
        "approval_hash": plan.approval_hash,
        "sources": [source.metadata() for source in plan.sources],
        "candidates": [candidate.to_dict() for candidate in plan.candidates],
        "transaction": result,
    }
