#!/usr/bin/env python3
"""Dependency Change Guard - PreToolUse.

Before an install command (npm/pnpm/yarn/bun, pip/uv/poetry, cargo) or an edit
to a dependency manifest (package.json, requirements*.txt, pyproject.toml), it
checks each EXACT pinned version against the public registry and flags:
  - a version that does not exist (a hallucinated / typo'd pin),
  - a yanked (PyPI/crates) or deprecated (npm) release,
  - a silent downgrade (manifest edits).
Only concrete exact pins are checked; ranges/dist-tags/partials are skipped.
Fails OPEN on anything uncertain; bounded in time/count. Standard library only.
"""
import sys, os, json, re, time, urllib.request, urllib.parse

EXACT = re.compile(r"\d+\.\d+\.\d+([-+.].*)?$")
NPM_ABBR = {"Accept": "application/vnd.npm.install-v1+json; q=1.0, application/json; q=0.8, */*"}
DEP_KEYS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
# a package.json dependency value that is a BARE exact pin (no range operator)
VER_VALUE_RE = re.compile(r"^\s*(\d+\.\d+\.\d+(?:[-+.]\S*)?)\s*$")


# ----------------------------- registry fetch -----------------------------
_FIXTURES = None


def _fixtures():
    global _FIXTURES
    if _FIXTURES is None:
        p = os.environ.get("DEPGUARD_FIXTURES")
        try:
            _FIXTURES = json.load(open(p)) if p and os.path.exists(p) else {}
        except Exception:
            _FIXTURES = {}
    return _FIXTURES


def http_json(url, timeout=3, headers=None):
    fx = _fixtures()
    if fx:
        if url in fx:
            if fx[url] == "__RAISE__":
                raise RuntimeError("simulated fetch failure")
            return fx[url]
        raise RuntimeError("no fixture for " + url)
    h = {"User-Agent": "dependency-change-guard", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ----------------------------- version helpers ----------------------------
def parse_ver(v):
    if not v:
        return None
    m = re.match(r"(\d+(?:\.\d+)*)", str(v).strip().lstrip("vV"))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def ver_lt(a, b):
    pa, pb = parse_ver(a), parse_ver(b)
    if pa is None or pb is None:
        return False
    n = max(len(pa), len(pb))
    return pa + (0,) * (n - len(pa)) < pb + (0,) * (n - len(pb))


def _pypi_has(version, rels):
    if version in rels:
        return True
    pv = parse_ver(version)
    if pv is None:
        return True
    for k in rels:
        pk = parse_ver(k)
        if pk is None:
            continue
        n = max(len(pv), len(pk))
        if pv + (0,) * (n - len(pv)) == pk + (0,) * (n - len(pk)):
            return True
    return False


# ----------------------------- registry checks ----------------------------
def check_package(eco, name, version, fetch=http_json):
    if not version or not EXACT.fullmatch(str(version).strip()):
        return []
    try:
        if eco == "npm":
            data = fetch("https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@/"), headers=NPM_ABBR)
            versions = data.get("versions", {}) or {}
            if version not in versions:
                latest = (data.get("dist-tags") or {}).get("latest", "?")
                return ["npm '%s' has no version %s (latest is %s) - likely a hallucinated or typo'd pin" % (name, version, latest)]
            if versions[version].get("deprecated"):
                return ["npm '%s@%s' is deprecated: %s" % (name, version, str(versions[version]["deprecated"])[:140])]
        elif eco == "pypi":
            data = fetch("https://pypi.org/pypi/%s/json" % urllib.parse.quote(name))
            rels = data.get("releases", {}) or {}
            if not _pypi_has(version, rels):
                latest = (data.get("info") or {}).get("version", "?")
                return ["PyPI '%s' has no version %s (latest is %s) - likely a hallucinated or typo'd pin" % (name, version, latest)]
            files = rels.get(version) or []
            if files and all(f.get("yanked") for f in files):
                reason = next((f.get("yanked_reason") for f in files if f.get("yanked_reason")), "")
                return ["PyPI '%s==%s' is yanked%s" % (name, version, (": " + reason) if reason else "")]
        elif eco == "crates":
            data = fetch("https://crates.io/api/v1/crates/%s" % urllib.parse.quote(name))
            vmap = {v.get("num"): v for v in (data.get("versions") or [])}
            if version not in vmap:
                crate = data.get("crate") or {}
                latest = crate.get("max_stable_version") or crate.get("newest_version", "?")
                return ["crate '%s' has no version %s (latest is %s) - likely a hallucinated or typo'd pin" % (name, version, latest)]
            if vmap[version].get("yanked"):
                return ["crate '%s@%s' is yanked" % (name, version)]
    except Exception:
        return []
    return []


# ----------------------------- command parsing ----------------------------
def _split_specs(rest):
    return [t.strip("\"'") for t in re.split(r"\s+", rest.strip()) if t and not t.startswith("-")]


def _npm_spec(tok):
    if tok.startswith("@"):
        if tok.count("@") >= 2:
            name, ver = tok.rsplit("@", 1)
            return name, ver
        return tok, None
    if "@" in tok:
        name, ver = tok.split("@", 1)
        return name, ver
    return tok, None


def parse_install_command(cmd):
    out = []
    m = re.search(r"\b(npm\s+(?:install|i|add)|pnpm\s+(?:add|install|i)|yarn\s+add|bun\s+(?:add|install|i))\b(.*)", cmd, re.I)
    if m:
        for tok in _split_specs(m.group(2)):
            name, ver = _npm_spec(tok)
            if name and ver:
                out.append(("npm", name, ver))
        return out
    m = re.search(r"\b((?:python3?\s+-m\s+)?pip3?\s+install|uv\s+pip\s+install|uv\s+add|poetry\s+add)\b(.*)", cmd, re.I)
    if m:
        for tok in _split_specs(m.group(2)):
            mm = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?==([\w.]+)", tok)
            if mm:
                out.append(("pypi", mm.group(1), mm.group(2)))
        return out
    m = re.search(r"\bcargo\s+(?:add|install)\b(.*)", cmd, re.I)
    if m:
        rest = m.group(1)
        vm = re.search(r"--vers(?:ion)?[=\s]+([\w.]+)", rest)
        cver = vm.group(1) if vm else None
        for tok in _split_specs(rest):
            if "@" in tok:
                name, ver = tok.split("@", 1)
                out.append(("crates", name, ver))
            elif cver and re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", tok):
                out.append(("crates", tok, cver))
                cver = None
        return out
    return out


# ----------------------------- manifest parsing ---------------------------
def _read(fp):
    try:
        with open(fp) as f:
            return f.read()
    except Exception:
        return None


def _strict_pkg(text):
    """Parse package.json (strict JSON only) -> {dep: exact-version}; None on bad JSON.
    Only DEP_KEYS are read (so name/version/engines are excluded) and only bare
    exact pins are recorded (ranges/dist-tags skipped)."""
    if text is None:
        return None
    try:
        obj = json.loads(text)
    except Exception:
        return None
    out = {}
    for k in DEP_KEYS:
        for name, rng in (obj.get(k) or {}).items():
            m = VER_VALUE_RE.match(str(rng))
            if m:
                out[name] = m.group(1)
    return out


def parse_requirements(text):
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^[\"']?([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?\s*==\s*([\w.]+)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def parse_pyproject(text):
    out = {}
    for m in re.finditer(r'"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([\w.]+)', text):
        out[m.group(1)] = m.group(2)
    return out


def manifest_eco(fp):
    b = os.path.basename(fp or "").lower()
    if b == "package.json":
        return "npm"
    if b.startswith("requirements") and b.endswith(".txt"):
        return "pypi"
    if b == "pyproject.toml":
        return "pypi"
    return None


def _npm_maps(fp, tool, ti):
    """Reconstruct the POST-edit package.json and strict-parse both sides, so a
    newly-added dep is seen while non-dependency keys stay excluded. Fail open."""
    disk = _read(fp)
    if tool == "Write":
        old = _strict_pkg(disk) or {}
        new = _strict_pkg(ti.get("content", "") or "")
        return (old, new) if new is not None else ({}, {})
    if disk is None:
        return {}, {}
    text = disk
    if tool == "Edit":
        edits = [(ti.get("old_string", ""), ti.get("new_string", ""))]
    else:  # MultiEdit
        edits = [(e.get("old_string", ""), e.get("new_string", ""))
                 for e in (ti.get("edits") or []) if isinstance(e, dict)]
    for old_s, new_s in edits:
        if not old_s or text.count(old_s) != 1:      # Edit's one-occurrence contract
            return {}, {}
        text = text.replace(old_s, new_s, 1)
    old, new = _strict_pkg(disk), _strict_pkg(text)
    if old is None or new is None:
        return {}, {}
    return old, new


def manifest_maps(fp, tool, ti):
    if os.path.basename(fp or "").lower() == "package.json":
        return _npm_maps(fp, tool, ti)
    parser = parse_pyproject if os.path.basename(fp or "").lower() == "pyproject.toml" else parse_requirements
    if tool == "Write":
        return {}, parser(ti.get("content", "") or "")
    if tool == "Edit":
        return parser(ti.get("old_string", "") or ""), parser(ti.get("new_string", "") or "")
    if tool == "MultiEdit":
        old, new = {}, {}
        for e in (ti.get("edits") or []):
            if isinstance(e, dict):
                old.update(parser(e.get("old_string", "") or ""))
                new.update(parser(e.get("new_string", "") or ""))
        return old, new
    return {}, {}


# ----------------------------- evaluation ---------------------------------
def evaluate(tool, ti, fetch=http_json, max_checks=12, budget_s=4.0):
    problems = []
    if not isinstance(ti, dict):
        return problems
    deadline = time.monotonic() + budget_s
    n = [0]

    def maybe_check(eco, name, ver):
        if n[0] >= max_checks or time.monotonic() > deadline:
            return []
        n[0] += 1
        return check_package(eco, name, ver, fetch)

    if tool == "Bash":
        for eco, name, ver in parse_install_command(ti.get("command", "") or ""):
            problems += maybe_check(eco, name, ver)
    elif tool in ("Edit", "Write", "MultiEdit"):
        fp = ti.get("file_path", "") or ""
        eco = manifest_eco(fp)
        if eco:
            old, new = manifest_maps(fp, tool, ti)
            for name, ver in new.items():
                if old.get(name) == ver:
                    continue
                problems += maybe_check(eco, name, ver)
                o = old.get(name)
                if o and ver and ver_lt(ver, o):
                    problems.append("'%s' pinned DOWN from %s to %s in %s - confirm this downgrade is intended"
                                    % (name, o, ver, os.path.basename(fp)))
    return problems


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    problems = evaluate(data.get("tool_name", ""), data.get("tool_input", {}) or {})
    if problems:
        reason = "Dependency Change Guard flagged this change:\n- " + "\n- ".join(problems[:8])
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
