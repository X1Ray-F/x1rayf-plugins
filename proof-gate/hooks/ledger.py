#!/usr/bin/env python3
"""Proof Gate - PostToolUse ledger + test-weakening scanner.

Records, per session, every VERIFICATION run (a real test/build command) and
whether it passed, plus every code edit (tracking net test-weakening). Written
BY the hook from the real tool result, so a pass cannot be narrated into being.
PostToolUse cannot block; always exits 0.

Claude Code's Bash tool_response is {stdout, stderr, interrupted,
timedOutAfterMs, ...} with NO exit-code field, so pass/fail is a bounded text
heuristic; success is therefore best-effort.
"""
import sys, os, json, re, time, tempfile, stat


# ------------------------------- secure ledger dir -------------------------
def ledger_dir():
    base = tempfile.gettempdir()
    getuid = getattr(os, "getuid", None)
    name = "proof-gate-%d" % getuid() if getuid else "proof-gate"
    d = os.path.join(base, name)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        if getuid:
            st = os.lstat(d)
            if stat.S_ISLNK(st.st_mode) or st.st_uid != getuid():
                return None
            os.chmod(d, 0o700)
    except OSError:
        return None
    return d


def ledger_path(session_id):
    d = ledger_dir()
    if not d:
        return None
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "nosession")
    return os.path.join(d, sid + ".jsonl")


def append(session_id, row):
    row["ts"] = time.time()
    p = ledger_path(session_id)
    if not p:
        return
    try:
        with open(p, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


# ------------------------------- verify detection --------------------------
DIRECT_RUNNERS = {"pytest", "py.test", "tox", "nox", "jest", "vitest", "mocha",
                  "ava", "jasmine", "rspec", "phpunit", "ctest"}
NONRUNNER = {"echo", "printf", "cat", ":", "true", "false", "grep", "egrep",
             "fgrep", "rg", "ack", "find", "ls", "which", "type", "whereis",
             "git", "pip", "pip3", "pipx", "apt", "apt-get", "yum", "dnf",
             "brew", "sed", "awk", "curl", "wget", "head", "tail", "cp", "mv",
             "rm", "touch", "mkdir", "export", "source", ".", "tee", "sort",
             "uniq", "wc", "cd", "pwd", "clear", "black", "isort", "whoami"}
INFO_FLAGS = {"--version", "-h", "--help", "--collect-only", "--dry-run",
              "--list", "--tasks", "--show-config"}
WRAPPERS = {"sudo", "env", "time", "nice", "xargs", "command", "exec", "stdbuf",
            "nohup", "timeout"}
_VALUE_FLAGS = {"-n", "-s", "--signal", "-k", "--kill-after", "-u", "-i", "-o", "-e"}
_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")
_MAKE_NONVERIFY = {"clean", "distclean", "mrproper", "fmt", "format", "gofmt",
                   "lint", "style", "tidy", "doc", "docs", "help", "install",
                   "uninstall", "dist", "release"}


def _seg_is_verify(toks):
    if not toks:
        return False
    if any(t in INFO_FLAGS for t in toks):
        return False
    p = os.path.basename(toks[0])
    args = toks[1:]
    if p in NONRUNNER:
        return False
    if p in DIRECT_RUNNERS or p in ("tsc", "rustc", "ninja"):
        return True
    if p == "gradle" or p == "gradlew" or p.endswith("gradlew"):
        return bool(args)
    if p == "cargo":
        return bool(args) and args[0] in ("test", "build", "check", "bench")
    if p == "go":
        return bool(args) and args[0] in ("test", "build", "vet")
    if p == "mvn":
        return any(a in ("test", "verify", "package", "install") for a in args)
    if p == "make":
        targets = [a for a in args if not a.startswith("-") and "=" not in a]
        return (not targets) or any(t not in _MAKE_NONVERIFY for t in targets)
    if p == "bazel":
        return bool(args) and args[0] in ("test", "build")
    if p == "flutter":
        return bool(args) and (args[0] in ("build", "analyze") or "test" in args)
    if p == "dart":
        return bool(args) and args[0] in ("test", "analyze")
    if p == "swift":
        return bool(args) and args[0] in ("test", "build")
    if p == "deno":
        return bool(args) and args[0] == "test"
    if p == "mix" or p == "composer":
        return "test" in args
    if p in ("npm", "pnpm", "yarn", "bun"):
        if args and args[0] in ("exec", "dlx"):
            return _seg_is_verify(args[1:])
        if "install" in args or "ci" in args or (args and args[0] == "add"):
            return False
        if args and args[0] in ("t", "test"):        # npm/pnpm built-in `t` alias
            return True
        return bool(re.search(r"\b(test|build)\b", " ".join(args)))
    if p == "npx" and args:
        return _seg_is_verify(args)
    if re.match(r"^python[0-9.]*$", p):
        if len(args) >= 2 and args[0] == "-m" and args[1] in ("pytest", "unittest", "nox", "tox"):
            return True
        if any(a.endswith("manage.py") for a in args) and "test" in args:
            return True
        return False
    if p in ("poetry", "pdm", "hatch", "rye", "uv"):
        if len(args) >= 2 and args[0] == "run":
            return _seg_is_verify(args[1:])
        return False
    if p in ("sh", "bash", "zsh") and args:
        return _seg_is_verify(args)
    return False


def is_verify_command(cmd):
    for seg in re.split(r"&&|\|\||;|\||\n|\(|\)", cmd or ""):
        toks = seg.strip().split()
        i = 0
        while i < len(toks):
            t = toks[i]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
                i += 1
            elif t in WRAPPERS:
                i += 1
                while i < len(toks):
                    tk = toks[i]
                    if tk.startswith("-"):
                        i += 1
                        if tk in _VALUE_FLAGS and i < len(toks):
                            i += 1
                    elif t == "timeout" and _DURATION.match(tk):
                        i += 1
                        break
                    else:
                        break
            else:
                break
        if _seg_is_verify(toks[i:]):
            return True
    return False


# ------------------------------- pass/fail heuristic -----------------------
# All numeric quantifiers bounded to keep matching linear (no ReDoS).
STRONG_FAIL = re.compile(
    r"(?<![\d.])[1-9]\d{0,9}\s+(failed|failing|failures?|errors?)\b|"
    r"(?<![\d.])[1-9]\d{0,9}\s+tests?\s+(failed|failures)\b|"
    r"(?:failed|failures)\s*[:=]\s*[1-9]|"
    r"(?<!\d)0%\s+tests?\s+passed|"
    r"test result:\s*FAILED|npm ERR!|BUILD FAILED|BUILD FAILURE|panic:|"
    r"make(?:\[\d+\])?:\s+\*\*\*|recipe for target .{0,120}? failed|"
    r"={2,200}\s{0,8}FAILURES", re.I)
STRONG_OK = re.compile(
    r"(?<![\d.])[1-9]\d{0,9}\s+passed\b|\b0\s+(failed|failures)\b|test result:\s*ok|"
    r"BUILD SUCCESSFUL|BUILD SUCCESS|all tests passed|Finished\s.{0,80}?target\(s\)|"
    r"compiled successfully|built in\b|Compilation finished|"
    r"\bBuilt \S+\.(?:apk|aab|ipa|app|exe|so|dylib)\b|No issues found", re.I)
WEAK_FAIL = re.compile(r"Traceback|AssertionError|\berror:|\bnot ok\b|\bFAIL\b", re.I)
WEAK_OK = re.compile(r"\bpassed\b|\bPASS\b|\bok\b", re.I)


def bash_ok(tr):
    if isinstance(tr, dict):
        if tr.get("interrupted") or tr.get("timedOutAfterMs"):
            return False
        out = str(tr.get("stdout", "") or "")
        err = str(tr.get("stderr", "") or "")
        text = out + "\n" + err
    else:
        out, err, text = str(tr), "", str(tr)
    if len(text) > 40000:                       # bound work (defense-in-depth)
        text = text[:20000] + "\n" + text[-20000:]
    if STRONG_FAIL.search(text):
        return False
    if STRONG_OK.search(text):
        return True
    if WEAK_FAIL.search(text):
        return False
    if isinstance(tr, dict) and out.strip() == "" and err.strip() == "":
        return True                             # silent-success build (go build, tsc, ...)
    if WEAK_OK.search(text):
        return True
    return None


# ------------------------------- edit / weakening --------------------------
TEST_PATH_RE = re.compile(r"(^|/)(tests?|spec|__tests__)(/|$)|(_test\.|\.test\.|\.spec\.|test_)", re.I)
TEST_MARKER_RE = re.compile(r"#\[test\]|#\[cfg\(test\)\]|@pytest|def\s+test_|\bdescribe\s*\(|\bit\s*\(|\bit\.(only|skip)|@Test\b|func\s+Test", re.I)
DOC_EXT_RE = re.compile(r"\.(md|markdown|txt|rst|json|ya?ml|toml|lock|cfg|ini|csv|html?)$", re.I)
SKIP_RE = re.compile(r"\.only\b|\.skip\b|\bxit\b|\bfit\b|\bxdescribe\b|\bfdescribe\b|it\.todo|@pytest\.mark\.skip|@pytest\.mark\.xfail|@unittest\.skip|#\[ignore\]|t\.Skip\(", re.I)
ASSERT_RE = re.compile(r"\bassert\b|\bexpect\s*\(|\bshould\b|EXPECT_|ASSERT_|\bassert_|self\.assert\w+|\.to(Be|Equal|Match)\b|t\.(Error|Fatal)\b", re.I)


def _strip_comments(t):
    keep = []
    for line in (t or "").splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith("//"):
            continue
        keep.append(line)
    return "\n".join(keep)


def _deltas(old, new):
    old_s, new_s = _strip_comments(old), _strip_comments(new)
    return (len(SKIP_RE.findall(new_s)) - len(SKIP_RE.findall(old_s)),
            len(ASSERT_RE.findall(new_s)) - len(ASSERT_RE.findall(old_s)))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    sid = data.get("session_id")
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    if not isinstance(ti, dict):
        sys.exit(0)

    if tool == "Bash":
        cmd = ti.get("command", "") or ""
        if is_verify_command(cmd):
            append(sid, {"kind": "verify", "ok": bash_ok(data.get("tool_response"))})
    elif tool in ("Edit", "Write", "MultiEdit"):
        fp = ti.get("file_path", "") or ""
        base = os.path.basename(fp)
        is_dotfile_noext = base.startswith(".") and "." not in base[1:]
        is_source = bool(fp) and not DOC_EXT_RE.search(fp) and not is_dotfile_noext
        skip_delta = assert_delta = 0
        blob = ""
        if tool == "Edit":
            old, new = ti.get("old_string", ""), ti.get("new_string", "")
            blob = (old or "") + "\n" + (new or "")
            sd, ad = _deltas(old, new)
            skip_delta += sd; assert_delta += ad
        elif tool == "MultiEdit":
            for e in (ti.get("edits") or []):
                if isinstance(e, dict):
                    old, new = e.get("old_string", ""), e.get("new_string", "")
                    blob += (old or "") + "\n" + (new or "") + "\n"
                    sd, ad = _deltas(old, new)
                    skip_delta += sd; assert_delta += ad
        elif tool == "Write":
            new = ti.get("content", "") or ""
            blob = new
            skip_delta = 0    # a new file cannot have weakened an existing test
            assert_delta = 0
        is_test = bool(TEST_PATH_RE.search(fp)) or bool(TEST_MARKER_RE.search(blob))
        row = {"kind": "edit", "file": fp, "is_test": is_test, "is_source": is_source}
        if is_test:
            row["skip_delta"] = skip_delta
            row["assert_delta"] = assert_delta
        append(sid, row)
    sys.exit(0)


if __name__ == "__main__":
    main()
