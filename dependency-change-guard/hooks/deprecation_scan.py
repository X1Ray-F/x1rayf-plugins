#!/usr/bin/env python3
"""Dependency Change Guard - PostToolUse deprecation harvester.

After a package-manager / build / test command runs, surfaces the deprecation
notices that scroll past unnoticed in long logs, via additionalContext. Scoped
to real markers (npm warn deprecated / Python DeprecationWarning / pip
DEPRECATION) AND gated on the command being a package/build/test command, so it
never fires on `git log`, `grep deprecated`, or a test merely named
test_deprecated. Never blocks; exits 0.
"""
import sys, os, json, re

DEP_MARKER_RE = re.compile(r"npm\s+warn\s+deprecated|DeprecationWarning|^\s*DEPRECATION:", re.I)
BUILD_CMD_RE = re.compile(r"\b(npm|pnpm|yarn|bun|pip3?|uv|poetry|cargo|pytest|tox|make|gradle|gradlew|mvn|go|dotnet)\b", re.I)


def extract_text(tr):
    if isinstance(tr, dict):
        parts = [str(tr.get(k, "")) for k in ("stdout", "stderr", "output", "content", "result")]
        return "\n".join(p for p in parts if p) or json.dumps(tr)
    return str(tr)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command", "") if isinstance(data.get("tool_input"), dict) else ""
    if not BUILD_CMD_RE.search(cmd or ""):
        sys.exit(0)
    text = extract_text(data.get("tool_response"))
    seen, hits = set(), []
    for line in text.splitlines():
        line = line.strip()
        if not line or not DEP_MARKER_RE.search(line):
            continue
        key = line[:160]
        if key in seen:
            continue
        seen.add(key)
        hits.append(line[:200])
        if len(hits) >= 8:
            break
    if hits:
        msg = ("Dependency Change Guard - deprecation notices in the output "
               "(worth addressing before they break):\n- " + "\n- ".join(hits))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": msg}}))
    sys.exit(0)


if __name__ == "__main__":
    main()
