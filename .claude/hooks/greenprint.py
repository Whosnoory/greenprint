#!/usr/bin/env python3
"""
Greenprint — a deterministic TDD enforcement gate for Claude Code.

One file. Python standard library only. No pip, no network, no accounts.

It wires three hooks plus a status line:

  PreToolUse  (Edit|Write|MultiEdit) -> deny edits to source until a failing
                                        test has reproduced the bug.
  PostToolUse (Edit|Write|MultiEdit|Bash) -> record touched files; refresh a
                                        guard's status the moment a fix lands.
  Stop        -> refuse to end the turn while any registered test is RED, or
                 while a changed source file has no passing test proving it.
  statusline  -> show RED / GREEN / clear at a glance.

The Stop gate is the reliable core: even if the PreToolUse deny is ignored by
the harness (a known intermittent bug), the edit is still *recorded on hook
fire*, so the Stop gate catches it and refuses to let the turn finish on an
unproven change. Enforcement degrades from "can't edit" to "can't finish" —
never to "no enforcement".

Subcommands:
  pretooluse | posttooluse | stop | statusline   (hook entry points; read JSON on stdin)
  runtest <test_file> [--target F] [--symbol S]   (run + classify + register a guard)
  check                                           (re-run guards, print status)
  on | off | allow <file> | reset                 (session controls / escape hatches)
  doctor                                          (self-check + environment dump)
"""

import sys
import os
import json
import io
import time
import re
import traceback

VERSION = 1
EDIT_TOOLS = ("Edit", "Write", "MultiEdit")


# --------------------------------------------------------------------------
# Paths / project root
# --------------------------------------------------------------------------
def _proj_root():
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    # this file lives at <root>/.claude/hooks/greenprint.py
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


ROOT = _proj_root()
try:
    os.chdir(ROOT)
except Exception:
    pass

STATE_PATH = os.path.join(ROOT, ".claude", "greenprint", "state.json")
CONFIG_PATH = os.path.join(ROOT, "greenprint.config.json")
LOG_PATH = os.path.join(ROOT, ".claude", "greenprint", "log.jsonl")

DEFAULT_CONFIG = {
    "enabled": True,
    "source_globs": ["**/*.py"],
    "ignore_globs": [
        "**/tests/**", "**/test_*.py", "**/*_test.py", "**/conftest.py",
        "**/__init__.py", "**/__pycache__/**",
        "docs/**", "**/*.md", "migrations/**", "setup.py", ".claude/**",
    ],
    "test_dir": "tests",
    "max_stop_blocks": 8,
    "pretool_hard_block": False,
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------
# Glob matching (supports ** over posix relative paths)
# --------------------------------------------------------------------------
_GLOB_CACHE = {}


def _glob_to_regex(glob):
    out = []
    i, n = 0, len(glob)
    while i < n:
        c = glob[i]
        if c == "*":
            if glob[i:i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if glob[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _matches(relpath, globs):
    for g in globs or []:
        rx = _GLOB_CACHE.get(g)
        if rx is None:
            rx = _glob_to_regex(g)
            _GLOB_CACHE[g] = rx
        if rx.match(relpath):
            return True
    return False


def _rel(path):
    if not path:
        return None
    try:
        ap = os.path.abspath(path)
        r = os.path.relpath(ap, ROOT).replace(os.sep, "/")
        return r
    except Exception:
        return None


def _is_source(relpath, cfg):
    if not relpath or relpath.startswith("../") or relpath == "..":
        return False
    if _matches(relpath, cfg.get("ignore_globs")):
        return False
    return _matches(relpath, cfg.get("source_globs"))


# --------------------------------------------------------------------------
# Config / state
# --------------------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r") as fh:
            user = json.load(fh)
        if isinstance(user, dict):
            for k, v in user.items():
                if k.startswith("_"):
                    continue
                cfg[k] = v
    except Exception:
        pass
    return cfg


def _blank_state():
    return {
        "version": VERSION,
        "session_id": None,
        "enabled": True,
        "guards": [],
        "touched": [],
        "session_allow": [],
        "stop_blocks": 0,
        "updated_at": None,
    }


def load_state():
    try:
        with open(STATE_PATH, "r") as fh:
            st = json.load(fh)
        if not isinstance(st, dict):
            return _blank_state()
        for k, v in _blank_state().items():
            st.setdefault(k, v)
        return st
    except Exception:
        return _blank_state()


def save_state(st):
    st["updated_at"] = _now()
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(st, fh, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def log_event(kind, payload):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as fh:
            rec = {"ts": _now(), "kind": kind}
            rec.update(payload or {})
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def ensure_session(st, sid, cfg):
    """Reset state when the session changes, so guards never leak between runs."""
    if sid and st.get("session_id") != sid:
        fresh = _blank_state()
        fresh["session_id"] = sid
        fresh["enabled"] = bool(cfg.get("enabled", True))
        st.clear()
        st.update(fresh)
    elif st.get("session_id") is None and sid:
        st["session_id"] = sid


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------
def find_guard(st, relfile):
    for g in st.get("guards", []):
        if g.get("target_file") == relfile:
            return g
    return None


def covered_green(st, relfile):
    for g in st.get("guards", []):
        if g.get("target_file") == relfile and g.get("status") == "green":
            return True
    return False


def record_touched(st, relfile, via):
    for t in st.get("touched", []):
        if t.get("file") == relfile:
            return
    st.setdefault("touched", []).append({"file": relfile, "via": via, "ts": _now()})


def _tail(text, n=1200):
    text = text or ""
    return text[-n:]


def load_and_run_test(test_rel):
    """Return (status, failures, errors, output). status in pass|fail|error."""
    import importlib.util
    import unittest

    test_abs = os.path.join(ROOT, test_rel)
    if not os.path.isfile(test_abs):
        return ("error", 0, 1, "test file not found: %s" % test_rel)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    modname = "gp_test_%d" % (abs(hash(test_rel)) % (10 ** 9))
    try:
        spec = importlib.util.spec_from_file_location(modname, test_abs)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
    except Exception:
        return ("error", 0, 1, "Test could not be imported / executed:\n" + traceback.format_exc())
    try:
        suite = unittest.defaultTestLoader.loadTestsFromModule(mod)
    except Exception:
        return ("error", 0, 1, "Could not load test cases:\n" + traceback.format_exc())
    buf = io.StringIO()
    result = unittest.TextTestRunner(stream=buf, verbosity=2).run(suite)
    out = buf.getvalue()
    nf, ne = len(result.failures), len(result.errors)
    if result.testsRun == 0:
        return ("error", 0, 1, "No tests found in %s\n%s" % (test_rel, out))
    if result.wasSuccessful():
        return ("pass", nf, ne, out)
    if ne > 0:
        return ("error", nf, ne, out)
    return ("fail", nf, ne, out)


def refresh_guards(st):
    for g in st.get("guards", []):
        status, nf, ne, out = load_and_run_test(g.get("test_file", ""))
        if status == "pass":
            g["status"] = "green"
        elif status == "fail":
            g["status"] = "red"
        else:
            g["status"] = "error"
        g["last_checked"] = _now()
        g["last_output_tail"] = _tail(out, 600)


def unprotected_files(st, cfg):
    out = []
    allow = set(st.get("session_allow", []))
    for t in st.get("touched", []):
        f = t.get("file")
        if not f or f in allow:
            continue
        if not _is_source(f, cfg):
            continue
        if not covered_green(st, f):
            out.append(f)
    return out


def infer_target(test_rel):
    try:
        with open(os.path.join(ROOT, test_rel), "r") as fh:
            text = fh.read()
    except Exception:
        return None
    stdlib = {"unittest", "sys", "os", "re", "json", "io", "typing", "math",
              "collections", "itertools", "functools", "time", "decimal", "pathlib"}
    for m in re.findall(r"^\s*(?:from|import)\s+([a-zA-Z_][\w\.]*)", text, re.M):
        top = m.split(".")[0]
        if top in stdlib:
            continue
        cand = os.path.join(*m.split(".")) + ".py"
        if os.path.isfile(os.path.join(ROOT, cand)):
            return cand.replace(os.sep, "/")
        pkg = os.path.join(*m.split("."))
        if os.path.isfile(os.path.join(ROOT, pkg, "__init__.py")):
            return pkg.replace(os.sep, "/")
    return None


# --------------------------------------------------------------------------
# stdin / output
# --------------------------------------------------------------------------
def read_stdin_json():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return {}


def emit_allow():
    sys.exit(0)


def emit_deny(reason, hard_block):
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload))
    if hard_block:
        sys.stderr.write(reason + "\n")
        sys.exit(2)
    sys.exit(0)


def emit_stop_block(reason):
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


# --------------------------------------------------------------------------
# Hook handlers
# --------------------------------------------------------------------------
def cmd_pretooluse(data):
    cfg = load_config()
    st = load_state()
    ensure_session(st, data.get("session_id"), cfg)
    if not st.get("enabled", True) or os.environ.get("GREENPRINT_DISABLE"):
        save_state(st)
        emit_allow()
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    r = _rel(ti.get("file_path"))
    if not r or not _is_source(r, cfg) or r in set(st.get("session_allow", [])):
        save_state(st)
        emit_allow()
    record_touched(st, r, tool)
    g = find_guard(st, r)
    save_state(st)
    if g is None:
        log_event("deny", {"file": r, "tool": tool})
        reason = (
            "Greenprint blocked this edit to `%s`.\n"
            "No failing test has reproduced the bug yet — guidance is not proof.\n"
            "Run  /repro  to write a test that FAILS for the right reason; this edit "
            "unlocks automatically once a RED test is registered.\n"
            "Not a bug fix? Run  /greenprint allow %s  or add the path to "
            "ignore_globs in greenprint.config.json." % (r, r)
        )
        emit_deny(reason, bool(cfg.get("pretool_hard_block", False)))
    log_event("allow", {"file": r, "tool": tool, "guard": g.get("id")})
    emit_allow()


def cmd_posttooluse(data):
    cfg = load_config()
    st = load_state()
    ensure_session(st, data.get("session_id"), cfg)
    if not st.get("enabled", True):
        save_state(st)
        sys.exit(0)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    if tool in EDIT_TOOLS:
        r = _rel(ti.get("file_path"))
        if r and _is_source(r, cfg) and r not in set(st.get("session_allow", [])):
            record_touched(st, r, tool)
        if r and find_guard(st, r):
            refresh_guards(st)  # flip RED->GREEN the moment a fix lands
    elif tool == "Bash":
        cmd = ti.get("command") or ""
        triggers = ["unittest", "greenprint.py", "pytest", "python -m", "python3 -m"]
        names = [os.path.basename(g.get("test_file", "")) for g in st.get("guards", [])]
        if any(t in cmd for t in triggers) or any(n and n in cmd for n in names):
            refresh_guards(st)
    save_state(st)
    sys.exit(0)


def cmd_stop(data):
    cfg = load_config()
    st = load_state()
    ensure_session(st, data.get("session_id"), cfg)
    if not st.get("enabled", True):
        save_state(st)
        sys.exit(0)
    refresh_guards(st)
    bad_guards = [g for g in st.get("guards", []) if g.get("status") != "green"]
    unprotected = unprotected_files(st, cfg)
    if not bad_guards and not unprotected:
        st["stop_blocks"] = 0
        save_state(st)
        sys.exit(0)

    st["stop_blocks"] = int(st.get("stop_blocks", 0)) + 1
    maxb = int(cfg.get("max_stop_blocks", 8))
    if st["stop_blocks"] > maxb:
        save_state(st)
        sys.stderr.write(
            "Greenprint: max stop-blocks (%d) reached; releasing the turn to avoid "
            "trapping the session. Run /greenprint status to see what is still RED.\n" % maxb
        )
        sys.exit(0)

    lines = ["Greenprint is holding this turn open — do NOT report the work as done yet."]
    for g in bad_guards:
        who = g.get("symbol") or g.get("target_file") or g.get("id")
        if g.get("status") == "error":
            lines.append(
                "- Guard test ERRORED: %s (for %s). Make the test runnable so it asserts."
                % (g.get("test_file"), who)
            )
        else:
            lines.append(
                "- Test still RED: %s (for %s). Fix the code and re-run: "
                "python3 .claude/hooks/greenprint.py check" % (g.get("test_file"), who)
            )
    for f in unprotected:
        lines.append(
            "- You changed `%s` but no passing test proves it. Run /repro to add a "
            "failing test for it, fix, and make it GREEN. (Not a bug fix? /greenprint allow %s)"
            % (f, f)
        )
    log_event("stop_block", {"bad_guards": [g.get("id") for g in bad_guards],
                             "unprotected": unprotected, "count": st["stop_blocks"]})
    save_state(st)
    emit_stop_block("\n".join(lines))


def cmd_statusline(data):
    # Read-only and fast: reflect last-known state, never run tests here.
    try:
        cfg = load_config()
        st = load_state()
    except Exception:
        sys.stdout.write("Greenprint")
        return
    if not st.get("enabled", True) or os.environ.get("GREENPRINT_DISABLE"):
        sys.stdout.write("⚪ Greenprint: off")
        return
    guards = st.get("guards", [])
    bad = [g for g in guards if g.get("status") != "green"]
    unprotected = unprotected_files(st, cfg)
    if bad or unprotected:
        names = []
        for g in bad:
            names.append(g.get("symbol") or os.path.basename(g.get("target_file") or g.get("id") or "?"))
        names.extend(os.path.basename(f) for f in unprotected)
        label = ", ".join(dict.fromkeys(names))[:32]
        sys.stdout.write("\U0001f534 Greenprint: RED — %s" % label)
        return
    if guards:
        names = ", ".join(dict.fromkeys(
            g.get("symbol") or os.path.basename(g.get("target_file") or "?") for g in guards))[:32]
        sys.stdout.write("\U0001f7e2 Greenprint: GREEN — %s" % names)
        return
    sys.stdout.write("\U0001f7e2 Greenprint: clear")


# --------------------------------------------------------------------------
# CLI handlers (called by the /repro and /greenprint skills)
# --------------------------------------------------------------------------
def _parse_flags(argv):
    pos, flags = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                flags[key] = argv[i + 1]
                i += 2
            else:
                flags[key] = True
                i += 1
        else:
            pos.append(a)
            i += 1
    return pos, flags


def cmd_runtest(argv):
    pos, flags = _parse_flags(argv)
    if not pos:
        print("usage: greenprint.py runtest <test_file> [--target FILE] [--symbol NAME]")
        sys.exit(2)
    test_rel = _rel(pos[0]) or pos[0]
    status, nf, ne, out = load_and_run_test(test_rel)

    if status == "error":
        print("GREENPRINT: ERROR — the test could not run cleanly, so it does NOT yet "
              "reproduce the bug.\nA valid reproduction fails on an ASSERTION, not an import/"
              "syntax/runtime error. Fix the test, then re-run.\n\n--- test output (tail) ---\n"
              + _tail(out, 1500))
        sys.exit(1)
    if status == "pass":
        print("GREENPRINT: PASS — this test already passes, so it does NOT reproduce a bug "
              "(or the bug is already fixed).\nWrite the assertion so it FAILS against the current "
              "buggy behavior, then re-run.\n\n--- test output (tail) ---\n" + _tail(out, 1000))
        sys.exit(1)

    # status == "fail" -> a valid RED reproduction
    cfg = load_config()
    st = load_state()
    target = _rel(flags.get("target")) if isinstance(flags.get("target"), str) else None
    if not target:
        target = infer_target(test_rel)
    symbol = flags.get("symbol") if isinstance(flags.get("symbol"), str) else None
    guard = {
        "id": symbol or (os.path.basename(target) if target else os.path.basename(test_rel)),
        "target_file": target,
        "symbol": symbol,
        "test_file": test_rel,
        "status": "red",
        "valid_red": True,
        "created_at": _now(),
        "last_checked": _now(),
        "last_output_tail": _tail(out, 600),
    }
    st["guards"] = [g for g in st.get("guards", []) if g.get("test_file") != test_rel]
    st["guards"].append(guard)
    if target:
        record_touched(st, target, "repro")
    save_state(st)
    log_event("register_red", {"test_file": test_rel, "target": target, "symbol": symbol})
    print("GREENPRINT: RED registered — the bug is reproduced (%d failing assertion%s)."
          % (nf, "" if nf == 1 else "s"))
    print("Guard: %s  ->  test %s" % (target or "(target inferred: none)", test_rel))
    print("Now fix %s and make this test pass. Verify with: "
          "python3 .claude/hooks/greenprint.py check" % (target or "the code"))
    sys.exit(0)


def cmd_check(argv):
    st = load_state()
    refresh_guards(st)
    save_state(st)
    cfg = load_config()
    guards = st.get("guards", [])
    if not guards:
        print("GREENPRINT: no guards registered this session. Nothing to verify.")
    else:
        print("GREENPRINT status:")
        for g in guards:
            mark = {"green": "✓ GREEN", "red": "✗ RED", "error": "! ERROR"}.get(
                g.get("status"), "? UNKNOWN")
            print("  [%s] %s  (test: %s)" % (mark, g.get("symbol") or g.get("target_file") or g.get("id"),
                                             g.get("test_file")))
    unp = unprotected_files(st, cfg)
    if unp:
        print("Changed source files with no passing test:")
        for f in unp:
            print("  - %s" % f)
    allgreen = guards and all(g.get("status") == "green" for g in guards) and not unp
    print("\nResult: %s" % ("ALL GREEN — safe to finish." if allgreen
                            else "NOT all green — the Stop gate will hold the turn open."))
    sys.exit(0)


def cmd_control(sub, argv):
    st = load_state()
    if sub == "off":
        st["enabled"] = False
        save_state(st)
        print("GREENPRINT: disabled for this session. Re-enable with /greenprint on.")
    elif sub == "on":
        st["enabled"] = True
        save_state(st)
        print("GREENPRINT: enabled.")
    elif sub == "allow":
        if not argv:
            print("usage: greenprint.py allow <file>")
            sys.exit(2)
        f = _rel(argv[0]) or argv[0]
        if f not in st.get("session_allow", []):
            st.setdefault("session_allow", []).append(f)
        # stop flagging it as an unprotected change
        st["touched"] = [t for t in st.get("touched", []) if t.get("file") != f]
        save_state(st)
        print("GREENPRINT: %s is allowed without a test for this session." % f)
    elif sub == "reset":
        sid = st.get("session_id")
        fresh = _blank_state()
        fresh["session_id"] = sid
        fresh["enabled"] = st.get("enabled", True)
        save_state(fresh)
        print("GREENPRINT: guards and touched files cleared for this session.")
    else:
        print("unknown control command: %s" % sub)
        sys.exit(2)
    sys.exit(0)


def cmd_doctor(argv):
    cfg = load_config()
    st = load_state()
    print("Greenprint doctor")
    print("  python    : %s" % sys.version.split()[0])
    print("  root      : %s" % ROOT)
    print("  config    : %s (%s)" % (CONFIG_PATH, "found" if os.path.isfile(CONFIG_PATH) else "defaults"))
    print("  state     : %s" % STATE_PATH)
    print("  enabled   : %s" % st.get("enabled", True))
    print("  source    : %s" % ", ".join(cfg.get("source_globs", [])))
    print("  ignore    : %s" % ", ".join(cfg.get("ignore_globs", [])))
    print("  guards    : %d" % len(st.get("guards", [])))
    for g in st.get("guards", []):
        print("     - [%s] %s (test %s)" % (g.get("status"), g.get("id"), g.get("test_file")))
    print("  touched   : %s" % ", ".join(t.get("file") for t in st.get("touched", [])) or "(none)")
    print("\nGreenprint is installed and runnable.")
    sys.exit(0)


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------
HOOK_CMDS = {"pretooluse": cmd_pretooluse, "posttooluse": cmd_posttooluse,
             "stop": cmd_stop, "statusline": cmd_statusline}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "doctor"
    rest = sys.argv[2:]

    if cmd in HOOK_CMDS:
        data = read_stdin_json()
        try:
            HOOK_CMDS[cmd](data)
        except SystemExit:
            raise
        except Exception:
            # Fail OPEN: never brick a session because of an internal error.
            try:
                log_event("internal_error", {"cmd": cmd, "trace": _tail(traceback.format_exc(), 800)})
            except Exception:
                pass
            sys.exit(0)
        return

    try:
        if cmd == "runtest":
            cmd_runtest(rest)
        elif cmd == "check":
            cmd_check(rest)
        elif cmd in ("on", "off", "allow", "reset"):
            cmd_control(cmd, rest)
        elif cmd == "doctor":
            cmd_doctor(rest)
        else:
            print(__doc__)
            sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        print("greenprint: internal error\n" + traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
