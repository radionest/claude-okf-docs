---
description: Update bundle pages, index, and log after a feature or before a PR
---

# /okf-docs:update — reconcile the bundle with recent changes

Use the okf-docs:maintaining-kb skill for page granularity, taxonomy, and linking rules.

## 1. Collect the delta

- In a git repo: `BASE=$(git merge-base HEAD main 2>/dev/null || git merge-base HEAD master)`;
  review `git diff $BASE..HEAD --stat` and the conversation context for what changed.
- Otherwise: use the conversation context alone.

## 2. Map changes to pages

Run `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/okf_lint.py" --format json` to locate
the bundle, then read its root index.md. For each behavioral change decide:
update an existing page, create a new one (per the skill's granularity rules), or nothing
(implementation detail already evident from code).

## 3. Apply edits

- Update affected pages (body + `timestamp`; keep `description` accurate).
- Add index entries for new pages; fix descriptions that drifted.
- Prepend to `log.md` under a `## <today ISO date>` heading (create the heading if absent —
  newest date first): one `* **Update**: ...` / `* **Creation**: ...` line per touched page,
  linking the page.

## 4. Verify and commit

1. `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/okf_lint.py" --strict`
2. Fix every finding; re-run until clean.
3. Commit: `docs: update KB for <topic>`.
