# ot-doom: crossfader uniformity — open question

The ot-doom matrix-chain design hands the crossfader N inputs per
stem and expects the fader to walk between them at uniform 1/N
fractions. In practice the transitions land in the wrong places,
and the placement isn't even deterministic — it shifts with sweep
direction and prior history. This file captures what we know and
why we can't compensate for it in the renderer.

## Symptom

With `|C| = 4` (four input cells per row) and the previous design
(N+1 = 5 slices per stem block, scene A = `block_start`, scene B =
`block_start + N`), the user observed input transitions at fader
positions **6, 11, 15** instead of the predicted **5, 9, 12-13**.

The error grows with input index — the last input gets squeezed.
Reverting to the simpler N-slice layout (scene A = 0, scene B =
N - 1) hasn't been re-measured yet but is unlikely to be perfect
either; it's the layout the user wanted to revert to so they could
re-test from a known starting point.

## What we currently model

`played_slice = floor(raw_strt / 2)` where `raw_strt` is the linear
lerp of scene A's STRT and scene B's STRT across fader fraction
`f ∈ [0, 1]`. octapy doubles `slice_index` when writing scene
locks (`slice_index = N → raw_strt = 2N`), so scene A=0 / scene
B=N-1 puts the live raw between 0 and 2(N-1), which under floor
gives N "wide" boundaries at raw = 2, 4, 6, ..., 2(N-1).

The **+1 design** (now reverted) added one extra slice per stem so
scene B could be `block_start + N` (raw `2N`). Under floor that
crosses N boundaries at fader fractions 1/N, 2/N, ..., (N-1)/N
exactly. Empirically it doesn't.

## Why the model probably isn't what the firmware does

The OT firmware's exact crossfade math isn't documented and isn't
in `ot-tools-io` (that library only reads/writes the on-disk
format — runtime mixing lives in firmware nobody outside Elektron
has decompiled). The discrepancy fits any of:

- **Different rounding.** OT might use round-half-up, banker's
  rounding, or ceil for `raw → slice`. Each shifts the per-slice
  fader band by half a slice.
- **Different internal scale.** STRT shows 0-127 on the display
  but might lerp on a finer internal scale (0-255, 14-bit, or
  log).
- **Non-linear fader curve.** Many crossfaders use a slight
  s-curve for "DJ feel" — uniform raw STRT motion would still
  give non-uniform played-slice transitions.

We haven't ruled any of these out. The user-observed 6 / 11 / 15
shows the error growing with index, which fits a non-linear
mapping more than a constant offset.

## Why a lookup table won't work

The natural compensation strategy — measure `fader → played_slice`
on the device, fit the inverse, and rewrite scene `slice_index`
values so the result is fader-uniform — assumes the mapping is a
deterministic function of fader position. It isn't.

Empirically the played slice at a given fader position depends on:

- **Sweep direction.** Walking the fader A → B and stopping at
  position p gives a different played slice than walking B → A
  and stopping at the same p.
- **Sweep history.** Repeated sweeps of the same range land on
  different slice transitions on different passes.

A static lookup table can't represent that. Whatever smoothing /
filtering / hysteresis the firmware applies between the encoder
and the scene-driven `slice_index` lerp has internal state we
can't observe and can't predict from the on-disk project format.
No correction we apply at render time will be the right one for
every gesture, so we don't apply one.

## What we won't do

- Build a calibration tool or lookup table — see above.
- Add rounding heuristics inside the renderer. Any guess that
  improves one gesture will harm another given the directional /
  history dependency.
- Switch to per-trig `slice_index` locks. That breaks the scene
  morph entirely (per-trig locks override scene STRT — see
  `docs/planning/ot-doom-packing.md` for the longer write-up).

## References

- `docs/export/ot-doom.md` — current design + the (now-historical)
  +1 uniformity argument.
- `docs/planning/ot-doom-packing.md` — why we can't pack more
  patterns per slot and why per-trig STRT can't help.
- octapy `AudioSceneTrack.slice_index` setter doubles its
  argument; OT STRT is 7-bit (0-127) addressing 64 slices.
