# x1rayf-plugins

Two deterministic Claude Code guardrails that make coding agents more trustworthy —
universal, zero-config, no secrets.

- **proof-gate** — makes "done / tests pass / fixed" mean something: a Stop-hook
  gate that blocks a completion claim unless a test/build actually passed after the
  latest code edit, and flags tests weakened to fake a green.
- **dependency-change-guard** — checks pinned dependency versions against the public
  registry (npm / PyPI / crates.io) before an install or manifest edit; flags
  hallucinated pins, yanked/deprecated releases and silent downgrades. Fails open.

## Install

```bash
/plugin marketplace add X1Ray-F/x1rayf-plugins
/plugin install proof-gate@x1rayf-plugins
/plugin install dependency-change-guard@x1rayf-plugins
```

Or try one locally without installing:

```bash
claude --plugin-dir ./proof-gate
```

**Requirements:** Node (bundled with Claude Code everywhere) and Python
(`python3`/`python`/`py` — the launcher resolves whichever exists, so it works on
Windows too).

## Tests

```bash
python3 proof-gate/tests/selftest.py               # 75 passed
python3 dependency-change-guard/tests/selftest.py   # 35 passed
```

MIT © X1Ray-F
