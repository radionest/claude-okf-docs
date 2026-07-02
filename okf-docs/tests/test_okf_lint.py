"""Tests for okf_lint.py (linter) and okf_gate.py (hook adapter)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
LINT = PLUGIN / "scripts" / "okf_lint.py"


def load_lint():
    spec = importlib.util.spec_from_file_location("okf_lint", LINT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = load_lint()


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


def test_slugify_github_style():
    assert M.slugify("Setup & Run") == "setup--run"
    assert M.slugify("Foo `bar` Baz") == "foo-bar-baz"
    assert M.slugify("With_underscore and-dash") == "with_underscore-and-dash"
