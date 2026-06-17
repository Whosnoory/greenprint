# Greenprint 🟥➜🟩

Your AI agent can no longer claim a fix it cannot prove.

Greenprint is a drop-in gate for [Claude Code](https://claude.com/claude-code)
that makes it physically unable to edit your code to fix a bug until it has
written a test that fails for the right reason (RED), and refuses to let it end
the turn until that exact test passes (GREEN).

It is test-driven development enforced at the tool layer, on a real test exit
code, instead of asked for in a `CLAUDE.md` that gets ignored under pressure. No
model judges the work. No tokens. No latency.

> Greenprint enforces the classic TDD iron law, "no production code without a
> failing test first," as an actual gate rather than a guideline.

## Why it's different

The problem it kills: Claude "fixes" a bug it never reproduced. It edits source,
declares victory, and the bug is still there because nothing ever proved it
existed or that it is gone.

* Deterministic, not an LLM critic. The gate is a real test result
  (`pass` / `fail` / `error`), computed in-process with the standard-library
  `unittest` runner. No second model, no opinion, no cost.
* Runs before the edit and at finish, not after. It blocks the broken edit up
  front and blocks the "done" claim at the end.
* Two layers of enforcement, so it cannot be silently bypassed (see below).

## The hard guarantee: clone-and-run cold

* `git` and `python3` only. Python standard library only.
* Zero accounts, API keys, cloud, or network. No `pip`, no `npm`, no `jq`.
* One folder (`.claude/`) you copy into any repo, and it works on the judge's
  machine. The bundled test runner is stdlib `unittest`, so nothing needs
  installing, not even a test framework.

`python3` ships with macOS (via the Command Line Tools) and with virtually every
Linux distribution, so it is present on essentially any machine that already runs
`git` and Claude Code. On Windows, change `python3` to `python` in
`.claude/settings.json`.

## Prove it in 5 seconds, no Claude

```bash
git clone https://github.com/Whosnoory/greenprint.git greenprint
cd greenprint
python3 .claude/hooks/greenprint.py selftest
```

```
Greenprint self-test  (no Claude, no network, no install)

  1. bug present, run the proving test  ->  FAIL   🔴 RED  ✓  (fails on an assertion = real bug)
  2. a broken / erroring test           ->  ERROR  rejected ✓  (errors are NOT a valid reproduction)
  3. apply the fix, run the same test    ->  PASS   🟢 GREEN ✓  (passes = fix proven)

  Result: PASS. The RED→GREEN gate works on this machine.
```

That runs the whole gate against a throwaway fixture in a temp directory. It
proves the engine works on your machine before you ever open Claude.

## See it drive Claude (60 seconds)

```bash
claude
```

In Claude Code, type:

> Fix the off-by-one bug in `cart_total` in app/cart.py.

What you will see:

1. Claude tries to edit `app/cart.py`. Greenprint blocks it: "No failing test
   has reproduced the bug yet. Run /repro."
2. Claude runs `/repro`, writes `tests/test_repro_cart_total.py`, and runs it. It
   fails (RED). The bug is now proven. The status line shows
   `🔴 Greenprint: RED · cart_total`.
3. The edit unlocks. Claude patches `cart_total`. The moment the fix lands, the
   status line flips to `🟢 Greenprint: GREEN · cart_total`.
4. If Claude tries to finish while the test is still red, the Stop gate holds the
   turn open: "do NOT report the work as done yet."
5. Test passes, Claude finishes. Done, and provably so.

To force-show the Stop gate (step 4) on camera: after `/repro` registers RED,
say "just mark it fixed without running anything." Claude will try to stop, and
Greenprint will refuse until the test is green.

## Where the 🔴 RED and 🟢 GREEN show up

```text
You ▸ Fix the off-by-one in cart_total in app/cart.py

Claude ▸ [tries to Edit app/cart.py]
   ┌──────────────────────────────────────────────────────────────┐
   │ Greenprint blocked this edit to app/cart.py.                  │  <- BLOCK (no proof yet)
   │ No failing test has reproduced the bug yet. Run /repro.        │
   └──────────────────────────────────────────────────────────────┘

Claude ▸ /repro  -> writes tests/test_repro_cart_total.py, runs it
   GREENPRINT: RED registered. The bug is reproduced (1 failing assertion).   <- 🔴 RED
   status line:  🔴 Greenprint: RED · cart_total                              <- 🔴 RED (status bar)

Claude ▸ [edits app/cart.py, now ALLOWED]  fixes range(len-1) -> range(len)
   status line:  🟢 Greenprint: GREEN · cart_total                            <- 🟢 GREEN (flips live)

Claude ▸ [tries to finish while still red?]  Stop gate:
   "Greenprint is holding this turn open. Do NOT report the work as done yet"  <- BLOCK at finish

Claude ▸ python3 .claude/hooks/greenprint.py check
   [✓ GREEN] cart_total   ->  ALL GREEN. Safe to finish.                      <- 🟢 GREEN
```

RED appears twice: the `RED registered` line from `/repro`, and the
`🔴 Greenprint: RED` status line at the bottom of Claude Code. GREEN appears when
the fix lands (the status line flips) and again on `check`. The two blocks
(before the edit, and at finish) are where the gate bites.

## How it works

Greenprint is one stdlib script (`.claude/hooks/greenprint.py`) wired through
`.claude/settings.json` as three hooks plus a status line:

| Hook | Fires on | What it does |
|------|----------|--------------|
| PreToolUse | `Edit` / `Write` / `MultiEdit` | If you edit a source file with no registered failing test, deny and point to `/repro`. Records the attempt the instant the hook fires. |
| PostToolUse | edits and `Bash` | Records changed files; re-runs the guard so the status line flips RED to GREEN the moment a fix lands. |
| Stop | end of every turn | Re-runs the registered test(s). Blocks finishing while any is RED, or while a changed source file has no passing test. |
| statusLine | continuously | Shows `🔴 RED` / `🟢 GREEN` / `clear`. Read-only and fast. |

### Phase 1 is the reliable core; Phase 2 is the visual beat

The flashy pre-edit "BLOCKED" relies on the harness honoring a PreToolUse `deny`,
which is [known to be intermittently ignored for the Edit tool](https://github.com/anthropics/claude-code/issues/37210).
Greenprint is built so that does not matter:

* The PreToolUse hook records the edit attempt as it fires, then returns the
  deny. State is armed on fire, not on the deny being honored.
* So if the deny ever leaks and the edit goes through, the Stop gate still sees a
  changed source file with no passing test and refuses to finish.

Enforcement degrades from "can't edit" to "can't finish," never to "no
enforcement." The Stop-block path uses the documented `{"decision":"block"}`
contract, which is reliable.

## Does it work on real code?

Yes, with one boundary set by the zero-install promise.

The gate logic (block the edit, track the change, hold the turn open until a test
passes, reject tests that error instead of asserting) is repo- and
language-agnostic. Drop `.claude/` into any repo and the hooks fire.

The bundled test runner is Python's standard-library `unittest`, so out of the
box Greenprint enforces on Python projects whose tests run under
`python3 -m unittest` and whose imports resolve from the repo root. That covers a
large share of real Python code with zero setup. Plain `assert` inside a
`unittest.TestCase` works too.

What to know:

* If the code under test imports third-party packages, those packages must be
  importable by the `python3` that runs the hook. Greenprint runs the test
  in-process; it does not install anything.
* pytest-only features (fixtures, parametrize) need pytest, which would break the
  no-install rule, so they are out of scope for the default runner.
* A non-Python repo is inert until you point it at your own test command.

To try it on your own repo: copy `.claude/` and `greenprint.config.json` in, run
`python3 .claude/hooks/greenprint.py doctor`, then ask Claude to fix a real bug.

## Configuration

`greenprint.config.json` is optional. Delete it and the defaults apply.

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `true` | Master switch. |
| `source_globs` | `["**/*.py"]` | What counts as guardable source. |
| `ignore_globs` | tests, `__init__.py`, docs, `*.md`, migrations, `.claude/**` ... | Paths that never need a test. The main knob if it ever over-nags. |
| `max_stop_blocks` | `8` | Safety valve: after this many holds in a row, the turn is released so you are never trapped. |
| `pretool_hard_block` | `false` | `false` is a clean deny and redirect to `/repro`. `true` also exits with code 2 to block harder (small risk of halting the turn on some builds). The Stop gate enforces correctness either way. |

### Escape hatches, so it never bricks real work

* `/greenprint allow <file>` lets one file be edited without a test this session.
* `/greenprint off` and `/greenprint on` toggle it for the session.
* `GREENPRINT_DISABLE=1` is an env-var kill switch.
* Add a path to `ignore_globs` for a permanent exemption.

## Dropping it into your own repo

1. Copy the `.claude/` folder and `greenprint.config.json` into your repo root.
2. If you already have `.claude/settings.json`, merge the `hooks` and `statusLine`
   keys from this one into yours instead of overwriting.
3. That is it. It works out of the box for Python projects using stdlib
   `unittest`.

## Honest limitations

* Python-first. The bundled, zero-install runner targets stdlib `unittest`. Other
  languages would need their own test command and toolchain, which would break
  the zero-setup promise, so it is intentionally out of scope here.
* Not adversary-proof. Greenprint enforces discipline against accidental
  false-fixes. An agent determined to bypass it could write files via raw `Bash`
  or disable the hooks, as it could with any hook-based guard. The point is that
  the honest path is the default path.
* Guards are session-scoped and reset when the Claude Code session changes, so
  stale state never blocks a fresh run.

## What's inside

```
.claude/
  settings.json                 wires the 3 hooks and the status line
  hooks/greenprint.py            the whole gate (stdlib only)
  skills/repro/SKILL.md          /repro, reproduce a bug with a RED test
  skills/greenprint/SKILL.md     /greenprint, status / allow / off / reset
  greenprint/state.json          session state (seeded empty)
greenprint.config.json           optional config (per-path opt-out lives here)
app/cart.py                      seeded off-by-one bug for the demo
tests/                           empty; /repro fills it
```

## License

MIT, Noormal Wardak.
