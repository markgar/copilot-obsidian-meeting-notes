---
name: meeting-notes-workflow
description: Automated workflow for pulling AI meeting notes from Microsoft 365, filing them into an Obsidian vault, and linking them to daily notes. Use when the user says "pull meeting notes", "file meeting notes", "get yesterday's meetings", "grab AI notes", or any request to retrieve and organize meeting recaps into the vault.
---

# Meeting Notes Workflow

This skill defines the end-to-end process for pulling AI-generated meeting recaps from Microsoft 365 and filing them into the Obsidian vault.

## Prerequisites

- **WorkIQ** skill must be available (for querying M365 calendar/transcripts)
- **obsidian-cli** skill must be available (for writing to the vault)
- Obsidian app should be running (CLI controls it)

## Step 1: Determine the Date Range

Ask the user what period to cover, or default to "yesterday" if they say "pull meeting notes."

## Step 2: Query Meetings with Transcripts

Use WorkIQ to find meetings with AI recaps:

```
workiq-ask_work_iq:
  "Which of my meetings from [START DATE] to [END DATE] have a meeting recap 
  or transcript available? Please check for AI-generated meeting notes or recaps."
```

## Step 3: Pull Full Recaps

For each transcribed meeting, pull the full AI recap:

```
workiq-ask_work_iq:
  "Give me the full AI-generated meeting recap for '[MEETING TITLE]' on [DATE]. 
  Include the summary, key discussion topics, decisions, and action items with 
  owners. Also include the list of attendees."
```

Pull up to 4 meetings in parallel to save time.

## Step 4: Classify Each Meeting

Determine the category and customer for each meeting:

| Pattern | Category | Customer |
|---------|----------|----------|
| Meeting involves a known customer | customer | `"[[Customer Name]]"` — check `Customers/` folder for existing notes and use matching short name |
| Meeting involves an external partner or vendor | customer | `"[[Partner Name]]"` |
| Internal Microsoft meeting | internal | omit customer field |

Check the `Customers/` folder in the vault for existing customer pages to link to.

## Step 5: Determine the Filing Path

Use this folder structure:

```
Meetings/YYYY/MM/Week of MM-DD/YYYY-MM-DD - [PREFIX] [Short Title].md
```

Rules:
- **Week folder** is anchored to the Monday of that week (even if the week spans months)
- **PREFIX** is the customer short name for customer meetings (e.g., `Contoso`, `Acme`)
- No prefix for internal meetings
- **Short Title** should be concise but recognizable

Examples:
```
Meetings/2026/01/Week of 01-19/2026-01-20 - Contoso Architecture Review.md
Meetings/2026/01/Week of 01-19/2026-01-20 - Acme Data Migration Prep.md
Meetings/2026/01/Week of 01-19/2026-01-19 - Azure Data Insiders Power BI Track.md
```

## Step 6: Format the Note

Use this template:

```markdown
---
date: YYYY-MM-DD
type: meeting-notes
customer: "[[CUSTOMER]]"     # omit for internal meetings
category: customer|internal
attendees:
  - Name 1
  - Name 2
tags:
  - ai-notes
  - topic-tag-1
  - topic-tag-2
---

# [Meeting Title]

**Date:** [Day], [Full Date] | [Time]
**Organizer:** [Name]

## Executive Summary

[Summary from AI recap]

## Key Discussion Topics

### 1. [Topic]
- [Key points]

### 2. [Topic]
- [Key points]

## Decisions

- [Decision 1]
- [Decision 2]

## Action Items

| Action | Owner |
|--------|-------|
| [Action] | [Person] |
```

Key formatting rules:
- Tags should be lowercase, hyphenated
- Always include `ai-notes` tag
- Add 2-4 topic tags based on content (e.g., `fabric`, `sql-migration`, `power-bi`, `copilot`)
- Attendees list should include all known attendees
- Customer wikilinks use the short name matching the file in `Customers/`

## Step 7: Create the Note via CLI

**ALWAYS use the Obsidian CLI to write files. NEVER use direct file edit/create tools.**

```powershell
$content = @"
[full note content here]
"@
$escapedContent = $content -replace "`r`n", "\n" -replace "`n", "\n"
obsidian create path="[filing path]" content="$escapedContent"
```

Note: Emojis don't render correctly through the CLI. Use plain text only.

## Step 8: Link to Daily Notes

After filing all meeting notes, check if daily notes exist for those dates:

```
obsidian files folder="Daily"
```

For each daily note that exists on a meeting date:

1. Read the daily note content
2. Read each meeting note from that date
3. **Match by content** — compare the scratch notes to the meeting recaps to find which meeting each section corresponds to
4. Add an inline wikilink next to the matching heading:

```markdown
# Contoso Project Update [[2026-01-20 - Contoso Architecture Review|AI Notes]]
 - my scratch notes here...
```

5. Rewrite the daily note via CLI:

```powershell
obsidian create path="Daily/YYYY-MM-DD.md" content="$escapedContent" overwrite
```

If a daily note section doesn't match any filed meeting, leave it alone.

## Step 9: Summary

After completing, show a summary table:

```
| Day | Note | Customer |
|-----|------|----------|
| Mon | Meeting Title | [[Contoso]] |
| Tue | Meeting Title | Internal |
```

And note how many daily notes were linked.

## Tips

- Pull recaps in parallel (up to 4 at a time) to save time
- If WorkIQ errors on a specific meeting, retry once then skip
- If a meeting was already filed (check existing files first), skip it
- When matching dailies to meetings, look for keyword overlap: customer names, topic keywords, attendee names
