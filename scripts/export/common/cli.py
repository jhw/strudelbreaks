"""Shared argparse skeleton for target renderers.

Every render target takes the same three arguments — the export path, an
optional output name stem, and an optional seed for the name generator.
Centralising the parser keeps the CLIs uniform across targets.
"""
from __future__ import annotations

import argparse
import pathlib
import random
import sys

from .names import generate_name


def build_parser(description: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument('export', type=pathlib.Path,
                    help='path to a tempera captures JSON export')
    ap.add_argument('--name', default=None,
                    help='output stem (default: generated adjective-noun)')
    ap.add_argument('--seed', type=int, default=None,
                    help='seed the name generator for reproducibility')
    return ap


def resolve_name(args) -> str:
    if args.name:
        return args.name
    rng = random.Random(args.seed) if args.seed is not None else None
    return generate_name(rng)


def require_file(path: pathlib.Path) -> None:
    if not path.is_file():
        sys.exit(f'not a file: {path}')
