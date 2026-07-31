---
name: wiki
description: Check, initialize, or adopt a local Obsidian wiki. Use for readiness checks, empty-vault setup, or non-destructive bootstrap of an existing vault. Route ingestion, retrieval, and scoped persistence to their dedicated skills.
---

# Wiki

Use the implemented doctor/init/adopt workflows for vault readiness and bootstrap. Delegate source conversion to `wiki-ingest`, retrieval to `wiki-query`, and scoped persistence to `save`. Do not perform file mutation in the skill.

## Engine Contract

Check readiness without mutation:

```bash
python -m copilot_obsidian_engine wiki doctor \
  --vault <local-vault-path> \
  --format json
```

Preview initialization for an empty vault or adoption for an existing vault:

```bash
python -m copilot_obsidian_engine wiki <init|adopt> \
  --vault <local-vault-path> \
  --format json
```

Apply only the exact preview the user approved:

```bash
python -m copilot_obsidian_engine wiki <init|adopt> \
  --vault <local-vault-path> \
  --apply \
  --approved-hash <preview-sha256> \
  --format json
```

Doctor is read-only. Init and adopt return deterministic bundles by default, then route approved writes through the transaction core; they never write directly.

## Policy

- Operate only on the explicitly selected local vault. Network access is outside the MVP contract.
- Treat the vault as read-only during planning.
- Use `init` only for an empty vault and `adopt` for an existing vault.
- Never apply init/adopt without both `--apply` and the hash from the current preview.
- Route bootstrap through init/adopt, source conversion through `wiki-ingest`, and scoped insight persistence through `save`; never write vault files directly.
- Keep all resolved vault paths within `--vault`, and surface invalid or ambiguous paths as errors.
- Preserve deterministic plans and surface stale approvals, conflicts, invalid markers, and unsafe paths as explicit errors.
