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
import shlex
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


_ASSIGN = re.compile(r"^\w+=")
SKIP_TOKEN = "SKIP_OKF_LINT=1"
# Transparent prefixes that run their trailing argv as the real command.
_WRAPPERS = {"env", "command"}
# env option flags that consume a value: short (`-u NAME`/`-uNAME`) and long.
_WRAPPER_VALUE_SHORT = "uC"
_WRAPPER_VALUE_LONG = {"--unset", "--chdir"}
# Cap on `env -S`/`--split-string` unwrap recursion (crafted deep nests fail closed).
_MAX_UNWRAP = 24
# `gh pr create` flags that consume the next token as a value, so a value that
# happens to look like --help/--dry-run is not mistaken for that flag.
_VALUE_FLAGS = {"-t", "--title", "-b", "--body", "-F", "--body-file",
                "-H", "--head", "-B", "--base", "-l", "--label", "-a", "--assignee",
                "-r", "--reviewer", "-m", "--milestone", "-p", "--project",
                "-T", "--template"}
_HARMLESS_FLAGS = {"--help", "-h", "--dry-run"}


def _split_statements(cmd: str) -> list[str]:
    """Quote/escape-aware split of a shell command into statement strings.

    Statements break on unquoted `; & | && ||` and newlines and around `( )`;
    unquoted `#` comments are dropped; quoted newlines (multi-line `--body`,
    `"$(… )"`) stay inside their statement. Redirections — including here-docs
    and here-strings (`<<`, `<<<`) — are left inline as ordinary tokens, so a
    here-doc body is parsed as commands too: the gate over-matches rather than
    ever failing open. Total and best-effort — malformed input never raises.
    """
    statements: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    continued = False

    def flush() -> None:
        s = "".join(cur).strip()
        if s:
            statements.append(s)
        cur.clear()

    for line in cmd.split("\n"):
        if quote is not None:
            cur.append("\n")  # a newline inside an open quote is literal
        i, length = 0, len(line)
        while i < length:
            c = line[i]
            if quote is not None:
                cur.append(c)
                if c == quote:
                    quote = None
                elif c == "\\" and quote == '"' and i + 1 < length:
                    cur.append(line[i + 1])
                    i += 2
                    continue
                i += 1
                continue
            if c == "\\":
                if i + 1 < length:
                    cur.append(c)
                    cur.append(line[i + 1])
                    i += 2
                else:
                    continued = True
                    cur.append(" ")
                    i += 1
                continue
            if c in "'\"":
                quote = c
                cur.append(c)
                i += 1
                continue
            if c == "#" and (i == 0 or line[i - 1] in " \t"):
                break  # comment runs to end of line
            if c in "&|;()":
                flush()
                i += 2 if line[i:i + 2] in ("&&", "||") else 1
                continue
            cur.append(c)
            i += 1
        if quote is not None:
            continue  # statement continues on the next physical line
        if continued:
            continued = False
            continue
        flush()
    flush()
    return statements


def _split_argv(stmt: str) -> list[str]:
    """Tokenize one statement into argv; on unbalanced quotes fall back to a
    naive split so a malformed `gh pr create` still fails closed."""
    try:
        return shlex.split(stmt, posix=True)
    except ValueError:
        return stmt.split()


def normalize_command(cmd: str) -> list[list[str]]:
    """Shell command -> statements, each a list of argv word tokens."""
    return [_split_argv(stmt) for stmt in _split_statements(cmd)]


def _command_words(words: list[str], _depth: int = 0) -> tuple[list[str], list[str]]:
    """Split a statement into (leading VAR=val assignments, the actual argv).

    Strips transparent wrappers (`env`, `command`) and their option flags —
    including bundled short clusters (`env -iS ...`, `env -iu NAME`) — so the
    real argv[0] is exposed. `env -S "<cmd>"` / `--split-string` is re-tokenized
    (its value is itself the command) and recursed, bounded by _MAX_UNWRAP so a
    crafted deep nest fails closed rather than overflowing the stack.
    """
    i = 0
    assigns: list[str] = []
    while i < len(words):
        w = words[i]
        if _ASSIGN.match(w):
            assigns.append(w)
            i += 1
            continue
        if w not in _WRAPPERS and w.rpartition("/")[2] not in _WRAPPERS:
            break
        i += 1  # skip the wrapper name
        while i < len(words) and words[i].startswith("-") and words[i] != "-":
            tok = words[i]
            split_val = None      # re-tokenized command from -S/--split-string
            consume_next = False  # option takes the following word as its value
            if tok.startswith("--"):
                name, sep, val = tok.partition("=")
                if name == "--split-string":
                    split_val = (_split_argv(val) + words[i + 1:]) if sep else \
                        ((_split_argv(words[i + 1]) if i + 1 < len(words) else []) + words[i + 2:])
                elif name in _WRAPPER_VALUE_LONG and not sep:
                    consume_next = True
            else:
                for j, c in enumerate(tok[1:]):
                    if c == "S":
                        rest = tok[2 + j:]
                        split_val = (_split_argv(rest) + words[i + 1:]) if rest else \
                            ((_split_argv(words[i + 1]) if i + 1 < len(words) else []) + words[i + 2:])
                        break
                    if c in _WRAPPER_VALUE_SHORT:
                        consume_next = not tok[2 + j:]  # next word only if no attached value
                        break
            if split_val is not None:
                if _depth >= _MAX_UNWRAP:
                    return assigns, ["gh", "pr", "create"]  # runaway nesting -> fail closed
                inner_assigns, inner_argv = _command_words(split_val, _depth + 1)
                return assigns + inner_assigns, inner_argv
            i += 1
            if consume_next and i < len(words):
                i += 1  # skip the option's value word
    return assigns, words[i:]


def _argv_is_pr_create(argv: list[str]) -> bool:
    if not argv or (argv[0] != "gh" and argv[0].rpartition("/")[2] != "gh"):
        return False
    rest = argv[1:]
    return any(rest[k] == "pr" and rest[k + 1] == "create" for k in range(len(rest) - 1))


def _is_pr_create(words: list[str]) -> bool:
    return _argv_is_pr_create(_command_words(words)[1])


def is_gh_pr_create(segments: list[list[str]]) -> bool:
    return any(_is_pr_create(seg) for seg in segments)


def _is_harmless(argv: list[str]) -> bool:
    """True iff argv carries --help/-h/--dry-run as a real flag (not a flag value)."""
    expect_value = False
    for tok in argv:
        if expect_value:
            expect_value = False
        elif tok in _VALUE_FLAGS:
            expect_value = True
        elif tok in _HARMLESS_FLAGS:
            return True
    return False


def has_inline_skip(cmd: str) -> bool:
    """True iff a real `gh pr create` carries SKIP_OKF_LINT=1 as its own env prefix."""
    for seg in normalize_command(cmd):
        assigns, argv = _command_words(seg)
        if _argv_is_pr_create(argv) and SKIP_TOKEN in assigns:
            return True
    return False


def gated_pr_create(cmd: str) -> bool:
    """True iff the command runs a real `gh pr create` that must pass the lint gate."""
    try:
        for seg in normalize_command(cmd):
            assigns, argv = _command_words(seg)
            if _argv_is_pr_create(argv) and SKIP_TOKEN not in assigns and not _is_harmless(argv):
                return True
        return False
    except Exception:  # noqa: BLE001 -- any parser failure fails closed (treat as gated)
        return True


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
    if os.environ.get("SKIP_OKF_LINT") == "1":
        return 0
    if not gated_pr_create(command):
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
