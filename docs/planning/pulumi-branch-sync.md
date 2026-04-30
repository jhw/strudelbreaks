# Syncing the pulumi deploy branch with main

`origin/claude/fastapi-to-lambda-pulumi-avwuH` carries the in-progress
"drop FastAPI, switch to Lambda + Pulumi" rewrite (commit `040cd53`).
It was branched off `e0e2ab1` (the aws-deploy plan revision) and has
been falling behind main since. This note captures how to bring it
up to date and why fast-forward doesn't apply here.

## The state

```
main:    e0e2ab1 → 46efb37 → ff7cbea → 06eb759 → cd4bd54   (4 new commits)
pulumi:  e0e2ab1 → 040cd53                                  (1 new commit)
```

Common ancestor: `e0e2ab1`. Both sides moved on.

## Why fast-forward doesn't apply

A fast-forward is pointer surgery: when the lagging branch has *zero*
commits of its own, merging the leading branch in just moves the
lagging branch's pointer forward to match — no merge commit, no
new branch. It only works when the lagging branch is a strict prefix
of the leading one.

The pulumi branch has `040cd53` of its own, so the histories have
diverged. We need either a merge or a rebase to combine them.

## Two real options

### Merge main into pulumi

```
git checkout pulumi
git merge main
```

Creates a merge commit on pulumi joining the two histories.
`040cd53` stays exactly as written. Conflict resolution lives
inside the merge commit.

### Rebase pulumi onto main

```
git checkout pulumi
git rebase main
git push --force-with-lease origin claude/fastapi-to-lambda-pulumi-avwuH
```

Replays `040cd53` on top of `cd4bd54`, giving pulumi a linear
history that looks like a continuation of main. Rewrites pulumi's
SHA, so a force-push is needed afterward. Conflicts get resolved
once during the replay.

## Recommended: rebase

Only one commit to replay; linear history makes the eventual PR
diff cleaner. The branch is single-developer-only — nobody else
has it checked out, so the force-push is safe.

If we ever push more commits to this branch and someone else
starts collaborating on it, switch to merge to avoid yanking
history out from under them.

## Conflict expectations

`040cd53` deletes `app/main.py`, `app/config.py`, `app/routes/`,
and `scripts/run.sh` as part of the FastAPI removal. Main's recent
commits touched:

- `app/export/octatrack/ot_doom/{audio,render}.py`
- `app/export/torso_s4/{audio,render}.py`
- `app/export/common/audio_fades.py` (new)
- `docs/export/{ot-doom,torso-s4}.md`
- `docs/planning/ot-doom-crossfader.md`
- `tempera.strudel.js` (UI tweaks)
- `scripts/demos/slaw_demo.py`

The two sets are largely disjoint — the FastAPI removal works on
`app/main.py` / `app/routes/` while main's work is in
`app/export/`. The new Lambda handlers in `app/api/` may need to
import the new `app/export/common/audio_fades.py` indirectly via
the renderers, but that should resolve cleanly.

## Logistics: branch is remote-only

No local tracking branch yet. To start work:

```
git fetch origin
git checkout -b pulumi origin/claude/fastapi-to-lambda-pulumi-avwuH
```

(local name `pulumi` is arbitrary — pick whatever's convenient.)
