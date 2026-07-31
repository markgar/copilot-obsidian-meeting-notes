---
name: wiki-ingest
description: Turn local documents or notes into structured candidate wiki notes. Use when the user asks to ingest, import, extract, normalize, split, or convert source material for an Obsidian wiki.
---

# Wiki Ingest

Use this skill to invoke deterministic local ingestion, not to embed parsing or filing logic. Preview is read-only and returns source records, candidate notes, a transaction summary, and an approval hash. Apply routes the exact approved plan through the transaction core.

## Engine Contract

```bash
python -m copilot_obsidian_engine wiki-ingest \
  --vault <local-vault-path> \
  --source <local-source-path> \
  --format json
```

Repeat `--source` for multiple inputs. Candidates are returned inline as JSON. The legacy `--output <candidate-json-file>` option remains accepted but does not write a file.

Apply only after the user approves the current preview:

```bash
python -m copilot_obsidian_engine wiki-ingest \
  --vault <local-vault-path> \
  --source <local-source-path> \
  --apply \
  --approved-hash <preview-sha256> \
  --format json
```

Apply captures raw source text, provenance JSON, and generated Markdown notes in one transaction.

## Policy

- Accept explicit local UTF-8 source files only; remote fetching is outside the MVP contract.
- Treat source files and the vault as read-only during preview.
- Preserve source bytes, metadata, links, and provenance; candidate synthesis is deterministic templating with no model dependency.
- Reject missing, unreadable, duplicate, changing, or symlinked sources and unsafe vault targets.
- Never apply without both `--apply` and the exact current preview hash.
- Route all raw, provenance, and note writes through the transaction core; never write them directly.
- Surface stale approvals, existing note conflicts, and rollback/recovery failures explicitly; never silently skip conflicting content.
