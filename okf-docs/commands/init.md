---
description: Scaffold the project's OKF docs bundle and migrate knowledge out of CLAUDE.md/rules
argument-hint: Optional bundle root (default docs/kb)
---

# /okf-docs:init — create the documentation bundle

Use the okf-docs:maintaining-kb skill for page granularity, taxonomy, and linking rules
before writing any page.

## 0. Guard

Run: `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/okf_lint.py"`
If it reports an existing bundle (anything other than "no OKF bundle"), STOP and tell the
user to use /okf-docs:update or /okf-docs:check instead.

## 1. Decide the bundle root

- Default: `docs/kb`. If the user passed an argument ($ARGUMENTS), use it instead and
  write `.claude/okf-docs.json` with `{"bundle_root": "<argument>"}`.
- Never place the bundle under `docs/superpowers/`.

## 2. Analyze the project

Read (in this order): root `CLAUDE.md`, every `.claude/rules/*.md`, nested `**/CLAUDE.md`,
README, and skim the source tree (entry points, major directories). Identify 3–7 starting
pages: durable knowledge an agent repeatedly needs — architecture overview, key subsystems,
recurring conventions, past decisions with rationale, operational playbooks.

## 3. Scaffold the bundle

Create, following the maintaining-kb skill for every file:

- `<bundle>/index.md` — frontmatter EXACTLY `okf_version: "0.1"` and nothing else; then
  section headings with `* [Title](/page.md) - description` items for every page.
- `<bundle>/log.md` — `# Update Log`, then `## <today ISO date>` with one
  `* **Initialization**: ...` entry per created page.
- One `<concept>.md` per identified page, each with complete frontmatter
  (`type`, `title`, `description`, `tags`, `timestamp` — current ISO 8601 datetime).

## 4. Migrate knowledge (thin CLAUDE.md)

Move explanation-shaped content from CLAUDE.md/rules INTO bundle pages. In CLAUDE.md keep
only operational instructions plus a short "Documentation" section pointing to the bundle:
a line per key page, markdown link or `@`-import (e.g. `@docs/kb/architecture.md`).
Target: CLAUDE.md under 200 lines. Do not delete rules files; where a rule duplicates a
page, replace the duplicated body with a link to the page.

## 5. Verify and commit

1. Run: `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/okf_lint.py" --strict`
2. Fix every finding; re-run until clean.
3. Commit all created/modified files: `docs: initialize OKF documentation bundle`.
