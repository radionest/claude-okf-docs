#!/usr/bin/env python3
"""Hook adapter for the okf-docs plugin (stdlib only).

PostToolUse(Edit|Write): if the edited file is in the bundle, is a CLAUDE.md,
or is under .claude/rules/, run a full lint; errors -> exit 2 with the report
on stderr so the agent fixes them immediately.

PreToolUse(Bash): on `gh pr create`, run a --strict lint; any finding -> JSON
permissionDecision deny. Bypass with SKIP_OKF_LINT=1.

Fails open (exit 0): no bundle, no uv on PATH, malformed payload, or linter
internal error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
LINT = PLUGIN_ROOT / "scripts" / "okf_lint.py"
DEFAULT_BUNDLE_ROOT = "docs/kb"
CONFIG_REL = ".claude/okf-docs.json"


def git_toplevel(cwd: str) -> Path | None:
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip())


def repo_root_for(cwd: str) -> Path:
    top = git_toplevel(cwd)
    return top.resolve() if top else Path(cwd).resolve()


def bundle_rel(repo_root: Path) -> str | None:
    """Repo-relative bundle root, or None when the project has no bundle."""
    config = repo_root / CONFIG_REL
    if config.is_file():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        rel_root = data.get("bundle_root")
        if not isinstance(rel_root, str) or not rel_root.strip():
            rel_root = DEFAULT_BUNDLE_ROOT
        if Path(rel_root).parts[:2] == ("docs", "superpowers"):
            return None
        resolved = (repo_root / rel_root).resolve()
        if not resolved.is_relative_to(repo_root):
            return None
        return rel_root if resolved.is_dir() else None
    if (repo_root / DEFAULT_BUNDLE_ROOT / "index.md").is_file():
        return DEFAULT_BUNDLE_ROOT
    return None


def run_lint(repo_root: Path, strict: bool) -> tuple[int, str]:
    """(returncode, combined output); returncode -1 means uv is unavailable."""
    if shutil.which("uv") is None:
        return -1, ""
    cmd = ["uv", "run", "--script", str(LINT), "--root", str(repo_root)]
    if strict:
        cmd.append("--strict")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except (subprocess.TimeoutExpired, OSError) as e:
        return 2, f"okf-lint could not run: {e}"
    out = r.stdout.strip()
    if r.stderr.strip():
        out = (out + "\n" + r.stderr.strip()).strip()
    if "okf-lint:" not in out:
        # uv launched but the linter never emitted its summary line (e.g. it
        # could not resolve pyyaml offline); the exit code is uv's, not the
        # linter's contract, so treat it as an internal error and fail open.
        return 2, out or "okf-lint did not run (uv could not launch the script)"
    return r.returncode, out


def _strip_strings(cmd: str) -> str:
    cmd = re.sub(r"'[^']*'", "", cmd)
    return re.sub(r'"[^"]*"', "", cmd)


ENV_PREFIX = re.compile(r"^(?:\w+=\S*\s+)+")
SUBSHELL_PREFIX = re.compile(r"^\$?\(\s*")


def normalize_command(cmd: str) -> list[str]:
    parts = re.split(r"&&|\|\||[;|]", _strip_strings(cmd))
    out: list[str] = []
    for part in parts:
        seg = ENV_PREFIX.sub("", SUBSHELL_PREFIX.sub("", part.strip())).strip()
        if seg:
            out.append(seg)
    return out


def is_gh_pr_create(lines: list[str]) -> bool:
    pattern = re.compile(r"^\s*gh\s+pr\s+create\b")
    return any(pattern.search(line) for line in lines)


def is_harmless_variant(lines: list[str]) -> bool:
    pattern = re.compile(r"^\s*gh\s+pr\s+create\b.*(--help|--dry-run)")
    return any(pattern.search(line) for line in lines)


def has_inline_skip(cmd: str) -> bool:
    return re.search(r"\bSKIP_OKF_LINT=1\b", _strip_strings(cmd)) is not None


def relevant_file(fp: str, repo_root: Path, bundle: str) -> bool:
    p = Path(fp)
    if not p.is_absolute():
        p = repo_root / p
    try:
        resolved = p.resolve()
        rel_path = resolved.relative_to(repo_root)
    except (ValueError, OSError):
        return False
    if rel_path.name == "CLAUDE.md" or rel_path.as_posix().startswith(".claude/rules/"):
        return True
    try:
        return resolved.is_relative_to((repo_root / bundle).resolve())
    except (ValueError, OSError):
        return False


def handle_post_tool_use(payload: dict, repo_root: Path, bundle: str) -> int:
    fp = (payload.get("tool_input") or {}).get("file_path", "")
    if not fp or not relevant_file(fp, repo_root, bundle):
        return 0
    rc, out = run_lint(repo_root, strict=False)
    if rc == 1:
        print(f"okf-docs lint found errors after this edit (paths relative to {repo_root}):\n"
              f"\n{out}\n\nFix these findings now, before moving on.", file=sys.stderr)
        return 2
    if rc == 2:
        print(f"okf-docs: linter internal error (non-blocking):\n{out}", file=sys.stderr)
    return 0


def handle_pre_tool_use(payload: dict, repo_root: Path) -> int:
    command = (payload.get("tool_input") or {}).get("command", "")
    if not command:
        return 0
    lines = normalize_command(command)
    if not is_gh_pr_create(lines) or is_harmless_variant(lines):
        return 0
    if os.environ.get("SKIP_OKF_LINT") == "1" or has_inline_skip(command):
        return 0
    rc, out = run_lint(repo_root, strict=True)
    if rc == 2:
        print(f"okf-docs: linter internal error (PR gate skipped):\n{out}", file=sys.stderr)
        return 0
    if rc != 1:
        return 0
    reason = ("BLOCKED: gh pr create requires a clean okf-docs lint (--strict; "
              "warnings count).\n\n" + out +
              "\n\nFix the findings (or run /okf-docs:check), commit, then retry "
              "gh pr create.\n\nEmergency bypass: SKIP_OKF_LINT=1 gh pr create ...")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    repo_root = repo_root_for(cwd)
    bundle = bundle_rel(repo_root)
    if bundle is None:
        return 0
    event = payload.get("hook_event_name", "")
    if event == "PostToolUse":
        return handle_post_tool_use(payload, repo_root, bundle)
    if event == "PreToolUse":
        return handle_pre_tool_use(payload, repo_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
