#!/usr/bin/env python
"""Remove stub directories from the strudelbeats set on the Octatrack CF card.

A stub is a non-AUDIO directory that does not contain a project.work file —
typically left behind by a partial / aborted push, or by an interrupted clean.
Valid projects (with project.work) are not touched.

Usage:
    clean_stubs.py              # list all, ask per stub
    clean_stubs.py pattern      # filter by name fragment
    clean_stubs.py -f           # remove all without prompting
    clean_stubs.py -f pattern   # remove matching without prompting
"""

import argparse
import pathlib
import shutil
import sys

OT_DEVICE = pathlib.Path('/Volumes/OCTATRACK')
OT_SET = OT_DEVICE / 'strudelbeats'
SHARED_DIRS = {'AUDIO'}


def is_stub(path):
    if path.name in SHARED_DIRS:
        return False
    try:
        if not path.is_dir():
            return False
        return not (path / 'project.work').exists()
    except OSError as e:
        # Unreadable entries (corrupt FAT, bad CF sector) are stub-worthy:
        # the user can't use them anyway, so flag for removal attempt.
        print(f'  warning: cannot stat {path.name}: {e}', file=sys.stderr)
        return True


def find_stubs(pattern=None):
    if not OT_SET.exists():
        return []
    stubs = [d for d in OT_SET.iterdir() if is_stub(d)]
    if pattern:
        pl = pattern.lower()
        stubs = [s for s in stubs if pl in s.name.lower()]
    return sorted(stubs, key=lambda p: p.name)


def remove_stub(stub_dir):
    shutil.rmtree(stub_dir)


def clean(pattern=None, force=False):
    if not OT_DEVICE.exists():
        print(f'Error: Octatrack not found at {OT_DEVICE}')
        sys.exit(1)
    if not OT_SET.exists():
        print('No strudelbeats set found on Octatrack')
        return

    stubs = find_stubs(pattern)
    if not stubs:
        print('No stub directories found in strudelbeats set')
        return

    print(f'Found {len(stubs)} stub(s) in strudelbeats:')
    removed = 0
    for s in stubs:
        prompt = force or input(f'  Remove {s.name}? [y/N] ').lower() == 'y'
        if not prompt:
            continue
        try:
            remove_stub(s)
            print(f'  Removed {s.name}' if force else '    Removed.')
            removed += 1
        except (OSError, shutil.Error) as e:
            print(f'  Failed to remove {s.name}: {e}', file=sys.stderr)
    print(f'\nRemoved {removed} of {len(stubs)} stub(s).')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('pattern', nargs='?', default=None)
    ap.add_argument('-f', '--force', action='store_true')
    args = ap.parse_args()
    clean(args.pattern, args.force)


if __name__ == '__main__':
    main()
