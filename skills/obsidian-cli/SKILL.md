---
name: obsidian-cli
description: Interact with Obsidian vaults using the official Obsidian CLI (v1.12+). Use this skill when the user wants to search notes, read or write vault content, manage tasks, work with daily notes, manage properties/frontmatter, browse tags/links/backlinks, or automate any Obsidian vault operation. Triggers include "obsidian", "vault", "daily note", "note", "search my notes", "my tasks", "backlinks", "tags", "frontmatter", "properties", "template", "bookmark".
---

# Obsidian CLI

The official Obsidian CLI (`obsidian`) controls a running Obsidian instance. It updates Obsidian's internal index, so links, tags, and properties stay consistent — unlike direct file edits.

## CRITICAL: Always Use the CLI for Vault Writes

**NEVER use direct file editing tools (edit, create) on files inside an Obsidian vault.**

Direct file writes trigger Obsidian's file watcher, which can overwhelm and crash the running Obsidian app — especially during rapid sequential edits. There is no configurable delay for the file watcher.

**Rules:**
- **Writing/creating/deleting/moving notes** → Always use the Obsidian CLI (`obsidian create`, `obsidian append`, `obsidian prepend`, `obsidian move`, `obsidian delete`, etc.)
- **Setting properties/frontmatter** → Always use `obsidian property:set` / `obsidian property:remove`
- **Reading notes** → `obsidian read` is preferred, but `view` tool is acceptable for reading since it doesn't trigger writes
- **Searching** → `obsidian search` is preferred, but `grep`/`glob` are acceptable for read-only searches
- **Complex content edits** (find-and-replace within a note body) → Use `obsidian read` to get the content, then `obsidian create` with `overwrite` to write it back. Do NOT use the `edit` tool directly.

## Key Concepts

- **file=** resolves by name (like wikilinks): `file="Meeting Notes"`
- **path=** resolves by exact path: `path="Meetings/2026-03-20.md"`
- Most commands default to the **active file** when file/path is omitted
- Quote values with spaces: `name="My Note"`
- Use `\n` for newline, `\t` for tab in content values
- Target a specific vault: `obsidian <command> vault="MyVault"`

## Running Commands

Run commands non-interactively from the shell:

```
obsidian <command> [options]
```

## Command Reference

### Reading & Searching

| Command | Description | Key Options |
|---------|-------------|-------------|
| `read` | Read file contents | `file=`, `path=` |
| `search query="text"` | Search vault for text | `path=`, `limit=`, `case`, `total`, `format=text\|json` |
| `search:context query="text"` | Search with matching line context | `path=`, `limit=`, `case`, `format=text\|json` |
| `search:open query="text"` | Open search view in Obsidian | |
| `file` | Show file info | `file=`, `path=` |
| `files` | List all files in vault | `folder=`, `ext=`, `total` |
| `folders` | List all folders | `folder=`, `total` |
| `folder` | Show folder info | `path=`, `info=files\|folders\|size` |
| `vault` | Show vault info | `info=name\|path\|files\|folders\|size` |

### Creating & Writing

| Command | Description | Key Options |
|---------|-------------|-------------|
| `create` | Create a new file | `name=`, `path=`, `content=`, `template=`, `overwrite`, `open`, `newtab` |
| `append` | Append content to file | `file=`, `path=`, `content=` (required), `inline` |
| `prepend` | Prepend content to file | `file=`, `path=`, `content=` (required), `inline` |
| `delete` | Delete a file | `file=`, `path=`, `permanent` |
| `move` | Move/rename a file | `file=`, `path=`, `to=` (required) |
| `rename` | Rename a file | `file=`, `path=`, `name=` (required) |
| `open` | Open a file in Obsidian | `file=`, `path=`, `newtab` |

### Daily Notes

| Command | Description | Key Options |
|---------|-------------|-------------|
| `daily` | Open today's daily note | `paneType=tab\|split\|window` |
| `daily:read` | Read daily note contents | |
| `daily:append` | Append to daily note | `content=` (required), `inline`, `open` |
| `daily:prepend` | Prepend to daily note | `content=` (required), `inline`, `open` |
| `daily:path` | Get daily note file path | |

### Properties / Frontmatter

| Command | Description | Key Options |
|---------|-------------|-------------|
| `property:read` | Read a property value | `name=` (required), `file=`, `path=` |
| `property:set` | Set a property on a file | `name=`, `value=` (required), `type=text\|list\|number\|checkbox\|date\|datetime`, `file=`, `path=` |
| `property:remove` | Remove a property | `name=` (required), `file=`, `path=` |
| `properties` | List properties in vault | `file=`, `name=`, `total`, `counts`, `sort=count`, `format=yaml\|json\|tsv`, `active` |

### Tags

| Command | Description | Key Options |
|---------|-------------|-------------|
| `tags` | List tags in vault | `file=`, `total`, `counts`, `sort=count`, `format=json\|tsv\|csv`, `active` |
| `tag` | Get info for a specific tag | `name=` (required), `total`, `verbose` |

### Links & Graph

| Command | Description | Key Options |
|---------|-------------|-------------|
| `links` | List outgoing links from a file | `file=`, `path=`, `total` |
| `backlinks` | List backlinks to a file | `file=`, `path=`, `counts`, `total`, `format=json\|tsv\|csv` |
| `orphans` | Files with no incoming links | `total`, `all` |
| `deadends` | Files with no outgoing links | `total`, `all` |
| `unresolved` | List unresolved/broken links | `total`, `counts`, `verbose`, `format=json\|tsv\|csv` |
| `aliases` | List aliases in vault | `file=`, `total`, `verbose`, `active` |

### Tasks

| Command | Description | Key Options |
|---------|-------------|-------------|
| `tasks` | List tasks in vault | `file=`, `total`, `done`, `todo`, `status="<char>"`, `verbose`, `format=json\|tsv\|csv`, `active`, `daily` |
| `task` | Show/update a single task | `ref=<path:line>`, `file=`, `line=`, `toggle`, `done`, `todo`, `status="<char>"`, `daily` |

### Bookmarks

| Command | Description | Key Options |
|---------|-------------|-------------|
| `bookmarks` | List bookmarks | `total`, `verbose`, `format=json\|tsv\|csv` |
| `bookmark` | Add a bookmark | `file=`, `subpath=`, `folder=`, `search=`, `url=`, `title=` |

### Templates

| Command | Description | Key Options |
|---------|-------------|-------------|
| `templates` | List available templates | `total` |
| `template:read` | Read template content | `name=` (required), `resolve`, `title=` |
| `template:insert` | Insert template into active file | `name=` (required) |

### Plugins

| Command | Description | Key Options |
|---------|-------------|-------------|
| `plugins` | List installed plugins | `filter=core\|community`, `versions`, `format=json\|tsv\|csv` |
| `plugins:enabled` | List enabled plugins | `filter=core\|community` |
| `plugin` | Get plugin info | `id=` (required) |
| `plugin:enable` | Enable a plugin | `id=` (required) |
| `plugin:disable` | Disable a plugin | `id=` (required) |
| `plugin:install` | Install community plugin | `id=` (required), `enable` |
| `plugin:uninstall` | Uninstall a plugin | `id=` (required) |
| `plugin:reload` | Reload a plugin (dev) | `id=` (required) |

### History & Sync

| Command | Description | Key Options |
|---------|-------------|-------------|
| `history` | List file history versions | `file=`, `path=` |
| `history:read` | Read a history version | `file=`, `path=`, `version=` |
| `history:restore` | Restore a history version | `file=`, `path=`, `version=` (required) |
| `sync:status` | Show sync status | |
| `sync` | Pause/resume sync | `on`, `off` |
| `diff` | List/diff local or sync versions | `file=`, `path=`, `from=`, `to=`, `filter=local\|sync` |

### Misc

| Command | Description | Key Options |
|---------|-------------|-------------|
| `outline` | Show headings for a file | `file=`, `format=tree\|md\|json`, `total` |
| `wordcount` | Count words/characters | `file=`, `words`, `characters` |
| `recents` | List recently opened files | `total` |
| `random` | Open a random note | `folder=`, `newtab` |
| `random:read` | Read a random note | `folder=` |
| `tabs` | List open tabs | `ids` |
| `commands` | List available Obsidian commands | `filter=<prefix>` |
| `command` | Execute an Obsidian command | `id=` (required) |
| `reload` | Reload the vault | |
| `vaults` | List known vaults | `total`, `verbose` |
| `version` | Show Obsidian version | |
| `theme` | Show/set theme | `name=` |
| `eval` | Execute JavaScript | `code=` (required) |

## Usage Patterns

### Quick capture to daily note
```
obsidian daily:append content="- [ ] Follow up with team on project status"
```

### Search and read
```
obsidian search query="meeting notes" limit=5
obsidian read file="Weekly Standup"
```

### Create a note from template
```
obsidian create name="Sprint Review 2026-03-20" template="Meeting Notes"
```

### Set frontmatter properties
```
obsidian property:set name="status" value="in-progress" file="Project Plan"
obsidian property:set name="tags" value="meeting,weekly" type=list file="Standup"
```

### Find incomplete tasks
```
obsidian tasks todo
obsidian tasks todo daily
```

### Vault analysis
```
obsidian orphans total
obsidian unresolved verbose
obsidian tags counts sort=count
```

## Tips

- Prefer `obsidian read` over direct file reads — it resolves wikilink-style names.
- Use `format=json` when you need to parse output programmatically.
- Chain commands with `&&` for multi-step workflows.
- The CLI launches Obsidian invisibly if it's not already running.
- Use `obsidian help <command>` for detailed help on any specific command.
