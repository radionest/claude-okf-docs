---
description: Run the OKF bundle linter and fix every finding
---

# /okf-docs:check — lint and repair the bundle

The linter decides WHAT is broken; you decide HOW to fix it. Use the
okf-docs:maintaining-kb skill for the repair rules.

## 1. Lint

Run: `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/okf_lint.py" --strict --format json`
If it prints "no OKF bundle", tell the user and suggest /okf-docs:init. If exit code is 0,
report "bundle is clean" and stop.

## 2. Fix every finding

Typical repairs (read the target files before editing — never guess):

| Code | Fix |
|---|---|
| E1/E2 | Add or repair frontmatter; set a real `type` from the taxonomy |
| E3/E4/E5 | Repoint the link to the right page, or create the missing page if the knowledge should exist, or drop a truly dead reference |
| E6 | Fix the anchor to match an existing heading (or add the heading) |
| W1 | Add an index entry (`* [Title](/page.md) - description`) in the right section |
| W2 | Link the page from related pages or CLAUDE.md — or merge/delete it if obsolete |
| W3 | Add `title`/`description` matching the page content |
| W4 | Fix date format to `YYYY-MM-DD` / reorder sections newest-first |
| W5 | Replace `[[wikilink]]` with a markdown link |

## 3. Verify

Re-run the linter with `--strict` after fixes; repeat until exit code 0. Report what was
fixed. Commit only if the user asked or a broader commit flow is already in progress.
