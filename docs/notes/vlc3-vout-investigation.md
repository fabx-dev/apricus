# LibVLC 3 video-output selection for embedded playback (verdict C)

Date: 2026-10-22. VLC 3.0.23 (Ubuntu `3.0.23-1`), python-vlc, X11 embedding
via `MediaPlayer.set_xwindow()`. Question: can Apricus select the VLC
video-output module (`opengl`, `xcb_x11`, `xcb_xv`) for its wallpaper
windows? Answer: **no, not with any supported LibVLC 3 mechanism.**

## Result

Every selection path was tested live and all of them end with the
vout-display probe running with filter **`matching "any"`**, i.e. VLC
uses its automatic pick (`gl` on this stack). The Settings renderer
selector was therefore removed: it respawned the player but could never
change the real output module.

## Root cause (VLC 3.0.23 source)

`lib/media_player.c:626` — every `libvlc_media_player_new()` creates its
own **empty** `vout` variable:

```c
var_Create (mp, "vout", VLC_VAR_STRING|VLC_VAR_DOINHERIT);
```

The lookup chain for an embedded window is:

1. `src/video_output/vout_wrapper.c:64,67` — the display is created with
   the variable reference `"$vout"`.
2. `src/modules/modules.c:224-228` — `$vout` resolves through
   `var_InheritString(obj, "vout")`; NULL/empty becomes `"any"`.
3. `src/video_output/display.c:82,109` — `module_need(vd, "vout display", …)`.
4. The vout thread is parented to the input resource's parent
   (`src/input/resource.c:241,286` → `src/video_output/video_output.c:259`
   `VoutCreate`), and in the libvlc path that resource belongs to the
   media player itself (`lib/media_player.c:742,987`).
5. So the walk is `vout-display → vout → media player`, and the walk
   **stops at the media player**, which owns an empty `vout` variable
   (`src/misc/variables.c:1177-1181`). The `--vout` value stored on the
   libvlc object by `src/config/cmdline.c`, and the global config
   fallback (`src/config/core.c:156`), are never reached.

`cvlc` works because its input resource is parented to the playlist
(`src/playlist/engine.c:272`), which creates no `vout` variable, so the
walk reaches the command-line value on the libvlc object. The
`MediaListPlayer` path is shadowed too: it wraps an internal
`libvlc_media_player_new` (`lib/media_list_player.c:501`).

## Selection paths tested (all ignored)

- `vlc.Instance(["--vout=xcb_x11"])`, `=` and space forms, string and
  list forms.
- Trusted media option `:vout=xcb_x11` (`libvlc_media_add_option` is
  `UNIQUE|TRUSTED`, `lib/media.c:524-526`): lands on the *input* object,
  which is not an ancestor of the vout in this path.
- `vout=` in a custom vlcrc via `--config`.
- Unknown values (e.g. `--vout=bogus-vout-123`): silently accepted,
  same `"any"` result — there is no validation feedback at all.

Reproducer: isolated process per case (one video, one Gtk3/X11 window),
comparing actually mapped `video_output/*.so` files plus VLC's own probe
lines. All cases mapped `egl_x11/gl/glx` and played. A minimal C program
against libvlc reproduced the same `"any"`, ruling out a python-vlc
artifact.

## Embedding limits (`set_xwindow`)

`set_xwindow` does not constrain the module choice per se — via the
`cvlc --drawable-xid` path, `xcb_x11` embeds with no errors and `opengl`
embeds after an internal egl→glx fallback (only `vaDeriveImage` fails,
no HW in this VM). `xcb_xv` loads (`using adaptor glamor textured
video`) but fails frame creation in this VM. All three modules ship in
the distro package; selectability, not availability, is the blocker.

## Alternatives considered (not implemented)

- **LibVLC video callbacks** (`video_set_callbacks` + `video_set_format`,
  public API): the only supported escape hatch — render to memory and
  composite in Apricus. Costs: per-frame CPU copy + Gtk upload, no HW
  presentation, substantial new format/scale/thread code, and it is an
  architectural change. Compatible with Wayland by construction.
- **Custom OpenGL rendering**: same family, higher complexity (GL
  context management inside Gtk3).
- **LibVLC 4 rendering APIs**: VLC 4 is not packaged for the supported
  distros; would require a custom VLC build (rejected).
- **Keep VLC automatic + Shell background-layer adoption** (current):
  zero cost, works live, icons stay above video regardless of module.

## Consequences for Apricus

- Settings no longer exposes a renderer selector; the respawn/rollback
  machinery tied to it was removed.
- `server.respawn_player()` is kept as a lifecycle primitive (mode
  reloads); the player watchdog (`playback_error`, first-frame
  deadline) is kept as dead-stream detection.
- `video_renderer` config key, ids, `VOUT_MODULES` and
  `resolve_renderer_vout()` are kept for backward compatibility (old
  configs load unchanged) and forward compatibility (canonical names
  for a future libvlc). The computed value is currently inert.
- `APRICUS_VOUT` is still accepted and canonicalized, but equally
  inert under LibVLC 3; no claim to the contrary is made anywhere.
