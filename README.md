# copilot-obsidian

A local-first [GitHub Copilot CLI](https://docs.github.com/copilot/concepts/agents/about-copilot-cli) plugin for a Copilot-based `claude-obsidian` MVP. Four skills route Obsidian vault work through a deterministic Python engine with grounded reads and transaction-backed writes.

## MVP Scope

The MVP exposes four routing skills:

| Skill | Purpose | Engine command |
|---|---|---|
| `wiki` | Plan and coordinate wiki work | `python -m copilot_obsidian_engine wiki` |
| `wiki-ingest` | Convert local source material into candidate notes | `python -m copilot_obsidian_engine wiki-ingest` |
| `wiki-query` | Search and answer from local vault content | `python -m copilot_obsidian_engine wiki-query` |
| `save` | Preview and safely apply vault writes | `python -m copilot_obsidian_engine save` |

All documented MVP workflows are executable. A legacy general `wiki --request` parser remains for compatibility but is not part of the skill contract.

## Compatibility Matrix

| Skill | Command | Mode | Gate |
|---|---|---|---|
| `wiki` | `wiki doctor` | Read-only readiness | None |
| `wiki` | `wiki init` / `wiki adopt` | Read-only preview | None |
| `wiki` | `wiki init` / `wiki adopt` | Mutating bootstrap | `--apply --approved-hash <preview-hash>` |
| `wiki-ingest` | `wiki-ingest --source ...` | Read-only preview | None |
| `wiki-ingest` | `wiki-ingest --source ...` | Mutating ingest | `--apply --approved-hash <preview-hash>` |
| `wiki-query` | `wiki-query --query ...` | Read-only retrieval | None; insufficient evidence returns a refusal |
| `save` | `save --changes ... --dry-run` | Read-only preview | `--dry-run` |
| `save` | `save --changes ... --apply` | Mutating scoped save | `--apply --approved-hash <dry-run-hash>` |

## Safety Model

- Work from local vault and source files by default.
- Treat wiki and ingest previews plus `wiki-query` as read-only planning operations.
- Route bootstrap, ingest, and scoped save application through the transaction core.
- Preview every mutating workflow and require its exact approved hash before apply.
- Never perform ad-hoc vault writes. The MVP does not support overwrite, append, move, or delete.
- Keep paths vault-relative and reject writes outside the selected vault.

## Prerequisites

- [GitHub Copilot CLI](https://docs.github.com/copilot/concepts/agents/about-copilot-cli)
- Python 3.10 or newer
- A local Obsidian vault

## Installation

Install the Python engine from a clone, then install the Copilot CLI plugin:

```bash
git clone https://github.com/markgar/copilot-obsidian-meeting-notes.git
cd copilot-obsidian-meeting-notes
python3 -m pip install .
/plugin install markgar/copilot-obsidian-meeting-notes
```

The GitHub repository retains its original slug; the installed plugin identity is `copilot-obsidian`. For a fork, replace `markgar/copilot-obsidian-meeting-notes` with `<owner>/<repo>` in both the clone URL and `/plugin install` command.

## Quick Start

Check an existing local vault first:

```bash
python -m copilot_obsidian_engine wiki doctor \
  --vault /path/to/vault \
  --format json
```

For an empty vault, preview initialization, copy the returned `approval_hash`, and apply that exact plan:

```bash
python -m copilot_obsidian_engine wiki init \
  --vault /path/to/vault \
  --format json

python -m copilot_obsidian_engine wiki init \
  --vault /path/to/vault \
  --apply \
  --approved-hash <approval_hash> \
  --format json
```

Use `wiki adopt` with the same preview/apply handshake when the vault already contains notes. Then open Copilot CLI in or near the vault and ask it to:

```
Ingest these local research notes into my wiki
```

```
What does my wiki say about retrieval strategies?
```

```
Save this retrieval insight with its evidence
```

## Command Reference

| Command | Purpose | Write gate |
|---|---|---|
| `wiki doctor --vault <vault>` | Report marker, transaction, and capability readiness | Read-only |
| `wiki init --vault <vault>` | Preview or apply an empty-vault bootstrap | `--apply --approved-hash <hash>` |
| `wiki adopt --vault <vault>` | Preview or apply missing bootstrap files in an existing vault | `--apply --approved-hash <hash>` |
| `wiki-ingest --vault <vault> --source <file> [...]` | Preview or apply local source capture and note creation | `--apply --approved-hash <hash>` |
| `wiki-query --vault <vault> --query <text> [--limit <n>]` | Return grounded lexical evidence or an explicit refusal | Read-only |
| `save --vault <vault> --changes <request.json> --dry-run` | Preview one scoped saved insight | Read-only preview |
| `save --vault <vault> --changes <request.json> --apply` | Apply the exact scoped save preview | `--approved-hash <hash>` |
| `transaction inspect --vault <vault> --bundle <bundle.json>` | Validate bundle structure and target preconditions | Read-only |
| `transaction apply --vault <vault> --bundle <bundle.json>` | Atomically apply an inspected bundle | Transaction lock and preconditions |
| `transaction recover --vault <vault> --transaction-id <id>` | Restore files from an incomplete transaction journal | Transaction lock and journal checks |

## Engine CLI

Run the engine from any directory after package installation, or directly from the repository root without installing:

```bash
python -m copilot_obsidian_engine wiki doctor \
  --vault /path/to/vault \
  --format json

python -m copilot_obsidian_engine wiki-ingest \
  --vault /path/to/vault \
  --source /path/to/source.md \
  --format json

python -m copilot_obsidian_engine wiki-query \
  --vault /path/to/vault \
  --query "retrieval strategies" \
  --limit 10 \
  --format json

python -m copilot_obsidian_engine save \
  --vault /path/to/vault \
  --changes /path/to/changes.json \
  --dry-run \
  --format json
```

The vault marker `.copilot-obsidian.json` must contain a JSON object. Doctor/init/adopt handle a missing marker; ingest/query/save require a valid marker. Commands emit JSON for success and expected failure, with non-zero exits for usage, invalid vault/configuration, conflicts, locks, preconditions, and transaction failures.

### Wiki readiness and bootstrap

Doctor is read-only and reports vault, marker, transaction, and capability readiness:

```bash
python -m copilot_obsidian_engine wiki doctor \
  --vault /path/to/vault \
  --format json
```

Use `init` for an empty vault. The first command only returns a deterministic operation bundle and approval hash:

```bash
python -m copilot_obsidian_engine wiki init \
  --vault /path/to/vault \
  --format json
```

Apply that exact plan by supplying both the explicit gate and returned hash:

```bash
python -m copilot_obsidian_engine wiki init \
  --vault /path/to/vault \
  --apply \
  --approved-hash <sha256-from-plan> \
  --format json
```

Use the same preview/apply handshake to adopt a vault that already contains notes:

```bash
python -m copilot_obsidian_engine wiki adopt \
  --vault /path/to/vault \
  --format json

python -m copilot_obsidian_engine wiki adopt \
  --vault /path/to/vault \
  --apply \
  --approved-hash <sha256-from-plan> \
  --format json
```

`init` creates `.copilot-obsidian.json`, `Home.md`, `Inbox/README.md`, and `Notes/README.md`. `adopt` preserves existing content and plans only missing bootstrap files. Apply regenerates the plan and refuses stale hashes, missing approval gates, conflicts, and unsafe paths before delegating writes to the transaction core.

### Local source ingestion

Preview one or more explicit local UTF-8 source files by repeating `--source`:

```bash
python -m copilot_obsidian_engine wiki-ingest \
  --vault /path/to/vault \
  --source /path/to/research.md \
  --source /path/to/notes.txt \
  --format json
```

The preview returns stable source records, candidate note JSON, a bundle summary, and an approval hash. Apply the exact current preview with:

```bash
python -m copilot_obsidian_engine wiki-ingest \
  --vault /path/to/vault \
  --source /path/to/research.md \
  --source /path/to/notes.txt \
  --apply \
  --approved-hash <sha256-from-preview> \
  --format json
```

Apply captures source text under `.copilot-obsidian/raw/<sha256>.<ext>`, writes an immutable per-source JSON record under `.copilot-obsidian/provenance/`, and creates a deterministic Markdown note under `Notes/Ingested/` in one transaction. For deterministic replay, MVP `captured_at` is the source file's normalized modification timestamp. The legacy `--output <path>` flag is accepted for command compatibility, but candidates are returned inline and no output file is written.

### Grounded wiki query

Query ingested notes and their provenance without writing vault state:

```bash
python -m copilot_obsidian_engine wiki-query \
  --vault /path/to/vault \
  --query "vector retrieval evidence" \
  --limit 5 \
  --format json
```

Results use transparent `lexical-overlap-v1` scoring: query-term coverage, bounded term frequency, and document density. Matching notes always rank ahead of provenance; each tier sorts by score and then vault-relative path. `--limit` accepts 1-100 and caps evidence items.

The JSON `data` payload contains:

```json
{
  "supported": true,
  "answer": "Grounded vault evidence:\n[1] ...",
  "refusal": null,
  "evidence": [
    {
      "citation": "[1]",
      "path": "Notes/Ingested/example.md",
      "kind": "note",
      "snippet": "...",
      "start_offset": 0,
      "end_offset": 120,
      "score": 7.5,
      "coverage": 1.0,
      "matched_terms": ["evidence", "retrieval", "vector"]
    }
  ],
  "confidence": {"level": "high", "score": 7.5, "coverage": 1.0},
  "reasoning": {"method": "lexical-overlap-v1"}
}
```

When no evidence reaches the minimum 50% query-term coverage, the command succeeds with `supported: false`, `answer: null`, and an explicit `insufficient_evidence` refusal rather than inventing an answer.

### Scoped save

Prepare one scoped insight as UTF-8 JSON:

```json
{
  "version": 1,
  "title": "Scoped retrieval insight",
  "summary": "Lexical evidence keeps local answers grounded.",
  "body": "Use deterministic ranking with explicit citations so each answer remains traceable to local vault evidence.",
  "tags": ["retrieval", "local-first"],
  "links": ["[[Retrieval Strategy]]"],
  "source_refs": [
    "Notes/Ingested/retrieval.md",
    ".copilot-obsidian/provenance/source-id.json"
  ]
}
```

Preview without writing:

```bash
python -m copilot_obsidian_engine save \
  --vault /path/to/vault \
  --changes /path/to/save-request.json \
  --dry-run \
  --format json
```

Apply the exact preview:

```bash
python -m copilot_obsidian_engine save \
  --vault /path/to/vault \
  --changes /path/to/save-request.json \
  --apply \
  --approved-hash <sha256-from-dry-run> \
  --format json
```

Save creates a deterministic `Notes/Saved/<slug>-<content-hash>.md` note and `.copilot-obsidian/saved/<content-hash>.json` provenance metadata in one transaction. It never overwrites. Source refs are restricted to existing `Notes/*.md` or ingest provenance JSON and are SHA-256 pinned in saved metadata. Empty, low-signal, oversized, transcript-style, stale, conflicting, or unsafe requests are refused.

### Transaction CLI

Inspect validates a bundle and all target preconditions without writing:

```bash
python -m copilot_obsidian_engine transaction inspect \
  --vault /path/to/vault \
  --bundle /path/to/bundle.json \
  --format json
```

Apply acquires the vault lock, writes an internal backup journal, and atomically applies each validated create or replace intent:

```bash
python -m copilot_obsidian_engine transaction apply \
  --vault /path/to/vault \
  --bundle /path/to/bundle.json \
  --format json
```

Recover rolls back an incomplete journal:

```bash
python -m copilot_obsidian_engine transaction recover \
  --vault /path/to/vault \
  --transaction-id tx-20260730-001 \
  --format json
```

Operation bundles use this versioned shape:

```json
{
  "version": 1,
  "transaction_id": "tx-20260730-001",
  "metadata": {},
  "operations": [
    {
      "target": "Notes/Example.md",
      "intent": "replace",
      "content": "# Updated note\n",
      "expected_sha256": "64-character SHA-256 of the current UTF-8 file"
    }
  ]
}
```

Supported intents are `create` (target absent, `expected_sha256` is `null`) and `replace` (target present with a matching SHA-256). Engine state is stored under `.copilot-obsidian/transactions/`.

Exit codes are `0` success, `1` general vault/configuration error, `2` CLI usage error, `3` unimplemented business command, `4` transaction conflict, `5` vault lock contention, `6` malformed bundle or failed precondition, and `7` apply/recovery failure.

## Validation

Run the complete stdlib test suite. A successful run ends with `OK`:

```bash
python3 -m unittest discover -s tests -v
```

Run only the subprocess-level MVP compatibility flows. A successful run executes ten tests and ends with `OK`:

```bash
python3 -m unittest discover -s tests -p 'test_mvp_blackbox.py' -v
```

## MVP Limitations

- The engine is local-only and has no network, model, or semantic-retrieval dependency.
- Ingestion accepts readable, regular UTF-8 text files only; binary formats and external URLs are unsupported.
- Query uses deterministic ASCII lexical overlap over local notes and provenance. It has no persistent index and refuses answers below its grounding threshold.
- Save accepts one bounded insight at a time. Transcript-dump detection is intentionally heuristic.
- Mutating workflows are create-only at the feature level. Update, overwrite, append, move, and delete operations are not exposed.
- Repeating an already-applied deterministic ingest or save plan conflicts rather than silently overwriting existing targets.
- Filesystem safety relies on POSIX primitives including descriptor-relative operations, no-follow flags, atomic rename, and hard links.
- `wiki --request` remains a non-mutating compatibility stub with exit code `3`; ingest `--output` is accepted but never writes an output file.

## Next Phases

1. Publish versioned plugin and Python package artifacts with upgrade guidance.
2. Add explicit platform compatibility coverage for filesystem primitives.
3. Add optional indexing and ingestion adapters for larger vaults.
4. Evaluate semantic retrieval only as an explicitly local, optional enhancement.

## License

MIT
