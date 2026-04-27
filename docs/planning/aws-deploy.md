# Deploy strudelbreaks server to AWS Lambda (Pulumi)

Plan for moving the FastAPI render server off `./scripts/run.sh` and
onto a permanently-deployed Lambda behind API Gateway, modelled on
`../outrights-mip`. Two motivations:

1. **No manual server start.** The tempera template currently fails
   if `./scripts/run.sh` isn't running. Deploying makes the endpoint
   always available.
2. **No ambient AWS credentials for S3.** Local dev pulls one-shot
   samples from `s3://wol-samplebank/samples/` via `aws s3 sync`, which
   requires the developer to be logged into AWS regularly. In Lambda,
   the function's IAM role grants the read directly — no per-laptop
   credential refresh.

## Reference: how outrights-mip does it

Two Pulumi stacks + a Docker image baked by CodeBuild:

```
infra/pipeline/   ECR + CodeBuild + S3 artifacts + IAM        (one-time)
docker/           Dockerfile + buildspec.yml                   (build inputs)
scripts/stack/    deploy.py — pipeline → build → app           (orchestration)
infra/app/        Lambda (container) + API Gateway HTTP API    (per-deploy)
```

`scripts/stack/deploy.py`:

1. `pulumi up` on `infra/pipeline` (idempotent — almost always no-op).
2. SHA-256 the source + Dockerfile; if unchanged vs. the hash stored
   in S3, skip the build. Otherwise upload source.zip to S3, kick
   off CodeBuild, stream logs, capture the resulting image digest.
3. `pulumi up` on `infra/app` with the new image URI as config.

Auth: HTTP Basic via an `AUTH_TOKEN` env var in the format
`username:password`; the handler decodes the `Authorization: Basic
<b64>` header and string-compares. Token is set in `config/setenv.sh`,
sourced before deploy, injected into the Lambda's environment.

Why containers and Pulumi (not ZIP + CloudFormation): the Lambda
ZIP ceiling (250 MB unpacked) is below numpy + scipy alone, and
CloudFormation can't sequence "create ECR → wait for build → create
Lambda" in one stack. Long-form rationale in
`outboard-brain/posts/markdown/lambda-container-deployments.md`.

## Why this fits strudelbreaks

The server is the render coordinator from `app/main.py`. Heavy deps
in `requirements.txt`:

- `beatwav` — pulls numpy + scipy (ZIP ceiling busted on its own).
- `octapy` — pure Python, but tied to the others.
- `pydub` — uses stdlib `wave`, no ffmpeg needed for our paths.
- `fastapi` + `uvicorn` — small, but adds up.

So: container Lambda. Same shape as outrights-mip.

## Where the design needs to differ

A few constraints don't carry over cleanly:

### 1. FastAPI ↔ Lambda

outrights-mip writes raw Lambda handlers (`event, context`). We've
got a FastAPI app at `app.main:app` with two routers (`text_export`,
`binary_export`) and the `./scripts/run.sh` local dev story.

**Plan:** wrap with [Mangum](https://mangum.io/), the standard
FastAPI ↔ Lambda adapter. One file (`app/lambda.py`):

```python
from mangum import Mangum
from app.main import app
handler = Mangum(app, lifespan="off")
```

`mangum` adds ~50 KB to the image. Local `uvicorn app.main:app` keeps
working unchanged.

### 2. S3 access for one-shot samples

Current `app/export/common/sample_source.py`:

```python
ONESHOT_S3_URI = 's3://wol-samplebank/samples/'
ONESHOT_CACHE = REPO_ROOT / 'tmp' / 'oneshots'

def ensure_oneshots_synced(verbose=False):
    if any(ONESHOT_CACHE.iterdir()):
        return ONESHOT_CACHE
    subprocess.run(['aws', 's3', 'sync', ONESHOT_S3_URI,
                    str(ONESHOT_CACHE) + '/'], check=True, ...)
    return ONESHOT_CACHE
```

Three problems for Lambda:

- `aws` CLI isn't in the base image — would have to install.
- `tmp/oneshots/` lives in the repo; in Lambda we get `/tmp` only
  (10 GB if we ask, but it's per-instance).
- The bucket URI is hard-coded.

**Plan:**

- Replace `subprocess.run(['aws', 's3', 'sync', ...])` with a small
  boto3 sync (list_objects_v2 paginator → download per object).
  boto3 is in the Lambda base image, so no extra install.
- Take the bucket URI from an env var (`ONESHOT_S3_URI`) which is
  set as a Pulumi config in `infra/app` and injected into the
  Lambda environment. **This is the user's "no code smell" fix —
  the bucket is a deploy-time argument, not a hard-coded path.**
- Cache to `/tmp/oneshots/` in Lambda; cache to
  `<repo>/tmp/oneshots/` locally. Path resolution flips on env:
  `STRUDELBREAKS_TMP=/tmp` in Lambda, defaults to repo-relative
  otherwise.
- Same pattern for `SAMPLES_CACHE` (the rendered per-break WAVs).

The IAM role created by `infra/app` grants `s3:GetObject` and
`s3:ListBucket` on the configured bucket. No more `aws sso login`.

### 3. Response size limits

Lambda sync response: 6 MB. API Gateway HTTP API: 10 MB.

Tempera-realistic exports today (1–4 rows × `|C|=4..8`) are well
under that. For `ot-doom` projects with many rows + per-stem chains
the zip could approach the limit; we'd have to either:

- (a) cap response size and 413 with a hint to use fewer rows, or
- (b) write the artifact to S3 and respond with a 5-minute presigned URL.

**Plan:** ship (a) — explicit limit, clear error — and note (b)
as a follow-up if real exports hit it. Avoids a second S3 bucket
and a second IAM grant for the v1 deploy.

### 4. Cold-start oneshot sync

First request after a cold start triggers the full S3 sync (a few
hundred MB, possibly). That's a 10–30 s cold-start penalty.

**Plan:** accept it for v1 — exports already take a few seconds, so
the first export of the day is slow but acceptable. Mitigations if
it bites:

- Bake a small "common" oneshot subset into the container image at
  build time (sub-100 MB) so the cache is warm.
- Provisioned concurrency = 1 (~$5/month) to keep one warm
  instance.
- Switch to EFS-mounted oneshots so every cold start sees the
  populated cache.

### 5. Output filename via `Content-Disposition`

The current responses set `Content-Disposition: attachment;
filename="..."`. API Gateway HTTP API forwards response headers
verbatim, so this should pass through unchanged. Verify in a smoke
test post-deploy.

### 6. Auth

**Plan:** HTTP Basic via `AUTH_TOKEN` env var, same as outrights-mip.
FastAPI middleware reads it on every request:

```python
# app/main.py
from fastapi import HTTPException, Request

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    expected = os.environ.get("AUTH_TOKEN")
    if not expected:
        return await call_next(request)  # auth disabled (local dev)
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return Response("Unauthorized", status_code=401,
                        headers={"WWW-Authenticate": "Basic"})
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
    except Exception:
        return Response("Unauthorized", status_code=401)
    if decoded != expected:
        return Response("Unauthorized", status_code=401)
    return await call_next(request)
```

Tempera-side: send `Authorization: Basic <b64>` on every export
POST. The credentials live in tempera's `localStorage` (one-time
prompt on first export, optionally with "remember me"); never in
the script source on jsDelivr. Or simpler v1: an inline constant
the user pastes in once and accepts the friction.

CORS: API Gateway sends the same `Access-Control-Allow-Origin: *`
+ allow-headers `Authorization, Content-Type` so strudel.cc can
preflight + send the auth header.

### 7. Config that becomes Pulumi-managed

| Setting | Today | After deploy |
|---|---|---|
| Bucket name | `s3://wol-samplebank/samples/` (hard-coded) | `pulumi config set onesh ot_s3_uri ...`; `ONESHOT_S3_URI` env var on Lambda |
| Auth token | None (loopback) | `pulumi config set --secret auth_token ...`; `AUTH_TOKEN` env var on Lambda |
| Sample-rate constants | `app/export/common/devices.py` | unchanged — same in container |
| Tmp dir | `<repo>/tmp/` | `STRUDELBREAKS_TMP=/tmp` env var on Lambda |

Multiple buckets work the same way: separate config keys, separate
env vars, separate IAM grant statements per bucket. Today there's
just one; the structure supports more without code changes.

## Stack layout (proposed)

```
infra/pipeline/
  Pulumi.yaml
  Pulumi.strudelbreaks.yaml
  __main__.py              ECR + CodeBuild + S3 artifacts + IAM (lambda role)
  modules/
    artifacts.py
    registry.py
    lambda_iam.py          ← grants s3:GetObject on configured bucket
    codebuild_iam.py
    build_pipeline.py

infra/app/
  Pulumi.yaml
  Pulumi.strudelbreaks.yaml
  __main__.py              Lambda (container) + API Gateway HTTP API + permissions

docker/
  Dockerfile               python:3.12 base + numpy/scipy/pydub/beatwav + app/
  buildspec.yml            ECR login + cache + build + push

scripts/stack/
  deploy.py                hash-or-build-or-skip orchestration
  smoke.py                 zero-arg smoke test against deployed dev stack

config/
  setenv.sh                AWS_REGION, AUTH_TOKEN, optional --stage args
```

## Local dev: unchanged

`./scripts/run.sh` keeps working. No `AUTH_TOKEN` set → middleware
no-ops → loopback access stays open. `aws s3 sync` path stays as the
local sync (or we switch to boto3 there too — same code path either
way once the abstraction lands).

## Tempera client changes

- `SERVER_URL` becomes the deployed API Gateway URL (config knob in
  the script header). Optional fallback to `127.0.0.1:8000` on a
  toggle so the user can hit the local server when offline.
- Every `postExport` call gains an `Authorization: Basic ...` header.
  Credentials read from `localStorage` (prompt on first export,
  "remember me" via the existing `createPersistedStore`).
- New `notify(...)` already in place handles the 401 / 403 / 5xx
  responses without blocking playback.

## Deploy workflow

```bash
source config/setenv.sh                 # AUTH_TOKEN, AWS_REGION
python scripts/stack/deploy.py --stage dev    # pipeline → build → app
python scripts/stack/smoke.py --stage dev     # POST a fixture export, expect 200 + zip bytes
```

`--stage dev` and `--stage prod` map to different Pulumi stack files
+ different AWS profiles. Single-developer scope ⇒ probably just
one stage at first.

## Open questions

1. **Single bucket or many?** Today only `wol-samplebank`. If we ever
   add per-customer or per-template buckets, the config + IAM grant
   pattern scales — but the schema needs a small list-of-buckets
   shape rather than a single env var. Pick one now.
2. **Where do auth credentials live for the user?** localStorage
   prompt vs. inline constant in the pasted script vs. tempera fetch
   from a public URL. localStorage prompt is friendliest, but means
   one extra UI primitive. Inline constant is fastest to ship and
   acceptable for a single-developer workflow.
3. **Provisioned concurrency or accept cold starts?** Cost ≈ $5/mo
   for 1 warm instance vs. 10–30 s on the first export of a session.
   Default: accept cold start; revisit if it bites.
4. **Domain / TLS.** API Gateway gives a generated `*.execute-api`
   URL out of the box. Custom domain (`api.strudelbreaks.dev` or
   similar) needs Route 53 + ACM. Skip for v1 unless there's a
   reason.
5. **Rollback.** outrights-mip pattern uses image digests so rollback
   is `pulumi config set image_uri <prev-digest> && pulumi up`.
   Inherit that.

## Non-goals for v1

- Multi-region.
- Custom domain.
- Provisioned concurrency.
- S3-presigned-URL response path for >6 MB exports (defer; explicit
  413 is fine for now).
- Per-user credentials (single shared `AUTH_TOKEN` is fine for a
  single-developer workflow).
- WAF / rate limiting (the auth token is the rate limit).

## Order of work, once green-lit

1. Refactor `sample_source.py` to take bucket + tmp paths from env;
   replace `aws s3 sync` with boto3.
2. Add `AUTH_TOKEN` middleware to `app/main.py`. Keep no-op when unset.
3. Add `app/lambda.py` (Mangum wrapper).
4. Write `docker/Dockerfile` + `buildspec.yml`.
5. Write `infra/pipeline/__main__.py` + modules (mostly mirrored
   from outrights-mip, with our IAM grants substituted).
6. Write `infra/app/__main__.py` (Lambda + HTTP API + permissions).
7. Write `scripts/stack/deploy.py` (port from outrights-mip; same
   hash-or-skip logic).
8. Smoke test deployed dev.
9. Update tempera to point at the deployed URL + send auth header.
10. Update `README.md` and `docs/export/README.md` for the new
    workflow.

## References

- `../outrights-mip/infra/{pipeline,app}/__main__.py` — the
  pattern this plan tracks.
- `../outrights-mip/scripts/stack/deploy.py` — the orchestration
  script to port.
- `../outboard-brain/posts/markdown/lambda-container-deployments.md`
  — long-form rationale for the container + Pulumi approach.
- `../outrights-mip/app/outrights_mip/api/simulate/handler.py:49`
  — HTTP Basic auth via `AUTH_TOKEN` env var.
- `app/export/common/sample_source.py` — current S3 sync code that
  needs the boto3 + env-var refactor.
