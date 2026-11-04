"""60s resource sampler for one M1 state. stdlib only.

Usage: /usr/bin/python3 -S bench.py ROOT LABEL SECONDS
Writes evidence/bench-LABEL.json. CPU% approximated from /proc stat deltas.
"""

import json
import os
import sys
import time
from pathlib import Path

CLK = os.sysconf("SC_CLK_TCK")
NCPU = os.cpu_count() or 1


def tree(root_pid):
    kids = {root_pid}
    changed = True
    rows = {}
    while changed:
        changed = False
        for p in Path("/proc").glob("[0-9]*"):
            try:
                f = (p / "stat").read_text().rsplit(")", 1)[1].split()
                pid, ppid = int(p.name), int(f[1])
                rows[pid] = (ppid, f)
                if ppid in kids and pid not in kids:
                    kids.add(pid)
                    changed = True
            except (OSError, IndexError):
                pass
    return kids, rows


def sample(pids):
    rss = pss = threads = 0
    procs = 0
    times = {}
    for pid in pids:
        try:
            f = (Path(f"/proc/{pid}/stat")).read_text().rsplit(")", 1)[1].split()
            times[pid] = int(f[11]) + int(f[12])
            st = (Path(f"/proc/{pid}/status")).read_text()
            for line in st.splitlines():
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
    root = Path(sys.argv[1]).resolve()
    label = sys.argv[2]
    seconds = int(sys.argv[3])
    server_pid = None
    for p in Path("/proc").glob("[0-9]*"):
        try:
            if (p / "comm").read_text().strip() == "apricus-server":
                server_pid = int(p.name)
        except OSError:
            pass
    assert server_pid, "no apricus-server running"
    snaps = []
    t_end = time.monotonic() + seconds
    prev = None
    cpu_samples = []
    while time.monotonic() < t_end:
        kids, _ = tree(server_pid)
        s = sample(kids)
        s["t"] = time.time()
        if prev:
            dt = s["t"] - prev["t"]
            d = sum(s["times"].get(p, 0) for p in s["times"]) - sum(
                prev["times"].get(p, 0) for p in prev["times"]
            )
            cpu_samples.append(max(0.0, (d / CLK) / dt * 100.0 / NCPU))
        prev = s
        snaps.append({k: v for k, v in s.items() if k != "times"})
        time.sleep(5)
    rec = dict(
        label=label,
        seconds=seconds,
        ncpu=NCPU,
        cpu_pct_mean=sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0,
        cpu_pct_max=max(cpu_samples) if cpu_samples else 0.0,
        rss_kb_mean=sum(s["rss_kb"] for s in snaps) / len(snaps),
        pss_kb_mean=sum(s["pss_kb"] for s in snaps) / len(snaps),
        procs=snaps[-1]["procs"],
        threads=snaps[-1]["threads"],
        snapshots=snaps,
    )
    (root / f"evidence/bench-{label}.json").write_text(json.dumps(rec, indent=2))
    print(json.dumps({k: v for k, v in rec.items() if k != "snapshots"}, indent=2))


if __name__ == "__main__":
    main()
