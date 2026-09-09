#!/usr/bin/env node
// Portable Python launcher. Node is present on every Claude Code platform;
// python3 is not (python.org Windows ships only python.exe/py.exe, and the
// Microsoft Store alias makes python3/python resolvable-but-broken). On Windows
// try `py -3` first (never shadowed by the Store alias) and skip a resolved-but-
// -broken Store stub; forward stdin; propagate the child's exit code verbatim
// (proof-gate's Stop gate blocks via exit 2); fail open if no interpreter works.
const { spawnSync } = require("child_process");
const fs = require("fs");
const isWin = process.platform === "win32";
const script = process.argv[2];
const rest = process.argv.slice(3);
let input = Buffer.alloc(0);
try { input = fs.readFileSync(0); } catch (e) {}
const candidates = isWin
  ? [["py", "-3"], ["python"], ["python3"]]
  : [["python3"], ["python"], ["py", "-3"]];
let res = null;
for (const c of candidates) {
  res = spawnSync(c[0], c.slice(1).concat([script], rest), {
    input, stdio: ["pipe", "inherit", "pipe"],
  });
  const enoent = res.error && res.error.code === "ENOENT";
  const err = res.stderr ? res.stderr.toString() : "";
  const stub = isWin && res.status !== 0 && /was not found|Microsoft Store|AppInstaller/i.test(err);
  if (enoent || stub) continue;
  if (err) process.stderr.write(err);   // re-emit (stderr is piped) so exit-2 reasons survive
  break;
}
if (res && ((res.error && res.error.code === "ENOENT") ||
            (isWin && res.status !== 0 && /was not found|Microsoft Store|AppInstaller/i.test(res.stderr ? res.stderr.toString() : "")))) {
  process.stderr.write("proof-gate/depguard: no working Python (python3/python/py) found; skipping hook.\n");
  process.exit(0);
}
process.exit(res ? (res.status === null ? 0 : res.status) : 0);
