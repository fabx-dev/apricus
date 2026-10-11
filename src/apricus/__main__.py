import argparse
import logging
import os
import sys

from apricus import perf, server  # perf first: import captures process-start T0
from apricus.commons import LOGGER_NAME, VIDEO_WALLPAPER_DIR
from apricus.utils import (
    is_flatpak,
    is_gnome,
    is_wayland,
    setup_systemd_autostart,
)

logger = logging.getLogger(LOGGER_NAME)


# TODO: Add locale support
def main(version="devel", pkgdatadir="/usr/share/apricus", localedir="/usr/share/locale"):
    # Make sure that X11 is the backend. Revert Wayland to XWayland.
    os.environ["GDK_BACKEND"] = "x11"
    # Suppress VLC Log
    os.environ["VLC_VERBOSE"] = "-1"

    parser = argparse.ArgumentParser(description=f"Apricus v{version}")
    parser.add_argument(
        "-p",
        "--pause",
        dest="p",
        type=int,
        default=0,
        help="Add pause before launching Apricus. [sec]",
    )
    parser.add_argument(
        "-b", "--background", action="store_true", help="Launch only the live wallpaper."
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Low-contention login startup: implies -b and skips the "
        "background static-wallpaper refresh (cached frame only).",
    )
    parser.add_argument("-d", "--debug", action="store_true", help="Print debug messages.")
    parser.add_argument("-r", "--reset", action="store_true", help="Reset user configuration.")
    parser.add_argument(
        "--systemd-enable",
        action="store_true",
        help="Install and enable user systemd autostart, then exit.",
    )
    parser.add_argument(
        "--systemd-disable",
        action="store_true",
        help="Disable and remove user systemd autostart, then exit.",
    )
    args = parser.parse_args()

    if args.systemd_enable or args.systemd_disable:
        ok = setup_systemd_autostart(bool(args.systemd_enable))
        action = "enabled" if args.systemd_enable else "disabled"
        print(f"systemd autostart {action}" if ok else "systemd autostart change FAILED")
        return 0 if ok else 1

    if args.fast:
        args.background = True
        os.environ["APRICUS_FAST"] = "1"

    # Setup logger
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        # Propagate to forkserver children (they are fresh interpreters).
        os.environ["APRICUS_DEBUG"] = "1"

    # Log system information
    sys_info = []
    sys_info.append("--- System information ---")
    sys_info.append(f"is_gnome = {is_gnome()}")
    sys_info.append(f"is_wayland = {is_wayland()}")
    sys_info.append(f"is_flatpak = {is_flatpak()}")
    sys_info.append("--------------------------")
    sys_info_str = "\n".join(sys_info)
    logger.info(f"Apricus v{version}\n{sys_info_str}")
    logger.info(f"[Args] {vars(args)}")
    perf.mark("main: imports done")

    # Make video folder if not exist
    os.makedirs(VIDEO_WALLPAPER_DIR, exist_ok=True)

    # Clear sys.argv as it has influence to the Gtk.Application
    sys.argv = [sys.argv[0]]
    perf.mark("main: starting server")
    server.main(version, pkgdatadir, localedir, args)


if __name__ == "__main__":
    main()
