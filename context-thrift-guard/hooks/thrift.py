#!/usr/bin/env python3
"""Context Thrift Guard - PreToolUse.

Advisory nudges (never a block) that cut token/credit waste from dumping large
files/blobs into context, delivered as additionalContext so they are safe in
interactive, auto-accept AND headless/background runs. Signals:
  1. a whole-file Read of a large TEXT file (> ~2000 lines or 120 KB)
  2. an unbounded cat/nl/tac/less/more of big file(s), a `jq .` pretty-print of a
     big JSON, or an UNSCOPED recursive `grep -r`
Piped / stdout-redirected / scoped-path / -l/-c/--include commands and binary
files are left alone. Fails OPEN; standard library only; ~0 context tokens.
Thresholds are env-tunable (THRIFT_READ_LINES / _READ_BYTES / _CAT_BYTES).
"""
import sys, os, json, re, shlex


def _int_env(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


READ_LINE_THRESHOLD = _int_env("THRIFT_READ_LINES", 2000)    # aligned with Read's ~2000-line cap
READ_BYTE_THRESHOLD = _int_env("THRIFT_READ_BYTES", 120_000)
CAT_BYTE_THRESHOLD = _int_env("THRIFT_CAT_BYTES", 50_000)

_GREP_LIMITER = re.compile(
    r"(^|\s)-[a-zA-Z]*[lcom][a-zA-Z]*\b|--files-with-matches|--count|--max-count|--include|--exclude", re.I)

# binary / non-text extensions the Read tool cannot offset/limit or grep (.svg is text -> excluded)
_BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".heic", ".avif",
               ".pdf", ".ipynb", ".mp4", ".mov", ".webm", ".mkv", ".mp3", ".wav", ".flac", ".ogg",
               ".zip", ".gz", ".bz2", ".xz", ".tar", ".7z", ".rar", ".woff", ".woff2", ".ttf", ".otf",
               ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".class", ".wasm", ".jar",
               ".png", ".pyc"}


def _human(size, lines):
    s = "%d KB" % (size // 1024) if size >= 1024 else "%d B" % size
    return ("%d lines, %s" % (lines, s)) if lines is not None else s


def _check_read(ti):
    fp = ti.get("file_path")
    if not fp or ti.get("offset") is not None or ti.get("limit") is not None:
        return None
    if not os.path.isfile(fp):
        return None
    if os.path.splitext(fp)[1].lower() in _BINARY_EXT:
        return None
    try:
        size = os.stat(fp).st_size
    except OSError:
        return None
    lines = None
    if size <= READ_BYTE_THRESHOLD:
        try:
            with open(fp, "rb") as f:
                data = f.read()
        except OSError:
            data = None
        if data is not None:
            if b"\x00" in data:            # binary content, extension aside
                return None
            lines = data.count(b"\n")
    else:
        try:
            with open(fp, "rb") as f:
                if b"\x00" in f.read(8192):  # sniff header of an unknown-extension big file
                    return None
        except OSError:
            pass
    if size > READ_BYTE_THRESHOLD or (lines is not None and lines > READ_LINE_THRESHOLD):
        return ("'%s' is large (%s) and you're reading the whole file. Prefer a ranged read "
                "(offset/limit) or a scoped grep for the part you need, to keep context lean."
                % (os.path.basename(fp), _human(size, lines)))
    return None


def _check_bash(ti):
    cmd = ti.get("command", "") or ""
    if "|" in cmd or re.search(r"(?<![0-9])>|(?<!\d)1>", cmd):   # piped or stdout-redirected
        return None

    # cat / pager dumps (flagged options + multiple files), size-summed
    if not any(ch in "|&;<>$`()" for ch in cmd):
        try:
            toks = shlex.split(cmd)
        except ValueError:
            toks = None
        if toks and toks[0] in ("cat", "bat", "nl", "tac", "less", "more"):
            total, names = 0, []
            for t in toks[1:]:
                if t.startswith("-"):
                    continue
                try:
                    if os.path.isfile(t):
                        total += os.path.getsize(t)
                        names.append(os.path.basename(t))
                        continue
                except OSError:
                    pass
                names = []
                break                       # a non-flag, non-file token -> bail (fail open)
            if names and total > CAT_BYTE_THRESHOLD:
                label = names[0] if len(names) == 1 else "%d files" % len(names)
                return ("`%s %s` dumps %d KB into context; read a range "
                        "(Read with offset+limit, sed -n, head) or grep for what you need."
                        % (toks[0], label, total // 1024))

    # jq '.' whole-document pretty-print of a big JSON (expands it into context)
    if re.match(r"^\s*jq\b", cmd) and not any(c in cmd for c in "&;<$`()"):
        try:
            toks = shlex.split(cmd)
        except ValueError:
            toks = []
        args, skip = [], False
        for t in toks[1:]:
            if skip:
                skip = False
                continue
            if t in ("-f", "--from-file", "-L"):
                skip = True
                continue
            if t.startswith("-"):
                continue
            args.append(t)
        if len(args) >= 2 and args[0] == ".":
            f = args[1]
            try:
                if os.path.isfile(f) and os.path.getsize(f) > CAT_BYTE_THRESHOLD:
                    return ("`jq . %s` pretty-prints and EXPANDS the whole document (%d KB) into context; "
                            "use a scoped filter (`.field`, `.items[]`) or `jq keys` to pull only what you need."
                            % (os.path.basename(f), os.path.getsize(f) // 1024))
            except OSError:
                return None

    # unscoped recursive grep (dumps every matching line)
    if re.match(r"^\s*grep\b", cmd) and re.search(r"(^|\s)-[a-zA-Z]*[rR][a-zA-Z]*\b|--recursive", cmd):
        toks = cmd.split()
        operands = [t.strip("\"'") for t in toks[1:] if not t.startswith("-")]
        operands = operands[1:]             # drop the search pattern; the rest are path operands
        scoped = any(o not in (".", "./", "*", "")
                     and (os.sep in o or os.path.isdir(o) or os.path.isfile(o)) for o in operands)
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
        sys.exit(0)
    if msg:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "Context Thrift Guard: " + msg,
        }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
