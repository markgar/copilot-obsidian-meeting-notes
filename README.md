# copilot-obsidian-meeting-notes

A [GitHub Copilot CLI](https://docs.github.com/copilot/concepts/agents/about-copilot-cli) plugin that brings Obsidian vault management and AI meeting notes automation to your terminal.

## What It Does

This plugin includes two skills:

### 🗃️ obsidian-cli
Full command reference for the [official Obsidian CLI](https://obsidian.md/cli) (v1.12+). Teaches Copilot how to interact with your vault through the CLI — reading, writing, searching, managing properties, tags, tasks, and more. Includes a critical safety rule: **always write through the CLI** to avoid crashing Obsidian's file watcher.

### 📋 meeting-notes-workflow
End-to-end workflow for pulling AI-generated meeting recaps from Microsoft 365 and filing them into your Obsidian vault:

1. **Queries** your M365 calendar for meetings with transcripts
2. **Pulls** full AI recaps (summary, discussion topics, decisions, action items)
3. **Classifies** each meeting (customer vs. internal)
4. **Files** notes with structured frontmatter into `Meetings/YYYY/MM/Week of MM-DD/`
5. **Links** your daily scratch notes to the corresponding full meeting notes

## Prerequisites

- [Obsidian](https://obsidian.md) v1.12+ with the CLI enabled (`Settings > General > Command line interface`)
- [GitHub Copilot CLI](https://docs.github.com/copilot/concepts/agents/about-copilot-cli)
- Microsoft 365 account with meeting transcripts (requires WorkIQ or similar M365 integration)

## Installation

```bash
/plugin install markgar/copilot-obsidian-meeting-notes
```

## Usage

Open Copilot CLI in your Obsidian vault directory, then:

```
Pull yesterday's meeting notes and file them
```

```
Get my meeting notes from last week
```

```
Search my vault for notes about Fabric
```

```
Show me my incomplete tasks
```

The skills are loaded automatically based on context. You can also invoke them explicitly with `/obsidian-cli` or `/meeting-notes-workflow`.

## Vault Structure

The meeting notes workflow creates this structure:

```
MyVault/
├── Daily/
│   └── 2026-03-20.md              ← your scratch notes (with links to AI notes)
├── Meetings/
│   └── 2026/
│       └── 03/
│           └── Week of 03-16/
│               ├── 2026-03-17 - Contoso Weekly Connect.md
│               └── 2026-03-17 - Acme Data Sync.md
└── Customers/
    ├── Contoso.md
    └── Acme.md
```

## Customization

Fork this repo and edit the `SKILL.md` files to match your vault structure, customer naming conventions, or frontmatter schema. Then install from your fork:

```bash
/plugin install your-username/copilot-obsidian-meeting-notes
```

## License

MIT
