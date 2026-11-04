# Isolated Snap strict e2e environment — plan (NOT EXECUTED)

Goal: run `docs/notes/m3-snap-e2e-protocol.md` without touching the primary
VM, its session, its Apricus install, or the host hypervisor.

## Why not a clone of the outer VM

This machine is itself a QEMU/KVM guest (`Ubuntu 26.04 PC i440FX`,
15 vCPUs, 4.8 GB RAM, 52 GB free). The outer hypervisor is not reachable
from inside, so cloning the primary VM can only happen outside this
environment (operator action). The faithful in-reach alternative is a
**nested LXD virtual machine**: full kernel, systemd, snapd, and a real
GNOME Wayland session, fully scriptable, zero host modification.

Read-only facts backing this plan:

- Nested virtualization works: `/dev/kvm` present, vmx/svm on all vCPUs.
- LXD functional (default profile: 4 vCPUs, 3 GiB RAM, `dir` pool on the
  52 GB-free filesystem). Ubuntu 26.04 ("resolute") VM images available.
- No libvirt/virsh/VirtualBox tooling installed; no changes made to them.

## Proposed test VM

- `lxc launch ubuntu:26.04 snap-210-test --vm -c limits.cpu=4
  -c limits.memory=3GiB -d root,size=25GiB`
  (exact image alias resolved at creation time; use the current
  `ubuntu:` remote `26.04` VM image, verify checksum shown by LXD).
- Inside (all as root via `lxc exec`, i.e. VM-local root, never host sudo):
  `apt install ubuntu-desktop gnome-shell snapd snapcraft`,
  enable Wayland GDM autologin for a test user, reboot.
- Build the 2.1.0 Snap **inside the VM** from pushed 2.1.0 sources with
  `snapcraft pack --destructive-mode`, then
  `snap install --dangerous apricus_2.1.0_amd64.snap`.
  (This also produces the first real 2.1.0 Snap build. No 2.1.0 Snap
  artifact exists today: neither CI — which built only the 1674b01 test
  Snap, predating both the release metadata and the server-owned bridge —
  nor any local build produced one.)
- Extension rev6: build the ZIP from the 2.1.0 tree on the operator side,
  push it into the VM, install with `install-host.sh --zip … --enable`,
  then logout/login via session restart (scripted, inside the VM only).
- Run the protocol via `lxc exec` + D-Bus/gdbus + journal assertions;
  optional frames via the Mutter ScreenCast portal. No step needs the
  primary session, display, or logout.

## Resource budget and protections

- RAM is the tightest resource (host 4.8 GB total, ~2.4 GB free): cap the
  test VM at 3 GiB, run only when the primary VM is idle, stop (not just
  idle) the VM between phases, delete it at the end. If the host shows
  memory pressure, abort the campaign before the primary session degrades.
- Disk: 25 GiB cap inside 52 GiB free; remove images/artifacts after.
- CPU: 4 vCPUs cap; transient contention only.
- Network: isolated `lxdbr0` NAT; no host ports opened, no host packages.
- Proven pristine-primary evidence: before/after fingerprints
  (`snap list`, extension SHA256 inventory, `~/.config/apricus` hash,
  Apricus process list) — expected byte-identical since every operation
  targets the VM.

## PASS/FAIL (from the protocol + acceptance criteria)

- PASS only on observed state: rev6 loaded, bridge name owned by the Snap,
  M3 pause/resume reasons in the engine snapshot with GUI closed,
  manual latches winning, server restart recovery, no duplicates/orphans/
  tracebacks, clean upgrade path from 2.0.7 (installed from Store stable
  inside the VM, then side-loaded 2.1.0 over it).
- Any AppArmor denial for new names, freeze, lost latch, or GUI-closed
  regression = FAIL with logs attached.

## Teardown

`snap remove` inside VM (optional), `lxc stop --force`, `lxc delete
--force`, optionally `lxc image delete` the fetched images; confirm
`lxc list` empty and host fingerprints unchanged.

## Blockers / authorizations still needed

1. Creating/starting the VM at all (explicitly not authorized yet).
2. ~4 GB of image/package downloads.
3. GNOME-on-llvmpipe is state-faithful but slow: state assertions are
   authoritative; visual smoothness is NOT part of PASS.
4. A separate Wayland autologin session inside the VM must be configured;
   the primary session is never touched.
