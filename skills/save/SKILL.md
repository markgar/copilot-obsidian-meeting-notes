---
name: save
description: Safely persist one scoped insight or grounded answer in a local Obsidian vault. Use when the user explicitly asks to save a concise result with optional tags, links, or evidence paths. Do not use for transcripts, bulk dumps, arbitrary file operations, overwrites, moves, or deletes.
---

# Save

Use this skill to preview and persist one concise insight. It routes the request to the Python engine and transaction core; do not perform direct filesystem writes or duplicate validation logic in the skill.

## Engine Contract

The `--changes` file is a UTF-8 JSON object with:

```json
{
  "version": 1,
  "title": "Required title",
  "summary": "Required concise summary",
  "body": "Required substantive insight body",
  "tags": ["optional-tag"],
  "links": ["[[Optional Wiki Link]]"],
  "source_refs": ["Notes/Ingested/evidence.md"]
}
```

Preview without mutation:

```bash
python -m copilot_obsidian_engine save \
  --vault <local-vault-path> \
  --changes <save-request-json-file> \
  --dry-run \
  --format json
```

Apply only the exact current preview:

```bash
python -m copilot_obsidian_engine save \
  --vault <local-vault-path> \
  --changes <save-request-json-file> \
  --apply \
  --approved-hash <dry-run-sha256> \
  --format json
```

Dry-run returns the deterministic note path and content, content hash, metadata path, change summary, bundle summary, and approval hash. Apply creates the note and saved-provenance metadata in one transaction.

## Policy

- Default to `--dry-run`; apply requires both `--apply` and its exact approval hash.
- Save one scoped insight only. Refuse empty, low-signal, oversized, transcript-style, or bulk payloads.
- Normalize tags and links. Accept source refs only for existing local Notes Markdown or schema-valid ingest provenance JSON, and pin their hashes.
- Never overwrite, append, move, or delete. Existing deterministic targets are conflicts.
- Keep all reads and writes local, reject unsafe paths and symlinks, and route both output files through one transaction.
- Preserve deterministic previews and surface stale approvals, source-ref changes, conflicts, and transaction failures explicitly.
