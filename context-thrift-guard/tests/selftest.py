#!/usr/bin/env python3
"""Context Thrift Guard self-test (v0.2) — drives the real PreToolUse hook."""
import sys, os, json, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")
TH = os.path.join(HOOKS, "thrift.py")
P = F = 0
def ok(d, c):
    global P, F
    print(("  ok   " if c else "  FAIL ") + d); P += bool(c); F += (not c)
def pre(tool, **ti):
    ev = {"session_id": "s", "tool_name": tool, "tool_input": ti}
    return subprocess.run([sys.executable, TH], input=json.dumps(ev), capture_output=True, text=True).stdout
def nudged(out): return '"additionalContext"' in out          # advisory, non-blocking

tmp = tempfile.mkdtemp()
f_biglines = os.path.join(tmp, "big.py"); open(f_biglines, "w").write("x = 1\n" * 1600)   # 1600 lines > 1500
f_normal = os.path.join(tmp, "mod.py"); open(f_normal, "w").write("x = 1\n" * 1000)        # 1000 lines: legit
f_small = os.path.join(tmp, "small.py"); open(f_small, "w").write("x = 1\n" * 5)
f_bigbytes = os.path.join(tmp, "data.txt"); open(f_bigbytes, "w").write("A" * 130000)      # 130 KB
os.makedirs(os.path.join(tmp, "src"), exist_ok=True)

# ---- Read ----
ok("Read whole large-by-lines file -> nudge", nudged(pre("Read", file_path=f_biglines)))
ok("Read normal 1000-line module -> NO nudge (below cap)", not nudged(pre("Read", file_path=f_normal)))
ok("Read large file WITH limit -> no nudge", not nudged(pre("Read", file_path=f_biglines, offset=1, limit=50)))
ok("Read large-by-bytes file -> nudge", nudged(pre("Read", file_path=f_bigbytes)))
ok("Read small file -> no nudge", not nudged(pre("Read", file_path=f_small)))
ok("Read missing file -> no nudge (fail open)", not nudged(pre("Read", file_path=os.path.join(tmp, "nope"))))
# re-read a large file twice -> still just the large-file nudge (no dedup machinery, no double behavior)
ok("re-read large file -> still nudge (no dedup FP path)", nudged(pre("Read", file_path=f_biglines)))

# ---- Bash ----
ok("cat big file -> nudge", nudged(pre("Bash", command="cat %s" % f_bigbytes)))
ok("cat big file | head -> no nudge (piped)", not nudged(pre("Bash", command="cat %s | head" % f_bigbytes)))
ok("cat big file > out -> no nudge (redirected)", not nudged(pre("Bash", command="cat %s > out.txt" % f_bigbytes)))
ok("cat small file -> no nudge", not nudged(pre("Bash", command="cat %s" % f_small)))
ok("grep -rn foo . (unscoped) -> nudge", nudged(pre("Bash", command="grep -rn foo .")))
ok("grep -rn foo src/ (scoped) -> no nudge", nudged(pre("Bash", command="grep -rn foo %s/src" % tmp)) is False)
ok("grep -rln (filenames) -> no nudge", not nudged(pre("Bash", command="grep -rln foo .")))
ok("grep -rn | head -> no nudge (piped)", not nudged(pre("Bash", command="grep -rn foo . | head")))
ok("grep -rn > out -> no nudge (redirected)", not nudged(pre("Bash", command="grep -rn foo . > out.txt")))
ok("grep -rn --include -> no nudge (scoped types)", not nudged(pre("Bash", command="grep -rn foo . --include=*.py")))
ok("plain grep (non-recursive) -> no nudge", not nudged(pre("Bash", command="grep foo file.txt")))
ok("normal command (ls) -> no nudge", not nudged(pre("Bash", command="ls -la")))

# ---- run.js launcher ----
rj = subprocess.run(["node", os.path.join(HOOKS, "run.js"), TH],
                    input=json.dumps({"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": f_biglines}}),
                    capture_output=True, text=True)
ok("run.js forwards + emits JSON", '"additionalContext"' in rj.stdout and rj.returncode == 0)

print("-----"); print("RESULT: %d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
