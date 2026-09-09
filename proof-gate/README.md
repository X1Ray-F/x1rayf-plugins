# Proof Gate

**Makes "done / tests pass / fixed" mean something.**

A Claude Code plugin. When the agent signals completion *and it changed code this
session*, a `Stop` hook blocks stopping unless a test or build actually **passed
after the most recent code edit**, and no test was **net-weakened** (a skip added,
an assertion removed) to manufacture a false green.

Deterministic, language-agnostic, zero-config, no secrets, no network.

## How it works

- **`PostToolUse`** records — from the real tool result, not the model's narration —
  every genuine verification run (the runner must be the invoked program of a
  command segment, so `echo "pytest: passed"` or `pip install pytest` do **not**
  count) and whether it passed, plus each code edit (tracking net test-weakening).
- **`Stop`** fires only on a real completion claim near a test/build word, and blocks
  (exit 2, reason on stderr) when: no verification ran, the last pass predates the
  last edit (stale green), or a test is net-weakened. An honest opt-out
  ("I did not run the tests") is respected; `stop_hook_active` prevents cascades.

Pass/fail is a **heuristic**: Claude Code's Bash result carries no exit code, so it
reads test/build summaries (`N passed`, `test result: ok`, `BUILD SUCCESSFUL`,
cargo `Finished … target(s)`, and silent-success builds where both streams are
empty), with failure summaries (`N failed`, `make: ***`, `panic:`, …) taking
precedence. Ambiguous output is treated as *not a pass* (safe default).

## Install

```bash
claude --plugin-dir /path/to/proof-gate      # local test
```
Or install from the marketplace (see the parent `x1rayf-plugins`). Requires
**Node** (present on every Claude Code platform) and **Python** (`python3`,
`python`, or `py` — the launcher resolves whichever exists, so it works on Windows).

## Verify

```bash
python3 tests/selftest.py     # -> RESULT: 75 passed, 0 failed
```

## Honest limits

- Success detection is heuristic (no exit code is exposed); it favors *not* a pass
  when unsure, so at worst it asks for one extra run.
- Editing a code file after a green marks it stale (re-run is the correct
  discipline); docs/config and extensionless dotfiles don't trigger it.
- Changed-line **coverage** ("did the passing suite execute your new code") is a
  planned optional adapter, not in this version.

MIT © X1Ray-F
