import logging
import os

from apricus.commons import LOGGER_NAME


class _LazyYTDLP:
    """Lazy proxy for yt_dlp so importing yt_utils costs 0 ms until extraction."""

    _mod = None

    def __getattr__(self, name):
        if self._mod is None:
            import yt_dlp

            type(self)._mod = yt_dlp
        return getattr(self._mod, name)


youtube_dl = _LazyYTDLP()

logger = logging.getLogger(LOGGER_NAME)

#: Flatpak install path of the bundled Deno binary (see pkgs/flatpak/deno.json).
FLATPAK_DENO_PATH = "/app/bin/deno"


#: yt-dlp releases before this cannot handle current YouTube player
#: responses (challenge/PO-token era): extraction ends with no formats.
MIN_YT_DLP_VERSION = (2025, 10, 1)


def _yt_dlp_version():
    """Running yt-dlp version as a (year, month, day) tuple, or None.

    None when yt-dlp is not installed (lazy import raises) or the version
    string is unparseable. Never raises.
    """
    try:
        version = youtube_dl.version.__version__
    except Exception:  # noqa: BLE001 - missing module or attribute
        return None
    try:
        parts = str(version).split(".")
        return (int(parts[0]), int(parts[1]), int(parts[2].split(".")[0]))
    except (IndexError, ValueError):
        return None


def js_runtime_options():
    """Explicit yt-dlp JavaScript runtime selection.

    yt-dlp enables only ``deno`` by default and silently degrades to
    partial extraction when no engine is found. Point it at the bundled
    binary when present (Flatpak); otherwise rely on ``PATH`` lookup and
    let yt-dlp warn, so traditional installs keep working without Deno.
    """
    if os.path.isfile(FLATPAK_DENO_PATH):
        return {"deno": {"path": FLATPAK_DENO_PATH}}
    snap_dir = os.environ.get("SNAP") or ""
    if snap_dir:
        for rel in (("usr", "bin", "deno"), ("bin", "deno")):
            snap_deno = os.path.join(snap_dir, *rel)
            if os.path.isfile(snap_deno):
                return {"deno": {"path": snap_deno}}
    return {}


def base_options(*, quiet=False):
    """Shared YoutubeDL options: no playlists, bounded sockets, JS engine.

    The default ``android_vr`` YouTube client yields ``googlevideo.com`` URLs
    that return HTTP 403 when opened directly by external players like VLC;
    ``web_embedded`` (with ``tv_simply`` / ``web`` fallbacks) produces standard
    HTTPS streams playable by libVLC.
    """
    import shutil

    js_runtimes = js_runtime_options()
    has_deno = bool(js_runtimes or shutil.which("deno"))
    clients = ["web_embedded"] if has_deno else ["web_embedded", "tv_simply", "ios"]
    options = {
        "noplaylist": True,
        "socket_timeout": 15,
        "remote_components": [],
        "extractor_args": {
            "youtube": {
                "player_client": clients,
            },
        },
    }
    if js_runtimes:
        # Only pin when we know a binary (Flatpak/Snap bundle). Passing an
        # empty dict would override yt-dlp's default PATH discovery and
        # disable runtimes instead of leaving them enabled.
        options["js_runtimes"] = js_runtimes
    if quiet:
        options["quiet"] = True
    return options


def _meta_cache_path():
    cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(cache_home, "apricus", "ytdlp-meta-cache.json")


def get_cached_meta(raw_url, ttl_s=180):
    import json
    import time

    try:
        with open(_meta_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        entry = data.get(raw_url)
        if isinstance(entry, dict) and (time.time() - float(entry.get("ts", 0))) < ttl_s:
            meta = entry.get("meta")
            if isinstance(meta, dict) and meta.get("formats"):
                return meta
    except Exception:  # noqa: BLE001
        pass
    return None


def save_cached_meta(raw_url, meta):
    import json
    import time

    if not isinstance(meta, dict) or not meta.get("formats"):
        return
    slim_formats = []
    for fmt in meta.get("formats") or []:
        if isinstance(fmt, dict) and fmt.get("url"):
            slim_formats.append(
                {
                    "format_id": fmt.get("format_id"),
                    "url": fmt.get("url"),
                    "ext": fmt.get("ext"),
                    "vcodec": fmt.get("vcodec"),
                    "acodec": fmt.get("acodec"),
                    "width": fmt.get("width"),
                    "height": fmt.get("height"),
                    "quality": fmt.get("quality"),
                    "tbr": fmt.get("tbr"),
                }
            )
    if not slim_formats:
        return
    slim = {
        "title": meta.get("title"),
        "uploader": meta.get("uploader"),
        "channel": meta.get("channel"),
        "duration": meta.get("duration"),
        "height": meta.get("height"),
        "thumbnail": meta.get("thumbnail"),
        "is_live": meta.get("is_live"),
        "live_status": meta.get("live_status"),
        "formats": slim_formats,
    }
    path = _meta_cache_path()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:  # noqa: BLE001
            data = {}
        now = time.time()
        data = {
            k: v
            for k, v in data.items()
            if isinstance(v, dict) and (now - float(v.get("ts", 0))) < 180
        }
        data[raw_url] = {"ts": now, "meta": slim}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def get_formats(raw_url):
    from apricus.utils import public_http_url, valid_http_url

    if not valid_http_url(raw_url):
        raise ValueError(f"URL not supported: {raw_url}")
    if not public_http_url(raw_url):
        raise ValueError("Local network address not supported.")
    info = get_cached_meta(raw_url)
    if info is None:
        try:
            with youtube_dl.YoutubeDL(base_options()) as ydl:
                info = ydl.extract_info(raw_url, download=False) or {}
        except ImportError as e:
            raise ValueError("yt-dlp is not installed. Install yt-dlp to play streams.") from e
        except Exception as e:
            if "no video formats found" in str(e).lower():
                version = _yt_dlp_version()
                if version is not None and version < MIN_YT_DLP_VERSION:
                    raise ValueError(
                        f"yt-dlp {version[0]}.{version[1]:02d} is too old to "
                        "extract this video. Update yt-dlp and try again."
                    ) from e
                raise ValueError("This video could not be extracted. Try again later.") from e
            raise
        save_cached_meta(raw_url, info)
    formats = info.get("formats") or []
    if not formats:
        raise ValueError("No playable formats found for this stream.")
    return formats


def classify_stream_error(message):
    """Map a yt-dlp failure to a user-facing message (pure logic)."""
    low = (message or "").lower()
    if "sign in to confirm" in low or "captcha" in low:
        return "YouTube is asking for bot verification. Try again later."
    if "rate-limit" in low or "rate limit" in low or "too many requests" in low:
        return "Rate limited. Try again later."
    if "age-restricted" in low or "age-verification" in low:
        return "Age-restricted: login required."
    if "geo-restricted" in low or "geo-blocked" in low:
        return "Not available in your region."
    if "private" in low or "login" in low or "sign in" in low:
        return "This video needs login or is private."
    if "drm" in low or "widevine" in low:
        return "DRM-protected streams are not supported."
    if "po token" in low or "challenge" in low or "signature" in low:
        return "Video protection needs a JavaScript runtime. Some formats may be missing."
    if "javascript runtime" in low or "js runtime" in low or "no supported" in low:
        return "Video protection needs a JavaScript runtime. Some formats may be missing."
    if "unsupported url" in low:
        return "URL not supported."
    if "no module named" in low:
        return "yt-dlp is not installed. Install yt-dlp to play streams."
    if "no video formats found" in low:
        return (
            "This video could not be extracted. If the problem persists, "
            "update yt-dlp to the latest release and try again."
        )
    if "no playable formats" in low:
        return "No playable formats found for this stream."
    if "404" in low or "not found" in low:
        return "Not found (404)."
    msg = message or ""
    return " ".join(msg.split(" ")[1:]) or msg or "Unknown stream error"


def filter_audio(formats):
    return filter(lambda x: x.get("acodec") != "none" and x.get("vcodec") == "none", formats)


def filter_video(formats):
    return filter(lambda x: x.get("acodec") == "none" and x.get("vcodec") != "none", formats)


def filter_audio_video(formats):
    return filter(lambda x: x.get("acodec") != "none" and x.get("vcodec") != "none", formats)


def _dims(fmt):
    """(width, height) of a yt-dlp format as ints, None when unknown.

    yt-dlp documents ints, but the generic extractor (direct links) may
    omit the keys or report None; anything else (strings, bools) is
    treated as unknown rather than guessed.
    """
    dims = []
    for key in ("width", "height"):
        value = fmt.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            dims.append(None)
        else:
            dims.append(int(value))
    return dims[0], dims[1]


def get_best(formats):
    filtered = list(filter_audio_video(formats))
    best = max(filtered, key=lambda x: x.get("quality", -1))
    width, height = _dims(best)
    return best["url"], width, height


def get_best_audio(formats):
    filtered = list(filter_audio(formats))
    if not filtered:
        filtered = list(filter_audio_video(formats))
    if not filtered:
        raise ValueError("No audio formats found for this stream.")
    best = max(filtered, key=lambda x: x.get("quality", -1))
    return best["url"]


def get_best_video(formats):
    filtered = list(filter_video(formats))
    if not filtered:
        filtered = list(filter_audio_video(formats))
    best = max(filtered, key=lambda x: x.get("quality", -1))
    width, height = _dims(best)
    return best["url"], width, height


def get_optimal_video(formats, height):
    """Pick the video format closest to *height*, with safe fallbacks.

    Formats without usable dimensions never raise: they sort after every
    format with a known height, and the winner reports None for unknown
    dimensions (the player falls back to video_get_size()). When no
    format passes the codec filters but formats exist (direct links via
    the generic extractor carry no codec metadata), the first format is
    used — its URL is the playable file. An empty list still raises
    ValueError (get_formats never returns one).
    """
    filtered = list(filter_video(formats))
    if not filtered:
        filtered = list(filter_audio_video(formats))
    if not filtered:
        filtered = list(formats)

    def _distance(fmt):
        _width, fmt_height = _dims(fmt)
        if fmt_height is None:
            return float("inf")
        return abs(fmt_height - height)

    best = min(filtered, key=_distance)
    width, best_height = _dims(best)
    return best["url"], width, best_height


if __name__ == "__main__":
    import vlc

    test_url = "https://www.youtube.com/watch?v=hdf9-E0Rt8Q"
    formats = get_formats(test_url)
    instance = vlc.Instance()
    player = instance.media_player_new()
    Media = instance.media_new(get_optimal_video(formats, 1080))
    player.set_media(Media)
    player.add_slave(vlc.MediaSlaveType(1), get_best_audio(formats), True)
    player.play()

    while True:
        pass
