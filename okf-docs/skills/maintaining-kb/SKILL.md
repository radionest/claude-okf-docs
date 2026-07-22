---
name: maintaining-kb
description: Use when creating, editing, splitting, merging, or linking pages in the project's OKF documentation bundle (docs/kb by default) — page granularity, frontmatter, type taxonomy, index/log conventions, and linking rules
---

# Maintaining the OKF knowledge bundle

The bundle is the project's single source of durable documentation. CLAUDE.md and
.claude/rules stay thin and POINT here. Every rule below is enforced or encouraged by
`scripts/okf_lint.py` (codes in parentheses) — run it after any bundle edit:

    uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/okf_lint.py" --strict

## Page anatomy

Every page (any non-reserved `.md` in the bundle):

```markdown
---
type: Subsystem            # REQUIRED, non-empty (E1/E2)
title: Auth pipeline       # recommended (W3)
description: One sentence a search snippet could use.  # recommended (W3)
tags: [auth, security]
timestamp: 2026-07-02T12:00:00Z   # update on every meaningful change
---

Body: structural markdown — headings, lists, tables, fenced code — over prose.
```

Reserved names: `index.md`, `log.md` — never use them for concept pages.

## Type taxonomy (open — start here, extend when nothing fits)

| type | Use for |
|---|---|
| `Subsystem` | A cohesive code area: its purpose, entry points, invariants |
| `Concept` | A cross-cutting idea or domain notion the code assumes |
| `Decision` | A choice with alternatives and rationale (mini-ADR) |
| `Convention` | A rule about how code/docs are written here, with the why |
| `Playbook` | Step-by-step operational procedure (deploy, debug, release) |

Pick self-explanatory names for new types; never leave `type` empty.

## Granularity — when to create, split, merge

- One page = one concept someone would want to read alone. Aim for 30–150 lines of body.
- CREATE a new page when knowledge doesn't fit an existing page's title, and it is durable
  (would matter in three months). Don't create pages for one-off task notes.
- SPLIT a page when readers routinely need only one of its sections, or it exceeds
  ~200 lines. Give each part a precise title; leave links behind.
- MERGE pages when neither can be understood without the other (persistent orphans — W2 —
  are merge candidates).
- Prefer editing an existing page over creating a near-duplicate.

## Linking

- Between pages: bundle-absolute `[title](/subsystems/auth.md)` preferred; relative
  `./page.md` acceptable nearby. Targets must exist (E3). Anchors must match headings (E6).
- NEVER wikilinks `[[...]]` (W5) — plain markdown only.
- From CLAUDE.md / .claude/rules: normal relative markdown links or `@`-imports
  (`@docs/kb/page.md`); they are linted too (E4).
- Links count only in the BODY: one inside YAML frontmatter (a `description:` value, say)
  is metadata — never linted, and it does NOT satisfy W1/W2.
- Every page must be reachable: listed in an index (W1) and linked from at least one other
  place (W2).

## index.md

- One per directory that has pages. Items: `* [Title](/path/page.md) - description`
  (description mirrors the page frontmatter). Group items under `#` section headings.
- Root index.md carries frontmatter `okf_version: "0.1"` and NOTHING else; subdirectory
  indexes have no frontmatter (E1).
- Adding a page = adding its index entry in the same change (E5 guards stale entries, W1
  guards missing ones).

## log.md

- Newest first: `## YYYY-MM-DD` ISO headings in strictly descending order (W4).
- Entries: `* **Update**: ...`, `* **Creation**: ...`, `* **Deprecation**: ...` — one line
  per touched page, linking it.
- Log the WHAT and WHY of knowledge changes, not code changes (git history covers code).

## Workflow for any bundle edit

1. Read the affected pages and the root index first.
2. Edit pages → update `timestamp` → update index entries → prepend log entry.
3. Run the linter (the PostToolUse hook also runs it on every edit); fix everything it
   reports before finishing. The PR gate runs `--strict` — warnings block too.
