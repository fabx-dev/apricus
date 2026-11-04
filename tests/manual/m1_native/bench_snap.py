"""Snap resource sampler: host-side /proc aggregation. stdlib only.

Usage: /usr/bin/python3 bench_snap.py LABEL SECONDS
Roots the tree at the confined snap.apricus.apricus server process.
Writes build/snap-live/bench-LABEL.json.
"""

import json
import os
import sys
import time
from pathlib import Path

try:
    CLK = os.sysconf("SC_CLK_TICK")
except (ValueError, OSError):
    CLK = 100
NCPU = os.cpu_count() or 1
LABEL = "snap.apricus.apricus"


def is_snap_server(pid):
    try:
        if (Path(f"/proc/{pid}/comm")).read_text().strip() != "apricus-server":
            return False
        if (Path(f"/proc/{pid}/attr/current")).read_text().split()[0] != LABEL:
            return False
        return True
    except OSError:
        return False


def tree(root):
    kids = {root}
    changed = True
    while changed:
        changed = False
        for p in Path("/proc").glob("[0-9]*"):
            try:
                f = p.joinpath("stat").read_text().rsplit(")", 1)[1].split()
                if int(f[1]) in kids and int(p.name) not in kids:
                    kids.add(int(p.name))
                    changed = True
            except (OSError, ValueError):
                pass
    return kids


def sample(pids):
    rss = pss = threads = 0
    procs = 0
    times = {}
    for pid in pids:
        try:
            f = (Path(f"/proc/{pid}/stat")).read_text().rsplit(")", 1)[1].split()
            times[pid] = int(f[11]) + int(f[12])
            for line in (Path(f"/proc/{pid}/status")).read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss += int(line.split()[1])
                elif line.startswith("Threads:"):
                    threads += int(line.split()[1])
            try:
                for line in (Path(f"/proc/{pid}/smaps_rollup")).read_text().splitlines():
                    if line.startswith("Pss:"):
                        pss += int(line.split()[1])
                        break
            except OSError:
                pass
            procs += 1
        except OSError:
            pass
    return dict(rss_kb=rss, pss_kb=pss, threads=threads, procs=procs, times=times)


def main():
    label, seconds = sys.argv[1], int(sys.argv[2])
    out = Path(__file__).resolve().parents[3] / f"build/snap-live/bench-{label}.json"
    servers = [p for p in (int(x.name) for x in Path("/proc").glob("[0-9]*")) if is_snap_server(p)]
    assert len(servers) == 1, f"expected 1 snap server, found {servers}"
    snaps = []
    cpu = []
    prev = None
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        s = sample(tree(servers[0]))
        s["t"] = time.time()
        if prev:
            dt = s["t"] - prev["t"]
            d = sum(s["times"].get(p, 0) for p in s["times"]) - sum(
                prev["times"].get(p, 0) for p in prev["times"]
            )
            cpu.append(max(0.0, (d / CLK) / dt * 100.0 / NCPU))
        prev = s
        snaps.append({k: v for k, v in s.items() if k != "times"})
        time.sleep(5)
    rec = dict(
        label=label,
        seconds=seconds,
        ncpu=NCPU,
        cpu_pct_mean=round(sum(cpu) / len(cpu), 2) if cpu else 0.0,
        cpu_pct_max=round(max(cpu), 2) if cpu else 0.0,
        rss_kb_mean=round(sum(s["rss_kb"] for s in snaps) / len(snaps), 1),
        pss_kb_mean=round(sum(s["pss_kb"] for s in snaps) / len(snaps), 1),
        procs=snaps[-1]["procs"],
        threads=snaps[-1]["threads"],
    )
    out.write_text(json.dumps(rec, indent=2))
    print(json.dumps(rec))


if __name__ == "__main__":
    main()
