#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Deterministic linter for a project's OKF v0.1 documentation bundle.

Checks bundle pages (frontmatter, links, index/log structure) and incoming
references from CLAUDE.md, **/CLAUDE.md and .claude/rules/*.md (markdown
links and @-imports). Findings print as `path:line: CODE message`.

Exit codes: 0 clean or no bundle; 1 findings (errors always, warnings with
--strict); 2 internal error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_BUNDLE_ROOT = "docs/kb"
CONFIG_REL = ".claude/okf-docs.json"
RESERVED = {"index.md", "log.md"}
FM_DELIM = re.compile(r"^---\s*$")


@dataclass
class Finding:
    path: str  # repo-root-relative, POSIX
    line: int
    code: str
    message: str

    @property
    def severity(self) -> str:
        return "error" if self.code.startswith("E") else "warning"

    def as_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "code": self.code,
                "severity": self.severity, "message": self.message}


# ---------- discovery ----------

def git_toplevel(cwd: Path) -> Path | None:
    try:
        r = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip())


def find_repo_root(cli_root: str | None) -> Path:
    if cli_root:
        return Path(cli_root).resolve()
    top = git_toplevel(Path.cwd())
    return top.resolve() if top else Path.cwd().resolve()


def _under_superpowers(path: Path, repo_root: Path) -> bool:
    try:
        return path.relative_to(repo_root).parts[:2] == ("docs", "superpowers")
    except ValueError:
        return False


def find_bundle_root(repo_root: Path) -> Path | None:
    """Absolute bundle root, or None when the project has no bundle.

    A bundle exists iff .claude/okf-docs.json names an existing directory,
    or the default docs/kb/index.md exists. docs/superpowers is never one.
    """
    config = repo_root / CONFIG_REL
    if config.is_file():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        rel_root = data.get("bundle_root") or DEFAULT_BUNDLE_ROOT
        bundle = (repo_root / rel_root).resolve()
        if _under_superpowers(bundle, repo_root) or not bundle.is_dir():
            return None
        return bundle
    bundle = (repo_root / DEFAULT_BUNDLE_ROOT).resolve()
    if (bundle / "index.md").is_file():
        return bundle
    return None


def collect_bundle(bundle_root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    pages, indexes, logs = [], [], []
    for p in sorted(bundle_root.rglob("*.md")):
        if p.name == "index.md":
            indexes.append(p)
        elif p.name == "log.md":
            logs.append(p)
        else:
            pages.append(p)
    return pages, indexes, logs


def rel(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


# ---------- parsing ----------

def read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError as e:
        return None, f"file is not valid UTF-8: {e.reason}"
    except OSError as e:
        return None, f"cannot read file: {e}"


def parse_frontmatter(text: str) -> tuple[dict | None, int, str | None, int]:
    """(meta, body_start_line, error, error_line).

    meta is None when there is no frontmatter block at all; error is set when
    a block opener exists but the block is unterminated or not a YAML mapping.
    """
    lines = text.splitlines()
    if not lines or not FM_DELIM.match(lines[0]):
        return None, 1, None, 1
    for i in range(1, len(lines)):
        if FM_DELIM.match(lines[i]):
            raw = "\n".join(lines[1:i])
            try:
                meta = yaml.safe_load(raw)
            except yaml.YAMLError as e:
                mark = getattr(e, "problem_mark", None)
                err_line = 2 + (mark.line if mark else 0)
                detail = getattr(e, "problem", None) or str(e).split("\n")[0]
                return None, i + 2, f"frontmatter is not valid YAML: {detail}", err_line
            if meta is None:
                meta = {}
            if not isinstance(meta, dict):
                return None, i + 2, "frontmatter is not a YAML mapping", 2
            return meta, i + 2, None, 1
    return None, len(lines) + 1, "unterminated frontmatter block ('---' never closed)", 1


# ---------- checks: frontmatter ----------

def check_page_frontmatter(page: Path, text: str, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    p = rel(page, repo_root)
    meta, _, error, err_line = parse_frontmatter(text)
    if error:
        return [Finding(p, err_line, "E1", error)]
    if meta is None:
        return [Finding(p, 1, "E1", "missing frontmatter (every bundle page needs a YAML block with 'type')")]
    type_val = meta.get("type")
    if not isinstance(type_val, str) or not type_val.strip():
        findings.append(Finding(p, 1, "E2", "frontmatter 'type' is missing or empty"))
    missing = [k for k in ("title", "description")
               if not isinstance(meta.get(k), str) or not str(meta.get(k)).strip()]
    if missing:
        findings.append(Finding(p, 1, "W3",
                                f"frontmatter missing recommended {' and '.join(repr(k) for k in missing)}"))
    return findings


def check_index_frontmatter(index: Path, text: str, repo_root: Path,
                            bundle_root: Path) -> list[Finding]:
    p = rel(index, repo_root)
    is_root = index.parent == bundle_root
    meta, _, error, err_line = parse_frontmatter(text)
    if error:
        return [Finding(p, err_line, "E1", error)]
    if meta is None:
        return []
    if not is_root:
        return [Finding(p, 1, "E1", "index.md outside the bundle root must not contain frontmatter")]
    if set(meta) != {"okf_version"}:
        return [Finding(p, 1, "E1",
                        "root index.md frontmatter may contain only 'okf_version'")]
    return []


# ---------- orchestrator ----------

def lint_bundle(repo_root: Path, bundle_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    pages, indexes, logs = collect_bundle(bundle_root)
    texts: dict[Path, str] = {}

    for f in pages + indexes + logs:
        text, error = read_text(f)
        if error:
            findings.append(Finding(rel(f, repo_root), 1, "E1", error))
            continue
        texts[f] = text

    for page in pages:
        if page in texts:
            findings.extend(check_page_frontmatter(page, texts[page], repo_root))
    for index in indexes:
        if index in texts:
            findings.extend(check_index_frontmatter(index, texts[index], repo_root, bundle_root))

    findings.sort(key=lambda f: (f.path, f.line, f.code))
    return findings


# ---------- output / CLI ----------

def format_text(findings: list[Finding], repo_root: Path, bundle_root: Path) -> str:
    lines = [f"{f.path}:{f.line}: {f.code} {f.message}" for f in findings]
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = len(findings) - errors
    lines.append(f"okf-lint: {errors} errors, {warnings} warnings in {rel(bundle_root, repo_root)}")
    return "\n".join(lines)


def format_json(findings: list[Finding], repo_root: Path, bundle_root: Path) -> str:
    errors = sum(1 for f in findings if f.severity == "error")
    return json.dumps({
        "bundle_root": rel(bundle_root, repo_root),
        "errors": errors,
        "warnings": len(findings) - errors,
        "findings": [f.as_dict() for f in findings],
    }, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lint the project's OKF docs bundle.")
    ap.add_argument("--root", help="repo root (default: git toplevel or cwd)")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--strict", action="store_true", help="warnings also fail (exit 1)")
    args = ap.parse_args(argv)

    repo_root = find_repo_root(args.root)
    bundle_root = find_bundle_root(repo_root)
    if bundle_root is None:
        print(f"okf-lint: no OKF bundle found under {repo_root} "
              f"(expected {DEFAULT_BUNDLE_ROOT}/index.md or {CONFIG_REL}); nothing to check.")
        return 0

    findings = lint_bundle(repo_root, bundle_root)
    if args.format == "json":
        print(format_json(findings, repo_root, bundle_root))
    else:
        print(format_text(findings, repo_root, bundle_root))

    has_errors = any(f.severity == "error" for f in findings)
    if has_errors or (args.strict and findings):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 -- exit 2 contract for internal errors
        print(f"okf-lint: internal error: {e}", file=sys.stderr)
        sys.exit(2)
