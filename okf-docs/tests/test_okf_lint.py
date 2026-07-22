"""Tests for okf_lint.py (linter) and okf_gate.py (hook adapter)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
LINT = PLUGIN / "scripts" / "okf_lint.py"
GATE = PLUGIN / "hooks" / "okf_gate.py"


def load_lint():
    spec = importlib.util.spec_from_file_location("okf_lint", LINT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = load_lint()


def load_gate():
    spec = importlib.util.spec_from_file_location("okf_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


G = load_gate()


def write(root: Path, rel_path: str, content: str) -> Path:
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


PAGE = """---
type: Concept
title: Auth
description: How auth works.
---

# Auth

Body text.
"""

ROOT_INDEX = """---
okf_version: "0.1"
---

# Bundle

* [Auth](/auth.md) - How auth works.
"""


def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel_path, content in files.items():
        write(tmp_path, rel_path, content)
    return tmp_path


def codes(findings) -> list[str]:
    return sorted(f.code for f in findings)


def run_lint(repo_root: Path):
    bundle = M.find_bundle_root(repo_root)
    assert bundle is not None
    return M.lint_bundle(repo_root, bundle)


# ---------- discovery ----------

def test_find_bundle_root_default(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE})
    assert M.find_bundle_root(tmp_path) == (tmp_path / "docs/kb").resolve()


def test_find_bundle_root_none_without_index(tmp_path):
    make_repo(tmp_path, {"docs/kb/auth.md": PAGE})
    assert M.find_bundle_root(tmp_path) is None


def test_find_bundle_root_from_config(tmp_path):
    make_repo(tmp_path, {
        ".claude/okf-docs.json": '{"bundle_root": "knowledge"}',
        "knowledge/index.md": ROOT_INDEX,
    })
    assert M.find_bundle_root(tmp_path) == (tmp_path / "knowledge").resolve()


def test_find_bundle_root_config_missing_dir(tmp_path):
    make_repo(tmp_path, {".claude/okf-docs.json": '{"bundle_root": "knowledge"}'})
    assert M.find_bundle_root(tmp_path) is None


def test_superpowers_never_a_bundle(tmp_path):
    make_repo(tmp_path, {
        ".claude/okf-docs.json": '{"bundle_root": "docs/superpowers/kb"}',
        "docs/superpowers/kb/index.md": ROOT_INDEX,
    })
    assert M.find_bundle_root(tmp_path) is None


# ---------- frontmatter: E1 / E2 / W3 ----------

def test_valid_page_no_findings(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE})
    assert run_lint(tmp_path) == []


def test_e1_missing_frontmatter(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX,
                         "docs/kb/auth.md": "# Auth\n\nNo frontmatter.\n"})
    fs = run_lint(tmp_path)
    assert codes(fs) == ["E1"]
    assert fs[0].path == "docs/kb/auth.md"
    assert fs[0].line == 1


def test_e1_unparseable_yaml_no_crash(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX,
                         "docs/kb/auth.md": "---\ntype: [unclosed\n---\nbody\n"})
    fs = run_lint(tmp_path)
    assert codes(fs) == ["E1"]
    assert "YAML" in fs[0].message


def test_e1_unterminated_frontmatter(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX,
                         "docs/kb/auth.md": "---\ntype: Concept\nbody without closing\n"})
    assert codes(run_lint(tmp_path)) == ["E1"]


def test_e1_non_utf8_file(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE})
    (tmp_path / "docs/kb/bad.md").write_bytes(b"\xff\xfe broken")
    assert codes(run_lint(tmp_path)) == ["E1"]


def test_e2_missing_and_empty_type(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n* [B2](/b.md) - b.\n",
        "docs/kb/a.md": "---\ntitle: A\ndescription: d.\n---\nbody\n",
        "docs/kb/b.md": "---\ntype: \"\"\ntitle: B\ndescription: d.\n---\nbody\n",
    })
    assert codes(run_lint(tmp_path)) == ["E2", "E2"]


def test_root_index_frontmatter_only_okf_version(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": '---\nokf_version: "0.1"\ntype: Extra\n---\n\n* [A](/a.md) - a.\n',
        "docs/kb/a.md": PAGE.replace("Auth", "A"),
    })
    fs = run_lint(tmp_path)
    assert "E1" in codes(fs)
    assert any("okf_version" in f.message for f in fs)


def test_root_index_frontmatter_absent_is_ok(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": "# Bundle\n\n* [Auth](/auth.md) - a.\n",
                         "docs/kb/auth.md": PAGE})
    assert codes(run_lint(tmp_path)) == []


def test_subdir_index_frontmatter_is_e1(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX.replace("/auth.md", "/sub/auth.md"),
        "docs/kb/sub/index.md": '---\nokf_version: "0.1"\n---\n\n* [Auth](/sub/auth.md) - a.\n',
        "docs/kb/sub/auth.md": PAGE,
    })
    fs = run_lint(tmp_path)
    assert "E1" in codes(fs)
    assert fs[0].path == "docs/kb/sub/index.md"


def test_w3_missing_title_description(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX,
                         "docs/kb/auth.md": "---\ntype: Concept\n---\nbody\n"})
    fs = run_lint(tmp_path)
    assert codes(fs) == ["W3"]
    assert "title" in fs[0].message and "description" in fs[0].message


def test_log_md_needs_no_frontmatter(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE,
                         "docs/kb/log.md": "# Log\n\n## 2026-07-02\n* **Init**: created.\n"})
    assert codes(run_lint(tmp_path)) == []


# ---------- CLI ----------

def run_cli(cwd: Path, *args: str):
    return subprocess.run([sys.executable, str(LINT), *args],
                          capture_output=True, text=True, cwd=str(cwd))


def test_cli_no_bundle_message_exit_0(tmp_path):
    r = run_cli(tmp_path, "--root", str(tmp_path))
    assert r.returncode == 0
    assert "no OKF bundle" in r.stdout


def test_cli_clean_exit_0(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE})
    r = run_cli(tmp_path, "--root", str(tmp_path))
    assert r.returncode == 0
    assert "0 errors" in r.stdout


def test_cli_errors_exit_1_clickable_format(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX,
                         "docs/kb/auth.md": "# no frontmatter\n"})
    r = run_cli(tmp_path, "--root", str(tmp_path))
    assert r.returncode == 1
    assert "docs/kb/auth.md:1: E1 " in r.stdout


def test_cli_warnings_exit_0_strict_exit_1(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX,
                         "docs/kb/auth.md": "---\ntype: Concept\n---\nbody\n"})
    assert run_cli(tmp_path, "--root", str(tmp_path)).returncode == 0
    assert run_cli(tmp_path, "--root", str(tmp_path), "--strict").returncode == 1


def test_cli_json_format(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX,
                         "docs/kb/auth.md": "---\ntype: Concept\n---\nbody\n"})
    r = run_cli(tmp_path, "--root", str(tmp_path), "--format", "json")
    data = json.loads(r.stdout)
    assert data["warnings"] == 1 and data["errors"] == 0
    assert data["findings"][0]["code"] == "W3"
    assert data["findings"][0]["severity"] == "warning"


# ---------- bundle-internal links: E3 / E5 / E6 / W5 ----------

def page(body: str, title: str = "P") -> str:
    return f"---\ntype: Concept\ntitle: {title}\ndescription: d.\n---\n\n{body}\n"


def test_e3_broken_absolute_and_relative_links(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n* [C](/sub/c.md) - c.\n",
        "docs/kb/a.md": page("See [gone](/nope.md) and [also](./missing.md)."),
        "docs/kb/sub/c.md": page("Up: [ok](../a.md)."),
    })
    fs = [f for f in run_lint(tmp_path) if f.code == "E3"]
    assert len(fs) == 2
    assert all(f.path == "docs/kb/a.md" for f in fs)
    assert fs[0].line == 7


def test_e3_valid_links_pass(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n* [C](/sub/c.md) - c.\n",
        "docs/kb/a.md": page("See [c](/sub/c.md) and [c rel](sub/c.md)."),
        "docs/kb/sub/c.md": page("Up: [a](../a.md), [a abs](/a.md)."),
    })
    assert codes(run_lint(tmp_path)) == []


def test_e3_in_log_md(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX,
        "docs/kb/auth.md": PAGE,
        "docs/kb/log.md": "# Log\n\n## 2026-07-02\n* **Update**: touched [gone](/gone.md).\n",
    })
    fs = run_lint(tmp_path)
    assert codes(fs) == ["E3"]
    assert fs[0].path == "docs/kb/log.md"


def test_links_outside_bundle_ignored(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX,
        "docs/kb/auth.md": page("See [src](../../src/app.py) and [web](https://example.com) "
                                "and [mail](mailto:x@y.z)."),
        "src/app.py": "print()\n",
    })
    assert codes(run_lint(tmp_path)) == []


def test_links_inside_code_ignored(tmp_path):
    body = "```md\n[gone](/gone.md)\n```\n\nInline `[gone2](/gone2.md)` too."
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": page(body)})
    assert codes(run_lint(tmp_path)) == []


def test_e5_index_entry_to_missing_file(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [Auth](/auth.md) - a.\n* [Gone](/gone.md) - g.\n",
        "docs/kb/auth.md": PAGE,
    })
    fs = run_lint(tmp_path)
    assert codes(fs) == ["E5"]
    assert fs[0].path == "docs/kb/index.md" and fs[0].line == 4


def test_e5_directory_entry_ok(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [Sub](sub/) - subdir.\n* [Auth](/sub/auth.md) - a.\n",
        "docs/kb/sub/auth.md": PAGE,
    })
    assert codes(run_lint(tmp_path)) == []


def test_e6_broken_anchor(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n* [B2](/b.md) - b.\n",
        "docs/kb/a.md": page("See [setup](/b.md#setup) and [nope](/b.md#missing-part)."),
        "docs/kb/b.md": page("# Setup\n\ntext", title="B2"),
    })
    fs = [f for f in run_lint(tmp_path) if f.code == "E6"]
    assert len(fs) == 1
    assert "#missing-part" in fs[0].message


def test_e6_same_file_anchor_and_dedupe(tmp_path):
    body = "# Part\n\ntext\n\n# Part\n\n[one](#part) [two](#part-1) [bad](#part-2)"
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": page(body)})
    fs = [f for f in run_lint(tmp_path) if f.code == "E6"]
    assert len(fs) == 1 and "#part-2" in fs[0].message


def test_w5_wikilink(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX,
                         "docs/kb/auth.md": page("See [[Other Page]].")})
    fs = run_lint(tmp_path)
    assert codes(fs) == ["W5"]
    assert "[[Other Page]]" in fs[0].message


def test_frontmatter_value_is_not_a_body_link(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX,
        "docs/kb/auth.md": '---\ntype: Concept\ntitle: A\n'
                           'description: "see [x](/gone.md)"\n---\n\n# A\n\nbody\n',
    })
    assert [f for f in run_lint(tmp_path) if f.code == "E3"] == []


def test_frontmatter_value_is_not_a_wikilink(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX,
        "docs/kb/auth.md": '---\ntype: Concept\ntitle: A\n'
                           'description: "see [[Other Page]]"\n---\n\n# A\n\nbody\n',
    })
    assert [f for f in run_lint(tmp_path) if f.code == "W5"] == []


def test_frontmatter_comment_is_not_a_heading(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n* [B](/b.md) - b.\n",
        "docs/kb/a.md": page("Link [s](/b.md#setup)."),
        "docs/kb/b.md": '---\ntype: Concept\ntitle: B\ndescription: d.\n'
                        '# Setup\n---\n\n# Real\n\nx\n',
    })
    # #setup is broken: b.md has no "Setup" heading, only a YAML comment.
    assert [f for f in run_lint(tmp_path) if f.code == "E6"]


def test_slugify_github_style():
    assert M.slugify("Setup & Run") == "setup--run"
    assert M.slugify("Foo `bar` Baz") == "foo-bar-baz"
    assert M.slugify("With_underscore and-dash") == "with_underscore-and-dash"


# ---------- incoming references: E4 ----------

def bundle_two_pages(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n* [B2](/b.md) - b.\n",
        "docs/kb/a.md": page("Links [b](/b.md)."),
        "docs/kb/b.md": page("text", title="B2"),
    })
    return tmp_path


def test_e4_root_claude_md_broken_link(tmp_path):
    bundle_two_pages(tmp_path)
    write(tmp_path, "CLAUDE.md", "# P\n\nSee [arch](docs/kb/gone.md) and [ok](docs/kb/a.md).\n")
    fs = run_lint(tmp_path)
    assert codes(fs) == ["E4"]
    assert fs[0].path == "CLAUDE.md" and fs[0].line == 3


def test_e4_nested_claude_md_relative(tmp_path):
    bundle_two_pages(tmp_path)
    write(tmp_path, "sub/CLAUDE.md",
          "See [ok](../docs/kb/a.md) and [bad](../docs/kb/gone.md).\n")
    fs = run_lint(tmp_path)
    assert codes(fs) == ["E4"]
    assert fs[0].path == "sub/CLAUDE.md"


def test_e4_rules_file(tmp_path):
    bundle_two_pages(tmp_path)
    write(tmp_path, ".claude/rules/arch.md",
          "Read [a](../../docs/kb/a.md); avoid [x](../../docs/kb/x.md).\n")
    fs = run_lint(tmp_path)
    assert codes(fs) == ["E4"]
    assert fs[0].path == ".claude/rules/arch.md"


def test_e4_at_import_fallback_to_repo_root(tmp_path):
    bundle_two_pages(tmp_path)
    write(tmp_path, "sub/CLAUDE.md", "Import @docs/kb/a.md here.\n")
    assert codes(run_lint(tmp_path)) == []


def test_e4_at_import_broken(tmp_path):
    bundle_two_pages(tmp_path)
    write(tmp_path, "CLAUDE.md", "Import @docs/kb/gone.md.\n")
    fs = run_lint(tmp_path)
    assert codes(fs) == ["E4"]
    assert "@docs/kb/gone.md" in fs[0].message


def test_incoming_skips_external_absolute_home_and_non_bundle(tmp_path):
    bundle_two_pages(tmp_path)
    write(tmp_path, "CLAUDE.md",
          "[web](https://x.y) [abs](/docs/kb/gone.md) [out](README.md) @~/.claude/g.md @README.md\n")
    write(tmp_path, "README.md", "readme\n")
    assert codes(run_lint(tmp_path)) == []


def test_incoming_skips_superpowers_and_node_modules(tmp_path):
    bundle_two_pages(tmp_path)
    write(tmp_path, "docs/superpowers/specs/CLAUDE.md", "[bad](../../kb/gone.md)\n")
    write(tmp_path, "node_modules/pkg/CLAUDE.md", "[bad](../../docs/kb/gone.md)\n")
    assert codes(run_lint(tmp_path)) == []


def test_e6_incoming_anchor(tmp_path):
    bundle_two_pages(tmp_path)
    write(tmp_path, "CLAUDE.md", "See [a](docs/kb/a.md#nope).\n")
    fs = run_lint(tmp_path)
    assert codes(fs) == ["E6"]


# ---------- graph warnings: W1 / W2 / W4 ----------

def test_w1_page_not_in_any_index(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n",
        "docs/kb/a.md": page("Links [b](/b.md)."),
        "docs/kb/b.md": page("text", title="B2"),
    })
    fs = run_lint(tmp_path)
    assert codes(fs) == ["W1"]
    assert fs[0].path == "docs/kb/b.md"


def test_w1_subdir_index_counts(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [Sub](sub/) - s.\n",
        "docs/kb/sub/index.md": "# S\n\n* [C](/sub/c.md) - c.\n",
        "docs/kb/sub/c.md": page("text", title="C"),
    })
    assert codes(run_lint(tmp_path)) == []


def test_w2_orphan_page(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n",
        "docs/kb/a.md": page("no outgoing"),
        "docs/kb/b.md": page("text", title="B2"),
    })
    fs = run_lint(tmp_path)
    assert codes(fs) == ["W1", "W2"]
    assert {f.path for f in fs} == {"docs/kb/b.md"}


def test_w2_claude_md_link_prevents_orphan(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n",
        "docs/kb/a.md": page("no outgoing"),
        "docs/kb/b.md": page("text", title="B2"),
        "CLAUDE.md": "See @docs/kb/b.md for details.\n",
    })
    assert codes(run_lint(tmp_path)) == ["W1"]


def test_w4_non_iso_date(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE,
        "docs/kb/log.md": "# Log\n\n## July 2, 2026\n* **Update**: x.\n",
    })
    fs = run_lint(tmp_path)
    assert codes(fs) == ["W4"]
    assert "ISO" in fs[0].message and fs[0].line == 3


def test_w4_ascending_order(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE,
        "docs/kb/log.md": "# Log\n\n## 2026-06-01\n* **A**: x.\n\n## 2026-07-02\n* **B**: y.\n",
    })
    fs = run_lint(tmp_path)
    assert codes(fs) == ["W4"]
    assert fs[0].line == 6


def test_w4_valid_log_descending(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE,
        "docs/kb/log.md": "# Log\n\n## 2026-07-02\n* **B**: y.\n\n## 2026-06-01\n* **A**: x.\n",
    })
    assert codes(run_lint(tmp_path)) == []


def test_w4_ignores_frontmatter_hash_line(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE,
        "docs/kb/log.md": "---\ntitle: Log\n## not a date\n---\n\n# Log\n\n"
                          "## 2026-07-02\n* **B**: y.\n",
    })
    assert [f for f in run_lint(tmp_path) if f.code == "W4"] == []


def test_empty_bundle_only_index_is_clean(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": "# Empty\n"})
    assert codes(run_lint(tmp_path)) == []


# ---------- hook adapter: okf_gate.py ----------


def run_gate(payload: dict, cwd: Path, env_extra: dict | None = None):
    import os
    env = dict(os.environ)
    env.pop("SKIP_OKF_LINT", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["python3", str(GATE)], input=json.dumps(payload),
                          capture_output=True, text=True, cwd=str(cwd), env=env)


def post_edit(fp: Path, cwd: Path) -> dict:
    return {"hook_event_name": "PostToolUse", "cwd": str(cwd),
            "tool_name": "Edit", "tool_input": {"file_path": str(fp)}}


def pre_bash(command: str, cwd: Path) -> dict:
    return {"hook_event_name": "PreToolUse", "cwd": str(cwd),
            "tool_name": "Bash", "tool_input": {"command": command}}


def broken_bundle(tmp_path) -> Path:
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX,
        "docs/kb/auth.md": page("See [gone](/gone.md)."),
    })
    return tmp_path


def test_gate_noop_when_no_bundle(tmp_path):
    write(tmp_path, "CLAUDE.md", "# p\n")
    r = run_gate(post_edit(tmp_path / "CLAUDE.md", tmp_path), tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == "" and r.stderr.strip() == ""


def test_gate_post_edit_bundle_error_exit_2(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(post_edit(tmp_path / "docs/kb/auth.md", tmp_path), tmp_path)
    assert r.returncode == 2
    assert "E3" in r.stderr and "docs/kb/auth.md" in r.stderr


def test_gate_post_edit_irrelevant_file_noop(tmp_path):
    broken_bundle(tmp_path)
    write(tmp_path, "src/app.py", "print()\n")
    r = run_gate(post_edit(tmp_path / "src/app.py", tmp_path), tmp_path)
    assert r.returncode == 0 and r.stderr.strip() == ""


def test_gate_post_edit_claude_md_and_rules_relevant(tmp_path):
    broken_bundle(tmp_path)
    write(tmp_path, "CLAUDE.md", "# p\n")
    write(tmp_path, ".claude/rules/x.md", "rule\n")
    assert run_gate(post_edit(tmp_path / "CLAUDE.md", tmp_path), tmp_path).returncode == 2
    assert run_gate(post_edit(tmp_path / ".claude/rules/x.md", tmp_path), tmp_path).returncode == 2


def test_gate_post_edit_warnings_only_exit_0(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX,
        "docs/kb/auth.md": "---\ntype: Concept\n---\nbody\n",  # W3 only
    })
    r = run_gate(post_edit(tmp_path / "docs/kb/auth.md", tmp_path), tmp_path)
    assert r.returncode == 0


def test_gate_pr_create_blocked_on_findings(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("gh pr create --fill", tmp_path), tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "E3" in hso["permissionDecisionReason"]
    assert "SKIP_OKF_LINT=1" in hso["permissionDecisionReason"]


def test_gate_pr_create_blocked_on_warnings_strict(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": ROOT_INDEX,
        "docs/kb/auth.md": "---\ntype: Concept\n---\nbody\n",  # W3 only
    })
    r = run_gate(pre_bash("gh pr create --fill", tmp_path), tmp_path)
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_gate_pr_create_clean_passes(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE})
    r = run_gate(pre_bash("gh pr create --fill", tmp_path), tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_gate_pr_create_bypass_env(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("gh pr create --fill", tmp_path), tmp_path,
                 env_extra={"SKIP_OKF_LINT": "1"})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_gate_non_pr_and_harmless_commands_noop(tmp_path):
    broken_bundle(tmp_path)
    for cmd in ("git status", 'echo "gh pr create"', "gh pr create --dry-run"):
        r = run_gate(pre_bash(cmd, tmp_path), tmp_path)
        assert r.returncode == 0 and r.stdout.strip() == "", cmd


def test_gate_malformed_payload_noop(tmp_path):
    broken_bundle(tmp_path)
    r = subprocess.run(["python3", str(GATE)], input="not json",
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0


def test_hooks_json_wires_gate():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    post = hooks["hooks"]["PostToolUse"][0]
    pre = hooks["hooks"]["PreToolUse"][0]
    assert post["matcher"] == "Edit|Write"
    assert pre["matcher"] == "Bash"
    for group in (post, pre):
        cmd = group["hooks"][0]["command"]
        assert "${CLAUDE_PLUGIN_ROOT}/hooks/okf_gate.py" in cmd


# ---------- hardening: malformed config / empty anchor ----------

def test_find_bundle_root_non_object_config(tmp_path):
    make_repo(tmp_path, {".claude/okf-docs.json": '["docs/kb"]',
                         "docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE})
    assert M.find_bundle_root(tmp_path) == (tmp_path / "docs/kb").resolve()


def test_find_bundle_root_non_str_bundle_root(tmp_path):
    make_repo(tmp_path, {".claude/okf-docs.json": '{"bundle_root": ["x"]}',
                         "docs/kb/index.md": ROOT_INDEX, "docs/kb/auth.md": PAGE})
    assert M.find_bundle_root(tmp_path) == (tmp_path / "docs/kb").resolve()


def test_empty_anchor_not_flagged(tmp_path):
    make_repo(tmp_path, {"docs/kb/index.md": ROOT_INDEX,
                         "docs/kb/auth.md": page("Back to [top](#).")})
    assert [f for f in run_lint(tmp_path) if f.code == "E6"] == []


def test_gate_malformed_config_no_traceback(tmp_path):
    broken_bundle(tmp_path)
    write(tmp_path, ".claude/okf-docs.json", '["docs/kb"]')
    r = run_gate(post_edit(tmp_path / "docs/kb/auth.md", tmp_path), tmp_path)
    assert "Traceback" not in r.stderr
    assert r.returncode == 2


# ---------- review fixes: gate fail-open, robustness, matching, containment ----------

def test_gate_run_lint_failopen_without_linter_marker(monkeypatch, tmp_path):
    import types
    monkeypatch.setattr(G.shutil, "which", lambda _name: "/usr/bin/uv")
    fake = types.SimpleNamespace(
        returncode=1,
        stdout="x No solution found when resolving script dependencies: pyyaml",
        stderr="")
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **k: fake)
    rc, _out = G.run_lint(tmp_path, strict=False)
    assert rc == 2  # uv failed to launch the linter -> fail open, not "errors"


def test_gate_run_lint_trusts_exit1_with_marker(monkeypatch, tmp_path):
    import types
    monkeypatch.setattr(G.shutil, "which", lambda _name: "/usr/bin/uv")
    fake = types.SimpleNamespace(
        returncode=1,
        stdout="docs/kb/a.md:7: E3 broken\nokf-lint: 1 errors, 0 warnings in docs/kb",
        stderr="")
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **k: fake)
    rc, _out = G.run_lint(tmp_path, strict=False)
    assert rc == 1


def test_target_exists_survives_oserror():
    assert M.target_exists(Path("/" + "a" * 5000 + ".md")) is False


def test_cli_overlong_link_is_e3_not_internal_error(tmp_path):
    make_repo(tmp_path, {
        "docs/kb/index.md": "# B\n\n* [A](/a.md) - a.\n",
        "docs/kb/a.md": page("See [bad](/" + "a" * 500 + ".md)."),
    })
    r = run_cli(tmp_path, "--root", str(tmp_path))
    assert r.returncode == 1  # was 2 (internal error) before the OSError guard
    assert "E3" in r.stdout


def test_gate_detects_env_prefixed_and_subshell_pr_create():
    assert G.is_gh_pr_create(G.normalize_command("GH_TOKEN=x gh pr create")) is True
    assert G.is_gh_pr_create(G.normalize_command("FOO=1 BAR=2 gh pr create --fill")) is True
    assert G.is_gh_pr_create(G.normalize_command("(gh pr create --fill)")) is True
    assert G.is_gh_pr_create(G.normalize_command("gh pr create")) is True
    assert G.is_gh_pr_create(G.normalize_command("git status")) is False


def test_gate_has_inline_skip():
    assert G.has_inline_skip("SKIP_OKF_LINT=1 gh pr create") is True
    assert G.has_inline_skip('gh pr create --title "SKIP_OKF_LINT=1"') is False
    assert G.has_inline_skip("gh pr create") is False


def test_gate_pr_create_blocked_env_prefixed(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("GH_TOKEN=x gh pr create --fill", tmp_path), tmp_path)
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_gate_pr_create_inline_skip_bypass(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("SKIP_OKF_LINT=1 gh pr create --fill", tmp_path), tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_find_bundle_root_rejects_relative_outside_repo(tmp_path):
    outside = tmp_path.parent / "outside_secret"
    outside.mkdir(exist_ok=True)
    (outside / "leak.md").write_text("secret\n", encoding="utf-8")
    make_repo(tmp_path, {".claude/okf-docs.json": '{"bundle_root": "../outside_secret"}'})
    assert M.find_bundle_root(tmp_path) is None


def test_find_bundle_root_rejects_absolute_outside_repo(tmp_path):
    make_repo(tmp_path, {".claude/okf-docs.json": '{"bundle_root": "/etc"}'})
    assert M.find_bundle_root(tmp_path) is None


def test_gate_bundle_rel_rejects_outside_repo(tmp_path):
    outside = tmp_path.parent / "outside_secret2"
    outside.mkdir(exist_ok=True)
    make_repo(tmp_path, {".claude/okf-docs.json": '{"bundle_root": "../outside_secret2"}'})
    assert G.bundle_rel(tmp_path.resolve()) is None


def test_relevant_file_normalizes_bundle_spelling(tmp_path):
    repo = tmp_path.resolve()
    (repo / "docs/kb").mkdir(parents=True)
    f = repo / "docs/kb/auth.md"
    for spelling in ("docs/kb", "docs/kb/", "./docs/kb"):
        assert G.relevant_file(str(f), repo, spelling) is True, spelling
    assert G.relevant_file(str(repo / "src/app.py"), repo, "docs/kb") is False


def test_gate_post_edit_relevant_with_trailing_slash_config(tmp_path):
    broken_bundle(tmp_path)
    write(tmp_path, ".claude/okf-docs.json", '{"bundle_root": "docs/kb/"}')
    r = run_gate(post_edit(tmp_path / "docs/kb/auth.md", tmp_path), tmp_path)
    assert r.returncode == 2  # feedback fires; was a silent no-op before the fix


def test_gate_has_future_annotations():
    assert "from __future__ import annotations" in GATE.read_text(encoding="utf-8")


# ---------- review fixes: PR-gate command matching ----------

def _denied(r) -> bool:
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_gate_matcher_detects_real_pr_create_variants():
    for cmd in (
        "gh pr create",
        "gh pr create --fill",
        "gh -R owner/repo pr create --fill",
        "gh --repo owner/repo pr create",
        "env GH_TOKEN=x gh pr create --fill",
        "/usr/bin/gh pr create",
        "git push -u origin feat\ngh pr create --fill",
        "sleep 1 & gh pr create --fill",
        "(gh pr create --fill)",
    ):
        assert G.is_gh_pr_create(G.normalize_command(cmd)) is True, cmd


def test_gate_matcher_ignores_non_pr_create():
    for cmd in (
        "git status",
        'echo "gh pr create"',
        'git commit -m "gh pr create"',
    ):
        assert G.is_gh_pr_create(G.normalize_command(cmd)) is False, cmd


def test_gate_inline_skip_only_as_env_prefix_of_pr_create():
    assert G.has_inline_skip("SKIP_OKF_LINT=1 gh pr create --fill") is True
    assert G.has_inline_skip("gh pr create --fill  # SKIP_OKF_LINT=1") is False
    assert G.has_inline_skip("echo SKIP_OKF_LINT=1 && gh pr create --fill") is False
    assert G.has_inline_skip("git config x SKIP_OKF_LINT=1; gh pr create --fill") is False
    assert G.has_inline_skip('gh pr create --title "SKIP_OKF_LINT=1"') is False


def test_gate_newline_separated_pr_create_is_blocked(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("git push -u origin feat\ngh pr create --fill", tmp_path), tmp_path)
    assert _denied(r)


def test_gate_flag_before_subcommand_is_blocked(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("gh -R owner/repo pr create --fill", tmp_path), tmp_path)
    assert _denied(r)


def test_gate_chained_harmless_then_real_create_is_blocked(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("gh pr create --dry-run && gh pr create --fill", tmp_path), tmp_path)
    assert _denied(r)


def test_gate_help_substring_is_not_harmless(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("gh pr create --fill --label help-wanted--help", tmp_path), tmp_path)
    assert _denied(r)


def test_gate_skip_token_in_comment_does_not_bypass(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("gh pr create --fill  # SKIP_OKF_LINT=1", tmp_path), tmp_path)
    assert _denied(r)


def test_gate_skip_token_on_other_segment_does_not_bypass(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("echo SKIP_OKF_LINT=1 && gh pr create --fill", tmp_path), tmp_path)
    assert _denied(r)


def test_gate_quoted_pr_create_literal_is_not_a_command(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash('echo "\\" && gh pr create"', tmp_path), tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


# ---------- pre-PR review fixes: shell-accurate, fail-closed matching ----------

def test_gate_matcher_handles_quoted_newlines_and_wrappers():
    assert G.is_gh_pr_create(G.normalize_command('gh pr create --body "a\nb"')) is True
    assert G.is_gh_pr_create(G.normalize_command("command gh pr create")) is True
    # --dry-run as an option VALUE (not a flag) must not clear the gate:
    assert G.gated_pr_create('gh pr create --title "--dry-run" --fill') is True
    # a genuine --dry-run flag is harmless:
    assert G.gated_pr_create("gh pr create --dry-run") is False


def test_gate_multiline_body_is_still_gated(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash('gh pr create --title "T" --body "line one\nline two"', tmp_path), tmp_path)
    assert _denied(r)


def test_gate_body_with_command_substitution_heredoc_is_gated(tmp_path):
    broken_bundle(tmp_path)
    cmd = ('gh pr create --fill --body "$(cat <<\'EOF\'\n'
           'hello\n'
           'EOF\n'
           ')"')
    r = run_gate(pre_bash(cmd, tmp_path), tmp_path)
    assert _denied(r)


def test_gate_unbalanced_quote_fails_closed(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash('gh pr create --title "unterminated', tmp_path), tmp_path)
    assert _denied(r)


def test_gate_midword_hash_is_not_a_comment(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("echo x#y && gh pr create --fill", tmp_path), tmp_path)
    assert _denied(r)


def test_gate_quoted_heredoc_marker_does_not_hide_pr_create(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash('echo "see << STOP"\ngh pr create --fill\nSTOP', tmp_path), tmp_path)
    assert _denied(r)


def test_gate_harmless_flag_as_option_value_is_gated(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash('gh pr create --title "-h" --fill', tmp_path), tmp_path)
    assert _denied(r)


def test_gate_command_wrapper_is_gated(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("command gh pr create --fill", tmp_path), tmp_path)
    assert _denied(r)


def test_gate_here_string_does_not_hide_following_pr_create(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash('jq . <<< "$response"\ngh pr create --fill', tmp_path), tmp_path)
    assert _denied(r)


def test_gate_env_option_flags_do_not_shield_command(tmp_path):
    broken_bundle(tmp_path)
    for cmd in ("env -i gh pr create --fill",
                "env -u FOO gh pr create --fill",
                "env -C /tmp gh pr create --fill"):
        r = run_gate(pre_bash(cmd, tmp_path), tmp_path)
        assert _denied(r), cmd


def test_gate_env_split_string_is_gated(tmp_path):
    broken_bundle(tmp_path)
    for cmd in ("env -S 'gh pr create --title x'",
                "env --split-string='gh pr create'",
                "env -u FOO -C /tmp -S 'gh pr create --fill'",
                "env -S 'FOO=bar gh pr create'"):
        r = run_gate(pre_bash(cmd, tmp_path), tmp_path)
        assert _denied(r), cmd


def test_gate_env_bundled_short_options_are_gated(tmp_path):
    broken_bundle(tmp_path)
    for cmd in ("env -iS 'gh pr create --fill'",     # bundled -i + -S
                "env -viS 'gh pr create'",           # bundled -v -i -S
                "env -iu FOO gh pr create --fill",   # bundled -i + -u FOO
                "env -uFOO gh pr create --fill"):    # -u with attached value
        r = run_gate(pre_bash(cmd, tmp_path), tmp_path)
        assert _denied(r), cmd


def test_gate_nested_env_split_string_is_gated(tmp_path):
    broken_bundle(tmp_path)
    r = run_gate(pre_bash("env -S \"env -S 'gh pr create --fill'\"", tmp_path), tmp_path)
    assert _denied(r)


def test_gate_command_words_depth_cap_fails_closed():
    # at the unwrap cap the -S branch returns a synthetic gh-pr-create (stays gated,
    # so a crafted deep `env -S` nest can't overflow the stack into an exit-1 bypass)
    assert G._command_words(["env", "-S", "env -S x"], _depth=G._MAX_UNWRAP) == ([], ["gh", "pr", "create"])


def test_gate_parser_error_fails_closed(monkeypatch):
    def boom(_cmd):
        raise RuntimeError("parser blew up")
    monkeypatch.setattr(G, "normalize_command", boom)
    assert G.gated_pr_create("gh pr create") is True
