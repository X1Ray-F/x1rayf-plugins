#!/usr/bin/env python3
"""Context Thrift Guard - PreToolUse.

Advisory nudges (never a block) that cut token/credit waste from dumping large
files/blobs into context. Two low-false-positive signals, delivered as
additionalContext so they are safe in interactive, auto-accept AND headless/
background runs (a permissionDecision would hard-block a non-interactive agent):
  1. a whole-file Read of a large file  -> read a range / grep instead
  2. an unbounded cat/less/more of a big file, or an unscoped recursive grep -r

Complements the built-ins (Read's ~2000-line cap and Bash's ~30k truncation are
blunt ceilings). Fails OPEN on anything uncertain; standard library only; adds
~0 context tokens on the common path. Thresholds are env-tunable.
"""
import sys, os, json, re


def _int_env(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


READ_LINE_THRESHOLD = _int_env("THRIFT_READ_LINES", 1500)   # near the Read tool's ~2000 cap
READ_BYTE_THRESHOLD = _int_env("THRIFT_READ_BYTES", 120_000)
CAT_BYTE_THRESHOLD = _int_env("THRIFT_CAT_BYTES", 50_000)

_GREP_LIMITER = re.compile(
    r"(^|\s)-[a-zA-Z]*[lcom][a-zA-Z]*\b|--files-with-matches|--count|--max-count|--include|--exclude", re.I)


def _human(size, lines):
    s = "%d KB" % (size // 1024) if size >= 1024 else "%d B" % size
    return ("%d lines, %s" % (lines, s)) if lines is not None else s


def _check_read(ti):
    fp = ti.get("file_path")
    if not fp or ti.get("offset") is not None or ti.get("limit") is not None:
        return None                         # ranged read loads only part -> nothing to nudge
    if not os.path.isfile(fp):
        return None
    try:
        size = os.stat(fp).st_size
    except OSError:
        return None
    lines = None
    if size <= READ_BYTE_THRESHOLD:         # only count lines where it can change the outcome
        try:
            with open(fp, "rb") as f:
                lines = f.read().count(b"\n")
        except OSError:
            lines = None
    if size > READ_BYTE_THRESHOLD or (lines is not None and lines > READ_LINE_THRESHOLD):
        return ("'%s' is large (%s) and you're reading the whole file. Prefer a ranged read "
                "(offset/limit) or a scoped grep for the part you need, to keep context lean."
                % (os.path.basename(fp), _human(size, lines)))
    return None


def _check_bash(ti):
    cmd = ti.get("command", "") or ""
    if "|" in cmd or re.search(r"(?<![0-9])>", cmd):   # piped or stdout-redirected -> nothing enters context
        return None
    m = re.match(r"^\s*(cat|bat|less|more)\s+(\S+)\s*$", cmd)
    if m:
        f = m.group(2).strip("\"'")
        try:
            if os.path.isfile(f) and os.path.getsize(f) > CAT_BYTE_THRESHOLD:
                return ("`%s %s` dumps a large file (%d KB) into context; read a range "
                        "(Read with offset+limit, sed -n, head) or grep for what you need."
                        % (m.group(1), os.path.basename(f), os.path.getsize(f) // 1024))
        except OSError:
            return None
        return None
    if re.match(r"^\s*grep\b", cmd) and re.search(r"(^|\s)-[a-zA-Z]*[rR][a-zA-Z]*\b|--recursive", cmd):
        toks = cmd.split()
        tail = toks[-1].strip("\"'") if toks else ""
        scoped = tail not in (".", "./", "*", "") and (os.sep in tail or os.path.isdir(tail))
        if not scoped and not _GREP_LIMITER.search(cmd):
            return ("this recursive grep is unscoped and dumps every matching line into context; "
                    "scope it to a path, add -l/-c, --include, or pipe to head.")
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    if not isinstance(ti, dict):
        sys.exit(0)
    msg = None
    try:
        if tool == "Read":
            msg = _check_read(ti)
        elif tool == "Bash":
            msg = _check_bash(ti)
    except Exception:
        sys.exit(0)                          # fail open
    if msg:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "Context Thrift Guard: " + msg,
        }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
