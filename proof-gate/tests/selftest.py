#!/usr/bin/env python3
"""Proof Gate self-test (real Bash payload shape, no exit_code). Round-1 + round-2 cases."""
import sys, os, json, re, subprocess, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")
sys.path.insert(0, HOOKS)
import ledger as L  # noqa: E402
P = 0; F = 0


def ok(desc, cond):
    global P, F
    print(("  ok   " if cond else "  FAIL ") + desc)
    P += bool(cond); F += (not cond)


def led_dir():
    uid = getattr(os, "getuid", None)
    return os.path.join(tempfile.gettempdir(), "proof-gate-%d" % uid() if uid else "proof-gate")


def reset(sid):
    try:
        os.remove(os.path.join(led_dir(), re.sub(r"[^A-Za-z0-9_.-]", "_", sid) + ".jsonl"))
    except OSError:
        pass


def led(sid, tool, **ti):
    ev = {"session_id": sid, "tool_name": tool, "tool_input": {k: v for k, v in ti.items() if k != "tool_response"}}
    if "tool_response" in ti:
        ev["tool_response"] = ti["tool_response"]
    subprocess.run([sys.executable, os.path.join(HOOKS, "ledger.py")], input=json.dumps(ev), capture_output=True, text=True)


def bash(sid, cmd, stdout="", stderr=""):
    led(sid, "Bash", command=cmd, tool_response={"stdout": stdout, "stderr": stderr, "interrupted": False})


def gate(sid, msg, stop_hook_active=False):
    ev = {"session_id": sid, "last_assistant_message": msg, "stop_hook_active": stop_hook_active}
    return subprocess.run([sys.executable, os.path.join(HOOKS, "proof_gate.py")], input=json.dumps(ev), capture_output=True, text=True).returncode


V = L.is_verify_command
B = L.bash_ok

# ---- verify detection (round-1 + round-2) ----
for d, c in [("pytest -q", True), ('echo "pytest: 12 passed"', False), ("pip install pytest", False),
             ("grep -rn pytest .", False), ("git commit -m 'add pytest cfg'", False), ("cargo build", True),
             ("go build ./...", True), ("./gradlew test", True), ("python -m pytest", True),
             ("poetry run pytest", True), ("npx jest", True), ("cd foo && pytest", True),
             ("npm run test:unit", True), ("npm install", False), ("pytest --version", False),
             # round-2:
             ("timeout 60 pytest", True), ("timeout 5s go test ./...", True), ("stdbuf -oL pytest", True),
             ("nice -n 5 pytest", True), ('timeout 60 echo "3 passed"', False), ("sudo -u pytest whoami", False),
             ("make clean", False), ("make fmt", False), ("make coverage", True), ("make -j4 clean", False),
             ("make lint test", True), ("make", True), ("flutter build apk", True), ("flutter analyze", True),
             ("flutter run", False), ("npm t", True), ("ctest -V", True), ("mvn -V test", True), ("mvn -V", False)]:
    ok("verify: %-28s -> %s" % (d, c), V(d) is c)

# ---- bash_ok (round-1 + round-2) ----
for d, tr, want in [
    ("'3 passed'", {"stdout": "3 passed"}, True),
    ("silent build both-empty", {"stdout": "", "stderr": ""}, True),
    ("go build FAIL (stderr)", {"stdout": "", "stderr": "./m.go:5: undefined: foo"}, "notpass"),
    ("make *** Error", {"stdout": "checking ok", "stderr": "make: *** [test] Error 1"}, False),
    ("cargo 0 failed", {"stdout": "test result: ok. 5 passed; 0 failed"}, True),
    ("mixed 1 failed 4 passed", {"stdout": "1 failed, 4 passed"}, False),
    ("BUILD SUCCESSFUL", {"stdout": "BUILD SUCCESSFUL in 2s"}, True),
    ("cargo Finished target(s)", {"stdout": "Finished dev [unopt] target(s) in 0.5s"}, True),
    ("pass despite error: line", {"stdout": "5 passed", "stderr": "error: expected (caught)"}, True),
    # round-2 ctest / dotnet:
    ("ctest FAIL", {"stdout": "67% tests passed, 1 tests failed out of 3\nThe following tests FAILED:\n\t2 - T2 (Failed)"}, False),
    ("ctest PASS", {"stdout": "100% tests passed, 0 tests failed out of 3"}, True),
    ("dotnet FAIL", {"stdout": "Failed: 2, Passed: 3"}, False),
    ("dotnet PASS", {"stdout": "Failed: 0, Passed: 5, Skipped: 0"}, True),
    ("flutter build ok", {"stdout": "Built build/app/outputs/flutter-apk/app-release.apk (18.2MB)."}, True),
    ("flutter/dart analyze clean", {"stdout": "No issues found!"}, True),
    ("flutter build FAIL", {"stdout": "FAILURE: Build failed with an exception."}, False),
]:
    r = B(tr)
    cond = (r is not True) if want == "notpass" else (r is want)
    ok("bash_ok: %-24s" % d, cond)

# version spoofs must never be a pass
for spoof in ["pytest 7.4.0", "cargo 1.75.0 (abc 2024)", "gradle -V\nGradle 8.5"]:
    ok("bash_ok spoof not pass: %.20s" % spoof, B({"stdout": spoof}) is not True)

# ---- ReDoS: bounded, must be fast ----
t0 = time.time(); B({"stdout": "=" * 200000}); B({"stdout": "9" * 200000 + " x"}); dt = time.time() - t0
ok("bash_ok on 200k adversarial input < 0.5s (%.3fs)" % dt, dt < 0.5)

# ---- e2e gate ----
s = "e1"; reset(s); led(s, "Edit", file_path="app.py", old_string="x=1", new_string="x=2")
ok("block: claim, no test ran", gate(s, "All tests pass, done.") == 2)
bash(s, "pytest -q", stdout="3 passed")
ok("allow: claim after fresh green", gate(s, "All tests pass now.") == 0)
led(s, "Edit", file_path="app.py", old_string="x=2", new_string="x=3")
ok("block: stale green", gate(s, "Fixed it, tests pass.") == 2)

s = "e-ctest"; reset(s); led(s, "Edit", file_path="a.c", old_string="a", new_string="b")
bash(s, "ctest", stdout="67% tests passed, 1 tests failed out of 3\nThe following tests FAILED:")
ok("block: failing ctest is not a green", gate(s, "All tests pass, done.") == 2)

s = "e-flutter"; reset(s); led(s, "Edit", file_path="lib/main.dart", old_string="a", new_string="b")
bash(s, "flutter build apk", stdout="Built build/app/outputs/flutter-apk/app-release.apk (18.2MB).")
ok("allow: successful flutter build", gate(s, "The build passes, done.") == 0)

s = "e2"; reset(s); led(s, "Edit", file_path="main.go", old_string="a", new_string="b")
bash(s, "go build ./...", stdout="", stderr="")
ok("allow: silent build success", gate(s, "The build passes, done.") == 0)

# opt-out honored on progress phrasings, still blocked on a HARD contradiction
s = "e-opt"; reset(s); led(s, "Edit", file_path="app.py", old_string="a", new_string="b")
ok("allow: 'Fixed it, but I have not run the tests.'", gate(s, "Fixed it, but I have not run the tests.") == 0)
ok("allow: 'bug is fixed; I did not run the tests.'", gate(s, "The bug is fixed; I did not run the tests.") == 0)
ok("allow: 'Works now, though I haven't tested it.'", gate(s, "Works now, though I haven't tested it.") == 0)
ok("block: 'All tests pass, but I did not run them.'", gate(s, "All tests pass, but I did not run them.") == 2)

# green claims: block genuine, allow prose
s = "e-green"; reset(s); led(s, "Edit", file_path="app.py", old_string="a", new_string="b")
ok("block: 'All green.'", gate(s, "All green.") == 2)
ok("block: 'CI is green.'", gate(s, "CI is green.") == 2)
ok("block: 'All checks pass.'", gate(s, "All checks pass.") == 2)
ok("allow: 'the green button'", gate(s, "I clicked the green button in the UI.") == 0)
ok("allow: 'all the green fields'", gate(s, "All the green fields in the form are valid.") == 0)

# Write a NEW test file with a skip marker -> not net-weakened
s = "e-write"; reset(s); led(s, "Edit", file_path="app.py", old_string="a", new_string="b")
led(s, "Write", file_path="tests/test_new.py", content="import pytest\n@pytest.mark.skip\ndef test_todo():\n    pass\n\ndef test_add():\n    assert 1+1==2\n")
bash(s, "pytest -q", stdout="1 passed, 1 skipped")
ok("allow: new test file with a skip marker", gate(s, "All tests pass, done.") == 0)

# weakening survives innocuous re-edit, clears on removal
s = "e6"; reset(s); led(s, "Edit", file_path="app.py", old_string="a", new_string="b")
led(s, "Edit", file_path="tests/test_app.py", old_string="def test_x():\n    assert f()==3", new_string="@pytest.mark.skip\ndef test_x():\n    assert f()==3")
led(s, "Edit", file_path="tests/test_app.py", old_string="", new_string="def test_z():\n    assert g()==2")
bash(s, "pytest -q", stdout="2 passed, 1 skipped")
ok("block: weakening survives innocuous re-edit", gate(s, "Tests pass, done.") == 2)
led(s, "Edit", file_path="tests/test_app.py", old_string="@pytest.mark.skip\ndef test_x():", new_string="def test_x():")
bash(s, "pytest -q", stdout="3 passed")
ok("allow: weakening cleared when skip removed", gate(s, "Tests pass, done.") == 0)

s = "e7"; reset(s); led(s, "Edit", file_path="app.py", old_string="a", new_string="b")
ok("allow: stop_hook_active never cascades", gate(s, "Tests pass, done.", stop_hook_active=True) == 0)
ok("allow: claim but no code edited", gate("e8-fresh", "Done, all tests pass.") == 0)

# run.js launcher propagates exit code
s = "e9"; reset(s); led(s, "Edit", file_path="app.py", old_string="a", new_string="b")
r = subprocess.run(["node", os.path.join(HOOKS, "run.js"), os.path.join(HOOKS, "proof_gate.py")],
                   input=json.dumps({"session_id": s, "last_assistant_message": "All tests pass, done."}),
                   capture_output=True, text=True)
ok("run.js: propagates block exit 2 + stderr reason", r.returncode == 2 and "Proof Gate" in r.stderr)

print("-----")
print("RESULT: %d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
