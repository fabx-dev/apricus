# M1 isolation adapter — preflight only

This is opt-in test instrumentation, **not production Apricus code**. It does
not offer a runtime launch command. Server/player/GUI constructors and session
mutations are blocked, even though their modules are imported for path checks.
Do not remove those guards without the user's separate live-test approval and
an independently usable backup/restoration procedure.

### Authorized live attempt: stopped and restored

`live_session.py` adds an explicit supervisor and a standalone stdlib recovery
copy, used only after separate user authorization. `launcher.py` remains
preflight-only. The first live attempt exposed denied native dconf access to
`/run/user/1000/dconf/user` and H.264 decoder errors; it was interrupted and the
original Snap restored. **Do not repeat live validation with the current guard.**
No filesystem exceptions were broadened. See the report's live-attempt section;
the earlier successful read-only probes do not establish live compatibility.

### Diagnosis without a new GNOME runtime

`diagnose_dconf.py` traces native host/guarded GSettings reads with `strace`,
including `pwrite64`. The normal dconf client initializes its real runtime marker
even for read APIs; the guarded probe never grants write permission. No settings
setter or Apricus application is invoked. `diagnose_codec.py` captures complete
verbose VLC logs for short dummy-output decoding, with/without the existing
guard, using private config/cache/data/temp. It does not reproduce an embedded
player or certify its renderer. Both retain before/after host-state evidence.

The preflight now fails on native dconf critical messages, even when values
match. **Current result: FAIL**, pending separate permission for the demonstrated
runtime-marker write requirement. No dconf exception has been applied.

### Approved existing-marker exception (supersedes the preceding status)

The user subsequently authorized **WRITE_FILE only** on the existing regular
file `/run/user/1000/dconf/user`. The preflight now passes its verified identity
to every interpreter; no directory rule is added. The guard requires UID/GID
1000, mode 0600, one link, no symlink in the path, the expected real runtime,
and matching device/inode. Missing/unsafe/replaced resources fail closed; no
creation, repair or replacement is attempted. The controller checks identity
every 50 ms while probes run and before/after; each guard and role checks too.
This is not an atomic filesystem replacement monitor: a future replacement
requires stopping/re-evaluation, not dynamically widening/rebinding the rule.

No TRUNCATE, MAKE_REG, REMOVE_FILE or parent-directory rights are granted.
Reads remain allowed by the original policy. WRITE_FILE is not byte-scoped and
can write other marker contents; the native client normally writes NUL at offset
1. This exception is for the runtime notification marker, not the configuration
database. Native dconf content initialization may update marker timestamps.

Repeat the approved syscall probe without starting Apricus:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/manual/m1_isolation/diagnose_dconf.py --allow-marker-write
```

Then run the usual preflight launcher. It retains database hashes/metadata,
real GSettings equality and stable-profile write-denial checks, adds marker
identity checks, same-size ftruncate denial, sibling/outside creation denial,
and denial of a write-open on the actual stable Snap config (no content write).
Latest result: **PASS**, `build/m1p-01ja42ur/`. This is NOT permission to run M1.

### Future VLC diagnostics, not enabled here

`vlc_diagnostics.py` is dormant tooling, with no import-time hook or VLC import.
For a separately approved future test, attach `NativeLog` to the real LibVLC
Instance immediately after creation and before media/player startup; use the
public file-logging API, then unset/close before Instance release. It adds no VLC
options and preserves the real binding class. Capture stderr separately because
the logging API cannot recover Instance-construction messages or FFmpeg's direct
stderr output. `snapshot()` can retain per-player mapped libraries/status and
media statistics at first-playing, first-error, and before teardown.

Before that future test, integrate these helpers only in the adapter, retain
complete logs (not only filtered errors), and wrap the controlled source launch
with `strace -f -yy` for openat/mmap/ftruncate/memfd_create/shm syscalls. Correlate
timestamps, decoder/vout module-selection and frame/allocation messages with
denied syscalls. Account for logging/tracing overhead in performance results.
The helper's FILE lifetime is unit-tested with a fake Instance; actual native
logging coverage and future live integration are **not tested/enabled** here.

Run from the dedicated M1 worktree using its independent environment:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/manual/m1_isolation/launcher.py
```

The launcher creates a fresh private `build/m1p-*/` directory; it never reuses
an existing profile. `report.json`, `host-before.json`, `host-after.json`,
`marker.json`, and `evidence/` are local evidence, not uploaded artifacts.
The marker fingerprints the adapter files used for that run. The existing
`.gitignore` already excludes both `build/` and `.venv/`.

## Design

- Keep HOME, XDG_CONFIG_HOME, XDG_RUNTIME_DIR, real session bus and displays.
  Never clone/link dconf or use a replacement/memory settings backend.
- Set cache/data/temp environment variables **only in probe processes**.
- Load `sitecustomize` before Apricus imports in each fresh interpreter. Patch
  application paths in `commons` before other modules capture aliases. Cover
  legacy import, media defaults, autostart, systemd path, export receipts and IPC.
- Apply a process-local Linux Landlock filesystem write boundary before importing
  application modules. It survives fork/exec; no sudo or AppArmor modification.
  Read/execute/network/session IPC remain real. Test roots are writable; host
  HOME/config/cache/runtime trees are not. Limited device I/O is allowed.
- Block Python GNOME-setting writes, extension toggles, systemd actions,
  external app launch and file choosers. These are not M1 preflight workflows.
- Adapt the application VLC option builder only to append the public `--config`
  option. Leave `vlc.Instance`, renderer/audio flags and player logic intact.
  Verify the flag with VLC **help**, without playing any media.
- Give WebKit's test factory a private WebsiteDataManager; inspect its actual
  base directories without constructing a WebView or starting a page.
- Check actual multiprocessing `spawn` and `forkserver` imports for server,
  GTK3 video/web players and GTK4 GUI, plus thread and exec workers. No toolkit
  application is constructed. Compare real GSettings in every probe with host
  CLI values; exercise safe config/cache/native-thumbnail writes and kernel
  write-denial probes. Independent bootstrap logs cover tracker/forkserver too.
  Escape aliases reference only a synthetic `control/sentinel` in the new test
  directory, never dconf or a stable user file.
- Startup bootstrap errors exit 78: Python's normal "log sitecustomize error and
  continue unisolated" behavior is deliberately not permitted.

## Limits

Linux x86_64 and Landlock ABI >= 3 are required; other systems fail closed.
Landlock is a filesystem-content guard, not a full sandbox: it does not mediate
session IPC, all metadata operations, or pre-existing write FDs. The adapter
rejects inherited regular write FDs outside its private directories, blocks
Python metadata writes elsewhere, and checks protected hashes/metadata. It is
not a security boundary against arbitrary malicious native code or a remote
service asked to write files. The reviewed preflight only uses read-only
session operations and creates no applications/portal choosers.

Native helper/GPU/audio/WebKit rendering compatibility remains **NOT TESTED**.
No new filesystem exception may be added silently if a live helper later fails;
stop and report the exact denied path. Native GNOME writes require separately
reviewed live-mode permissions/restoration, not a weaker private dconf view.
This instrumentation may affect startup cost; it is not an uninstrumented
release/package performance result.

Detailed path inventory, results and reversible next steps:
[`docs/notes/perf-m1.md`](../../../docs/notes/perf-m1.md).
