# Context Thrift Guard

**Advisory nudges that keep an agent's context (and token bill) lean.**

A Claude Code `PreToolUse` plugin that spots the biggest avoidable token sink —
dumping a large file or blob into context — and injects a short piece of advice
steering a cheaper approach. It **never blocks** (uses `additionalContext`, not a
permission decision), so it is safe in interactive, auto-accept **and** headless /
background runs.

Two low-false-positive signals:
- A **whole-file Read of a large file** (> ~1500 lines or > 120 KB) → suggests a
  ranged read (`offset`/`limit`) or a scoped grep.
- An **unbounded `cat`/`nl`/`tac`/`less`/`more` (incl. flags & multiple files), a
  `jq .` whole-document pretty-print of a big JSON, or an unscoped recursive `grep -r`** → suggests scoping / piping to `head`. Piped or redirected commands,
  scoped-path greps, and `-l`/`-c`/`--include` greps are left alone.

Fail-open, standard-library Python only, **~0 context tokens** on the common path.

## Honest scope

Because it never blocks, a nudge steers the *next* action rather than pre-empting
the current one — the tokens for the current read are still spent, and the advice
lands in the following turn. It complements, and does not replace, Claude Code's
built-in prompt caching, `/compact`, Read's ~2000-line cap and Bash's ~30k
truncation. It is deliberately the most conservative of the three x1rayf-plugins:
it only advises, and only on clear over-reads. The **biggest** credit savings are
config/habits (model tiering, effort per task, `/clear`), not this plugin.

## Configure (optional)

Thresholds are env-tunable — raise them if you routinely read large generated files:
`THRIFT_READ_LINES` (1500), `THRIFT_READ_BYTES` (120000), `THRIFT_CAT_BYTES` (50000).

## Install & verify

```bash
claude --plugin-dir /path/to/context-thrift-guard
python3 tests/selftest.py     # -> RESULT: 35 passed, 0 failed
```
Requires Node + Python (`python3`/`python`/`py`; the launcher resolves whichever exists).

MIT © X1Ray-F
