---
name: wiki-query
description: Search and answer questions from a local Obsidian vault. Use when the user asks to find notes, summarize known material, trace links, compare topics, or answer a question grounded in their wiki.
---

# Wiki Query

Use this skill for read-only, local retrieval. Route the request to the Python engine and preserve its evidence, offsets, confidence, and refusal fields; do not embed or override ranking logic in the skill.

## Engine Contract

```bash
python -m copilot_obsidian_engine wiki-query \
  --vault <local-vault-path> \
  --query <query-text> \
  --limit <result-count> \
  --format json
```

The command returns JSON containing an answer, matching vault-relative note paths, and supporting excerpts or link evidence. It must not modify the vault.

`--limit` defaults to 10 and must be between 1 and 100. The engine scans local `Notes/` Markdown plus validated provenance JSON, ranks matching notes before provenance, then uses deterministic `lexical-overlap-v1` score and path ordering.

Expected `data` fields are:

- `supported`: whether evidence meets the grounding threshold
- `answer`: citation-only evidence summary, or `null`
- `refusal`: `insufficient_evidence` details, or `null`
- `evidence`: ordered path, kind, snippet, offsets, score, coverage, and matched terms
- `confidence`: level, score, coverage, and reason
- `reasoning`: ranking method, normalized query terms, corpus size, threshold, and tie-break rule

## Policy

- Query local vault data first and keep vault content on the local machine.
- Do not consult remote sources; external retrieval is outside the MVP contract.
- Treat missing or insufficient evidence as a successful `insufficient_evidence` refusal rather than inventing an answer.
- Preserve citation order and all engine-provided evidence, confidence, offset, and reasoning fields.
- Surface malformed markers, provenance, raw captures, unsafe paths, and invalid limits as explicit errors.
- Route any follow-up edits or generated notes through `save`.
