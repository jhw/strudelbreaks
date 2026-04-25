#!/usr/bin/env python
"""Push torso-s4 project zips to the S-4 over USB mass storage.

Extracts .zip files from tmp/torso-s4/ into
/Volumes/S4/samples/strudelbeats/. The S-4's `/samples/` is the
manual-defined location for user-imported wavs (factory content
lives in `/FACTORY/` and isn't visible in MSD mode); we keep all
strudelbreaks output corralled under a single `strudelbeats/`
subfolder there so it's easy to back up or wipe as a unit.

Usage:
    push.py              # list all, ask per project
    push.py pattern      # filter by name fragment
    push.py -f           # copy all without prompting (skip existing)
    push.py -f pattern   # copy matching without prompting
"""

import argparse
import pathlib
import sys
import zipfile

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
SOURCE_DIR = REPO_ROOT / 'tmp' / 'torso-s4'
S4_DEVICE = pathlib.Path('/Volumes/S4')
S4_SET = S4_DEVICE / 'samples' / 'strudelbeats'


def find_projects(pattern=None):
    if not SOURCE_DIR.exists():
        return []
    projects = list(SOURCE_DIR.glob('*.zip'))
    if pattern:
        pl = pattern.lower()
        projects = [p for p in projects if pl in p.name.lower()]
    return sorted(projects, key=lambda p: p.name)


def project_name_from_zip(zip_path):
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for name in zf.namelist():
            head = name.split('/', 1)[0]
            if head:
                return head
    return zip_path.stem


def exists_on_s4(project_name):
    if not S4_SET.exists():
        return False
    pl = project_name.lower()
    for d in S4_SET.iterdir():
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
            dest = S4_SET / member
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, 'wb') as dst:
                dst.write(src.read())
            if member.endswith('.wav'):
                sample_count += 1
    return sample_count


def push(pattern=None, force=False):
    if not S4_DEVICE.exists():
        print(f'Error: S-4 not found at {S4_DEVICE}')
        sys.exit(1)
    S4_SET.mkdir(parents=True, exist_ok=True)

    projects = find_projects(pattern)
    if not projects:
        print('No .zip files found in tmp/torso-s4/')
        return

    print(f'Found {len(projects)} project(s):')
    copied = 0
    for p in projects:
        name = project_name_from_zip(p)
        if exists_on_s4(name):
            print(f'  {name} (already exists, skipping)')
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
    print(f'\nCopied {copied} project(s) to {S4_SET}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('pattern', nargs='?', default=None)
    ap.add_argument('-f', '--force', action='store_true')
    args = ap.parse_args()
    push(args.pattern, args.force)


if __name__ == '__main__':
    main()
