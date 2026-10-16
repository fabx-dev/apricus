import logging
import os

import yt_dlp as youtube_dl

from apricus.commons import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

#: Flatpak install path of the bundled Deno binary (see pkgs/flatpak/deno.json).
FLATPAK_DENO_PATH = "/app/bin/deno"


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
        snap_deno = os.path.join(snap_dir, "bin", "deno")
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
    options = {
        "noplaylist": True,
        "socket_timeout": 15,
        "remote_components": [],
        "extractor_args": {
            "youtube": {
                "player_client": ["web_embedded", "tv_simply", "web"],
            },
        },
    }
    js_runtimes = js_runtime_options()
    if js_runtimes:
        # Only pin when we know a binary (Flatpak bundle). Passing an
        # empty dict would override yt-dlp's default PATH discovery and
        # disable runtimes instead of leaving them enabled.
        options["js_runtimes"] = js_runtimes
    if quiet:
        options["quiet"] = True
    return options


def get_formats(raw_url):
    from apricus.utils import public_http_url, valid_http_url

    if not valid_http_url(raw_url):
        raise ValueError(f"URL not supported: {raw_url}")
    if not public_http_url(raw_url):
        raise ValueError(f"Local network address not supported: {raw_url}")
    with youtube_dl.YoutubeDL(base_options()) as ydl:
        info = ydl.extract_info(raw_url, download=False) or {}
    formats = info.get("formats") or []
    if not formats:
        raise ValueError(f"No playable formats for {raw_url}")
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
