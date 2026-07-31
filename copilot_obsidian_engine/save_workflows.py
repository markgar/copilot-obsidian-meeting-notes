from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import load_config
from .errors import PreconditionError
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

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
TAG_PATTERN = re.compile(r"[^a-z0-9]+")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP_LINE = re.compile(
    r"^\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]|\d{1,2}:\d{2}(?::\d{2})?)"
)
SPEAKER_LINE = re.compile(r"^\s*[A-Za-z][A-Za-z0-9 .'-]{0,40}:\s+\S")
SPEAKER_SEGMENT = re.compile(
    r"(?:^|\s)(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?"
    r"[A-Za-z][A-Za-z0-9 .'-]{0,40}:\s+\S",
    re.MULTILINE,
)
TIMESTAMP_SEGMENT = re.compile(
    r"(?:^|\s)\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s+",
    re.MULTILINE,
)
MAX_REQUEST_BYTES = 65_536
MAX_BODY_CHARS = 12_000
MAX_BODY_WORDS = 2_000
ApplyHook = Callable[[int, FileIntent], None]


@dataclass(frozen=True)
class SourceReference:
    path: str
    sha256: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class SaveRequest:
    title: str
    summary: str
    body: str
    tags: tuple[str, ...]
    links: tuple[str, ...]
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class SavePlan:
    bundle: OperationBundle
    approval_hash: str
    target_path: str
    metadata_path: str
    content: str
    content_sha256: str
    source_refs: tuple[SourceReference, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "workflow": "save",
            "approval_hash": self.approval_hash,
            "apply_required": True,
            "target_path": self.target_path,
            "metadata_path": self.metadata_path,
            "content": self.content,
            "content_sha256": self.content_sha256,
            "source_refs": [
                source_ref.to_dict() for source_ref in self.source_refs
            ],
            "change_summary": {
                "creates": 2,
                "updates": 0,
                "overwrites": False,
            },
            "bundle_summary": {
                "transaction_id": self.bundle.transaction_id,
                "operations": [
                    {
                        "target": operation.target,
                        "intent": operation.intent,
                    }
                    for operation in self.bundle.operations
                ],
            },
        }


def _symlink_component(value: Path) -> Path | None:
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


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    if path.is_symlink():
        raise PreconditionError(
            f"{label} may not be a symbolic link.",
            details={"path": str(path)},
        )
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise PreconditionError(
                    f"{label} must be a regular file.",
                    details={"path": str(path)},
                )
            if before.st_size > MAX_REQUEST_BYTES and label == "Save request":
                raise PreconditionError(
                    "Save request is too large for a scoped insight.",
                    details={"path": str(path), "size": before.st_size},
                )
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read()
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PreconditionError(
            f"{label} is unreadable.",
            details={"path": str(path), "reason": str(error)},
        ) from error
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise PreconditionError(
            f"{label} changed while it was being read.",
            details={"path": str(path)},
        )
    return content


def _normalize_scalar(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise PreconditionError(f"Save request {field} must be a string.")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise PreconditionError(
            f"Save request {field} must be 1-{maximum} characters."
        )
    return normalized


def _normalize_body(value: object) -> str:
    if not isinstance(value, str):
        raise PreconditionError("Save request body must be a string.")
    body = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    tokens = TOKEN_PATTERN.findall(body)
    unique_tokens = {token.lower() for token in tokens}
    if len(body) > MAX_BODY_CHARS or len(tokens) > MAX_BODY_WORDS:
        raise PreconditionError(
            "Save request body must be a concise, substantive insight."
        )
    lines = [line for line in body.splitlines() if line.strip()]
    transcript_lines = sum(
        bool(TIMESTAMP_LINE.match(line) or SPEAKER_LINE.match(line))
        for line in lines
    )
    speaker_segments = len(SPEAKER_SEGMENT.findall(body))
    timestamp_segments = len(TIMESTAMP_SEGMENT.findall(body))
    transcript_density = speaker_segments / max(len(lines), 1)
    if (
        timestamp_segments >= 2
        or speaker_segments >= 3
        or (
            speaker_segments >= 2
            and len(tokens) >= 80
            and transcript_density >= 0.5
        )
        or (
            transcript_lines >= 3
            and transcript_lines / max(len(lines), 1) >= 0.3
        )
    ):
        raise PreconditionError(
            "Transcript-style bulk dumps are not accepted by save."
        )
    if len(tokens) < 8 or len(unique_tokens) < 5:
        raise PreconditionError(
            "Save request body must be a concise, substantive insight."
        )
    return body


def _normalize_tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 10:
        raise PreconditionError("Save request tags must be a list of at most 10.")
    normalized: set[str] = set()
    for tag in value:
        if not isinstance(tag, str):
            raise PreconditionError("Each save tag must be a string.")
        ascii_tag = unicodedata.normalize("NFKD", tag).encode(
            "ascii",
            "ignore",
        ).decode("ascii")
        result = TAG_PATTERN.sub("-", ascii_tag.lower()).strip("-")
        if not result:
            raise PreconditionError("Save tags must contain letters or numbers.")
        normalized.add(result)
    return tuple(sorted(normalized))


def _normalize_links(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 20:
        raise PreconditionError("Save request links must be a list of at most 20.")
    normalized: set[str] = set()
    for link in value:
        if not isinstance(link, str):
            raise PreconditionError("Each save link must be a string.")
        result = link.strip()
        if result.startswith("[[") and result.endswith("]]"):
            result = result[2:-2].strip()
        if (
            not result
            or len(result) > 200
            or "\n" in result
            or "[[" in result
            or "]]" in result
        ):
            raise PreconditionError("Save links contain an invalid value.")
        normalized.add(result)
    return tuple(sorted(normalized))


def _normalize_source_refs(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 20:
        raise PreconditionError(
            "Save request source_refs must be a list of at most 20."
        )
    normalized: set[str] = set()
    for source_ref in value:
        if not isinstance(source_ref, str) or "\\" in source_ref:
            raise PreconditionError("Each source ref must be a POSIX path.")
        if any(ord(character) < 32 for character in source_ref):
            raise PreconditionError(
                "Source refs may not contain control characters."
            )
        path = PurePosixPath(source_ref)
        result = path.as_posix()
        is_note = result.startswith("Notes/") and path.suffix.lower() == ".md"
        is_provenance = (
            result.startswith(".copilot-obsidian/provenance/")
            and path.suffix.lower() == ".json"
        )
        if (
            path.is_absolute()
            or ".." in path.parts
            or not (is_note or is_provenance)
        ):
            raise PreconditionError(
                "Source refs must target Notes markdown or provenance JSON."
            )
        normalized.add(result)
    return tuple(sorted(normalized))


def load_save_request(path_value: str | Path) -> SaveRequest:
    request_path = Path(path_value).expanduser()
    symlink = _symlink_component(request_path)
    if symlink is not None:
        raise PreconditionError(
            "Save request path may not traverse symbolic links.",
            details={"path": str(request_path), "component": str(symlink)},
        )
    try:
        request_path = request_path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise PreconditionError(
            "Save request does not exist or cannot be resolved.",
            details={"path": str(request_path), "reason": str(error)},
        ) from error
    content = _read_regular_bytes(request_path, label="Save request")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreconditionError(
            "Save request must be valid UTF-8 JSON.",
            details={"path": str(request_path), "reason": str(error)},
        ) from error
    if not isinstance(value, dict):
        raise PreconditionError("Save request must be a JSON object.")
    allowed = {
        "version",
        "title",
        "summary",
        "body",
        "tags",
        "links",
        "source_refs",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PreconditionError(
            "Save request contains unsupported fields.",
            details={"fields": unknown},
        )
    if value.get("version", 1) != 1:
        raise PreconditionError("Save request version must be 1.")
    summary = _normalize_scalar(value.get("summary"), "summary", 500)
    if len(TOKEN_PATTERN.findall(summary)) < 3:
        raise PreconditionError("Save request summary is too low-signal.")
    return SaveRequest(
        title=_normalize_scalar(value.get("title"), "title", 120),
        summary=summary,
        body=_normalize_body(value.get("body")),
        tags=_normalize_tags(value.get("tags")),
        links=_normalize_links(value.get("links")),
        source_refs=_normalize_source_refs(value.get("source_refs")),
    )


def _read_vault_regular_bytes(
    vault: Path,
    path_value: str,
    *,
    label: str,
) -> bytes:
    parts = PurePosixPath(path_value).parts
    directory_flags = os.O_RDONLY
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY
    file_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags |= getattr(os, "O_NONBLOCK", 0)
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(vault, directory_flags))
        for part in parts[:-1]:
            descriptors.append(
                os.open(
                    part,
                    directory_flags,
                    dir_fd=descriptors[-1],
                )
            )
        descriptor = os.open(
            parts[-1],
            file_flags,
            dir_fd=descriptors[-1],
        )
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PreconditionError(
                f"{label} must be a regular file.",
                details={"path": path_value},
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read()
        after = os.fstat(descriptor)
    except OSError as error:
        raise PreconditionError(
            f"{label} is unreadable or traverses an unsafe path.",
            details={"path": path_value, "reason": str(error)},
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise PreconditionError(
            f"{label} changed while it was being read.",
            details={"path": path_value},
        )
    return content


def _validate_source_ref(vault: Path, path_value: str) -> SourceReference:
    try:
        resolve_vault_target(vault, path_value)
    except (ValueError, OSError) as error:
        raise PreconditionError(
            "Save source ref path is invalid.",
            details={"path": path_value, "reason": str(error)},
        ) from error
    content = _read_vault_regular_bytes(
        vault,
        path_value,
        label="Save source ref",
    )
    if path_value.startswith(".copilot-obsidian/provenance/"):
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PreconditionError(
                "Save provenance source ref is not valid JSON.",
                details={"path": path_value},
            ) from error
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or not all(
                isinstance(value.get(field), str)
                for field in (
                    "source_id",
                    "sha256",
                    "note_path",
                    "raw_path",
                )
            )
            or not SHA256_PATTERN.fullmatch(value["source_id"])
            or not SHA256_PATTERN.fullmatch(value["sha256"])
            or PurePosixPath(path_value).stem != value["source_id"]
            or not value["note_path"].startswith("Notes/")
            or not value["raw_path"].startswith(
                f".copilot-obsidian/raw/{value['sha256']}"
            )
        ):
            raise PreconditionError(
                "Save provenance source ref has an invalid schema.",
                details={"path": path_value},
            )
    return SourceReference(
        path=path_value,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode(
        "ascii",
        "ignore",
    ).decode("ascii")
    slug = TAG_PATTERN.sub("-", ascii_value.lower()).strip("-")
    return slug or "insight"


def _frontmatter_list(name: str, values: tuple[str, ...]) -> list[str]:
    if not values:
        return [f"{name}: []"]
    return [f"{name}:", *(f"  - {json.dumps(value)}" for value in values)]


def _render_content(
    request: SaveRequest,
    source_refs: tuple[SourceReference, ...],
) -> str:
    lines = [
        "---",
        "type: saved-insight",
        f"title: {json.dumps(request.title)}",
        f"summary: {json.dumps(request.summary)}",
        *_frontmatter_list("tags", request.tags),
        *_frontmatter_list(
            "links",
            tuple(f"[[{link}]]" for link in request.links),
        ),
        *_frontmatter_list(
            "sources",
            tuple(source_ref.path for source_ref in source_refs),
        ),
        "---",
        "",
        f"# {request.title}",
        "",
        f"> {request.summary}",
        "",
        request.body,
        "",
    ]
    return "\n".join(lines)


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
        "save-plan",
        operations,
        metadata={"workflow": "save", "schema_version": 1},
        allow_engine_targets=True,
    )
    base = f"save-{_approval_hash(seed)[:12]}"
    root = vault / ".copilot-obsidian" / "transactions"
    candidate = base
    attempt = 1
    while (root / candidate).exists() or (root / candidate).is_symlink():
        attempt += 1
        candidate = f"{base}-{attempt}"
    return candidate


def plan_save(vault: Path, request_path: str | Path) -> SavePlan:
    vault = vault.resolve()
    if load_config(vault).marker_path is None:
        raise PreconditionError(
            "Vault is not initialized; run wiki init or wiki adopt first."
        )
    request = load_save_request(request_path)
    source_refs = tuple(
        _validate_source_ref(vault, path)
        for path in request.source_refs
    )
    content = _render_content(request, source_refs)
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    target_path = (
        f"Notes/Saved/{_slug(request.title)}-{content_sha256[:12]}.md"
    )
    metadata_path = f".copilot-obsidian/saved/{content_sha256}.json"
    metadata = json.dumps(
        {
            "version": 1,
            "type": "saved-insight",
            "title": request.title,
            "summary": request.summary,
            "note_path": target_path,
            "content_sha256": content_sha256,
            "tags": list(request.tags),
            "links": list(request.links),
            "source_refs": [
                source_ref.to_dict() for source_ref in source_refs
            ],
        },
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    operations = [
        FileIntent(
            target=target_path,
            intent="create",
            content=content,
            expected_sha256=None,
        ),
        FileIntent(
            target=metadata_path,
            intent="create",
            content=metadata,
            expected_sha256=None,
        ),
    ]
    bundle = build_bundle(
        _transaction_id(vault, operations),
        operations,
        metadata={"workflow": "save", "schema_version": 1},
        allow_engine_targets=True,
    )
    inspect_operation_bundle(vault, bundle)
    return SavePlan(
        bundle=bundle,
        approval_hash=_approval_hash(bundle),
        target_path=target_path,
        metadata_path=metadata_path,
        content=content,
        content_sha256=content_sha256,
        source_refs=source_refs,
    )


def _verify_source_refs(
    vault: Path,
    source_refs: tuple[SourceReference, ...],
) -> None:
    for expected in source_refs:
        current = _validate_source_ref(vault, expected.path)
        if current != expected:
            raise PreconditionError(
                "Save source ref changed after preview.",
                details={"path": expected.path},
            )


def apply_save(
    vault: Path,
    request_path: str | Path,
    approved_hash: str,
    *,
    before_apply: ApplyHook | None = None,
) -> dict[str, JsonValue]:
    plan = plan_save(vault, request_path)
    if approved_hash != plan.approval_hash:
        raise PreconditionError(
            "Approved hash does not match the current save plan.",
            details={
                "approved_hash": approved_hash,
                "current_hash": plan.approval_hash,
            },
        )
    result = apply_operation_bundle(
        vault,
        plan.bundle,
        bundle_source="generated:save",
        locked_precondition=lambda: _verify_source_refs(
            vault,
            plan.source_refs,
        ),
        before_apply=before_apply,
    )
    return {
        "workflow": "save",
        "approval_hash": plan.approval_hash,
        "target_path": plan.target_path,
        "metadata_path": plan.metadata_path,
        "content_sha256": plan.content_sha256,
        "transaction": result,
    }
