"""Adjective-noun name generator shared by the strudel and octatrack renderers."""
from __future__ import annotations

import json
import pathlib
import random

_HERE = pathlib.Path(__file__).resolve().parent
_ADJECTIVES = json.loads((_HERE / 'adjectives.json').read_text())
_NOUNS = json.loads((_HERE / 'nouns.json').read_text())


def generate_name(rng: random.Random | None = None) -> str:
    r = rng or random
    return f"{r.choice(_ADJECTIVES)}-{r.choice(_NOUNS)}"
