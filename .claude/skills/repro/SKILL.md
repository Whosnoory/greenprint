---
name: repro
description: Use when asked to fix a bug, error, crash, or wrong/incorrect behavior. Reproduces the bug with a failing (RED) stdlib unittest BEFORE any source edit, registers it with Greenprint, and keeps the turn open until that test passes (GREEN). Required because Greenprint blocks source edits until a RED test exists.
---

# /repro: reproduce a bug with a failing test first

Greenprint will not let you edit source code to fix a bug until a test has
**failed for the right reason** (proving the bug is real), and will not let you
end the turn until that test **passes** (proving the fix works). This skill is
how you satisfy that gate. No production code without a failing test first.

Use Python's standard-library `unittest` only, no pytest, no pip, no extra
deps. Tests go in `tests/test_repro_<symbol>.py`.

## Steps

1. **Identify the target.** From the bug description, find the source file and
   the function/symbol that is wrong (e.g. `app/cart.py`, `cart_total`). Read it.

2. **Write a failing test** at `tests/test_repro_<symbol>.py` that asserts the
   *correct* behavior, so it fails against the current buggy code. Import the
   target from the repo root. Template:

   ```python
   import unittest
   from app.cart import cart_total  # adjust import to the real target

   class TestCartTotalRepro(unittest.TestCase):
       def test_includes_every_item(self):
           # Correct behavior: total is the sum of ALL prices.
           self.assertEqual(cart_total([10, 20, 30]), 60)

   if __name__ == "__main__":
       unittest.main()
   ```

   Writing the test file is allowed. Greenprint never blocks edits under
   `tests/`.

3. **Run it through Greenprint** so the failure is verified and the guard is
   registered (pass the target file and symbol):

   ```bash
   python3 .claude/hooks/greenprint.py runtest tests/test_repro_<symbol>.py --target <path/to/source.py> --symbol <symbol>
   ```

   - `GREENPRINT: RED registered` → the bug is reproduced. Continue to step 4.
   - `GREENPRINT: ERROR` → the test could not run (import/syntax/runtime error).
     That is **not** a valid reproduction. Fix the test so it fails on an
     `assertEqual`/`assert*`, then re-run.
   - `GREENPRINT: PASS` → the test does not actually exercise the bug. Tighten
     the assertion so it fails against the current behavior, then re-run.

4. **Fix the source.** Now edit the target file. Greenprint allows it because a
   guard covers that file.

5. **Confirm GREEN before you finish:**

   ```bash
   python3 .claude/hooks/greenprint.py check
   ```

   Only report the bug as fixed when this prints `ALL GREEN`. If you try to end
   the turn while it is still RED, the Stop gate will send you back here.
