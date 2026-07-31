from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .config import load_config
from .errors import PreconditionError, VaultPathError
from .models import JsonValue
from .vault import resolve_vault_target

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXTENSION_PATTERN = re.compile(r"^(\.[a-z0-9]{1,10})?$")
STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "does",
    "for",
    "from",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "say",
    "the",
    "to",
    "what",
    "wiki",
    "with",
}
MINIMUM_COVERAGE = 0.5
MAX_LIMIT = 100


@dataclass(frozen=True)
class CorpusDocument:
    path: str
    kind: str
    content: str


@dataclass(frozen=True)
class RankedDocument:
    document: CorpusDocument
    score: float
    coverage: float
    matched_terms: tuple[str, ...]
    start_offset: int
    end_offset: int
    snippet: str

    def evidence(self, citation_order: int) -> dict[str, JsonValue]:
        return {
            "citation": f"[{citation_order}]",
            "citation_order": citation_order,
            "path": self.document.path,
            "kind": self.document.kind,
            "snippet": self.snippet,
            "offset_basis": (
                "file"
                if self.document.kind == "note"
                else "canonical_projection"
            ),
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "score": self.score,
            "coverage": self.coverage,
            "matched_terms": list(self.matched_terms),
        }


def _safe_files(root: Path, suffix: str) -> list[Path]:
    if root.is_symlink():
        raise VaultPathError(
            "Query corpus root may not be a symbolic link.",
            details={"path": str(root)},
        )
    if not root.exists():
        return []
    if not root.is_dir():
        raise PreconditionError(
            "Query corpus root is not a directory.",
            details={"path": str(root)},
        )

    files: list[Path] = []
    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        try:
            mode = entry.lstat().st_mode
        except OSError as error:
            raise PreconditionError(
                "Query corpus entry could not be inspected.",
                details={"path": str(entry), "reason": str(error)},
            ) from error
        if stat.S_ISLNK(mode):
            raise VaultPathError(
                "Query corpus may not traverse symbolic links.",
                details={"path": str(entry)},
            )
        if stat.S_ISDIR(mode):
            files.extend(_safe_files(entry, suffix))
        elif stat.S_ISREG(mode) and entry.suffix.lower() == suffix:
            files.append(entry)
        elif not stat.S_ISREG(mode):
            raise PreconditionError(
                "Query corpus contains a non-regular entry.",
                details={"path": str(entry)},
            )
    return files


def _read_text(path: Path) -> str:
    if path.is_symlink():
        raise VaultPathError(
            "Query corpus file may not be a symbolic link.",
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
                    "Query corpus entry is not a regular file.",
                    details={"path": str(path)},
                )
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read()
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PreconditionError(
            "Query corpus file is unreadable.",
            details={"path": str(path), "reason": str(error)},
        ) from error
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise PreconditionError(
            "Query corpus file changed while it was being read.",
            details={"path": str(path)},
        )
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreconditionError(
            "Query corpus file is not UTF-8 text.",
            details={"path": str(path)},
        ) from error


def _load_corpus(vault: Path) -> list[CorpusDocument]:
    notes_root = resolve_vault_target(vault, "Notes")
    notes = [
        CorpusDocument(
            path=path.relative_to(vault).as_posix(),
            kind="note",
            content=_read_text(path),
        )
        for path in _safe_files(notes_root, ".md")
    ]

    provenance: list[CorpusDocument] = []
    provenance_root = resolve_vault_target(
        vault,
        ".copilot-obsidian/provenance",
    )
    for path in _safe_files(provenance_root, ".json"):
        content = _read_text(path)
        try:
            value = json.loads(content)
        except json.JSONDecodeError as error:
            raise PreconditionError(
                "Provenance record is not valid JSON.",
                details={
                    "path": str(path),
                    "line": error.lineno,
                    "column": error.colno,
                },
            ) from error
        string_fields = (
            "source_id",
            "path",
            "sha256",
            "mime",
            "extension",
            "captured_at",
            "raw_path",
            "note_path",
        )
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or not all(isinstance(value.get(field), str) for field in string_fields)
            or not isinstance(value.get("size"), int)
            or value["size"] < 0
            or not isinstance(value.get("modified_ns"), int)
            or value["modified_ns"] < 0
            or not SHA256_PATTERN.fullmatch(value["source_id"])
            or not SHA256_PATTERN.fullmatch(value["sha256"])
            or not EXTENSION_PATTERN.fullmatch(value["extension"])
            or path.stem != value["source_id"]
            or not Path(value["path"]).is_absolute()
            or value["raw_path"]
            != f".copilot-obsidian/raw/{value['sha256']}{value['extension']}"
            or PurePosixPath(value["note_path"]).is_absolute()
            or not value["note_path"].startswith("Notes/")
            or ".." in PurePosixPath(value["note_path"]).parts
        ):
            raise PreconditionError(
                "Provenance record has an invalid schema.",
                details={"path": str(path)},
            )
        expected_source_id = hashlib.sha256(
            f"{value['path']}\0{value['sha256']}".encode("utf-8")
        ).hexdigest()
        try:
            captured_at = datetime.fromisoformat(
                value["captured_at"].replace("Z", "+00:00")
            )
        except ValueError as error:
            raise PreconditionError(
                "Provenance captured_at is invalid.",
                details={"path": str(path)},
            ) from error
        if expected_source_id != value["source_id"] or captured_at.tzinfo is None:
            raise PreconditionError(
                "Provenance record identity is inconsistent.",
                details={"path": str(path)},
            )
        raw_path = resolve_vault_target(vault, value["raw_path"])
        raw_content = _read_text(raw_path).encode("utf-8")
        if (
            len(raw_content) != value["size"]
            or hashlib.sha256(raw_content).hexdigest() != value["sha256"]
        ):
            raise PreconditionError(
                "Provenance raw capture does not match its metadata.",
                details={"path": str(path), "raw_path": value["raw_path"]},
            )
        projection = {
            field: value[field]
            for field in (
                "version",
                "source_id",
                "path",
                "sha256",
                "size",
                "mime",
                "extension",
                "captured_at",
                "modified_ns",
                "raw_path",
                "note_path",
            )
        }
        provenance.append(
            CorpusDocument(
                path=path.relative_to(vault).as_posix(),
                kind="provenance",
                content=json.dumps(
                    projection,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=True,
                )
                + "\n",
            )
        )
    return notes + provenance


def _tokens(value: str) -> list[str]:
    return [
        match.group(0).lower()
        for match in TOKEN_PATTERN.finditer(value)
    ]


def _query_tokens(value: str) -> list[str]:
    return [
        token
        for token in _tokens(value)
        if token not in STOPWORDS
    ]


def _snippet(content: str, matched_terms: set[str]) -> tuple[int, int, str]:
    first_offset = 0
    for match in TOKEN_PATTERN.finditer(content):
        if match.group(0).lower() in matched_terms:
            first_offset = match.start()
            break
    start = max(0, first_offset - 80)
    end = min(len(content), first_offset + 240)
    return start, end, content[start:end]


def _rank(
    documents: list[CorpusDocument],
    query_tokens: list[str],
) -> list[RankedDocument]:
    query_counts = Counter(query_tokens)
    unique_query = set(query_counts)
    ranked: list[RankedDocument] = []
    for document in documents:
        document_tokens = _tokens(document.content)
        counts = Counter(document_tokens)
        matched = tuple(sorted(term for term in unique_query if counts[term]))
        if not matched:
            continue
        coverage = len(matched) / len(unique_query)
        bounded_frequency = sum(
            min(counts[term], 3) * query_counts[term]
            for term in matched
        )
        density = sum(counts[term] for term in matched) / max(
            len(document_tokens),
            1,
        )
        kind_weight = 1.0 if document.kind == "note" else 0.7
        score = round(
            (coverage * 5 + bounded_frequency + density) * kind_weight,
            6,
        )
        start, end, snippet = _snippet(document.content, set(matched))
        ranked.append(
            RankedDocument(
                document=document,
                score=score,
                coverage=round(coverage, 6),
                matched_terms=matched,
                start_offset=start,
                end_offset=end,
                snippet=snippet,
            )
        )
    return sorted(
        ranked,
        key=lambda result: (
            0 if result.document.kind == "note" else 1,
            -result.score,
            result.document.path,
        ),
    )


def query_vault(vault: Path, query: str, limit: int) -> dict[str, JsonValue]:
    vault = vault.resolve()
    if load_config(vault).marker_path is None:
        raise PreconditionError(
            "Vault is not initialized; run wiki init or wiki adopt first."
        )
    if limit < 1 or limit > MAX_LIMIT:
        raise PreconditionError(
            f"Query limit must be between 1 and {MAX_LIMIT}.",
            details={"limit": limit},
        )
    meaningful_tokens = _query_tokens(query)
    documents = _load_corpus(vault)
    if not meaningful_tokens:
        return {
            "query": query,
            "supported": False,
            "answer": None,
            "refusal": {
                "code": "insufficient_evidence",
                "message": "The query contains no meaningful lexical terms.",
            },
            "evidence": [],
            "confidence": {
                "level": "none",
                "score": 0.0,
                "coverage": 0.0,
                "reason": "No meaningful query terms remained after normalization.",
            },
            "reasoning": {
                "method": "lexical-overlap-v1",
                "query_tokens": [],
                "documents_considered": len(documents),
                "minimum_coverage": MINIMUM_COVERAGE,
            },
        }

    ranked = _rank(documents, meaningful_tokens)
    selected = ranked[:limit]
    evidence = [
        result.evidence(index)
        for index, result in enumerate(selected, start=1)
    ]
    support = next(
        (
            result
            for result in selected
            if result.coverage >= MINIMUM_COVERAGE
        ),
        None,
    )
    supported = support is not None

    if not supported:
        answer = None
        refusal: dict[str, JsonValue] | None = {
            "code": "insufficient_evidence",
            "message": "The local vault does not contain enough lexical support for this query.",
        }
        confidence_level = "none"
        confidence_reason = (
            "No matching evidence was found."
            if not selected
            else "Top evidence did not meet the minimum query-term coverage."
        )
    else:
        citations = []
        for index, result in enumerate(selected[:3], start=1):
            excerpt = " ".join(result.snippet.split())
            citations.append(f"[{index}] {excerpt[:200]}")
        answer = "Grounded vault evidence:\n" + "\n".join(citations)
        refusal = None
        confidence_level = (
            "high"
            if support.coverage == 1.0 and support.score >= 7
            else "medium"
            if support.coverage >= 0.75
            else "low"
        )
        confidence_reason = (
            "Confidence reflects lexical query-term coverage and frequency in the top evidence."
        )

    return {
        "query": query,
        "supported": supported,
        "answer": answer,
        "refusal": refusal,
        "evidence": evidence,
        "confidence": {
            "level": confidence_level,
            "score": support.score if support is not None else 0.0,
            "coverage": support.coverage if support is not None else 0.0,
            "reason": confidence_reason,
        },
        "reasoning": {
            "method": "lexical-overlap-v1",
            "query_tokens": sorted(set(meaningful_tokens)),
            "documents_considered": len(documents),
            "minimum_coverage": MINIMUM_COVERAGE,
            "tie_break": "note before provenance, score desc, path asc",
        },
    }
