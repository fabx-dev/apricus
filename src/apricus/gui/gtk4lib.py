# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK4 video library: GridView + async thumbnails (Fase D).

Gio.ListStore of VideoItem + Gtk.MultiSelection + Gtk.SignalListItemFactory.
Thumbnails load in a bounded pool; stale results (item recycled or gallery
rebuilt) are dropped by (generation, path) checks. Main-thread updates only.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

from apricus.commons import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gtk4thumb")
_texture_cache = {}
_TEXTURE_CACHE_MAX = 128


def _cache_put(key, texture):
    if texture is None:
        return
    if key in _texture_cache:
        _texture_cache.pop(key, None)
    elif len(_texture_cache) >= _TEXTURE_CACHE_MAX:
        _texture_cache.pop(next(iter(_texture_cache)), None)
    _texture_cache[key] = texture


class VideoItem(GObject.Object):
    path = GObject.Property(type=str)
    name = GObject.Property(type=str)
    texture = GObject.Property(type=Gdk.Texture)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.name = os.path.basename(path)
        self.texture = _texture_cache.get((path, 192))


def _load_pixbuf(video_path, width=192):
    """Decode a thumbnail pixbuf in a worker thread. Texture creation stays
    on the GTK main thread (Gdk.Texture is display-bound)."""
    from apricus.gui.gui_utils import generate_thumbnail

    thumb = generate_thumbnail(video_path)
    source = thumb or video_path
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_size(source, width, -1)
    except (GLib.Error, OSError) as e:
        logger.debug("[GTK4Thumb] Skipped %s: %s", os.path.basename(video_path), e)
        return None


def _factory_setup(_factory, item):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    picture = Gtk.Picture()
    picture.set_size_request(192, 108)
    picture.set_can_shrink(True)
    label = Gtk.Label()
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_max_width_chars(22)
    box.append(picture)
    box.append(label)
    item.set_child(box)
    item._picture = picture
    item._label = label
    item._bound_path = None


def _factory_bind(_factory, item):
    video = item.get_item()
    if video is None:
        return
    list_item = item
    list_item._label.set_text(video.name)
    list_item._bound_path = video.path
    texture = video.texture
    if texture is not None:
        list_item._picture.set_paintable(texture)
        return
    list_item._picture.set_paintable(None)

    def _done(future):
        path = video.path

        def _apply():
            # Item may have been recycled or gallery rebuilt: verify.
            if list_item._bound_path != path:
                return False
            try:
                pixbuf = future.result()
            except Exception as e:  # noqa: BLE001 - best effort
                logger.debug("[GTK4Thumb] %s: %s", os.path.basename(path), e)
                return False
            if pixbuf is not None:
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                _cache_put((path, 192), texture)
                video.texture = texture
                list_item._picture.set_paintable(texture)
            return False

        GLib.idle_add(_apply)

    _pool.submit(lambda: _load_pixbuf(video.path)).add_done_callback(_done)


def make_factory():
    factory = Gtk.SignalListItemFactory()
    factory.connect("setup", _factory_setup)
    factory.connect("bind", _factory_bind)
    return factory


def load_texture_async(video, callback, width=192):
    """Decode a thumbnail for *video* off-thread, then call
    ``callback(texture_or_None)`` on the GTK main loop.

    The caller owns staleness checks (recycled rows): capture the bound
    path and ignore the callback when it no longer matches.
    """

    def _emit(texture):
        callback(texture)
        return False

    if video.texture is not None and width == 192:
        GLib.idle_add(_emit, video.texture)
        return
    cached = _texture_cache.get((video.path, width))
    if cached is not None:
        if width == 192:
            video.texture = cached
        GLib.idle_add(_emit, cached)
        return

    def _done(future):
        def _apply():
            try:
                pixbuf = future.result()
            except Exception as e:  # noqa: BLE001 - best effort
                logger.debug("[GTK4Thumb] %s: %s", os.path.basename(video.path), e)
                return False
            texture = None
            if pixbuf is not None:
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                _cache_put((video.path, width), texture)
                if width == 192:
                    video.texture = texture
            callback(texture)
            return False

        GLib.idle_add(_apply)

    _pool.submit(lambda: _load_pixbuf(video.path, width)).add_done_callback(_done)


def build_store(video_paths):
    store = Gio.ListStore.new(VideoItem)
    for path in video_paths:
        store.append(VideoItem(path))
    return store


def selected_paths(selection, store):
    """Return selected video paths in order."""
    paths = []
    bitset = selection.get_selection()
    for index in range(store.get_n_items()):
        if bitset.contains(index):
            item = store.get_item(index)
            if item is not None:
                paths.append(item.path)
    return paths
