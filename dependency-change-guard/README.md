# Dependency Change Guard

**Stops bad dependency changes before they land.**

A Claude Code plugin. Before an install command (`npm/pnpm/yarn/bun`,
`pip/uv/poetry`, `cargo`) or an edit to `package.json` / `requirements*.txt` /
`pyproject.toml`, it checks each **exact** pinned version against the public
registry and asks for confirmation when:

- the pinned version **doesn't exist** (a hallucinated / typo'd pin),
- the release is **yanked** (PyPI / crates.io) or **deprecated** (npm),
- it's a **silent downgrade** (a manifest edit lowering a pin).

It also surfaces **deprecation warnings** from package-manager / build / test
output. Deterministic, **fails open** (never blocks on anything uncertain), no
secrets, standard-library Python only.

## How it works

- **`PreToolUse`** parses each pinned `name@version`, and only for a **concrete
  exact pin** (`1.2.3`, `1.2.3-rc.1`) queries the registry — ranges, `latest`/
  `next`/`beta`, and partials like `^4.17` or `1` are skipped (fail-open, no false
  "ask"). npm uses the abbreviated packument; PyPI compares under PEP 440 zero-pad
  (so `Django==4.2.0` isn't mislabeled because PyPI keys it `4.2`). Checks are
  time- and count-bounded so a big `package.json` never stalls the session.
- **`PostToolUse`** surfaces real deprecation markers (`npm warn deprecated`,
  `DeprecationWarning`, `DEPRECATION:`) — scoped to build commands, so `git log`
  or a test named `test_deprecated` won't trigger it.

## Install

```bash
claude --plugin-dir /path/to/dependency-change-guard
```
Requires **Node** and **Python** (`python3`/`python`/`py`; the launcher resolves
whichever exists) plus outbound HTTPS to the public registries (without it, it
simply allows everything).

## Verify

```bash
python3 tests/selftest.py     # -> RESULT: 35 passed, 0 failed
```

## Honest limits

- Guards **explicit exact pins**; ranges and dist-tags are intentionally not
  existence-checked (they're valid by construction). A bare `npm i pkg` (latest)
  is left alone.
- Manifests: `package.json`, `requirements*.txt`, `pyproject.toml`. `Cargo.toml`,
  `go.mod`, and peer/engines compatibility are planned (go.mod needs care to avoid
  false-flagging private modules).

MIT © X1Ray-F
