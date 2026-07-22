# claude-okf-docs

Personal Claude Code marketplace (`nest-dev`) hosting the **okf-docs** plugin:
a single source of linked project documentation stored as an
[OKF v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundle, referenced from `CLAUDE.md` and `.claude/rules/*.md`, with referential
integrity enforced by a deterministic linter — not by agents.

## Install

Add to `~/.claude/settings.local.json`:

```json
{
  "extraKnownMarketplaces": {
    "nest-dev": {"source": {"source": "github", "repo": "radionest/claude-okf-docs"}}
  },
  "enabledPlugins": {"okf-docs@nest-dev": true}
}
```

Requires `uv` on PATH (the linter is a uv inline-script; hooks no-op without it).

## Layout of a project bundle

- Bundle root: `docs/kb/` (override via `.claude/okf-docs.json`: `{"bundle_root": "path"}`).
- Pages `<concept>.md`: YAML frontmatter with required `type`, recommended `title`,
  `description`, `tags`, `timestamp`. Body links: bundle-absolute `/page.md` or relative `./page.md`.
- `index.md` per directory: `* [Title](url) - description` items. Frontmatter only in the
  root index and only `okf_version: "0.1"`.
- `log.md`: `## YYYY-MM-DD` headings, newest first.

## Commands

| Command | Purpose |
|---|---|
| `/okf-docs:init` | Scaffold the bundle; migrate knowledge out of CLAUDE.md/rules into pages |
| `/okf-docs:update` | After a feature/PR: update affected pages, index, log |
| `/okf-docs:check` | Run the linter and fix every finding |

## Linter

```
uv run --script okf-docs/scripts/okf_lint.py [--root DIR] [--format text|json] [--strict]
```

Errors (exit 1): E1 missing/bad frontmatter · E2 empty `type` · E3 broken internal link ·
E4 broken incoming link/@-import from CLAUDE.md or rules · E5 index entry to missing file ·
E6 broken `#anchor`. Warnings (exit 0, or 1 with `--strict`): W1 page not in any index ·
W2 orphan page · W3 missing title/description · W4 log.md date issues · W5 wikilink.

Only bodies are scanned: a link, `[[wikilink]]` or `@`-import inside YAML frontmatter is
metadata, not a reference — it is never flagged, and never counts as an incoming link.

## Hooks

- PostToolUse(Edit|Write) on bundle/CLAUDE.md/rules files → full lint; errors are fed back
  to the agent (exit 2) for an immediate fix.
- PreToolUse(Bash `gh pr create`) → `--strict` lint; findings block the PR.
  Emergency bypass: `SKIP_OKF_LINT=1 gh pr create ...`
- No bundle in the project → hooks are a silent no-op.
