#!/usr/bin/env python3
"""Proof Gate - Stop hook.

Blocks a premature completion claim (exit 2, reason on stderr) when the session
changed code AND either no verifying test/build passed after the last edit, or a
test was net-weakened. Fires only on a real completion claim; an honest opt-out
("I did not run the tests") is respected unless a HARD verification-pass claim is
also present. Honors stop_hook_active so it never cascades.
"""
import sys, os, json, re, tempfile, stat


STRONG_CLAIM = re.compile(r"""(?ix)
   \b(
     all\s+tests?\s+(now\s+)?pass(ing|es|ed)? |
     tests?\s+(are\s+)?(now\s+)?pass(ing|es|ed)? |
     tests?\s+(are\s+)?green | everything\s+(now\s+)?passes |
     (the\s+)?(bug|issue|error|failure)\s+is\s+(now\s+)?fixed |
     fixed\s+(it|the\s+\w+) |
     (the\s+)?build\s+(now\s+)?(succeeds|passes|is\s+green) |
     builds?\s+(now\s+)?(cleanly|successfully) |
     (compiles|compiled)\s+(cleanly|successfully) |
     verified\s+(that|it) | confirmed\s+(that|it)\s+works |
     works\s+now | trec\s+testele | merge\s+acum |
     (all|everything|ci|the\s+suite|tests?|build|pipeline|checks?)[^.\n]{0,15}green\b(?!\s+(?!now\b|again\b|already\b)\w) |
     all\s+checks?\s+pass(ing|ed|es)?
   )\b
""")
COMPLETION_NEAR = re.compile(r"""(?ix)
   \b(done|complete[d]?|finished|gata|repar(at|ăm|am))\b[^.\n]{0,40}\b(test|tests|build|compil\w+|suite|pass\w*|green)\b
   |
   \b(test|tests|build|compil\w+|suite|pass\w*|green)\b[^.\n]{0,40}\b(done|complete[d]?|finished|gata)\b
""")
# Genuine verification OUTCOME claims (contradict "I didn't run them"); a narrow
# subset of STRONG_CLAIM. Pure-progress ("fixed it", "works now") is excluded.
HARD_PASS = re.compile(r"""(?ix)\b(
   all\s+tests?\s+(now\s+)?pass(ing|es|ed)? | tests?\s+(are\s+)?(now\s+)?pass(ing|es|ed)? |
   tests?\s+(are\s+)?green | everything\s+(now\s+)?passes |
   (the\s+)?build\s+(now\s+)?(succeeds|passes|is\s+green) | builds?\s+(now\s+)?(cleanly|successfully) |
   (compiles|compiled)\s+(cleanly|successfully) | verified\s+(that|it) | confirmed\s+(that|it)\s+works |
   all\s+checks?\s+pass(ing|ed|es)? | trec\s+testele )\b""")
SUPPRESS = re.compile(r"""(?ix)
   \b(did\s*n[o']?t|have\s*n[o']?t|didn't|haven't|not)\s+(yet\s+)?(run|ran|test|tested|verif\w+)\b
   | \buntested\b | \bwithout\s+running\s+(the\s+)?tests?\b | \bno\s+tests?\s+(were\s+)?run\b
""")


def ledger_dir():
    base = tempfile.gettempdir()
    getuid = getattr(os, "getuid", None)
    name = "proof-gate-%d" % getuid() if getuid else "proof-gate"
    d = os.path.join(base, name)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        if getuid:
            st = os.lstat(d)
            if stat.S_ISLNK(st.st_mode) or st.st_uid != getuid():
                return None
            os.chmod(d, 0o700)
    except OSError:
        return None
    return d


def ledger_rows(session_id):
    d = ledger_dir()
    if not d:
        return []
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "nosession")
    p = os.path.join(d, sid + ".jsonl")
    rows = []
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    except (FileNotFoundError, OSError):
        pass
    return rows


def last_assistant_text(data):
    t = data.get("last_assistant_message")
    if isinstance(t, str) and t.strip():
        return t
    tp = data.get("transcript_path")
    if tp and os.path.exists(tp):
        try:
            with open(tp) as f:
                lines = f.readlines()
            for line in reversed(lines):
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") == "assistant" or obj.get("role") == "assistant":
                    msg = obj.get("message", obj)
                    c = msg.get("content")
                    if isinstance(c, str):
                        return c
                    if isinstance(c, list):
                        return " ".join(b.get("text", "") for b in c
                                        if isinstance(b, dict) and b.get("type") == "text")
        except Exception:
            pass
    return ""


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("stop_hook_active"):
        sys.exit(0)

    text = last_assistant_text(data)
    if not (STRONG_CLAIM.search(text) or COMPLETION_NEAR.search(text)):
        sys.exit(0)
    # honor an honest opt-out unless a HARD verification-pass claim is also made
    if not HARD_PASS.search(text) and SUPPRESS.search(text):
        sys.exit(0)

    rows = ledger_rows(data.get("session_id"))
    edits = [r for r in rows if r.get("kind") == "edit" and (r.get("is_source") or r.get("is_test"))]
    if not edits:
        sys.exit(0)

    last_edit_ts = max(r["ts"] for r in edits)
    passes = [r["ts"] for r in rows if r.get("kind") == "verify" and r.get("ok") is True]
    last_pass_ts = max(passes) if passes else None

    skip_net, assert_net = {}, {}
    for r in rows:
        if r.get("kind") == "edit" and r.get("is_test"):
            f = r.get("file", "")
            skip_net[f] = skip_net.get(f, 0) + int(r.get("skip_delta", 0))
            assert_net[f] = assert_net.get(f, 0) + int(r.get("assert_delta", 0))
    weak = {}
    for f in set(list(skip_net) + list(assert_net)):
        sigs = []
        if skip_net.get(f, 0) > 0:
            sigs.append("skip/only/xfail added")
        if assert_net.get(f, 0) < 0:
            sigs.append("assertion removed")
        if sigs:
            weak[f] = sigs

    if last_pass_ts is None or last_pass_ts < last_edit_ts:
        why = ("no test or build command ran this session"
               if last_pass_ts is None else
               "your last passing run predates your most recent code edit (the green is stale)")
        sys.stderr.write(
            "Proof Gate: you signaled completion, but " + why + ". "
            "Run the project's tests or build so a green reflects your latest change, then stop. "
            "If you deliberately did not verify, say that plainly (e.g. 'I did not run the tests').\n")
        sys.exit(2)

    if weak:
        detail = "; ".join("%s (%s)" % (f, ", ".join(s)) for f, s in weak.items())
        sys.stderr.write(
            "Proof Gate: a test was net-weakened, which can manufacture a false green -> " + detail + ". "
            "Restore the assertion / remove the skip, or state explicitly why the change is legitimate.\n")
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
