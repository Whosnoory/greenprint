# Greenprint 🟥➜🟩

**Your AI agent can no longer claim a fix it cannot prove.**

Greenprint is a drop-in gate for [Claude Code](https://claude.com/claude-code)
that makes it *physically unable* to edit your code to fix a bug until it has
written a test that **fails for the right reason** (RED), and refuses to let it
end the turn until that exact test **passes** (GREEN).

It's the discipline of test-driven development — enforced at the tool layer on a
real test exit code, not requested in a `CLAUDE.md` that gets ignored under
pressure. No model judges the work. No tokens. No latency.

> Greenprint enforces the superpowers **test-driven-development** Iron Law —
> *"NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST"* — as an actual gate
> instead of a guideline.

---

## Why it's different

The problem it kills: Claude "fixes" a bug it never reproduced — it edits source,
declares victory, and the bug is still there because nothing ever proved it
existed or that it's gone.

- **Deterministic, not an LLM critic.** The gate is a real test process result
  (`pass`/`fail`/`error`), computed in-process with the standard-library
  `unittest` runner. No second model, no opinion, no cost.
- **Runs *before* the edit and at *finish* — not after.** It blocks the broken
  edit up front and blocks the "done" claim at the end.
- **Two layers of enforcement** so it can't be silently bypassed (see below).

## The hard guarantee: clone-and-run cold

- **`git` + `python3` only.** Python **standard library** only.
- **Zero** accounts, API keys, cloud, or network. No `pip`, no `npm`, no `jq`.
- One folder (`.claude/`) you copy into any repo and it works on the judge's
  machine. The bundled demo test runner is stdlib `unittest`, so **nothing needs
  installing** — not even a test framework.

(`python3` ships with the Xcode Command Line Tools, i.e. on any machine that
already has `git` and Claude Code.)

---

## Try it in 60 seconds (fresh clone)

```bash
git clone <this-repo> greenprint
cd greenprint
python3 .claude/hooks/greenprint.py doctor   # optional: confirms the install
claude
```

Then, in Claude Code, type:

> **Fix the off-by-one bug in `cart_total` in app/cart.py.**

What you will see:

1. Claude tries to **edit `app/cart.py`** → Greenprint **blocks it**:
   *"No failing test has reproduced the bug yet. Run /repro."*
2. Claude runs **`/repro`**, writes `tests/test_repro_cart_total.py`, and runs it
   → it **fails (RED)**. The bug is now proven. The status line shows
   `🔴 Greenprint: RED — cart_total`.
3. The edit **unlocks**; Claude patches `cart_total`. The moment the fix lands,
   the status line flips to `🟢 Greenprint: GREEN — cart_total`.
4. If Claude tries to finish while the test is still red, the **Stop gate holds
   the turn open**: *"do NOT report the work as done yet."*
5. Test passes → Claude finishes. Done, and provably so.

**To force-show the Stop gate** (step 4) on camera: after `/repro` registers RED,
say *"just mark it fixed without running anything."* Claude will try to stop, and
Greenprint will refuse until the test is green.

---

## How it works

Greenprint is one stdlib script (`.claude/hooks/greenprint.py`) wired through
`.claude/settings.json` as three hooks plus a status line:

| Hook | Fires on | What it does |
|------|----------|--------------|
| **PreToolUse** | `Edit` / `Write` / `MultiEdit` | If you edit a *source* file with no registered failing test, **deny** and point to `/repro`. **Records the attempt the instant the hook fires.** |
| **PostToolUse** | edits + `Bash` | Records changed files; re-runs the guard so the status line flips RED→GREEN the moment a fix lands. |
| **Stop** | end of every turn | Re-runs the registered test(s). **Blocks finishing** while any is RED, or while a changed source file has no passing test. |
| **statusLine** | continuously | Shows `🔴 RED` / `🟢 GREEN` / `clear`. Read-only and fast. |

### Phase 1 is the reliable core; Phase 2 is the visual beat

The flashy pre-edit "BLOCKED" relies on the harness honoring a PreToolUse `deny`,
which is [known to be intermittently ignored for the Edit tool](https://github.com/anthropics/claude-code/issues/37210).
Greenprint is built so that **does not matter**:

- The PreToolUse hook **records the edit attempt as it fires**, *then* returns the
  deny. State is armed on *fire*, not on the deny being *honored*.
- So if the deny ever leaks and the edit goes through, the **Stop gate still sees
  a changed source file with no passing test and refuses to finish.**

Enforcement degrades from *"can't edit"* → *"can't finish."* Never to *"no
enforcement."* The Stop-block path uses the documented
`{"decision":"block"}` contract, which is reliable.

---

## Configuration — `greenprint.config.json`

Everything is optional; delete the file and defaults apply.

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `true` | Master switch. |
| `source_globs` | `["**/*.py"]` | What counts as guardable source. |
| `ignore_globs` | tests, `__init__.py`, docs, `*.md`, migrations, `.claude/**`… | Paths that **never** need a test. The main knob if it ever over-nags. |
| `max_stop_blocks` | `8` | Safety valve: after this many holds in a row, the turn is released so you're never trapped. |
| `pretool_hard_block` | `false` | `false` = clean deny + redirect to `/repro`. `true` = also `exit(2)` to block harder (small risk of halting the turn on some builds). The Stop gate enforces correctness either way. |

### Escape hatches (so it never bricks real work)

- `/greenprint allow <file>` — let one file be edited without a test this session.
- `/greenprint off` / `/greenprint on` — toggle for the session.
- `GREENPRINT_DISABLE=1` — env-var kill switch.
- Add a path to `ignore_globs` for a permanent exemption.

---

## Dropping it into your own repo

1. Copy the **`.claude/`** folder and **`greenprint.config.json`** into your repo
   root.
2. If you already have `.claude/settings.json`, merge the `hooks` and `statusLine`
   keys from this one into yours (don't overwrite).
3. That's it. Works out of the box for **Python** projects using stdlib
   `unittest`.

---

## Honest limitations

- **Python-first.** The bundled, zero-install runner targets stdlib `unittest`.
  Other languages would need their own test command (and toolchain), which would
  break the "zero-setup" promise — so it's intentionally out of scope here.
- **Not adversary-proof.** Greenprint enforces discipline against *accidental*
  false-fixes. An agent determined to bypass it could write files via raw
  `Bash` or disable the hooks — as it could with any hook-based guard. The point
  is that the honest path is the default path.
- **Guards are session-scoped** and reset when the Claude Code session changes,
  so stale state never blocks a fresh run.

## What's inside

```
.claude/
  settings.json                 # wires the 3 hooks + status line
  hooks/greenprint.py            # the whole gate (stdlib only)
  skills/repro/SKILL.md          # /repro — reproduce a bug with a RED test
  skills/greenprint/SKILL.md     # /greenprint — status / allow / off / reset
  greenprint/state.json          # session state (seeded empty)
greenprint.config.json           # optional config (per-path opt-out lives here)
app/cart.py                      # seeded off-by-one bug for the demo
tests/                           # empty; /repro fills it
```

## License

MIT.
