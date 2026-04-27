#!/usr/bin/env python
"""Unified push / clean / status / watch for strudelbreaks devices.

Replaces the per-device scripts that used to live under
tools/<device>/. Auto-detects the connected device by scanning
/Volumes/; pass --device when ambiguous (zero devices mounted, or
both Octatrack and Torso S-4 plugged in at once).

Subcommands:

    sync.py                              # default = status
    sync.py push  [pattern] [-f] [--device DEVICE]
    sync.py clean local  [pattern] [-f] [--device DEVICE]
    sync.py clean remote [pattern] [-f] [--device DEVICE]
    sync.py clean stubs  [pattern] [-f]               # OT-only
    sync.py status [--device DEVICE]
    sync.py watch [-f] [--device DEVICE] [--interval N]
        Poll /Volumes/ and ~/Downloads; auto-push new local-only
        zips when a device is mounted. Push-only — never cleans.

Devices:

    octatrack  -> /Volumes/OCTATRACK/strudelbeats/<NAME>/        (gated by project.work)
    torso-s4   -> /Volumes/S4/samples/strudelbeats/<name>/       (no project marker)
    strudel    -> ~/Downloads/<adj>-<noun>.strudel.js            (no remote — paste into browser)

Aliases:  --device ot == --device octatrack;  --device s4 == --device torso-s4.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import sys
import time
import zipfile
from typing import Optional


DOWNLOADS = pathlib.Path.home() / 'Downloads'

# Per-device specs. `volume`/`remote_root` are None for paste-only
# targets (currently just strudel). `project_marker`, when set, is the
# filename inside a project dir that distinguishes it from a stub /
# from `shared_remote_dirs` siblings (currently just OT's AUDIO/).
DEVICES = {
    'octatrack': {
        'volume': pathlib.Path('/Volumes/OCTATRACK'),
        'remote_root': pathlib.Path('/Volumes/OCTATRACK/strudelbeats'),
        'suffix': '.ot.zip',
        'project_marker': 'project.work',
        # Paths relative to remote_root that should also be removed
        # when a project is cleaned (OT pairs <NAME>/ with
        # AUDIO/projects/<NAME>/ under the same set).
        'paired_remote_dirs': ['AUDIO/projects/{name}'],
        # Top-level entries inside remote_root that are *not* projects
        # (and must not be flagged as stubs).
        'shared_remote_dirs': {'AUDIO'},
    },
    'torso-s4': {
        'volume': pathlib.Path('/Volumes/S4'),
        'remote_root': pathlib.Path('/Volumes/S4/samples/strudelbeats'),
        'suffix': '.s4.zip',
        'project_marker': None,
        'paired_remote_dirs': [],
        'shared_remote_dirs': set(),
    },
    'strudel': {
        'volume': None,
        'remote_root': None,
        'suffix': '.strudel.js',
        'project_marker': None,
        'paired_remote_dirs': [],
        'shared_remote_dirs': set(),
    },
}

DEVICE_ALIASES = {'ot': 'octatrack', 's4': 'torso-s4'}


# ---- device resolution ----

def adj_noun_pattern(suffix: str) -> re.Pattern:
    """Strict <adj>-<noun><suffix> regex.

    Custom-named exports (e.g. ones the user generated with `--name
    MYPROJECT`) deliberately don't match — auto-batch verbs would be
    too dangerous if they could pick up arbitrary names. Such files
    stay manual."""
    return re.compile(rf'^[a-z]+-[a-z]+{re.escape(suffix)}$')


def detect_device() -> Optional[str]:
    """Return the only-mounted device key, or None if 0 / >1 are mounted."""
    mounted = [
        name for name, spec in DEVICES.items()
        if spec['volume'] is not None and spec['volume'].exists()
    ]
    return mounted[0] if len(mounted) == 1 else None


def resolve_device(arg: Optional[str]) -> tuple[str, dict]:
    """Resolve --device or auto-detect; exit with a helpful message
    on ambiguity rather than silently picking one."""
    if arg:
        key = DEVICE_ALIASES.get(arg, arg)
        if key not in DEVICES:
            allowed = sorted(set(DEVICES) | set(DEVICE_ALIASES))
            sys.exit(f'unknown --device: {arg!r} (allowed: {allowed})')
        return key, DEVICES[key]
    detected = detect_device()
    if detected is not None:
        return detected, DEVICES[detected]
    mounted_count = sum(
        1 for s in DEVICES.values()
        if s['volume'] is not None and s['volume'].exists()
    )
    if mounted_count == 0:
        sys.exit('no device mounted; pass --device {octatrack,torso-s4,strudel}')
    sys.exit('multiple devices mounted; pass --device to disambiguate')


# ---- find ----

def find_local(suffix: str, pattern: Optional[str] = None) -> list[pathlib.Path]:
    if not DOWNLOADS.exists():
        return []
    rx = adj_noun_pattern(suffix)
    files = [p for p in DOWNLOADS.iterdir() if p.is_file() and rx.match(p.name)]
    if pattern:
        pl = pattern.lower()
        files = [p for p in files if pl in p.name.lower()]
    return sorted(files, key=lambda p: p.name)


def find_remote_projects(spec: dict, pattern: Optional[str] = None) -> list[pathlib.Path]:
    """Project dirs under remote_root. If the device has a
    `project_marker`, dirs missing the marker are *not* returned (use
    `find_remote_stubs` for those)."""
    root = spec['remote_root']
    if root is None or not root.exists():
        return []
    marker = spec['project_marker']
    shared = spec['shared_remote_dirs']
    out = []
    for d in root.iterdir():
        if not d.is_dir() or d.name in shared:
            continue
        if marker and not (d / marker).exists():
            continue
        out.append(d)
    if pattern:
        pl = pattern.lower()
        out = [p for p in out if pl in p.name.lower()]
    return sorted(out, key=lambda p: p.name)


def find_remote_stubs(spec: dict, pattern: Optional[str] = None) -> list[pathlib.Path]:
    """Non-project dirs under remote_root — only meaningful when the
    device has a `project_marker` to distinguish them from real
    projects. Unreadable entries are flagged as stubs so the user can
    decide whether to remove them."""
    root = spec['remote_root']
    marker = spec['project_marker']
    if root is None or marker is None or not root.exists():
        return []
    shared = spec['shared_remote_dirs']
    out = []
    for d in root.iterdir():
        if d.name in shared:
            continue
        try:
            if not d.is_dir():
                continue
            if not (d / marker).exists():
                out.append(d)
        except OSError as e:
            print(f'  warning: cannot stat {d.name}: {e}', file=sys.stderr)
            out.append(d)
    if pattern:
        pl = pattern.lower()
        out = [p for p in out if pl in p.name.lower()]
    return sorted(out, key=lambda p: p.name)


# ---- prompt ----

def confirm(prompt: str, force: bool) -> bool:
    return True if force else input(prompt).lower() == 'y'


# ---- push ----

def project_name_from_zip(zip_path: pathlib.Path,
                          marker: Optional[str], suffix: str) -> str:
    """Project name is whichever top-level dir hosts the marker file
    (OT: `<NAME>/project.work`); for marker-less devices we take the
    first non-empty top-level directory entry. Falls back to the
    filename stem if the zip has neither shape."""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
    if marker:
        for n in names:
            if n.endswith('/' + marker) or n == marker:
                head = n.split('/', 1)[0]
                if head and head != marker:
                    return head
    for n in names:
        head = n.split('/', 1)[0]
        if head:
            return head
    return zip_path.name[:-len(suffix)]


def remote_has_project(spec: dict, name: str) -> bool:
    root = spec['remote_root']
    if root is None or not root.exists():
        return False
    pl = name.lower()
    for d in root.iterdir():
        try:
            if d.is_dir() and d.name.lower() == pl:
                return True
        except OSError as e:
            print(f'  warning: cannot stat {d.name}: {e}', file=sys.stderr)
    return False


def extract_project(zip_path: pathlib.Path, dest_root: pathlib.Path) -> int:
    """Extract every file in the zip under `dest_root`, preserving the
    zip's internal directory structure. Returns the number of .wav
    files written (used for the per-project status line)."""
    sample_count = 0
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for member in zf.namelist():
            if member.endswith('/'):
                continue
            dest = dest_root / member
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, 'wb') as dst:
                dst.write(src.read())
            if member.endswith('.wav'):
                sample_count += 1
    return sample_count


def _ensure_remote_root(spec: dict) -> None:
    """Make sure the device's strudelbeats subdir exists.

    Only ever creates entries *inside* an already-mounted volume —
    never the volume entry itself. mkdir(parents=True) on
    `/Volumes/OCTATRACK/strudelbeats` would, in a TOCTOU race with
    the device unmounting, silently create `/Volumes/OCTATRACK/`
    as a regular folder under macOS's /Volumes mount root. That
    leaves a mystery directory that can shadow the next mount.
    Walking volume → remote_root component-by-component (no
    parents=True) makes the failure mode loud instead of silent.
    """
    volume = spec['volume']
    if volume is None or not volume.exists():
        sys.exit(f'device not mounted at {volume}')
    cur = volume
    for part in spec['remote_root'].relative_to(volume).parts:
        cur = cur / part
        if not cur.exists():
            cur.mkdir()


def push(spec: dict, pattern: Optional[str], force: bool) -> None:
    if spec['remote_root'] is None:
        sys.exit('`push` not supported for this device (paste-into-browser target)')
    _ensure_remote_root(spec)

    files = find_local(spec['suffix'], pattern)
    if not files:
        print(f'no {spec["suffix"]} files matching <adj>-<noun> in {DOWNLOADS}')
        return

    print(f'found {len(files)} project(s):')
    copied = 0
    for p in files:
        name = project_name_from_zip(p, spec['project_marker'], spec['suffix'])
        if remote_has_project(spec, name):
            print(f'  {name} (already on device, skipping)')
            continue
        if not confirm(f'  copy {name}? [y/N] ', force):
            continue
        n = extract_project(p, spec['remote_root'])
        if force:
            print(f'  {name} -> extracted ({n} samples)')
        else:
            print(f'    extracted ({n} samples)')
        copied += 1
    print(f'\ncopied {copied} project(s) to {spec["remote_root"]}')


# ---- clean ----

def clean_local(spec: dict, pattern: Optional[str], force: bool) -> None:
    files = find_local(spec['suffix'], pattern)
    if not files:
        print(f'no {spec["suffix"]} files matching <adj>-<noun> in {DOWNLOADS}')
        return
    print(f'found {len(files)} file(s):')
    removed = 0
    for p in files:
        if not confirm(f'  remove {p.name}? [y/N] ', force):
            continue
        p.unlink()
        print(f'  removed {p.name}' if force else '    removed.')
        removed += 1
    print(f'\nremoved {removed} of {len(files)} file(s).')


def clean_remote(spec: dict, pattern: Optional[str], force: bool) -> None:
    if spec['remote_root'] is None:
        sys.exit('`clean remote` not supported for this device (no remote target)')
    if not spec['volume'].exists():
        sys.exit(f'device not mounted at {spec["volume"]}')
    if not spec['remote_root'].exists():
        print(f'no strudelbeats set on {spec["volume"].name}')
        return

    projects = find_remote_projects(spec, pattern)
    if not projects:
        print('no projects found in strudelbeats set')
        return
    print(f'found {len(projects)} project(s):')
    removed = 0
    for p in projects:
        if not confirm(f'  remove {p.name}? [y/N] ', force):
            continue
        shutil.rmtree(p)
        for paired in spec['paired_remote_dirs']:
            aux = spec['remote_root'] / paired.format(name=p.name)
            if aux.exists():
                shutil.rmtree(aux)
        print(f'  removed {p.name}' if force else '    removed.')
        removed += 1
    print(f'\nremoved {removed} of {len(projects)} project(s).')


def clean_stubs(spec: dict, pattern: Optional[str], force: bool) -> None:
    if spec['project_marker'] is None:
        sys.exit('`clean stubs` not supported for this device (no project marker)')
    if not spec['volume'].exists():
        sys.exit(f'device not mounted at {spec["volume"]}')
    if not spec['remote_root'].exists():
        print(f'no strudelbeats set on {spec["volume"].name}')
        return

    stubs = find_remote_stubs(spec, pattern)
    if not stubs:
        print('no stub directories found in strudelbeats set')
        return
    print(f'found {len(stubs)} stub(s):')
    removed = 0
    for s in stubs:
        if not confirm(f'  remove {s.name}? [y/N] ', force):
            continue
        try:
            shutil.rmtree(s)
            print(f'  removed {s.name}' if force else '    removed.')
            removed += 1
        except (OSError, shutil.Error) as e:
            print(f'  failed to remove {s.name}: {e}', file=sys.stderr)
    print(f'\nremoved {removed} of {len(stubs)} stub(s).')


# ---- status ----

def status(spec: dict) -> None:
    """One-shot summary: local files, remote projects, and the
    intersection / per-side leftovers."""
    local_files = find_local(spec['suffix'])
    print(f'local  ({len(local_files):>2}): '
          + (', '.join(p.name for p in local_files) or '—'))

    if spec['remote_root'] is None:
        return
    if not (spec['volume'] and spec['volume'].exists()):
        print(f'remote  ( ?): device not mounted at {spec["volume"]}')
        return

    remote_projects = find_remote_projects(spec)
    remote_names = {p.name.lower() for p in remote_projects}
    print(f'remote ({len(remote_names):>2}): '
          + (', '.join(sorted(p.name for p in remote_projects)) or '—'))

    local_names = {
        project_name_from_zip(p, spec['project_marker'], spec['suffix']).lower()
        for p in local_files
    }
    in_sync = sorted(local_names & remote_names)
    only_local = sorted(local_names - remote_names)
    only_remote = sorted(remote_names - local_names)
    print(f'  in sync     : {", ".join(in_sync) or "—"}')
    print(f'  local-only  : {", ".join(only_local) or "—"}'
          + ('  (run `push` to ship)' if only_local else ''))
    print(f'  remote-only : {", ".join(only_remote) or "—"}')


# ---- watch ----

def _watch_tick(targets: list, seen: dict, force: bool) -> dict:
    """One iteration of the watch loop. Detects mount/unmount events
    and new local files per device, and re-runs `push` whenever the
    state changed for a mounted device. Returns the updated `seen`
    dict so the loop carries state across ticks.

    Idempotent: `push` already skips zips already on the device, so
    even if state appears to change spuriously the worst case is a
    short status print — never a duplicate extract.

    Push-only: this never invokes any clean verb. A watcher that
    deleted things on its own would be a footgun."""
    for spec in targets:
        vol = spec['volume']
        mounted = vol.exists() if vol is not None else False
        local = {p.name for p in find_local(spec['suffix'])}
        key = id(spec)
        prev = seen.get(key, {'mounted': False, 'local': set()})
        label = vol.name if vol is not None else spec['suffix']

        if mounted != prev['mounted']:
            print(f'[{label}] {"mounted" if mounted else "unmounted"}')
        for n in sorted(local - prev['local']):
            print(f'[{label}] local: {n}')

        changed = mounted != prev['mounted'] or local != prev['local']
        if changed and mounted and local:
            push(spec, pattern=None, force=force)

        seen[key] = {'mounted': mounted, 'local': local}
    return seen


def watch(targets: list, interval: float, force: bool) -> None:
    """Long-running poller. Stops on Ctrl-C."""
    if not targets:
        sys.exit('watch needs at least one device with a volume path '
                 '(strudel has no remote — nothing to watch for)')
    print('watching... Ctrl-C to stop')
    seen: dict = {}
    while True:
        try:
            seen = _watch_tick(targets, seen, force)
            time.sleep(interval)
        except KeyboardInterrupt:
            print('\nstopped')
            return


# ---- argparse ----

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # No-subcommand default is `status` — running bare `sync.py` should
    # show what's where, never touch anything. Push/clean stay
    # explicit so destructive intent is always typed.
    sub = ap.add_subparsers(dest='cmd')

    def add_common(p):
        p.add_argument('pattern', nargs='?', default=None,
                       help='filter project names by substring match')
        p.add_argument('-f', '--force', action='store_true',
                       help='no per-item prompt')
        p.add_argument('--device',
                       help='octatrack | torso-s4 | strudel '
                            '(aliases: ot, s4); auto-detected when '
                            'one device is mounted')

    add_common(sub.add_parser('push', help='extract local zips onto the device'))

    clean_p = sub.add_parser('clean', help='remove local files or remote projects')
    clean_sub = clean_p.add_subparsers(dest='clean_what', required=True)
    add_common(clean_sub.add_parser('local',  help='~/Downloads/<adj>-<noun>.<suffix>'))
    add_common(clean_sub.add_parser('remote', help='device-side project dirs'))
    add_common(clean_sub.add_parser('stubs',  help='OT-only: dangling non-project dirs'))

    status_p = sub.add_parser('status', help='compare local vs remote for the detected device')
    status_p.add_argument('--device')

    watch_p = sub.add_parser(
        'watch',
        help='poll /Volumes and ~/Downloads; auto-push new local-only zips',
    )
    watch_p.add_argument('-f', '--force', action='store_true',
                         help='no per-item prompt when pushing')
    watch_p.add_argument('--device',
                         help='watch only this device (default: any '
                              'device with a volume path)')
    watch_p.add_argument('--interval', type=float, default=2.0,
                         help='poll interval in seconds (default 2)')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.cmd or 'status'

    if cmd == 'watch':
        # `watch` doesn't go through resolve_device — that exits when
        # no device is mounted, but the whole point of watching is to
        # wait for one to appear. Without --device we watch every
        # known device with a volume path.
        if args.device:
            _, spec = resolve_device(args.device)
            targets = [spec]
        else:
            targets = [s for s in DEVICES.values() if s['volume'] is not None]
        watch(targets, args.interval, args.force)
        return

    _, spec = resolve_device(getattr(args, 'device', None))
    if cmd == 'push':
        push(spec, args.pattern, args.force)
    elif cmd == 'status':
        status(spec)
    elif cmd == 'clean':
        if args.clean_what == 'local':
            clean_local(spec, args.pattern, args.force)
        elif args.clean_what == 'remote':
            clean_remote(spec, args.pattern, args.force)
        elif args.clean_what == 'stubs':
            clean_stubs(spec, args.pattern, args.force)


if __name__ == '__main__':
    main()
