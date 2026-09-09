#!/usr/bin/env python3
"""Offline self-test for Dependency Change Guard (fake registry). Round-1 + round-2."""
import sys, os, json, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")
sys.path.insert(0, HOOKS)
import depguard as dg  # noqa: E402

NPM_LODASH = {"dist-tags": {"latest": "4.17.21"},
              "versions": {"4.17.20": {}, "4.17.21": {}, "3.0.0": {"deprecated": "old"}}}
NPM_LEFTPAD = {"dist-tags": {"latest": "1.0.0"}, "versions": {"1.0.0": {}}}
NPM_REACT = {"dist-tags": {"latest": "18.2.0"}, "versions": {"18.2.0": {}}}
PYPI_REQUESTS = {"info": {"version": "2.32.0"},
                 "releases": {"2.32.0": [{"yanked": False}], "2.31.0": [{"yanked": False}],
                              "2.30.0": [{"yanked": True, "yanked_reason": "broken"}]}}
PYPI_DJANGO = {"info": {"version": "5.2.1"}, "releases": {"4.2": [{"yanked": False}], "5.0": [{"yanked": False}]}}
CRATES_SERDE = {"crate": {"max_stable_version": "1.0.200"},
                "versions": [{"num": "1.0.200", "yanked": False}, {"num": "1.0.150", "yanked": True}]}
FX = {"https://registry.npmjs.org/lodash": NPM_LODASH, "https://registry.npmjs.org/leftpad": NPM_LEFTPAD,
      "https://registry.npmjs.org/react": NPM_REACT, "https://pypi.org/pypi/requests/json": PYPI_REQUESTS,
      "https://pypi.org/pypi/Django/json": PYPI_DJANGO, "https://crates.io/api/v1/crates/serde": CRATES_SERDE}


def fake_fetch(url, timeout=4, headers=None):
    if url in FX:
        return FX[url]
    raise RuntimeError("no fixture for " + url)


def raising_fetch(url, timeout=4, headers=None):
    raise RuntimeError("network down")


P = F = 0
def ok(d, c):
    global P, F
    print(("  ok   " if c else "  FAIL ") + d); P += bool(c); F += (not c)
def has(ps, n): return any(n in p for p in ps)


# ---- command parsing ----
ok("parse npm w/ver", dg.parse_install_command("npm install lodash@4.17.21 --save") == [("npm", "lodash", "4.17.21")])
ok("parse scoped pnpm", dg.parse_install_command("pnpm add @vue/cli@5.0.8") == [("npm", "@vue/cli", "5.0.8")])
ok("parse pip ==", dg.parse_install_command("pip install requests==2.32.0 flask") == [("pypi", "requests", "2.32.0")])
ok("parse pip QUOTED", dg.parse_install_command('pip install "requests==9.9.9"') == [("pypi", "requests", "9.9.9")])
ok("parse pip EXTRAS", dg.parse_install_command("pip install requests[security]==9.9.9") == [("pypi", "requests", "9.9.9")])
ok("parse cargo @ver", dg.parse_install_command("cargo add serde@1.0.200") == [("crates", "serde", "1.0.200")])

# ---- exact-pin gate (Bash side) ----
for c in ["npm install lodash@^4.17.21", "npm install lodash@latest", "npm install lodash@18", "cargo add serde@1"]:
    ok("skip non-exact: %s" % c, dg.evaluate("Bash", {"command": c}, fake_fetch) == [])
ok("exact bad pin flagged", has(dg.evaluate("Bash", {"command": "npm install lodash@9.9.9"}, fake_fetch), "no version 9.9.9"))
ok("exact good pin clean", dg.evaluate("Bash", {"command": "npm install lodash@4.17.21"}, fake_fetch) == [])

# ---- existence / yank / deprecated / zero-pad ----
ok("npm deprecated flagged", has(dg.check_package("npm", "lodash", "3.0.0", fake_fetch), "deprecated"))
ok("pypi yanked flagged", has(dg.check_package("pypi", "requests", "2.30.0", fake_fetch), "yanked"))
ok("crates yanked flagged", has(dg.check_package("crates", "serde", "1.0.150", fake_fetch), "yanked"))
ok("pypi 4.2.0 == key '4.2' clean", dg.check_package("pypi", "Django", "4.2.0", fake_fetch) == [])
ok("pypi Django 4.2.99 flagged", has(dg.check_package("pypi", "Django", "4.2.99", fake_fetch), "no version 4.2.99"))
ok("network error -> allow", dg.check_package("npm", "lodash", "9.9.9", raising_fetch) == [])

# ---- package.json manifest edits (reconstruct + strict-parse) ----
tmp = tempfile.mkdtemp()
pkg = os.path.join(tmp, "package.json")
json.dump({"name": "app", "version": "1.0.0", "dependencies": {"lodash": "^4.17.21"}}, open(pkg, "w"))
def ev(tool, **ti): return dg.evaluate(tool, dict(file_path=pkg, **ti), fake_fetch)
ok("R2: bump own 'version' up -> no flag", ev("Edit", old_string='"version": "1.0.0"', new_string='"version": "2.0.0"') == [])
ok("R2: bump lodash range ^4.17.0 (floor absent) -> no flag",
   ev("Edit", old_string='"lodash": "^4.17.21"', new_string='"lodash": "^4.17.0"') == [])
ok("R2: change lodash to exact bad pin -> flagged",
   has(ev("Edit", old_string='"lodash": "^4.17.21"', new_string='"lodash": "9.9.9"'), "no version 9.9.9"))
ok("R2: ADD new dep (leftpad@9.9.9) via Edit -> flagged",
   has(ev("Edit", old_string='"lodash": "^4.17.21"', new_string='"lodash": "^4.17.21", "leftpad": "9.9.9"'), "no version 9.9.9"))
ok("R2: ADD new good exact dep via Edit -> clean",
   ev("Edit", old_string='"lodash": "^4.17.21"', new_string='"lodash": "^4.17.21", "leftpad": "1.0.0"') == [])
ok("R2: edit producing invalid JSON -> fail open",
   ev("Edit", old_string='{"name"', new_string='{{"name"') == [])
# exact->exact downgrade on an exact-pinned dep
pkg2 = os.path.join(tmp, "pkg2", "package.json"); os.makedirs(os.path.dirname(pkg2))
json.dump({"dependencies": {"lodash": "4.17.21"}}, open(pkg2, "w"))
dprob = dg.evaluate("Edit", {"file_path": pkg2, "old_string": '"lodash": "4.17.21"', "new_string": '"lodash": "4.17.20"'}, fake_fetch)
ok("R2: exact->exact downgrade flagged", has(dprob, "pinned DOWN from 4.17.21 to 4.17.20"))

# ---- requirements.txt / pyproject still work ----
mprob = dg.evaluate("Edit", {"file_path": "requirements.txt", "old_string": "requests==2.32.0", "new_string": "requests==2.30.0"}, fake_fetch)
ok("requirements yanked flagged", has(mprob, "yanked"))
ok("requirements downgrade flagged", has(mprob, "pinned DOWN from 2.32.0 to 2.30.0"))
ok("pyproject bad pin flagged", has(dg.evaluate("Write", {"file_path": "pyproject.toml", "content": '[project]\ndependencies=["requests==9.9.9"]'}, fake_fetch), "no version 9.9.9"))
ok("pyproject version= not a dep", dg.evaluate("Write", {"file_path": "pyproject.toml", "content": '[project]\nversion = "9.9.9"'}, fake_fetch) == [])

# ---- deprecation harvester scoping ----
def ds(cmd, out): return subprocess.run(["python3", os.path.join(HOOKS, "deprecation_scan.py")],
    input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "tool_response": {"stdout": out}}), capture_output=True, text=True).stdout
ok("dep: surfaces npm warn", "left-pad@1.3.0" in ds("npm install", "npm warn deprecated left-pad@1.3.0: use padStart"))
ok("dep: silent on git log", ds("git log", "a1 Remove deprecated auth path").strip() == "")
ok("dep: silent on grep", ds("grep -rn deprecated .", "# deprecated: v2").strip() == "")

# ---- main() + run.js ----
fxfile = os.path.join(tempfile.gettempdir(), "dg_fx.json"); json.dump(FX, open(fxfile, "w"))
env = dict(os.environ, DEPGUARD_FIXTURES=fxfile)
mo = subprocess.run(["python3", os.path.join(HOOKS, "depguard.py")], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "pip install requests==9.9.9"}}), capture_output=True, text=True, env=env)
ok("main() -> ask on bad pin", '"permissionDecision": "ask"' in mo.stdout and "no version 9.9.9" in mo.stdout)
mc = subprocess.run(["python3", os.path.join(HOOKS, "depguard.py")], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "npm install lodash@^4.17.21"}}), capture_output=True, text=True, env=env)
ok("main() silent on range pin", mc.stdout.strip() == "")
rj = subprocess.run(["node", os.path.join(HOOKS, "run.js"), os.path.join(HOOKS, "depguard.py")], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "pip install requests==9.9.9"}}), capture_output=True, text=True, env=env)
ok("run.js forwards stdin + JSON", '"permissionDecision"' in rj.stdout and rj.returncode == 0)

print("-----")
print("RESULT: %d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
