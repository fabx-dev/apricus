# M1 isolation adapter — preflight only

This is opt-in test instrumentation, **not production Apricus code**. It does
not offer a runtime launch command. Server/player/GUI constructors and session
mutations are blocked, even though their modules are imported for path checks.
Do not remove those guards without the user's separate live-test approval and
an independently usable backup/restoration procedure.

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
