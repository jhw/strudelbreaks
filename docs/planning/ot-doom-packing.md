# ot-doom: chain packing — considered, not adopted

Notes on a slot-saving optimisation for `scripts/export/octatrack/ot-doom/` that
looked attractive on paper but doesn't survive the OT's
scene-is-part-scoped constraint. Kept here so the next person who
reaches for it doesn't have to re-derive why it's a dead end.

## The motivation

Each ot-doom pattern uses `|C|` flex slots (one per chain). The OT's
flex pool is **128 slots project-wide**. Combined with the
per-pattern slot count, this caps the number of patterns we can
ship per project:

| `|C|` | slots/pattern | max patterns |
|---|---|---|
| 4 | 4 | 32 (across 2 banks) |
| 8 | 8 | 16 (exactly fills 1 bank) |
| 16 | 16 | 8 (half a bank) |

The `|C| = 16` ceiling (8 patterns per project) is tight for "many
morph sets in one project" workflows. Each chain has only `|C| + 1`
slices (5/9/17 for `|C|` = 4/8/16) but the OT supports up to **64
slices per sample** (verified in `ot-tools-io/src/slices.rs:181`,
`samples.rs:185`). So most of each chain's sample-slot capacity sits
unused — packing seemed obvious.

## The proposed scheme

Pack chain-position-`k` from multiple patterns into one big sample
slot. For `|C| = 16` (17-slice chains), 64 // 17 = 3 patterns per
packed slot. For `|C| = 4` (5-slice chains), 64 // 5 = 12 per slot.

If it worked, naive ceilings (ignoring other constraints) would be:

| `|C|` | naive packed max patterns |
|---|---|
| 4 | 16 banks × 12 packed = 192+ (some other ceiling kicks first) |
| 8 | 16 banks × 7 packed = 112 |
| 16 | 16 banks × 3 packed = 48 |

vs current 32 / 16 / 8 — looks like a 6×–10× win.

## Why it doesn't work

The Octatrack's `slice_index` parameter is set by **scenes**, and
scenes are configured at the **part** level. `tempera`-realistic
ot-doom exports run all patterns on **part 1** so they share one
scene config (`slice_index = 0` / `slice_index = N`). Two failure
modes follow:

### Cross-pattern packing — needs per-pattern scene configs

If pattern 1's chain-`k` audio occupies slices 0..4 of a packed slot
and pattern 2's audio occupies slices 5..9, the scene lerp that
addresses each pattern's range is different (`A=0,B=4` vs `A=5,B=9`).
Different scene configs require different parts. Each bank has 4
parts (A/B/C/D), so cross-pattern packing caps at 4 patterns per
bank — a 4× regression from the current 16-patterns-per-bank model
which more than offsets the slot savings.

### Within-pattern packing — needs per-trig slice offsets

If we packed all `N` of one pattern's chains into a single sample
slot (`N × (N+1)` slices), each trig would need to play a different
slice range — trig 0 plays the chain-0 region, trig 1 plays the
chain-1 region, etc. The only mechanism for this is per-trig
`slice_index` (= STRT) locks. But per-trig `slice_index` locks
**override** the scene's `slice_index` — they don't add.

(This was the bug behind the original ot-doom redesign in
`ce459d8`: every trig had `slice_index = 0`, which overrode the
scene's STRT lerp and made the crossfader silent.)

So per-trig differentiation kills the crossfader morph that's the
whole point of doom.

## Conclusion

Under the single-part-per-bank model:

- **Cross-pattern packing** → would need per-pattern parts → drops
  patterns/bank from 16 to 4 → net loss of capacity in most cases.
- **Within-pattern packing** → would need per-trig `slice_index` →
  breaks the crossfader.

There's no path to packing that preserves both the
single-part design *and* the scene-driven crossfader morph. The
current "one flex slot per chain" design is the best we can do.

## When to revisit

Two conditions would change the calculus:

1. **You're routinely hitting the 8-pattern ceiling for `|C| = 16`
   and need more.** Then the multi-part trade — 4 patterns/bank with
   distinct scene configs, packed slots — buys 24+ patterns at
   `|C| = 16`. The per-bank capacity loss (16 → 4) is acceptable
   because `|C| = 16` exports are big and rare.
2. **The OT firmware exposes additive `slice_index` locks** (per-trig
   offset added to scene STRT). Within-pattern packing would then
   become possible. No sign of this in current OT firmware.

If either holds, the implementation sketch:

- New `pack_chain_position_k(patterns_at_k, n)` builds an
  AudioSegment of `len(patterns_at_k) × (n + 1)` slices.
- `set_equal_slices` writes `len × (n + 1)` markers per packed slot.
- Bank assignment caps at 4 patterns/bank, one per part (A/B/C/D).
- Each part's scene config addresses its pattern's slice range
  within the packed slot.
- Validator updates: pattern-per-bank ceiling drops to 4; slot
  ceiling math becomes per-bank `N × ceil(P_pack_used / max_P_pack)`.

Until then, ship the current design and use the capacity ceilings
documented in `docs/export/ot-doom.md`.

## OT limits — verified

- **Flex slot pool**: 128 (`ot-tools-io/src/projects/slots.rs:97-98`).
- **Slices per sample**: 64 (`ot-tools-io/src/slices.rs:181`,
  `samples.rs:185`, `markers.rs:493`).
- **Patterns per bank**: 16 (OT manual, also reflected in octapy's
  `bank.pattern(N)` accepting 1..16).
- **Parts per bank**: 4 (A/B/C/D — OT manual; octapy `bank.part(N)`
  accepts 1..4).
