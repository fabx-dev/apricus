# Preserved M2 source live harness changes

The two launcher changes left by the interrupted live run are retained, not
discarded. They are test-harness corrections, separate from the D-Bus fix:

- `tests/manual/m1_native/session.py` accepts `APRICUS_TEST_PYTHON` as an
  explicit existing dependency interpreter, checks that the file exists, and
  passes it to the source launcher with `-S`. Without the variable it retains
  the worktree-local `.venv/bin/python` default.
- `tests/manual/m1_native/source_entry.py` checks the interpreter prefix
  against that selected venv. It compares the launcher's lexical parent path,
  not `Path(binary).resolve()`: venv Python is a symlink to `/usr/bin/python3`,
  and resolving the binary incorrectly selects `/usr` as the venv prefix.
  The actual source root remains this worktree; `-S`, no-sitecustomize checks,
  and the no-Landlock probe remain in place. Reusing installed dependencies
  does not install packages or modify the dependency venv.

These changes were necessary because M2 has no local dependency venv. They
are not temporary production workarounds. An operator should supply an
absolute interpreter path and review it before any future authorized launch.
The harness still affects real user configuration during a live run; this
change does not certify its backup provenance or recovery on its own. A fresh
verified backup and separate live authorization remain mandatory. No launcher
or probe was executed as part of this correction session.

The next pause observation must follow the timing protocol in
`m2-dbus-snapshot.md`, not the previous one-second observation.
