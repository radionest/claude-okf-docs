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
        if not isinstance(data, dict):
            data = {}
        rel_root = data.get("bundle_root")
        if not isinstance(rel_root, str) or not rel_root.strip():
            rel_root = DEFAULT_BUNDLE_ROOT
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


# ---------- links ----------

FENCE = re.compile(r"^\s*(```|~~~)")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKILINK = re.compile(r"\[\[[^\]]+\]\]")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def strip_code(text: str) -> list[str]:
    """Blank fenced blocks and inline code spans; keep the line count."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence:
            out.append("")
            continue
        out.append(re.sub(r"`[^`]*`", "", line))
    return out


def extract_links(lines: list[str]) -> list[tuple[int, str]]:
    return [(i, m.group(1))
            for i, line in enumerate(lines, 1)
            for m in MD_LINK.finditer(line)]


def extract_wikilinks(lines: list[str]) -> list[tuple[int, str]]:
    return [(i, m.group(0))
            for i, line in enumerate(lines, 1)
            for m in WIKILINK.finditer(line)]


def classify_target(target: str) -> tuple[str, str, str]:
    """(kind, path, fragment); kind: external | anchor | absolute | relative."""
    if SCHEME.match(target) or target.startswith("//"):
        return "external", "", ""
    path, _, frag = target.partition("#")
    if not path:
        return "anchor", "", frag
    if path.startswith("/"):
        return "absolute", path, frag
    return "relative", path, frag


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[`*]", "", s)
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s", "-", s.strip())  # each whitespace char -> '-', GitHub-style


def heading_slugs(text: str) -> set[str]:
    seen: dict[str, int] = {}
    slugs: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING.match(line)
        if not m:
            continue
        base = slugify(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        slugs.add(base if n == 0 else f"{base}-{n}")
    return slugs


def target_exists(cand: Path) -> bool:
    return cand.is_file() or cand.is_dir()


def check_anchor(src_rel: str, line: int, frag: str, target_file: Path,
                 texts: dict[Path, str], repo_root: Path) -> Finding | None:
    if not frag:
        return None
    text = texts.get(target_file)
    if text is None:
        text, error = read_text(target_file)
        if error:
            return None
        texts[target_file] = text
    if slugify(frag) in heading_slugs(text):
        return None
    return Finding(src_rel, line, "E6",
                   f"broken anchor '#{frag}' in link to '{rel(target_file, repo_root)}': no such heading")


def check_bundle_links(repo_root: Path, bundle_root: Path, texts: dict[Path, str],
                       pages: list[Path], indexes: list[Path], logs: list[Path],
                       ) -> tuple[list[Finding], set[Path], set[Path]]:
    """Findings E3/E5/E6/W5 + (linked_targets, listed_by_index) resolved sets."""
    findings: list[Finding] = []
    linked_targets: set[Path] = set()
    listed_by_index: set[Path] = set()

    for src in pages + logs + indexes:
        text = texts.get(src)
        if text is None:
            continue
        src_rel = rel(src, repo_root)
        is_index = src.name == "index.md"
        code = "E5" if is_index else "E3"
        noun = "index entry links to" if is_index else "broken link:"
        lines = strip_code(text)

        for line_no, raw in extract_wikilinks(lines):
            findings.append(Finding(src_rel, line_no, "W5",
                                    f"wikilink '{raw}' is not allowed (use markdown links)"))

        for line_no, target in extract_links(lines):
            kind, path_part, frag = classify_target(target)
            if kind == "external":
                continue
            if kind == "anchor":
                f = check_anchor(src_rel, line_no, frag, src, texts, repo_root)
                if f:
                    findings.append(f)
                continue
            if kind == "absolute":
                cand = (bundle_root / path_part.lstrip("/")).resolve()
            else:
                cand = (src.parent / path_part).resolve()
            if not cand.is_relative_to(bundle_root):
                continue  # out of jurisdiction
            if not target_exists(cand):
                findings.append(Finding(src_rel, line_no, code,
                                        f"{noun} '{target}': target does not exist in bundle"))
                continue
            linked_targets.add(cand)
            if is_index:
                listed_by_index.add(cand)
            if frag and cand.is_file() and cand.suffix == ".md":
                f = check_anchor(src_rel, line_no, frag, cand, texts, repo_root)
                if f:
                    findings.append(f)

    return findings, linked_targets, listed_by_index


# ---------- incoming references (CLAUDE.md / .claude/rules) ----------

AT_IMPORT = re.compile(r"(?:^|(?<=\s))@([\w~./][^\s`)\]\"']*)")


def extract_at_imports(lines: list[str]) -> list[tuple[int, str]]:
    found = []
    for i, line in enumerate(lines, 1):
        for m in AT_IMPORT.finditer(line):
            path = m.group(1).rstrip(".,;:!?")
            if path:
                found.append((i, path))
    return found


def incoming_files(repo_root: Path) -> list[Path]:
    files: set[Path] = set()
    for p in repo_root.rglob("CLAUDE.md"):
        parts = p.relative_to(repo_root).parts
        if any(part.startswith(".") for part in parts[:-1]):
            continue
        if "node_modules" in parts or parts[:2] == ("docs", "superpowers"):
            continue
        files.add(p)
    rules = repo_root / ".claude" / "rules"
    if rules.is_dir():
        files.update(rules.glob("*.md"))
    return sorted(files)


def check_incoming(repo_root: Path, bundle_root: Path, texts: dict[Path, str],
                   ) -> tuple[list[Finding], set[Path]]:
    """E4 for broken bundle references in CLAUDE.md/rules; E6 for their anchors."""
    findings: list[Finding] = []
    incoming_targets: set[Path] = set()

    for src in incoming_files(repo_root):
        text, error = read_text(src)
        if error:
            continue  # CLAUDE.md health is out of jurisdiction
        src_rel = rel(src, repo_root)
        lines = strip_code(text)

        for line_no, target in extract_links(lines):
            kind, path_part, frag = classify_target(target)
            if kind != "relative":
                continue  # external/anchor/leading-'/' have no bundle semantics here
            cand = (src.parent / path_part).resolve()
            if not cand.is_relative_to(bundle_root):
                continue
            if not target_exists(cand):
                findings.append(Finding(src_rel, line_no, "E4",
                                        f"broken reference into bundle: '{target}' does not exist"))
                continue
            incoming_targets.add(cand)
            if frag and cand.is_file() and cand.suffix == ".md":
                f = check_anchor(src_rel, line_no, frag, cand, texts, repo_root)
                if f:
                    findings.append(f)

        for line_no, imp in extract_at_imports(lines):
            if imp.startswith("~"):
                continue
            candidates = [(src.parent / imp).resolve(), (repo_root / imp).resolve()]
            existing = [c for c in candidates if target_exists(c)]
            if existing:
                incoming_targets.update(c for c in existing if c.is_relative_to(bundle_root))
                continue
            if any(c.is_relative_to(bundle_root) for c in candidates):
                findings.append(Finding(src_rel, line_no, "E4",
                                        f"broken @-import '@{imp}': target does not exist"))

    return findings, incoming_targets


# ---------- log.md structure ----------

LOG_HEADING = re.compile(r"^##\s+(.+?)\s*$")


def check_log(log_path: Path, text: str, repo_root: Path) -> list[Finding]:
    from datetime import date

    findings: list[Finding] = []
    p = rel(log_path, repo_root)
    prev: date | None = None
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = LOG_HEADING.match(line)
        if not m:
            continue
        raw = m.group(1)
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            findings.append(Finding(p, i, "W4",
                                    f"log heading '{raw}' is not an ISO date (YYYY-MM-DD)"))
            continue
        if prev is not None and d >= prev:
            findings.append(Finding(p, i, "W4",
                                    f"log dates out of descending order: {d} appears after {prev}"))
        prev = d
    return findings


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

    for page_path in pages:
        if page_path in texts:
            findings.extend(check_page_frontmatter(page_path, texts[page_path], repo_root))
    for index in indexes:
        if index in texts:
            findings.extend(check_index_frontmatter(index, texts[index], repo_root, bundle_root))
    for log in logs:
        if log in texts:
            findings.extend(check_log(log, texts[log], repo_root))

    link_findings, linked_targets, listed_by_index = check_bundle_links(
        repo_root, bundle_root, texts, pages, indexes, logs)
    findings.extend(link_findings)

    incoming_findings, incoming_targets = check_incoming(repo_root, bundle_root, texts)
    findings.extend(incoming_findings)

    referenced = linked_targets | incoming_targets
    for page_path in pages:
        if page_path not in texts:
            continue  # unreadable pages already reported as E1
        resolved = page_path.resolve()
        if resolved not in listed_by_index:
            findings.append(Finding(rel(page_path, repo_root), 1, "W1",
                                    "page is not listed in any index.md"))
        if resolved not in referenced:
            findings.append(Finding(rel(page_path, repo_root), 1, "W2",
                                    "orphan page: no incoming links from bundle, index, CLAUDE.md or rules"))

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
