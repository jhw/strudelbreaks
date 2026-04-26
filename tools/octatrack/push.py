#!/usr/bin/env python
"""Push OT project zips to the Octatrack CF card under the strudelbeats set.

Source zips live in `~/Downloads/<adjective>-<noun>.ot.zip` — the
filename the strudelbreaks server hands the browser. The two render
variants (ot-basic, ot-doom) both emit `.ot.zip`; on-device they're
just project directories, so push doesn't need to distinguish.

Iterates Downloads, lists every `<adj>-<noun>.ot.zip` whose project
name (read from the .work entry inside the zip) is **not** already a
directory under `/Volumes/OCTATRACK/strudelbeats/`, and asks once per
project whether to extract.

Usage:
    push.py              # list all, ask per project
    push.py pattern      # filter by name fragment
    push.py -f           # copy all without prompting
    push.py -f pattern   # copy matching without prompting
"""

import argparse
import pathlib
import re
import sys
import zipfile

DOWNLOADS = pathlib.Path.home() / 'Downloads'
SUFFIX = '.ot.zip'
# Strict adj-noun guard so unrelated `.ot.zip` files (e.g. ones the
# user generated with `--name MYPROJECT`) don't get auto-pushed
# alongside the seeded ones. Custom-named exports stay manual.
NAME_PATTERN = re.compile(r'^[a-z]+-[a-z]+\.ot\.zip$')

OT_DEVICE = pathlib.Path('/Volumes/OCTATRACK')
OT_SET = OT_DEVICE / 'strudelbeats'


def find_projects(pattern=None):
    if not DOWNLOADS.exists():
        return []
    projects = [
        p for p in DOWNLOADS.iterdir()
        if p.is_file() and NAME_PATTERN.match(p.name)
    ]
    if pattern:
        pl = pattern.lower()
        projects = [p for p in projects if pl in p.name.lower()]
    return sorted(projects, key=lambda p: p.name)


def project_name_from_zip(zip_path):
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for name in zf.namelist():
            if name.endswith('.work'):
                return name.split('/')[0]
    return zip_path.name[:-len(SUFFIX)].upper()


def exists_on_ot(project_name):
    if not OT_SET.exists():
        return False
    pl = project_name.lower()
    for d in OT_SET.iterdir():
        try:
            if d.is_dir() and d.name.lower() == pl:
                return True
        except OSError as e:
            print(f'  warning: cannot stat {d.name}: {e}', file=sys.stderr)
    return False


def extract_project(zip_path):
    sample_count = 0
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for member in zf.namelist():
            if member.endswith('/'):
                continue
            dest = OT_SET / member
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, 'wb') as dst:
                dst.write(src.read())
            if member.endswith('.wav'):
                sample_count += 1
    return sample_count


def push(pattern=None, force=False):
    if not OT_DEVICE.exists():
        print(f'Error: Octatrack not found at {OT_DEVICE}')
        sys.exit(1)
    OT_SET.mkdir(parents=True, exist_ok=True)

    projects = find_projects(pattern)
    if not projects:
        print(f'No {SUFFIX} files matching <adj>-<noun> found in {DOWNLOADS}')
        return

    print(f'Found {len(projects)} project(s):')
    copied = 0
    for p in projects:
        name = project_name_from_zip(p)
        if exists_on_ot(name):
            print(f'  {name} (already on device, skipping)')
            continue
        if force:
            n = extract_project(p)
            print(f'  {name} -> extracted ({n} samples)')
            copied += 1
        else:
            if input(f'  Copy {name}? [y/N] ').lower() == 'y':
                n = extract_project(p)
                print(f'    Extracted ({n} samples).')
                copied += 1
    print(f'\nCopied {copied} project(s) to {OT_SET}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('pattern', nargs='?', default=None)
    ap.add_argument('-f', '--force', action='store_true')
    args = ap.parse_args()
    push(args.pattern, args.force)


if __name__ == '__main__':
    main()
