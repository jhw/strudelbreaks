# Crossfader slice transitions — non-uniform spacing on a 4-slice chain

*Draft for posting in the [Megabreak of Doom thread](https://www.elektronauts.com/t/octatrack-64-breakbeat-x-16-slices-megabreak-of-doom/337) (or its own thread linking back to it).*

---

**Title:** Crossfader walks slice positions unevenly — feature, bug, or my misunderstanding?

Hi all — I've been building a Megabreak-of-Doom-style export pipeline (inspired by [the original thread](https://www.elektronauts.com/t/octatrack-64-breakbeat-x-16-slices-megabreak-of-doom/337)) and I've hit a crossfader behaviour I can't explain. Hoping someone here can shed light.

## The setup

A minimal sandbox project, four sliced samples, designed to make the issue audible at a glance:

- Four input tones at 220 / 330 / 440 / 660 Hz, each one bar long at 120 BPM.
- Four matrix chains. Chain `k` is built from segment `k` of every input concatenated end-to-end (the standard Doom layout: each chain holds the kth segment of every input, so slice index selects the input).
- T1 only, slice mode ON, FX1 = DJ_EQ, FX2 = COMPRESSOR.
- Pattern: 16 steps, trigs at 1 / 5 / 9 / 13, each sample-locked to its chain.
- Scene A: T1 `slice_index = 0`. Scene B: T1 `slice_index = 3`. Active scene A = 1, active scene B = 2.

Expected: sweep the crossfader A → B and the played input should walk 0 → 1 → 2 → 3 in four uniform fader bands. Transitions at fader positions 5, 9, 13 on a 16-tick scale (i.e. quarter / half / three-quarter).

## The symptom

The fader does walk through all four inputs, in order — so the basic chain layout is right. But the transitions don't land where I expect. I see them at fader **6, 11, 15**, not 5, 9, 13.

The error grows with input index: input 0 → 1 is one tick late, 1 → 2 is two ticks late, 2 → 3 is two-to-three ticks late. The last input gets squeezed into a sliver at the end of the fader's travel.

## My (probably wrong) mental model

I'd been modelling it as `played_slice = floor(raw_strt / 2)` where `raw_strt` lerps linearly between scene A's STRT and scene B's STRT across the fader fraction. Under that model, scene A `slice_index = 0` (raw 0) → scene B `slice_index = 3` (raw 6) crosses three boundaries at fader 1/3, 2/3, 3/3 — clearly not what I'm seeing either way, and definitely not the 6/11/15 the device gives me.

My current best guess is something interpolation-related: the crossfader physically reports something like a 7-bit (0–127) value, but the slice-index space is much smaller. There's necessarily a mapping somewhere from a fine encoder value to a coarse slice value, and that mapping doesn't seem linear-and-uniform.

But this is all speculation, because…

## I can't see the slider's value anywhere

This is the main thing making it hard to debug. Is there a way to display the live crossfader value (the raw 0–127 — or whatever range it is — on the encoder side) on the OT screen? I've poked around the menus and not found one. Without a number to read off, all I have is "the tone changes around tick X" by ear, which isn't great calibration data.

## Questions

1. Does anyone know the actual math the firmware uses to lerp between scene A and scene B for a `slice_index` lock? Linear in some hidden internal scale, with a rounding rule? Non-linear (DJ-style s-curve)? Something else?
2. Is the mapping from physical fader to internal value documented anywhere? Or is this just folklore?
3. Is there a way to see the live fader value on the screen, even in a debug mode?
4. Is the non-uniformity I'm seeing expected behaviour (i.e. "feature, deal with it") or does it sound like something I'm getting wrong on the project setup side?

If it's a known thing and the answer is "the fader just isn't uniform, calibrate against it," that's fine — I'll generate a lookup table and compensate in the renderer. But before I do that I'd love to know whether anyone has a more authoritative description of what's actually happening between the encoder and the played slice.

## Reproducer

If anyone wants to poke at the same project: I can attach a `slaw-demo-4.ot.zip` (the four-tone sandbox above) plus -8 and -16 variants that reuse the same four pitches but at finer slice grids (8 and 16 slices — same audio, denser sampling of the fader's behaviour). Useful for figuring out whether the slaw is per-fader-tick or per-slice.

Thanks in advance — really appreciate the depth of knowledge in this community.
