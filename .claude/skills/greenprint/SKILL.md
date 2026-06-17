---
name: greenprint
description: Use to inspect or control the Greenprint TDD gate: check what is RED/GREEN, temporarily disable it, allow a specific file to be edited without a test for this session, or reset the session's guards. The human's escape hatch when Greenprint blocks a change that is not a bug fix.
---

# /greenprint: control and inspect the gate

Greenprint enforces "no source fix without a proving test". These commands let
you see its state and get unstuck when a block is not warranted.

## Commands

- **Status**: show every guard (RED/GREEN/ERROR) and any changed-but-unproven file:
  ```bash
  python3 .claude/hooks/greenprint.py check
  ```

- **Allow one file without a test (this session)**: for a legitimate change that
  is not a bug fix:
  ```bash
  python3 .claude/hooks/greenprint.py allow <path/to/file.py>
  ```

- **Disable / re-enable for this session:**
  ```bash
  python3 .claude/hooks/greenprint.py off
  python3 .claude/hooks/greenprint.py on
  ```

- **Reset the session's guards and touched files:**
  ```bash
  python3 .claude/hooks/greenprint.py reset
  ```

- **Self-check the install / environment:**
  ```bash
  python3 .claude/hooks/greenprint.py doctor
  ```

## Notes

- Permanent exemptions (docs, generated code, migrations) belong in
  `ignore_globs` in `greenprint.config.json`, not in a per-session `allow`.
- A full kill switch with no edits: set the env var `GREENPRINT_DISABLE=1`.
