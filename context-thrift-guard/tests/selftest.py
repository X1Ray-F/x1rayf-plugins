#!/usr/bin/env python3
"""Context Thrift Guard self-test (v0.3)."""
import sys, os, json, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")
TH = os.path.join(HOOKS, "thrift.py")
P = F = 0
def ok(d, c):
    global P, F
    print(("  ok   " if c else "  FAIL ") + d); P += bool(c); F += (not c)
def pre(tool, cwd=None, **ti):
    ev = {"session_id": "s", "tool_name": tool, "tool_input": ti}
    return subprocess.run([sys.executable, TH], input=json.dumps(ev), capture_output=True, text=True, cwd=cwd).stdout
def nudged(out): return '"additionalContext"' in out

tmp = tempfile.mkdtemp()
def mk(name, content, binary=False):
    p = os.path.join(tmp, name)
    open(p, "wb" if binary else "w").write(content)
    return p
f_biglines = mk("big.py", "x = 1\n" * 2100)       # 2100 lines > 2000
f_1600 = mk("mid.py", "x = 1\n" * 1600)           # 1600 lines: now BELOW threshold
f_normal = mk("mod.py", "x = 1\n" * 1000)
f_small = mk("small.py", "x = 1\n" * 5)
f_bigbytes = mk("data.txt", "A" * 130000)          # 130 KB text
f_a = mk("a.log", "A" * 30000); f_b = mk("b.log", "B" * 30000)   # 30 KB each -> 60 KB together
f_bigjson = mk("big.json", '{"k":"' + "v" * 60000 + '"}')        # 60 KB json
f_png = mk("shot.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 300000, binary=True)   # binary ext + NUL
f_dat = mk("blob.dat", b"\x00" * 130000, binary=True)            # unknown ext, NUL, >120KB
mk("config.txt", "foo\n")                          # for bare-filename grep (cwd=tmp)
os.makedirs(os.path.join(tmp, "src"), exist_ok=True)

# ---- Read ----
ok("Read 2100-line file -> nudge", nudged(pre("Read", file_path=f_biglines)))
ok("Read 1600-line file -> NO nudge (below 2000 cap)", not nudged(pre("Read", file_path=f_1600)))
ok("Read 1000-line module -> no nudge", not nudged(pre("Read", file_path=f_normal)))
ok("Read 130KB text -> nudge", nudged(pre("Read", file_path=f_bigbytes)))
ok("Read small -> no nudge", not nudged(pre("Read", file_path=f_small)))
ok("Read with limit -> no nudge", not nudged(pre("Read", file_path=f_biglines, offset=1, limit=50)))
ok("Read missing -> no nudge", not nudged(pre("Read", file_path=os.path.join(tmp, "nope"))))
ok("Read 300KB .png (binary ext) -> no nudge", not nudged(pre("Read", file_path=f_png)))
ok("Read 130KB .dat (NUL sniff) -> no nudge", not nudged(pre("Read", file_path=f_dat)))

# ---- cat ----
ok("cat big -> nudge", nudged(pre("Bash", command="cat %s" % f_bigbytes)))
ok("cat -n big -> nudge (flagged)", nudged(pre("Bash", command="cat -n %s" % f_bigbytes)))
ok("cat -A big -> nudge (flagged)", nudged(pre("Bash", command="cat -A %s" % f_bigbytes)))
ok("cat a b (multi-file 60KB) -> nudge", nudged(pre("Bash", command="cat %s %s" % (f_a, f_b))))
ok("cat single 30KB file -> no nudge", not nudged(pre("Bash", command="cat %s" % f_a)))
ok("cat big | head -> no nudge (piped)", not nudged(pre("Bash", command="cat %s | head" % f_bigbytes)))
ok("cat big > out -> no nudge (redirected)", not nudged(pre("Bash", command="cat %s > out.txt" % f_bigbytes)))
ok("cat small -> no nudge", not nudged(pre("Bash", command="cat %s" % f_small)))

# ---- jq ----
ok("jq . bigjson -> nudge", nudged(pre("Bash", command="jq . %s" % f_bigjson)))
ok("jq .items bigjson -> no nudge (scoped filter)", not nudged(pre("Bash", command="jq .items %s" % f_bigjson)))
ok("jq keys bigjson -> no nudge", not nudged(pre("Bash", command="jq keys %s" % f_bigjson)))
ok("jq -r .name bigjson -> no nudge", not nudged(pre("Bash", command="jq -r .name %s" % f_bigjson)))
ok("jq . bigjson | head -> no nudge (piped)", not nudged(pre("Bash", command="jq . %s | head" % f_bigjson)))
ok("jq . small.json -> no nudge", not nudged(pre("Bash", command="jq . %s" % f_small)))

# ---- grep ----
ok("grep -rn foo . (unscoped) -> nudge", nudged(pre("Bash", command="grep -rn foo .")))
ok("grep -rn foo src/ (scoped) -> no nudge", not nudged(pre("Bash", command="grep -rn foo %s/src" % tmp)))
ok("grep -rn foo src/ -A3 (trailing flag) -> no nudge", not nudged(pre("Bash", command="grep -rn foo %s/src -A3" % tmp)))
ok("grep -rn foo src/ -i (trailing flag) -> no nudge", not nudged(pre("Bash", command="grep -rn foo %s/src -i" % tmp)))
ok("grep -rn foo config.txt (single real file) -> no nudge", not nudged(pre("Bash", cwd=tmp, command="grep -rn foo config.txt")))
ok("grep -rln foo . (filenames) -> no nudge", not nudged(pre("Bash", command="grep -rln foo .")))
ok("grep -rn foo . | head -> no nudge (piped)", not nudged(pre("Bash", command="grep -rn foo . | head")))
ok("grep -rn foo . 1> out (explicit stdout) -> no nudge", not nudged(pre("Bash", command="grep -rn foo . 1> out.txt")))
ok("grep -rn foo . --include -> no nudge", not nudged(pre("Bash", command="grep -rn foo . --include=*.py")))
ok("plain grep (non-recursive) -> no nudge", not nudged(pre("Bash", command="grep foo file.txt")))
ok("ls -> no nudge", not nudged(pre("Bash", command="ls -la")))

# ---- run.js ----
rj = subprocess.run(["node", os.path.join(HOOKS, "run.js"), TH],
                    input=json.dumps({"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": f_biglines}}),
                    capture_output=True, text=True)
ok("run.js forwards + emits JSON", '"additionalContext"' in rj.stdout and rj.returncode == 0)

print("-----"); print("RESULT: %d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
