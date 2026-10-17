import logging
import os
import subprocess
import tempfile
import threading

import gi

gi.require_version("GnomeDesktop", "4.0")
from gi.repository import GdkPixbuf, Gio, GLib, GnomeDesktop

from apricus.commons import LOGGER_NAME
from apricus.utils import is_flatpak, is_snap

logger = logging.getLogger(LOGGER_NAME)


def _generate_thumbnail_flatpak(filename):
    # Inside Flatpak/Snap, DesktopThumbnailFactory runs the thumbnailer via
    # `flatpak-spawn --sandbox`, where glycin (which writes the PNG on
    # recent GNOME runtimes) can't spawn its own sandboxed loader — nested
    # sandboxes are blocked, so every thumbnail fails. Run the bundled
    # thumbnailer (or ffmpeg fallback) directly instead.
    import shutil

    with tempfile.TemporaryDirectory() as tmp_dir:
        output = os.path.join(tmp_dir, "thumbnail.png")
        if shutil.which("totem-video-thumbnailer"):
            subprocess.run(
                ["totem-video-thumbnailer", "-s", "256", filename, output],
                check=True,
                timeout=60,
            )
        else:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    "00:00:01",
                    "-i",
                    filename,
                    "-vframes",
                    "1",
                    "-vf",
                    "scale=256:-1",
                    output,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=60,
            )
        return GdkPixbuf.Pixbuf.new_from_file(output)


_thumb_factory = None
_thumb_factory_lock = threading.Lock()


def _get_thumb_factory():
    global _thumb_factory
    if _thumb_factory is None:
        with _thumb_factory_lock:
            if _thumb_factory is None:
                _thumb_factory = GnomeDesktop.DesktopThumbnailFactory()
    return _thumb_factory


def generate_thumbnail(filename):
    """Generate and cache a thumbnail. Returns its path, or None if one can't
    be produced (e.g. no usable thumbnailer, or it failed)."""
    factory = _get_thumb_factory()
    try:
        mtime = os.path.getmtime(filename)
    except OSError:
        return None
    file = Gio.file_new_for_path(filename)
    uri = file.get_uri()
    try:
        info = file.query_info("standard::content-type", Gio.FileQueryInfoFlags.NONE, None)
        mime_type = info.get_content_type()
    except GLib.Error:
        return None

    with _thumb_factory_lock:
        try:
            cached = factory.lookup(uri, mtime)
        except Exception:  # noqa: BLE001 - corrupt cache must not kill thumbs
            cached = None
        if cached is not None:
            return cached

    # Inside Flatpak/Snap the .thumbnailer files for video (totem/ffmpeg)
    # are not shipped, so can_thumbnail() is always False even though our
    # direct ffmpeg fallback below works fine. Skip the gate there.
    if not (is_flatpak() or is_snap()):
        with _thumb_factory_lock:
            try:
                if not factory.can_thumbnail(uri, mime_type, mtime):
                    return None
            except Exception:  # noqa: BLE001
                return None

    if is_flatpak() or is_snap():
        try:
            pixbuf = _generate_thumbnail_flatpak(filename)
        except Exception as e:  # noqa: BLE001 - ffmpeg/totem failure
            logger.debug(f"[Thumbnail] fallback generation failed: {e}")
            return None
    else:
        try:
            pixbuf = factory.generate_thumbnail(uri, mime_type)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[Thumbnail] generation failed: {e}")
            return None
    if pixbuf is None:
        return None

    with _thumb_factory_lock:
        try:
            factory.save_thumbnail(pixbuf, uri, mtime)
            return factory.lookup(uri, mtime)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[Thumbnail] cache save failed: {e}")
            return None


def _set_thumbnail(list_store, idx, video_path, thumbnail, token=0, current_token=None):
    # Runs on the GTK main thread (via idle_add): ListStore is not thread-safe.
    if current_token is not None and current_token() != token:
        return False
    try:
        list_store[idx][0] = GdkPixbuf.Pixbuf.new_from_file_at_size(thumbnail, -1, 96)
    except (GLib.Error, OSError, IndexError) as e:
        logger.debug("[Thumbnail] Skipped %s: %s", os.path.basename(video_path), e)
    return False


def get_thumbnail(video_path, list_store, idx, token=0, current_token=None):
    # Best-effort: a preview thumbnail must never crash the GUI. On failure the
    # generic video icon set by the caller stays in place. (In the Flatpak the
    # sandboxed thumbnailer can fail; that's fine, we just skip the preview.)
    # Heavy work (thumbnail lookup/generation) runs in a pool worker thread;
    # the ListStore update is marshalled to the main thread. Stale results
    # (gallery rebuilt while workers were in flight) are dropped via token.
    try:
        info = Gio.File.new_for_path(video_path).query_info(
            "thumbnail::path", Gio.FileQueryInfoFlags.NONE, None
        )
        thumbnail = info.get_attribute_byte_string("thumbnail::path") or generate_thumbnail(
            video_path
        )
        if thumbnail:
            if current_token is not None and current_token() != token:
                return
            GLib.idle_add(
                _set_thumbnail, list_store, idx, video_path, thumbnail, token, current_token
            )
    except (GLib.Error, OSError, subprocess.SubprocessError) as e:
        logger.debug("[Thumbnail] Skipped %s: %s", os.path.basename(video_path), e)


def debounce(wait_time):
    """
    Decorator that will debounce a function so that it is called after wait_time seconds
    If it is called multiple times, will wait for the last call to be debounced and run only this one.
    Reference:
    https://github.com/salesforce/decorator-operations/blob/master/decoratorOperations/debounce_functions/debounce.py
    """

    def decorator(function):
        def debounced(*args, **kwargs):
            def call_function():
                debounced._timer = None
                return function(*args, **kwargs)

            if debounced._timer is not None:
                debounced._timer.cancel()

            debounced._timer = threading.Timer(wait_time, call_function)
            debounced._timer.start()

        debounced._timer = None
        return debounced

    return decorator
